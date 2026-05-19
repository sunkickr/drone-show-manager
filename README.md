# Drone Show Manager (Jira Bot)

A terminal chat agent that helps ADHOC manage drone shows in Jira. Built as an MVP to test out the workflow before adding a web frontend.

The agent uses OpenAI models, the Jira REST API, and the OpenAI Agents SDK. Every step is traced to Arize AX so you can observe and evaluate it.

## Architecture

```
                          ┌──────────────────────┐
                          │  User (terminal)     │
                          │  $ python main.py    │
                          └──────────┬───────────┘
                                     │  user message
                                     ▼
                ┌──────────────────────────────────────┐
                │   agent/drone_show_agent.run()        │
                │   • Workflow-trace lifecycle          │
                │   • Tool-call streaming to stderr     │
                └──────────────┬───────────────────────┘
                               │
                               ▼
      ┌─────────────────────────────────────────────────────────┐
      │       OpenAI Agents SDK Agent  —  gpt-4.1-mini          │
      │  System prompt: pipeline rules · mandatory lookup ·     │
      │                 refusal structure · field→section map   │
      └──┬──────────┬────────────────┬──────────────┬─────────┬─┘
         │          │                │              │         │
         ▼          ▼                ▼              ▼         ▼
      ┌─────┐  ┌──────┐  ┌────────────────────┐  ┌──────┐  ┌────────────┐
      │list_│  │ get_ │  │ list_shows_by_     │  │create│  │ transition │
      │shows│  │ show │  │ field              │  │ show*│  │ show*      │
      └──┬──┘  └──┬───┘  └─────────┬──────────┘  └──┬───┘  └──────┬─────┘
         └────────┴────────────────┴────────────────┴─────────────┘
                                   │
                                   ▼
              ┌──────────────────────────────────────────────┐
              │  backend/                                    │
              │   jira_client.py  — REST wrapper             │
              │   show_format.py  — parser + writer          │
              │   show_schema.py  — rubric (source of truth) │
              └──────────────────┬───────────────────────────┘
                                 │  HTTPS
                                 ▼
                       ┌────────────────────┐
                       │   Jira Cloud       │
                       │   project KAN      │
                       └────────────────────┘

   Observability — branches off every Agent / LLM / Tool span:
     OpenInference instrumentation  →  Arize AX
        ├─ Online LLM judges score live traces (evals/online_evals.md)
        ├─ Offline experiments via evals/run_experiment.py (dataset → run → score)
        └─ Prompt Playground tests prompt variants against the dataset

   * mutates Jira state — guarded by adjacency + required-field checks
```

The agent is one reasoning loop that picks one of five tools per turn. Two of them (`create_show`, `transition_show`) mutate Jira state and are guarded by the schema in `backend/show_schema.py` — the LLM never decides whether a transition is valid; the schema does. Everything else flows through the same backend layer that knows how to parse and write Jira description blocks (three formats supported: plain `Field: value`, split-line, and Jira wiki markup).

## Setup

```bash
git clone <repo>
cd drone-show-manager
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in OPENAI_API_KEY, JIRA_* and (optional) ARIZE_* keys
python main.py
```

The agent runs locally without Arize keys — tracing just no-ops if `ARIZE_SPACE_ID` or `ARIZE_API_KEY` is missing.

## What it can do

Five tools, scoped to Jira project `KAN`:

1. `list_shows(status=None)` — list active shows, or shows in a specific status
2. `get_show(query)` — details for one show by key or name, plus what's missing to advance
3. `list_shows_by_field(section, field, value=None, status=None)` — find shows by a field inside the description, or pull a field across shows
4. `create_show(fields)` — make a new show with Contact + Lead Info
5. `transition_show(key, target_status, new_fields)` — advance one step through the pipeline (Sales → Contract → Show Design → Show Operations → Complete) with required-field validation

## Sample prompts

- *"What active shows do we have?"*
- *"What's in Contract?"*
- *"Tell me about the Toronto show"*
- *"What's missing on Auckland to move forward?"*
- *"Create a show for SkyTech Berlin in Berlin, Germany"*
- *"Move Reykjavik to Show Design"*

## Project layout

```
agent/      system prompt + Agents SDK Agent
tools/      the 5 tools, decorated for the SDK
backend/    Jira client, show schema, parser/writer, Arize tracing
tests/      parser/writer round-trip tests
frontend/   reserved for a later iteration
```

## Tests

```bash
pytest                          # unit tests (parser/schema, ~11 cases)
python tests/smoke_test.py      # 13-prompt end-to-end test against live Jira
```

`pytest` covers the show description parser — the load-bearing piece (every refusal and "what's missing" depends on it). It auto-skips `tests/smoke_test.py` because pytest only collects files named `test_*.py`.

`tests/smoke_test.py` runs all 13 PRD verification prompts against the real Jira board and reports pass/fail. It snapshots the board before each run and **restores it afterward** — any tickets created during tests are deleted, any status/description changes are reverted. Safe to run repeatedly.

Each smoke test prompt is wrapped in its own named trace (`smoke:01_list_contract`, etc.) so they're easy to filter in the Arize AX dashboard.

## Observability

When `ARIZE_SPACE_ID` and `ARIZE_API_KEY` are set, every turn, tool call, and OpenAI call shows up as a span in Arize AX under the project named in `ARIZE_PROJECT_NAME` (default `drone-show-manager`).

Traces are grouped at the **workflow** level — a single lookup is one trace, while a multi-turn create or transition flow is also one trace that spans all the intake turns. The boundary is detected automatically: a workflow trace closes when a mutation tool fires successfully, or when the agent's response no longer demands more user input. See `ARCHITECTURE.md` for the heuristic.

The exporter uses HTTP/protobuf to `https://otlp.arize.com/v1/traces`. We bypass `arize-otel`'s `register()` because of a bug where `Endpoint.ARIZE` isn't unwrapped from its enum representation when HTTP transport is requested.

LLM-as-judge evals are planned for v2, after the agent stabilizes.
