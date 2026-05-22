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
- **Clickable cards**: any small card with a key is clickable — it opens that show's editable large card via a direct `GET /api/show/{key}` fetch (no agent turn, no token cost). Typing still works. See Feature 5 for the editable form.
- **Show-list element**: when a turn returns 2+ plain show cards (a list result), they render inside one bordered `.show-list` box capped at ~4 cards (`max-height: 252px`) with its own always-visible scrollbar — so a long list (e.g. "who are the sales people" listing every show) scrolls within the box instead of flooding the main chat scroll. Cards keep their individual styling; `flex: 0 0 auto` stops them compressing to fit. The scrollbar is explicitly styled (`overflow-y: scroll` + `::-webkit-scrollbar`) because macOS overlay scrollbars are invisible until you scroll, which hid the affordance. Single results, big cards, and post-mutation highlighted cards still render standalone. Grouping is client-side in `frontend/app.js` (`renderShowList`); the API `cards[]` shape is unchanged.
- **Greeting + chips**: `POST /api/session` returns a hardcoded `GREETING` constant plus four example chips (both matching the system prompt's bullets) — no agent round-trip, so the greeting paints instantly on load. The agent runs for the first time on the user's first real message. Chips disappear after the first user message. If the system prompt greeting changes, update the `GREETING` constant in `backend/web.py`.
- **Empty-state layout**: on load, the greeting + chips + composer are centered in the viewport (`body.state-empty`); after the first message the composer docks to the bottom and messages scroll above it (`body.state-active`). Toggled by a body class in `frontend/app.js`.
- **Intake**: agent behavior unchanged — fields are still collected one at a time during create/transition flows. Forms-in-chat is explicit v2.
- **Hosting**: `.replit` invokes `uvicorn backend.web:app --host 0.0.0.0 --port $PORT`. Sessions live in memory (single-process). Replit Secrets supply API keys.
- **Theme**: dark graphite background, off-white text, accent on post-mutation pills and missing-field labels. Inspired by adhoccreativehouse.com. No images, no icons in MVP.
- **No-cache headers**: a middleware sets `Cache-Control: no-cache` on `/` and `/static/*` so the browser always pulls fresh `index.html` / `app.js` / `style.css` during iteration (stale caches were masking UI changes).
- **Verified state**: REPL still works (`python main.py`); backend serves locally (`uvicorn backend.web:app --reload`); existing unit tests pass.

## 5. Show edit & transition form (web)

**Description**: The large show card in the web UI is an editable form. Clicking any small card opens it; the user can expand collapsed sections, edit any field (including blank/missing ones), save the changes, and then transition the show one status forward. Editing fields is a **web-only UI capability** — the agent deliberately does not have it (its contract is create/transition only). Updating and transitioning are always two separate actions: a show can only advance once its required fields for the next status are saved and validated.

**Location**: `backend/show_service.py`, `backend/web.py` (`/api/show/{key}` GET/update/transition), `frontend/app.js` (`renderBigCard`, `openShowCard`, `saveEdits`, `doTransition`), `frontend/style.css`

**Details**:
- **Shared service**: `backend/show_service.py` holds `summarize` / `parsed_show` (moved from `tools/jira_tools.py`, which now imports them — single show shape for agent + web), plus `fetch_show`, `update_show_fields`, `transition_show_status`, and `form_sections`. All description I/O goes through `show_format`; all validation through `show_schema` (per CLAUDE.md). The agent tools are unchanged in behavior.
- **Form model**: `form_sections(show)` builds the per-section field list the form renders — every field with its current value and a `missing` flag (blank AND required to reach the next status). A section is shown if it's populated OR required to advance, keeping the form focused. Schema knowledge stays server-side; the frontend just renders `show.form`.
- **Three endpoints (web-only, untraced — deterministic CRUD, not agent reasoning)**:
  - `GET /api/show/{key}` → parsed show + form model.
  - `POST /api/show/{key}/update` → merge non-blank field values, validate field/section names against the schema, persist via `update_description`. Never transitions.
  - `POST /api/show/{key}/transition` → advance one step; refuses if not adjacent or if any required field for the target is still blank. Takes no field values.
- **Click-to-edit**: the big card is read-only by default (sections collapsed behind titles, e.g. `CONTACT INFO`, with a `missing info` badge on any section short of fields for the next status). Clicking the card enters edit mode — the card border highlights (`.card-editing`), fields become inputs, and sections still needing info auto-expand. A `Save changes` button commits; `Cancel` discards.
- **Transition gating**: the `Transition to <next>` button shows only in view mode and is disabled until `missing_for_next_status` is empty. Because edits must be saved (a separate call) before the missing list clears, a user can never update and transition in one move — enforced both in the UI (disabled button) and server-side (`transition_show_status` rejects blank required fields).
- **Big cards from the agent are editable too**: when the agent calls `get_show`, `extract_cards` enriches the big card with `form_sections`, so a card opened by typing "tell me about Toronto" is the same editable form as one opened by clicking.
- **Verified state**: full browser E2E confirmed — click small card → form opens (collapsed accordion, missing section badged, transition disabled) → click to edit (highlight, inputs, missing section expanded) → fill + Save (missing badge clears, transition enables) → Transition (status advances, card refreshes to new status with the next set of missing sections). Board mutated during the test was snapshot-restored. 11/11 unit tests pass.
