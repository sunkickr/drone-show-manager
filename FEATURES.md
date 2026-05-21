# Features

## 1. Drone Show Manager Agent (MVP v1)

**Description**: Terminal chat agent that manages ADHOC drone shows in Jira project KAN. Answers status queries, surfaces what's missing to advance, helps create new shows, and refuses to advance shows without required fields. Evidence-enforced — never fabricates show data.

**Location**: `agent/`, `tools/`, `backend/`, `main.py`

**Details**:
- 5 tools: `list_shows`, `get_show`, `list_shows_by_field`, `create_show`, `transition_show`
- Tools return self-describing dicts (`{"status": "found" | "ambiguous" | "not_found", ...}` from `get_show`) so trace consumers can interpret outcomes without tool-specific schema knowledge
- Stack: OpenAI Agents SDK, OpenAI models (default `gpt-4.1-mini`), `atlassian-python-api`
- Show data stored in Jira description; parser handles three formats (plain `Field: value`, split-line `Field\n: value`, and Jira wiki markup `* *Field*: value` from the live REST API)
- Required-fields-per-status validation lives in `backend/show_schema.py` (no LLM judgment in refusals)
- Arize AX tracing via OpenInference over HTTP/protobuf (env-gated; no-op without `ARIZE_*` keys). One trace per detected workflow; trace renamed after the first tool call. `EnrichingTracingProcessor` in `backend/tracing.py` lifts `trace.metadata` onto the workflow span as `input.value` / `output.value` so trace-scoped evaluators resolve `{input}` and `{output}` deterministically.
- Pipeline: Sales → Contract → Show Design → Show Operations → Complete (no skipping)
- System prompt's "today's date" is injected dynamically at agent-build time (`date.today()`), not hardcoded.
- **Pronoun resolution**: when a user uses "it" / "the show" without naming a show and the immediately preceding turn named one, the agent resolves to that show (then calls `get_show` to confirm). System prompt enforces; tested in adversarial dataset.
- **Cross-show fabrication refusal**: requests to "copy contact info from another show" into a new show are refused as fabrication, even when the user explicitly asks.
- **History compaction**: at workflow boundaries, tool exchanges are dropped from history and the surviving user/assistant text turns are capped at 10 messages. Prevents context-window bloat (which previously produced punctuation-only responses) while preserving cross-workflow pronoun context.
- **Verified state**: 11/11 unit tests pass, 13/13 smoke tests pass against live Jira (see `tests/smoke_test.py`). 14/15 adversarial tests pass after the cross-show-copy fix (see `tests/dataset_smoke_test.py` against `drone_show_manager_adversarial_v1`).

## 2. Eval suite — datasets, experiments, evaluators (development + observability workflows)

**Description**: Full Arize workflow for the agent — offline experiments (development) plus live trace-scoped evaluators (observability). Includes a 13-row PRD dataset, a 15-row adversarial dataset, 7 offline evaluators, 3 online evaluators with eval-as-code definitions, and a documented iteration loop.

**Location**: `evals/` — `dataset.py`, `evaluators.py`, `run_experiment.py`, `README.md`, `online_evals.md`, `adversarial_tests.json`, `evaluators/` (eval-as-code JSON files); plus `tests/dataset_smoke_test.py` as the loop driver.

**Details (development workflow)**:
- Dataset `drone_show_manager_v1`: 13 rows of PRD verification prompts with rubric columns (`expected_kind`, `expected_tool`, `expected_keys`, `must_contain`, `must_not_contain`, `mutation_allowed`)
- Code evaluators: `contains_expected_keys`, `contains_required_substrings`, `no_forbidden_substrings`, `correct_refusal` (deterministic, free)
- LLM judges (default `gpt-5.4`): `evidence_grounded`, `right_tool_chosen` (hybrid code+LLM), `response_quality`
- All LLM judges share an `AGENT_RULES` block so they evaluate against the agent's actual operating contract
- `run_experiment.py` snapshots the Jira board and restores it afterward — mutation rows are reverted
- `--dry-run` flag runs evaluators without uploading; note the Arize SDK runs dry-run on only the first 10 rows

**Details (observability workflow)**:
- Three trace-scoped LLM judges configured in Arize on the `drone-show-manager` project: `evidence_grounded`, `right_tool_chosen`, `response_quality`. Templates documented in `evals/online_evals.md`.
- **Eval-as-code**: each judge has a canonical JSON definition in `evals/evaluators/<name>.json` containing template, model, classification choices, granularity, direction, etc. Updates flow file → `ax evaluators create-template-evaluator-version`. JSON file is committed alongside the push so the repo and Arize stay in sync.
- Adversarial dataset `drone_show_manager_adversarial_v1`: 15 hand-crafted prompts targeting authority pressure, urgency bypass, cross-show fabrication, backward transitions, false context, schema hallucination, etc. Source in `evals/adversarial_tests.json`.
- Live iteration loop (documented in `evals/README.md` "Iterating on live evaluators"): run smoke tests → wait for async scoring → fetch evals via `ax spans export` → diagnose agent vs judge → push new evaluator version → repeat.
- `tests/dataset_smoke_test.py` accepts a dataset name as CLI arg; works against both `drone_show_manager_v2` and `drone_show_manager_adversarial_v1`.

**Verified state**:
- Baseline experiment `baseline_gpt41mini_v6`: 6/7 offline evaluators at 100%, `contains_required_substrings` at 12/13
- Live evaluators: stable across multiple smoke runs after the `EnrichingTracingProcessor` fix in `backend/tracing.py` (without it, `evidence_grounded` produced a false positive on the pronoun-disambiguation adversarial test due to variable-resolution fallback)
- Adversarial run: 14/15 clean, 1 genuine catch (cross-show copy intent — fixed via system prompt rule)

## 3. Alyx Fix prototype (proposed product feature)

**Description**: Standalone Python script demonstrating the proposed `ax alyx fix` CLI — an agent-native interface where a coding agent (e.g. Claude Code) calls Alyx with project context plus a workflow tag, and Alyx returns a per-evaluator analysis plus optional fix patches. With `--apply`, the script patches the local `evals/evaluators/<name>.json` and pushes a new evaluator version via `ax evaluators create-template-evaluator-version`. Closes the live-evaluator iteration loop end-to-end.

**Location**: `prototype/` — `alyx_fix.py`, `README.md`, `SKILL.md`

**Details**:
- CLI shape matches the proposed product: `--project`, `--workflow`, `--evaluator`, `--context`, `--wait`, `--apply`
- Reads recent test traces from Arize via `ax spans export`; falls back to mock traces if Arize is unreachable so the demo always produces output
- Calls OpenAI with a system prompt approximating Alyx's role (analyze evaluator scores, decide agent vs judge, propose a precise `old_text`/`new_text` template patch)
- Output uses the per-evaluator block format from the proposal: `▸ evaluator: / analysis: / fix: / context-needed:`
- `--apply` mutates local JSON in `evals/evaluators/` and pushes a new evaluator version to Arize. Off by default for safety.
- Auto-discovers the OpenAI AI integration ID via `ax ai-integrations list` if `ARIZE_OPENAI_INTEGRATION_ID` is unset
- Skill file (`prototype/SKILL.md`) documents agent-side invocation; can be installed as a Claude Code skill by copying into `.claude/skills/`
- **Caveat**: this is a prototype, not the real feature. The real `ax alyx fix` would live inside Arize with platform-internal access to evaluator state, variable resolution, and update endpoints. The prototype works from `ax` CLI output plus a system prompt.
- **Verified state**: script parses, runs against live Arize traces, fetches evaluator JSON, produces structured analysis. Full demo loop (smoke → analyze → `--apply` → re-run) works end-to-end when all of `ARIZE_SPACE_ID`, `OPENAI_API_KEY`, and the `ax` CLI are configured.

## 4. Web chat UI (MVP)

**Description**: Single-page dark-themed chat frontend for the drone show manager, served by a thin FastAPI app. Same agent and same Arize trace lifecycle as the terminal REPL — both surfaces share `AgentSession`, so one trace covers each user workflow even when it spans multiple HTTP requests. Show results render as inline cards inside the chat: a small card per row for list results, and a big card with section-grouped fields for `get_show` that highlights any fields blocking the next status transition.

**Location**: `backend/web.py`, `agent/session.py`, `frontend/` (`index.html`, `app.js`, `style.css`), `.replit`

**Details**:
- **Stack**: FastAPI + vanilla HTML/JS, no framework. One Python service, one deploy.
- **Shared lifecycle**: `agent/session.py:AgentSession` extracts the trace lifecycle that previously lived in `drone_show_agent.run()` closures. The REPL was refactored to use it; the web uses it with `workflow_prefix="frontend:"`. No duplicated lifecycle code.
- **Trace per workflow, not per request**: a multi-turn create/transition flow stays in one Arize trace until the agent's reply no longer demands more input or a mutation tool fires. Same boundary detection as the REPL (`_INTAKE_MARKERS`, `MUTATION_TOOLS`).
- **`frontend:` workflow-name prefix**: web traces are filterable in Arize and don't mix with `smoke:` / `dataset_smoke:` traces in evaluator analyses.
- **Cards as a side-channel**: server walks `result.new_items`, shapes the latest tool outputs into a `cards: []` array next to the agent's text. The agent's text — and therefore `output.value` on the workflow span — is unchanged, so the three live evaluators (`evidence_grounded`, `right_tool_chosen`, `response_quality`) score web traffic with the same templates as REPL traffic.
- **Card kinds**: `small` (key/summary/status, with `highlight: created|transitioned` for mutation confirmations) and `big` (full `show` payload from `get_show`, with `missing_for_next_status` fields highlighted).
- **Clickable cards**: any small card with a key is clickable — it sends `Tell me about <key>` so the user can drill into a show without typing. Typing still works.
- **Show-list element**: when a turn returns 2+ plain show cards (a list result), they render as one bordered `.show-list` box with a "N shows" header and a body capped at ~4 rows (`max-height: 208px`) that scrolls internally — so a long list (e.g. "who are the sales people" listing every show) doesn't flood the chat. Single results, big cards, and post-mutation highlighted cards still render standalone. Grouping decision is client-side in `frontend/app.js` (`renderShowList`); the API `cards[]` shape is unchanged.
- **Greeting + chips**: `POST /api/session` returns a hardcoded `GREETING` constant plus four example chips (both matching the system prompt's bullets) — no agent round-trip, so the greeting paints instantly on load. The agent runs for the first time on the user's first real message. Chips disappear after the first user message. If the system prompt greeting changes, update the `GREETING` constant in `backend/web.py`.
- **Empty-state layout**: on load, the greeting + chips + composer are centered in the viewport (`body.state-empty`); after the first message the composer docks to the bottom and messages scroll above it (`body.state-active`). Toggled by a body class in `frontend/app.js`.
- **Intake**: agent behavior unchanged — fields are still collected one at a time during create/transition flows. Forms-in-chat is explicit v2.
- **Hosting**: `.replit` invokes `uvicorn backend.web:app --host 0.0.0.0 --port $PORT`. Sessions live in memory (single-process). Replit Secrets supply API keys.
- **Theme**: dark graphite background, off-white text, accent on post-mutation pills and missing-field labels. Inspired by adhoccreativehouse.com. No images, no icons in MVP.
- **Verified state**: REPL still works (`python main.py`); backend serves locally (`uvicorn backend.web:app --reload`); existing unit tests pass.
