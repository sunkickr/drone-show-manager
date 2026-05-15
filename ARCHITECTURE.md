# Architecture

## Data flow

```
user (terminal)
    │
    ▼
agent/drone_show_agent.py  ── OpenAI Agents SDK loop ──┐
    │                                                   │
    │  calls tools                                      │ each LLM call,
    ▼                                                   │ tool call, and
tools/jira_tools.py         (5 tools, validated)        │ span exported
    │                                                   │ to Arize AX
    ▼                                                   │
backend/jira_client.py      (REST via atlassian-python-api)
    │
    ▼
Jira Cloud (project KAN)
```

`backend/show_schema.py` and `backend/show_format.py` are pure-Python helpers — no I/O — used by the tools to validate and (de)serialize show descriptions.

## How show data is stored

All show fields live in the Jira ticket's **description**. The description is a series of named sections, each with `Field: value` lines:

```
Contact Info
Full Name: Diego Fernández
Company: Patagonia Adventure Co
...

Lead Info
Lead Source: Web
Lead Status: Won
...
```

Some legacy mock data uses `Field\n: value` (newline before colon). The parser handles both; the writer always emits the single-line form.

## Schema (sections required per status)

| Status to enter   | Required sections                                                       |
|-------------------|-------------------------------------------------------------------------|
| Sales             | Contact Info, Lead Info                                                 |
| Contract          | Contract Info                                                           |
| Show Design       | Show Design Info                                                        |
| Show Operations   | Event Details, Permit Requirements and Timeline, Gear List, Media Capture Plan |
| Complete          | Drone Show Debrief                                                      |

Each section's field list lives in `backend/show_schema.SECTION_FIELDS`. To enter a status, the show must have **every** section required by **every previous status** plus the sections for the target status, with each field populated (`N/A` is acceptable, blank is not).

## Tools (canonical reference)

| Tool                  | Args                                                          |
|-----------------------|---------------------------------------------------------------|
| `list_shows`          | `status: str \| None`                                         |
| `get_show`            | `query: str`                                                  |
| `list_shows_by_field` | `section: str, field: str, value: str \| None, status: str \| None` |
| `create_show`         | `fields: dict`                                                |
| `transition_show`     | `key: str, target_status: str, new_fields: dict \| None`      |

## Environment variables

| Var                  | Required | Purpose                                                |
|----------------------|----------|--------------------------------------------------------|
| `OPENAI_API_KEY`     | yes      | Auth for OpenAI                                        |
| `OPENAI_MODEL`       | no       | Default `gpt-4.1-mini`                                 |
| `OPENAI_JUDGE_MODEL` | no       | Default `gpt-5.4` — used by v2 judge                   |
| `JIRA_URL`           | yes      | e.g. `https://yourcompany.atlassian.net`               |
| `JIRA_USER_EMAIL`    | yes      | Email of the account that owns the API token           |
| `JIRA_API_TOKEN`     | yes      | Atlassian API token                                    |
| `JIRA_PROJECT_KEY`   | no       | Default `KAN`                                          |
| `ARIZE_SPACE_ID`     | no       | If set with `ARIZE_API_KEY`, traces export to Arize AX |
| `ARIZE_API_KEY`      | no       | "                                                      |
| `ARIZE_PROJECT_NAME` | no       | Default `drone-show-manager`                           |

## Evals (development workflow)

`evals/` implements the Arize development workflow — see `evals/README.md` for full usage. Quick orientation:

```
evals/
├── dataset.py          13-row dataset (PRD verification prompts + rubric columns)
├── evaluators.py       7 evaluators (3 code, 4 LLM/hybrid) + shared AGENT_RULES block
└── run_experiment.py   snapshot board → run agent over dataset → score → restore board
```

Key design points:

- **Evaluators are the shared currency.** The same functions in `evaluators.py` can score an offline experiment (current use) or live traces (online evals, future). Code evaluators are deterministic and free; LLM judges use `OPENAI_JUDGE_MODEL`.
- **All LLM judges receive `AGENT_RULES`.** A judge only knows what's in its prompt — without the agent's operating contract (pipeline order, never-fabricate, one-question intake) it penalizes correct behavior. `AGENT_RULES` in `evaluators.py` must stay in sync with the agent's `SYSTEM_PROMPT`.
- **Each judge has one narrow job.** `evidence_grounded` judges only fabrication (with tool outputs as evidence); `response_quality` judges only writing clarity (explicitly told to *assume* facts are correct). Scope creep between judges was the main calibration bug during development.
- **The experiment runner is board-safe.** It snapshots all tickets before the run and restores them after — the `mutation` row (Lisbon → Complete) is reverted automatically.
- **Task functions must be async.** Arize's `run_experiment` manages its own asyncio loop; the task awaits `Runner.run` (not `Runner.run_sync`). The task parameter must be named `dataset_row` — Arize binds by parameter name.

## Where to extend

- **New tool**: add a function to `tools/jira_tools.py`, register it in `agent/drone_show_agent.py`.
- **New required field**: add to `SECTION_FIELDS` in `backend/show_schema.py`. All downstream validation picks it up automatically.
- **New eval row**: append to `EXAMPLES` in `evals/dataset.py`.
- **New evaluator**: write a function returning `EvaluationResult`, add it to `EVALUATORS` in `evals/evaluators.py`.
- **Frontend**: `frontend/` is reserved. A later iteration will likely add a thin FastAPI server that wraps the same `agent.drone_show_agent.run()` entry point used by `main.py`.
- **Online evals**: the `evaluators.py` functions can be pointed at live traces fetched from Arize — the next step for the observability workflow.

## Smoke test pattern

`tests/smoke_test.py` runs every PRD verification prompt against live Jira. The shape:

1. `snapshot_board()` → captures `{key: {status, description}}` for every ticket.
2. For each test case: open a named trace, send the prompt, run `Runner.run_sync`, assert against `must_contain` / `must_not_contain`.
3. In a `finally` block: `restore_board(snapshot)` deletes new tickets, reverts status transitions, restores descriptions.

This pattern is the obvious base for v2 evals — replace the string assertions with an LLM judge and the same skeleton becomes a regression harness.

## Tracing groupings

The agent uses three of the seven OpenInference span kinds: **Agent** (the agent loop), **LLM** (each OpenAI completion), and **Tool** (each function call). We deliberately do not use **Chain** spans — chains describe deterministic application orchestration, but our agent's orchestration lives inside the LLM's reasoning, not in code.

Trace boundaries are set per **user workflow**, not per turn:

| Surface                       | Trace boundary                          | Workflow name in Arize                                  |
|-------------------------------|------------------------------------------|---------------------------------------------------------|
| `python main.py`              | One trace per detected workflow         | Dynamically set from the first tool the agent calls — `list_shows`, `get_show`, `create_show`, `transition_show`, etc. |
| `python tests/smoke_test.py`  | One trace per test case                  | `smoke:01_list_contract`, `smoke:02_list_sales`, …      |
| `python tests/verify_workflow_traces.py` | One trace per scenario        | Workflow is renamed after the first tool call           |

### How workflow boundaries are detected

The REPL loop in `agent/drone_show_agent.py` opens a new trace when no workflow is currently active, and closes it when the agent's last response indicates the workflow is done:

- **Close immediately** if a mutation tool (`create_show` or `transition_show`) fired successfully — the workflow accomplished its goal.
- **Keep open** if the agent's last paragraph contains a structured-input demand: `"what is the …"`, `"please provide"`, `"which one would you like"`, etc. (See `_INTAKE_MARKERS` in `drone_show_agent.py` for the full list.)
- **Close** otherwise — including refusals, status answers, and polite "anything else?" closings.

This means:

- A single-turn lookup ("What's in Contract?") = one trace
- A multi-turn create flow (~16 user turns answering field questions) = one trace, with ~16 Agent spans nested inside
- An ambiguous lookup that requires clarification ("Tell me about Spain show" → "Which one would you like?") = one trace spanning both turns

The first tool the agent calls inside a workflow renames the trace via `span.update_name()` so it's immediately identifiable in Arize.
