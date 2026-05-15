# Features

## 1. Drone Show Manager Agent (MVP v1)

**Description**: Terminal chat agent that manages ADHOC drone shows in Jira project KAN. Answers status queries, surfaces what's missing to advance, helps create new shows, and refuses to advance shows without required fields. Evidence-enforced — never fabricates show data.

**Location**: `agent/`, `tools/`, `backend/`, `main.py`

**Details**:
- 5 tools: `list_shows`, `get_show`, `list_shows_by_field`, `create_show`, `transition_show`
- Stack: OpenAI Agents SDK, OpenAI models (default `gpt-4.1-mini`), `atlassian-python-api`
- Show data stored in Jira description; parser handles three formats (plain `Field: value`, split-line `Field\n: value`, and Jira wiki markup `* *Field*: value` from the live REST API)
- Required-fields-per-status validation lives in `backend/show_schema.py` (no LLM judgment in refusals)
- Arize AX tracing via OpenInference over HTTP/protobuf (env-gated; no-op without `ARIZE_*` keys). One trace per detected workflow; trace renamed after the first tool call.
- Pipeline: Sales → Contract → Show Design → Show Operations → Complete (no skipping)
- System prompt's "today's date" is injected dynamically at agent-build time (`date.today()`), not hardcoded.
- **Verified state**: 11/11 unit tests pass, 13/13 smoke tests pass against live Jira with board correctly restored after mutations (see `tests/smoke_test.py`)

## 2. Eval suite — datasets, experiments, evaluators (development workflow)

**Description**: The Arize development workflow for the agent. A 13-row dataset of PRD verification prompts, an experiment runner that runs the agent against it, and 7 evaluators (3 deterministic code checks + 4 LLM-as-judge / hybrid). Each run is captured as an Arize experiment so prompt/model changes can be compared.

**Location**: `evals/` — `dataset.py`, `evaluators.py`, `run_experiment.py`, `README.md`

**Details**:
- Dataset `drone_show_manager_v1`: 13 rows, each with input + rubric columns (`expected_kind`, `expected_tool`, `expected_keys`, `must_contain`, `must_not_contain`, `mutation_allowed`)
- Code evaluators: `contains_expected_keys`, `contains_required_substrings`, `no_forbidden_substrings`, `correct_refusal` (deterministic, free)
- LLM judges (default `gpt-5.4`): `evidence_grounded`, `right_tool_chosen` (hybrid code+LLM), `response_quality`
- All LLM judges share an `AGENT_RULES` block so they evaluate against the agent's actual operating contract (without it, judges penalize correct behavior like one-question intake and refusals)
- `run_experiment.py` snapshots the Jira board and restores it afterward — mutation rows (e.g. moving Lisbon to Complete) are reverted
- `--dry-run` flag runs evaluators without uploading; note the Arize SDK runs dry-run on only the first 10 rows
- **Verified state**: baseline experiment `baseline_gpt41mini_v6` — 6/7 evaluators at 100%, `contains_required_substrings` at 12/13 (genuine finding: the agent sometimes gives a "lazy" refusal that skips looking up the show's current status)
