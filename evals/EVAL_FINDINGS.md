# Findings from Eval / Experiment / Playground Work

A running log of every behavioral bug, eval-setup bug, and Arize-platform observation surfaced by running real experiments against the drone-show agent. Source material for the PM deliverable.

Organized by category — within each, ordered roughly by when the finding was discovered.

---

## A. Agent behavior bugs (surfaced by evals/experiments)

These were *real bugs in the agent* that our testing surfaced. None of them showed up in interactive REPL use.

### A1. Greeting on every prompt in batch contexts
**Discovered:** v7 Playground demo prep. Every dataset row produced the agent's opening greeting instead of an answer.

**Why it happened:** System prompt said *"On the first message of every session, greet the user..."*. In the REPL, `main.py` kicks off with `"Hello"` which legitimately triggers the greeting, then real prompts arrive as turn 2+. In the experiment, each row is sent as a fresh one-message conversation — so every row is technically a "first message."

**Why eval caught it but interactive testing didn't:** Hidden behind the `"Hello"` kickoff. The REPL is a leaky test environment for greeting behavior.

**Fix:** Replaced the implicit "first message" trigger with explicit content patterns (`# CRITICAL: NO GREETING ON SPECIFIC REQUESTS` block in `agent/drone_show_agent.py`). Greeting only fires on `"Hello"`, `"Hi"`, `"Hey"`, or `"What can you do?"`.

**Score delta:** `contains_required_substrings` 12/13 → 13/13.

---

### A2. Lazy refusals (agent skipping show lookups)
**Discovered:** Smoke test run-to-run variance — sometimes "Move Kyoto straight to Complete" produced a refusal that named Kyoto's actual status ("currently in Sales..."), sometimes it produced a generic "you can't skip steps" without any lookup.

**Why it mattered:** The PRD's expected behavior calls for refusals that state the show's current status. A refusal without a lookup is less actionable for the user.

**Fix:** Added `# Mandatory lookup` rule to the system prompt: *"For ANY question or action about a SPECIFIC show, you MUST call a tool to retrieve that show's real data BEFORE you answer, refuse, or transition."*

**Compounding result:** Adversarial row 14 ("Move Bariloche's launch date to 2026-06-01") then correctly began with `get_show("Bariloche")` before refusing.

---

### A3. Unstructured refusals (rambly, lecturing)
**Discovered:** v8 experiment. `response_quality` judge flagged row 14's refusal: *"writing is somewhat cluttered and unfocused, introducing irrelevant pipeline details."*

**Why it mattered:** Even when the agent did the right thing structurally, its refusals included unnecessary context (full pipeline restatements, irrelevant fields). The judge was right.

**Fix:** Strengthened the `# How to refuse` rule from "Keep refusals brief and concrete" to:
> *"Keep refusals tight: aim for 3-4 sentences total. Do not restate the full pipeline. Do not add context the user didn't ask for. Name what's blocking and the correct next move — that's it."*

**Pattern:** Multiple explicit "do not" prohibitions land more reliably than positive instructions. Same principle as the `CRITICAL: NO GREETING` block.

**Score delta:** `response_quality` 17/18 → 18/18 in v9.

---

### A4. Wrong section name for cross-field queries (real capability gap)
**Discovered:** v8 row 18. Agent called `list_shows_by_field(section="Event Details", field="Estimated Budget")` — wrong section. Tool returned an error. Agent retried with same wrong section, then gave up and asked the user.

**Why it mattered:** The agent had no internal knowledge of which section holds which field. The schema lives in `backend/show_schema.py` (code), but the agent only sees the system prompt (text).

**Fix:** Added a **field-to-section cheat sheet** under `list_shows_by_field`'s description in the system prompt. Lists which fields live in which sections (Lead Info, Contact Info, Event Details, Show Design Info, etc.).

**Score delta:** Row 18 went from `contains_required_substrings: 0.0` to `1.0`. v8 → v9 was 124/126 → 126/126.

**Crucial coupling exposed:** When you add a field to `SECTION_FIELDS` in `show_schema.py`, you also need to update the cheat sheet in the system prompt. Documented in ARCHITECTURE.md.

---

### A5. Prose drift on ambiguity ("two active shows" with a Complete one)
**Discovered:** v7 experiment. On "What's the status of the Spain show?", agent said *"There are two active shows... 1. Andalusian (Status: Complete), 2. Costa Brava (Status: Sales)"*. The label "active" contradicted the listed Complete status. Caught by `evidence_grounded` judge.

**Why it mattered:** The agent's prose drifted from its data — an internal contradiction in a single sentence. Only an LLM judge with semantic understanding of "active" caught it. No deterministic check could have.

**Status:** Surfaced but not yet fixed in the prompt. Candidate v10 addition: *"When listing multiple matches, do not editorialize about their status — quote it verbatim from the tool output."*

---

### A6. Redundant confirmation on mutations
**Discovered:** v4 experiment row 13. Agent asked *"Just to confirm, do you want me to use 'N/A' as the Debrief?"* instead of executing the move directly. The user had already provided "N/A" in their original prompt.

**Why it mattered:** Caught by `correct_refusal` (mutation expected but didn't fire). The agent over-confirms on intuitively-clear mutation requests in some runs.

**Status:** Real variance, not yet fully fixed. Worth a future prompt addition: *"If the user supplied a value in their message, use it. Do not ask for re-confirmation."*

---

### A7. Hardcoded date in system prompt
**Discovered:** Time-based questions ("What's the next show to go on-site?") referenced "today" against the wrong date as days passed.

**Fix:** `{today}` template placeholder filled via `date.today().isoformat()` at agent-build time in `build_agent()`.

---

## B. Eval setup bugs (so the metrics measure what we think)

These were *not* agent bugs — they were bugs in our own evaluator code, dataset, or runner. Each one would have given misleading scores until fixed.

### B1. `bool("False") == True` — mutation_allowed inverted
**Discovered:** v2 experiment. `correct_refusal` scored 1/13. Every non-mutation row marked as "unauthorized_mutation."

**Why it happened:** `mutation_allowed: False` round-trips through pandas → Arize → back as the string `"False"`. Naive `bool("False")` is `True` in Python.

**Fix:** `_row_bool()` helper in `evals/evaluators.py` that handles strings (`"true"`, `"false"`), numpy bools, ints, and real bools.

**General lesson:** Whenever data crosses a serialization boundary, type coercion is a hidden hazard. Especially with bools.

---

### B2. Tool output truncation hid real evidence from the LLM judge
**Discovered:** v3 experiment row 3 (Bariloche). `evidence_grounded` flagged the agent's detailed answer as ungrounded.

**Why it happened:** Task function truncated tool outputs to 2000 chars before storing them. Bariloche's parsed sections are ~5KB. Judge literally couldn't see the evidence.

**Fix:** Bumped truncation to 15000 chars. Considered "no truncation" but kept a cap for token cost.

**General lesson:** LLM judges are only as good as the evidence you feed them. Aggressive token-saving truncation can silently invalidate the judge's verdict.

---

### B3. LLM judges penalize correct behavior without `AGENT_RULES`
**Discovered:** v3 experiment. `evidence_grounded` flagged refusal rows as ungrounded *"because the agent stated pipeline order without supporting tool output."* But the pipeline IS the agent's instructions — it's not a fabrication.

**Why it happened:** The judge only sees its own prompt. It doesn't know the agent's system prompt, so anything stated as a rule (pipeline, required fields, capability limits) looks like an unsupported claim.

**Fix:** Added a shared `AGENT_RULES` constant in `evaluators.py` injected into every LLM judge. Declares the agent's operating contract — same rules the agent itself follows.

**General lesson — this came up FOUR times in different forms:**
1. evidence_grounded penalizing system-prompt knowledge (fixed by AGENT_RULES)
2. evidence_grounded penalizing user-supplied facts (fixed by adding "facts in user's prompt are also grounded")
3. response_quality penalizing intake/refusals as "unnecessary friction" (fixed by AGENT_RULES)
4. Playground judge `pg_correct_first_move` needed the same context (separate AGENT_RULES block at the top of that prompt)

Each judge needs the agent's operating contract or it scores correct behavior as wrong.

---

### B4. Judge scope creep (`response_quality` doing groundedness)
**Discovered:** v5 experiment. `response_quality` flagged a correct retrieved answer because *"agent invents specific show details without indication they were retrieved."*

**Why it happened:** With `AGENT_RULES` injected, `response_quality` learned the "never fabricate" rule and started judging fabrication — but **it doesn't have the tool outputs in its prompt**. So it was attempting groundedness without evidence.

**Fix:** Narrowed `response_quality`'s scope explicitly:
> *"SCOPE — judge ONLY the writing. Do NOT judge whether facts are accurate or retrieved (a separate evaluator handles groundedness — ASSUME all factual content is correct)."*

**General lesson:** Each judge should have one narrow job. When you add context (like AGENT_RULES), the judge can scope-creep into adjacent territory it doesn't have evidence to evaluate.

---

### B5. `expected_keys` mismatch for single-show queries
**Discovered:** v3 dry-run. `contains_expected_keys: 5/10`. Failures on rows like "Tell me about Bariloche" expecting "KAN-14" in the answer.

**Why it happened:** The agent naturally refers to single shows by name ("the Patagonia show"), not by Jira key. My rubric was wrong, not the agent.

**Fix:** Trimmed `expected_keys` to only **list queries** (rows 01, 02) where citing keys is naturally expected. For single-show queries, rely on `must_contain` with names instead.

**General lesson:** *Tighten what you expect rather than loosen the evaluator.* Loosening evaluators to make scores green is how evals rot.

---

### B6. `must_contain` too literal for varied phrasing
**Discovered:** v3 row 6. `must_contain: ["Contact Info"]` failed when the agent said "contact and lead information."

**Why it happened:** The agent's phrasing varies turn-to-turn. Exact-substring expectations on natural-language phrasing are brittle.

**Fix:** For intake/refusal rows, removed `must_contain` and relied on behavioral evaluators (`correct_refusal`, `right_tool_chosen`) instead.

**General lesson:** Assert on what shouldn't happen (`must_not_contain` — clean failure mode) rather than how the LLM phrases what does happen.

---

### B7. Missing evaluator for the `must_contain` column
**Discovered:** Reviewing v3 results. Dataset had a `must_contain` column but no evaluator read it.

**Fix:** Added `contains_required_substrings` evaluator. Symmetric counterpart to `no_forbidden_substrings`.

**General lesson:** Every column in a rubric dataset should map to an evaluator, or the column is dead weight.

---

### B8. `load_dotenv()` silently shadowed by shell env
**Discovered:** API key debugging session. User updated `.env` with a fresh key; subsequent Python runs continued using the *old* key. Wasted ~30 min on auth misdiagnosis.

**Why it happened:** `python-dotenv` defaults to `override=False`. If a variable is already in `os.environ` from the shell, `.env` does NOT overwrite it. Common when the user has previously `export`-ed values.

**Fix:** `load_dotenv(override=True)` in all 5 entry points (`main.py`, `evals/run_experiment.py`, `evals/dataset.py`, `tests/smoke_test.py`, `tests/verify_workflow_traces.py`).

**General lesson:** In a dev workflow, `.env` should be the source of truth. Default behavior of common libs often disagrees.

---

### B9. Task function parameter naming convention
**Discovered:** v2 first experiment run. Task function defined as `def run_one(example):` — every row failed with `AttributeError: 'dict' object has no attribute 'dataset_row'`.

**Why it happened:** Arize's `run_experiment` binds the task function's single parameter by **name**:
- `def task(input):` → gets `example.input`
- `def task(dataset_row):` → gets `example.dataset_row`
- `def task(other_name):` → defaults to `example.dataset_row`

Naming the param `example` makes Arize default to passing `example.dataset_row` directly — so `example` IS the row, and `example.dataset_row` is an attribute error.

**Fix:** Renamed parameter to `dataset_row`.

**General lesson:** Library magic via parameter naming is undiscoverable without reading the source.

---

### B10. `Runner.run_sync` inside Arize's asyncio loop
**Discovered:** v2 first experiment run (concurrent with B9). All task calls produced empty `final_output`.

**Why it happened:** Arize's `run_experiment` manages its own asyncio event loop and dispatches tasks via it. `Runner.run_sync` from the OpenAI Agents SDK internally calls `asyncio.run()`, which fails inside an existing event loop.

**Fix:** Made task `async` and used `await Runner.run(...)`.

---

## C. Tracing & infrastructure findings

### C1. `arize-otel`'s `register()` bug on HTTP transport
**Discovered:** Initial tracing setup. Default gRPC export failed with `UNAVAILABLE` (likely network/firewall). Switched to `Transport.HTTP` — got new error: `Invalid URL 'Endpoint.ARIZE': No scheme supplied. Perhaps you meant https://Endpoint.ARIZE?`

**Root cause:** `arize-otel`'s register() doesn't unwrap the `Endpoint.ARIZE` enum when HTTP transport is requested — passes the literal string `"Endpoint.ARIZE"` as the URL.

**Fix:** Bypassed `arize-otel.register()`. Built the tracer provider directly using `opentelemetry-sdk` + `OTLPSpanExporter` from the OTLP HTTP module, pointing at `https://otlp.arize.com/v1/traces` with auth headers set manually.

**See:** `backend/tracing.py`.

---

### C2. Trace boundary granularity
**Discovered:** Originally wrapped the entire REPL session in one trace. After running a few prompts, every workflow merged into one giant tree — unreadable in Arize.

**Fix:** Switched to **per-workflow** traces using a heuristic: open a new trace on each prompt; close it when (a) a mutation tool fires successfully, OR (b) the agent's response doesn't end with an intake-style follow-up question. Workflow name set dynamically to the first tool called.

**See:** `_INTAKE_MARKERS` and `_workflow_complete()` in `agent/drone_show_agent.py`. Verified by `tests/verify_workflow_traces.py`.

---

### C3. JQL syntax with `ORDER BY` inside parens
**Discovered:** First live Jira query. `project = KAN AND (status != "Complete" ORDER BY status)` returned 400.

**Fix:** Don't wrap the inner JQL in parens — `ORDER BY` is a top-level clause modifier.

---

### C4. `transition()` reading the wrong dict key
**Discovered:** Smoke test row 13. Agent said "moved to Complete" but the status didn't actually change. Reasoning: `transition()` raised `ValueError`.

**Root cause:** `atlassian-python-api`'s `get_issue_transitions` returns dicts shaped `{"name": "Done", "to": "Complete"}` — flat string. My code read `t["to_status"]` (key doesn't exist).

**Fix:** Read `t.get("to")` to identify the target status.

**General lesson:** Library response shapes drift from official Jira API shape. Inspect what the library actually returns rather than trusting docs.

---

### C5. Description parser missed Jira wiki markup
**Discovered:** First end-to-end smoke run. `get_show` returned empty `sections: {}` for every show.

**Root cause:** The MCP server used during design returned descriptions as plain text. The live REST API returns Jira **wiki markup** (`h2. Section`, `* *Field*: value`) — a different format my parser didn't recognize.

**Fix:** Added `_normalize_wiki_markup()` preprocessor in `backend/show_format.py` that converts wiki markup to the plain-text form the existing parser handles. Three formats now supported: plain, split-line, wiki markup.

**General lesson:** Always grab fixtures from the same layer your production code reads from. The MCP was a leaky test source.

---

### C6. Strict-mode JSON schema can't represent free-form `dict`
**Discovered:** First agent build attempt. `create_show` and `transition_show` registration failed with `UserError: additionalProperties should not be set for object types`.

**Root cause:** Both tools accept a `fields: dict` parameter. The Agents SDK's strict mode generates JSON schemas with `additionalProperties: false`, which conflicts with free-form `dict`.

**Fix:** `@function_tool(strict_mode=False)` on the two mutation tools.

---

## D. Arize platform paper-cuts (for the PM deliverable)

These are observations about the *platform itself*, not our code. Each one is a friction point a developer hits when using Arize for the first time on an agent project.

### D1. `FlightUnauthenticatedError: invalid token` actually means "duplicate dataset name"
**Discovered:** Trying to re-upload `drone_show_manager_v1` after a key rotation. The SDK returned the same error code as a real auth failure. We spent ~90 minutes misdiagnosing.

**Distinguishing diagnostic:** `list_datasets` (read) succeeds but `create_dataset` (write of an existing name) fails — same key, two different verdicts. The error message gives you no clue about the actual cause.

**MVP fix proposal:** Distinct error codes for auth failure vs name conflict. Even a generic "dataset 'X' already exists" message would have saved 90 minutes.

---

### D2. Playground silently mismatches agents without tool schemas
**Discovered:** First Playground demo attempt. Every dataset row produced "Please provide the name or key of the show..." because the model had no tools to call.

**Why it matters:** A developer's first instinct in the Playground (paste prompt, run dataset) returns useless responses for tool-using agents. The prompt that should test best (stronger tool-use enforcement) tests worst.

**MVP fix proposal:** When a developer opens the Playground from a dataset that came from a traced agent, pre-populate the tool schemas automatically from the latest trace. Optional: simulate tool outputs from the dataset's tool-call columns for multi-turn preview.

---

### D3. Different output shapes need different evaluators
**Discovered:** Configured the offline experiment's evaluators against Playground output → all rows failed because the output is `Tool: list_shows / arguments: ...` (a tool call), not a final answer.

**Why it matters:** A developer who set up evaluators for their offline experiment has to write a *separate* set of evaluators for the Playground because the output shapes are different.

**MVP fix proposal:** Either a unified "model output" view that combines tool calls + text, OR Playground-specific evaluator templates that ship with the platform.

---

### D4. `load_dotenv()` shadowing isn't Arize's fault — but is amplified by it
**Discovered:** B8 above.

**Why it's Arize-adjacent:** When the failure mode shows up, the developer's first instinct is "Arize is rejecting my key." Hours of misdiagnosis follow. A clearer Arize-side error ("this key is valid but lacks scope X" instead of generic "invalid token") would have caught the mistake sooner.

---

### D5. `--dry-run` runs only the first 10 rows
**Discovered:** Sanity-checking the experiment runner. Saw `rows processed: 10` for a 13-row dataset — looked like a bug.

**Root cause:** Hidden in `arize/experimental/datasets/core/client.py`: *"only dry_run experiment on a subset (first 10 rows) of the dataset."*

**Why it matters:** Not documented in the public API. A developer using `--dry-run` to iterate on their evaluators will miss rows 11+ entirely without knowing it.

**MVP fix proposal:** Either log "running dry-run on first 10 rows" prominently, OR make the subset size configurable.

---

### D6. LLM judges need the agent's operating contract
**Discovered:** B3 above — the deepest finding of the project. Same lesson hit four different times.

**Why it matters:** Every LLM judge requires re-supplying the agent's rules in its own prompt. There's no platform mechanism to inherit context from a project's known agent spec. A developer authoring 5 judges hand-copies the same AGENT_RULES block into each.

**MVP fix proposal:** First-class "agent project" concept. When a developer registers an agent's system prompt with a project, all judges in that project inherit the operating context automatically.

---

### D7. Misleading auth error for partial key scope
**Originally suspected this, then revised:** see D1. The "two key types" theory turned out to be wrong in this case — the real issue was duplicate-name conflict. But the underlying observation stands: Arize returns the same generic auth-error string for several distinct server-side states (revoked, wrong org, name conflict). A developer can't distinguish them without server logs.

---

## E. Net behavior changes in the agent (the prompt commits)

Concise summary of all the prompt edits that landed:

| # | Change | Source | Where in prompt |
|---|---|---|---|
| 1 | Replaced "On the first message of every session" with explicit content-pattern gating | A1 — greeting bug | `# CRITICAL: NO GREETING ON SPECIFIC REQUESTS` |
| 2 | Added mandatory-lookup rule before any show-specific answer/refusal | A2 — lazy refusals | `# Mandatory lookup` |
| 3 | Added 3-part refusal structure (status, blocker, next step) | A3 — refusal quality | `# How to refuse` |
| 4 | Tightened refusal brevity (3-4 sentences, no pipeline restatement) | A3 v2 — wordy refusals | `# How to refuse` |
| 5 | Added field-to-section cheat sheet under `list_shows_by_field` | A4 — wrong section bug | `# Your tools` section |
| 6 | Made "today's date" a dynamic template (`{today}`) | A7 — date drift | `# Today's date` |

---

## How to use this document

For the PM deck:
- Section A is **what the dev workflow caught** that interactive testing wouldn't have. Best material for the "evals find real bugs" slide.
- Section B is **how the calibration loop worked** — useful for the "iterative judge tuning" slide.
- Section D is **direct product feedback for Arize** — the strongest investments-to-make list.
- Section E is **the compounding deltas** — visualization-friendly with experiment IDs (v7 → v8 → v9) attached.

For future engineers on this codebase:
- Sections A, B, C are the institutional memory. Every fix has the original symptom and the experiment ID it was found in, so you can rerun the experiment to verify a regression.
