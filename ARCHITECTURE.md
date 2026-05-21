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
| `create_show`         | `summary: str, fields: dict`                                  |
| `transition_show`     | `key: str, target_status: str, new_fields: dict \| None`      |

### Tool return conventions

Tools return self-describing dicts. `get_show` returns one of:

- `{"status": "found", "show": {...}}` — single match
- `{"status": "ambiguous", "query": ..., "candidates": [...], "message": ...}` — 2+ summary matches
- `{"status": "not_found", "query": ..., "message": ...}` — no match

The `status` field is the agent's branching signal. The `message` field exists so trace consumers (evaluators, log readers) can understand the outcome without knowing tool-specific schema. Mutating tools (`create_show`, `transition_show`) return `{"created": {...}}` / `{"transitioned": {...}}` on success or `{"error": ..., "missing": [...]}` on validation failure.

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

`evals/` implements both the Arize development workflow (offline experiments) and the observability workflow (live/online evaluators). See `evals/README.md` for full usage. Quick orientation:

```
evals/
├── dataset.py                 13-row offline dataset (PRD prompts + rubric columns)
├── evaluators.py              7 offline evaluators (3 code, 4 LLM/hybrid)
├── run_experiment.py          snapshot → run agent → score → restore (offline)
├── online_evals.md            full templates for the 3 trace-scoped judges in Arize
├── evaluators/                eval-as-code: canonical JSON per online evaluator
│   ├── evidence_grounded.json
│   ├── right_tool_chosen.json
│   └── response_quality.json
└── adversarial_tests.json     15 adversarial prompts uploaded as dataset
                                drone_show_manager_adversarial_v1
```

Key design points:

- **Evaluators are the shared currency.** The same functions in `evaluators.py` can score an offline experiment (current use) or live traces (online evals, future). Code evaluators are deterministic and free; LLM judges use `OPENAI_JUDGE_MODEL`.
- **All LLM judges receive `AGENT_RULES`.** A judge only knows what's in its prompt — without the agent's operating contract (pipeline order, never-fabricate, one-question intake) it penalizes correct behavior. `AGENT_RULES` in `evaluators.py` must stay in sync with the agent's `SYSTEM_PROMPT`.
- **Each judge has one narrow job.** `evidence_grounded` judges only fabrication (with tool outputs as evidence); `response_quality` judges only writing clarity (explicitly told to *assume* facts are correct). Scope creep between judges was the main calibration bug during development.
- **The experiment runner is board-safe.** It snapshots all tickets before the run and restores them after — the `mutation` row (Lisbon → Complete) is reverted automatically.
- **Task functions must be async.** Arize's `run_experiment` manages its own asyncio loop; the task awaits `Runner.run` (not `Runner.run_sync`). The task parameter must be named `dataset_row` — Arize binds by parameter name.
- **Online evaluators are eval-as-code.** The three trace-scoped judges configured in Arize (`evidence_grounded`, `right_tool_chosen`, `response_quality`) have their canonical definitions in `evals/evaluators/*.json`. Updates flow file → `ax evaluators create-template-evaluator-version`. See the "Iterating on live evaluators" section in `evals/README.md` for the five-step iteration loop.

## Where to extend

- **New tool**: add a function to `tools/jira_tools.py`, register it in `agent/drone_show_agent.py`.
- **New required field**: add to `SECTION_FIELDS` in `backend/show_schema.py`. All downstream validation picks it up automatically.
- **New eval row**: append to `EXAMPLES` in `evals/dataset.py`.
- **New evaluator**: write a function returning `EvaluationResult`, add it to `EVALUATORS` in `evals/evaluators.py`.
- **Frontend**: `frontend/` ships a single-page chat UI (vanilla HTML/JS) served by `backend/web.py`. Extend by adding card kinds in `extract_cards()` and matching renderers in `frontend/app.js`.
- **Online evaluator update**: edit `evals/evaluators/<name>.json`, push via `ax evaluators create-template-evaluator-version`. Keep the JSON file as source of truth; commit alongside the push.
- **New adversarial test**: append to `evals/adversarial_tests.json` and re-upload as a new dataset version, OR add to the existing `drone_show_manager_adversarial_v1` dataset via the Arize UI.
- **Agent-to-Alyx prototype**: `prototype/alyx_fix.py` demonstrates the proposed `ax alyx fix` CLI for autonomous evaluator iteration. See `prototype/README.md`.

## Smoke test pattern

`tests/smoke_test.py` runs every PRD verification prompt against live Jira. The shape:

1. `snapshot_board()` → captures `{key: {status, description}}` for every ticket.
2. For each test case: open a named trace, send the prompt, run `Runner.run_sync`, assert against `must_contain` / `must_not_contain`.
3. In a `finally` block: `restore_board(snapshot)` deletes new tickets, reverts status transitions, restores descriptions.

`tests/dataset_smoke_test.py` is the dataset-driven variant: takes a dataset name as a CLI argument (e.g. `drone_show_manager_v2` or `drone_show_manager_adversarial_v1`), fetches the prompts from Arize via `ax datasets export`, and runs them through the same snapshot/restore pattern. Used for adversarial-test runs and for the live-evaluator iteration loop.

## Tracing groupings

The agent uses three of the seven OpenInference span kinds: **Agent** (the agent loop), **LLM** (each OpenAI completion), and **Tool** (each function call). We deliberately do not use **Chain** spans — chains describe deterministic application orchestration, but our agent's orchestration lives inside the LLM's reasoning, not in code.

Trace boundaries are set per **user workflow**, not per turn:

| Surface                       | Trace boundary                          | Workflow name in Arize                                  |
|-------------------------------|------------------------------------------|---------------------------------------------------------|
| `python main.py`              | One trace per detected workflow         | Dynamically set from the first tool the agent calls — `list_shows`, `get_show`, `create_show`, `transition_show`, etc. |
| `uvicorn backend.web:app`     | One trace per session workflow (NOT per HTTP request) | Same as REPL but prefixed `frontend:` — `frontend:list_shows`, `frontend:get_show`, … |
| `python tests/smoke_test.py`  | One trace per test case                  | `smoke:01_list_contract`, `smoke:02_list_sales`, …      |
| `python tests/verify_workflow_traces.py` | One trace per scenario        | Workflow is renamed after the first tool call           |

### How workflow boundaries are detected

The shared `AgentSession` in `agent/session.py` opens a new trace when no workflow is currently active, and closes it when the agent's last response indicates the workflow is done:

- **Close immediately** if a mutation tool (`create_show` or `transition_show`) fired successfully — the workflow accomplished its goal.
- **Keep open** if the agent's last paragraph contains a structured-input demand: `"what is the …"`, `"please provide"`, `"which one would you like"`, etc. (See `_INTAKE_MARKERS` in `drone_show_agent.py` for the full list.)
- **Close** otherwise — including refusals, status answers, and polite "anything else?" closings.

This means:

- A single-turn lookup ("What's in Contract?") = one trace
- A multi-turn create flow (~16 user turns answering field questions) = one trace, with ~16 Agent spans nested inside
- An ambiguous lookup that requires clarification ("Tell me about Spain show" → "Which one would you like?") = one trace spanning both turns

The first tool the agent calls inside a workflow renames the trace via `span.update_name()` so it's immediately identifiable in Arize.

### Workflow-span enrichment

The OpenAI Agents SDK's `agents.trace()` doesn't populate `input.value` or `output.value` on the workflow's OTel root span — without help, those attributes are empty and Arize evaluators' trace-scoped `{input}` / `{output}` variables fall back to inconsistent descendant spans. `backend/tracing.py` defines `EnrichingTracingProcessor`, a subclass of `OpenInferenceTracingProcessor` that reads `trace.metadata["input"]` and `trace.metadata["output"]` and writes them onto the OTel root span at `on_trace_start` / `on_trace_end`. `AgentSession` passes metadata when opening a trace and mutates the same dict with `output` before close; the REPL, the web frontend, and the smoke-test drivers all flow through it. Without the processor, trace-scoped evaluators produce false positives (see `evals/online_evals.md` for the failure pattern).

### History compaction at workflow boundaries

`AgentSession._close()` compacts `history` when a workflow closes — discarding all tool calls and tool outputs, capping the surviving user/assistant text messages at `_MAX_HISTORY_MESSAGES = 10` (in `agent/drone_show_agent.py`). This prevents context-window bloat across long sessions (which previously degraded the model into punctuation-only responses) while preserving enough conversational history for pronoun resolution ("move it to show design" referring to a show discussed a few workflows ago).

## Frontend / web surface

`backend/web.py` is a FastAPI app that serves the chat UI from `frontend/` and exposes two routes: `POST /api/session` (mint a session, send `"Hello"` through the agent, return greeting + four example chips) and `POST /api/chat` (run one turn on an existing session). Each session holds one `AgentSession(workflow_prefix="frontend:")`. The REPL and the web are two surfaces over the same lifecycle code — there is no duplicated trace-lifecycle logic.

Show cards are a **server side-channel**, not part of the agent's text response. After each turn, the server walks `result.new_items` and shapes the latest tool outputs into a `cards: []` array shipped alongside the agent's text. This deliberately keeps `output.value` on the workflow span identical to what the REPL produces, so the three live evaluators (`evidence_grounded`, `right_tool_chosen`, `response_quality`) score web traffic with the same templates as REPL traffic — no eval re-templating required.

Card kinds:
- **small** — for list rows (`list_shows`, `list_shows_by_field`) and post-mutation confirmations (`create_show`, `transition_show`). Renders `{key, summary, status}`. Post-mutation cards carry `highlight: "created" | "transitioned"` so the UI accents the new status pill.
- **big** — for `get_show` results with `status: found`. Ships the full `show` payload (sections, next_status, missing_for_next_status) and the frontend highlights the fields blocking the next status transition.

The session registry is in-memory (`sessions: dict[str, AgentSession]`). The MVP runs as a single Replit process so this is sufficient; a Vercel-style serverless deploy would need an external store.
