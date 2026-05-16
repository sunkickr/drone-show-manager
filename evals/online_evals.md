# Online Evals — Observability Workflow

This is the **observability half** of the deliverable: judges that score every live trace as it arrives in Arize, not against a fixed dataset. They're configured in the Arize UI and run automatically on every new trace.

For the offline development workflow (datasets + experiments), see `evals/README.md`.

---

## Concept: online vs offline evals

| Dimension | Offline (experiment) | Online |
|---|---|---|
| **Input** | A pre-built dataset row with rubric columns (`expected_keys`, `must_contain`, etc.) | A live trace from the running agent |
| **When** | Triggered explicitly by `python evals/run_experiment.py` | Automatic — runs on every new trace |
| **Where evaluator runs** | Local Python process (your machine) | Arize's infrastructure |
| **Config** | `evals/evaluators.py` + `EVALUATORS` registry | Arize UI: Project → Evaluators → Add evaluator |
| **Use case** | Tracking regressions across prompt/model changes | Production monitoring, alerting on drift |
| **Compatible judges** | All 7 of ours | Only 3 (the LLM-judge ones — code evaluators need rubric columns we don't have on live traces) |

### Why not all 7 evaluators work online

Four of our offline evaluators are **deterministic checks against dataset rubric columns**:

- `contains_expected_keys` — needs the row's `expected_keys` list
- `contains_required_substrings` — needs `must_contain`
- `no_forbidden_substrings` — needs `must_not_contain`
- `correct_refusal` — needs `mutation_allowed`

Live traces don't have those columns. There's no rubric for "what should this answer contain" because the user just asked a free-form question. The signal those evaluators provide can only come from an offline dataset where the ground truth is known.

The three **LLM judges** translate cleanly to online because they reason from the trace alone:

- `evidence_grounded` — "Are all show-specific facts in the answer supported by tool outputs?"
- `right_tool_chosen` — "Is the tool the agent picked reasonable for the user's request?"
- `response_quality` — "Is the response well-written?"

---

## Setup steps

### 1. Verify trace data is flowing

Online evals score whatever traces are arriving in your project. Confirm traces are reaching Arize:

```bash
.venv/bin/python main.py
# Type a couple of prompts, e.g. "What's in Contract?" then exit
```

Then open Arize → your project (set via `ARIZE_PROJECT_NAME`) → Traces. You should see new spans. If not, online evals will have nothing to score.

### 2. Configure each evaluator in the Arize UI

For each of the three prompts below:

1. In Arize, navigate to your project
2. Find the **Online Evaluations** / **Evaluators** / **Monitors** section (Arize names this differently per UI version)
3. Click **Add Evaluator** or **New Evaluator**
4. Pick **LLM-as-Judge** as the evaluator type
5. **Name** the evaluator (use the names below — they match our offline judges for easy correlation)
6. **Model**: `gpt-5.4` (or whatever your `OPENAI_JUDGE_MODEL` is set to)
7. **Output schema**: JSON with `label` (string), `score` (0 or 1), `explanation` (string)
8. **Prompt template**: paste the prompt block below verbatim
9. **Trigger**: every new trace (the default)
10. Save

Arize will start scoring new traces immediately and may backfill recent ones (depending on version).

### 3. View results

Once configured, score columns appear on every trace in the Traces view. You can:
- **Filter** traces by score (e.g., show me all traces where `evidence_grounded=0`)
- **Aggregate** mean scores over time on the project dashboard
- **Set alerts** if a score drops below a threshold

---

## Trace-shaped evaluator prompts

These are adapted from the offline judges in `evals/evaluators.py`. The key difference: online prompts reference Arize's standard trace variables (`{{input}}`, `{{output}}`, plus any tool-call attributes the UI exposes) instead of dataset row columns.

> **Variable note:** Arize's online evaluator UI typically provides `{{input}}` (user message) and `{{output}}` (agent's final response). For tool calls, the variable name may differ per Arize version — common names include `{{tool_calls}}`, `{{attributes.tool_calls}}`, or `{{spans}}`. Check your project's evaluator panel for the exact variable list. The prompts below use `{{tool_calls}}` as a placeholder; substitute your version's actual variable name when pasting.

### Shared AGENT_RULES preamble

Every prompt below includes the agent's operating contract. This is the SAME `AGENT_RULES` block from `evals/evaluators.py` — when an LLM judge doesn't know the agent's rules, it penalizes correct behavior (one-question intake, refusal, etc.). Keep this block in sync with the agent's `SYSTEM_PROMPT` in `agent/drone_show_agent.py`.

---

### Evaluator 1: `evidence_grounded`

**Purpose:** Catch fabrication. Score 0 if the agent invents any show-specific fact (Jira key, name, date, budget, link) not supported by tool output, the user's prompt, or the agent's own rules.

```
Evaluate whether the agent's answer is grounded — i.e. it does not fabricate facts about specific drone shows.

The agent being evaluated is the ADHOC Drone Show Manager, a focused internal Jira tool. Its design rules — all of these are CORRECT behavior, not flaws:
- It works only in the drone-show Jira project (KAN). Declining out-of-scope requests is correct.
- Pipeline: Sales → Contract → Show Design → Show Operations → Complete; shows move one step at a time; skipping is correctly refused.
- It must NEVER fabricate show information. Refusing to invent values is correct.
- During create/transition flows it collects required fields ONE QUESTION AT A TIME — multi-step intake is the intended design, not unnecessary friction.
- It cannot delete shows or do bulk operations; refusing those is correct.
- "N/A" is a valid field value; blank is not.

User prompt: {{input}}

Tool calls and outputs the agent observed this turn:
{{tool_calls}}

Agent's final answer to the user:
{{output}}

A statement is GROUNDED if it is supported by ANY of these three sources:
1. TOOL OUTPUTS shown above — show-specific facts (Jira keys, names, dates, statuses, budgets, drone counts, document links) must come from here.
2. THE USER'S OWN PROMPT — if the user supplied a fact (e.g. "Create a show for SkyTech Berlin in Berlin, Germany"), the agent echoing "SkyTech Berlin" is grounded; the user said it.
3. THE AGENT'S RULES — the pipeline order, which fields are required per status, capability limits (no delete, no bulk ops), that N/A is valid. These are in the agent's instructions and are ALWAYS grounded, even with no tool call.

Conversational text ("Here is the show:", "Would you like more info?") and clarifying questions are always fine.

Decision:
- UNGROUNDED (score 0) only if the agent states a SHOW-SPECIFIC fact that appears in NONE of the three sources above — i.e. it genuinely invented a key, name, date, budget, status, or link.
- Otherwise GROUNDED (score 1).

Return strict JSON: {"label": "grounded" | "ungrounded", "score": 1 | 0, "explanation": "<one or two sentences naming the specific ungrounded claim if any>"}
```

---

### Evaluator 2: `right_tool_chosen`

**Purpose:** Catch tool routing mistakes — the agent picking the wrong tool, or picking a tool with bad arguments. This evaluator is purely LLM-based for online (the offline version has a code shortcut that doesn't apply without an `expected_tool` rubric column).

```
Evaluate whether the agent picked a reasonable tool to address the user's request.

The agent being evaluated is the ADHOC Drone Show Manager. It has five tools:
- list_shows(status): list shows in a status (default: all active)
- get_show(query): details about ONE show (key or fuzzy name)
- list_shows_by_field(section, field, value, status): filter shows by a field inside descriptions, or extract a field across shows. Common section mappings: budget/sales contact/show description/project doc link live in "Lead Info"; producer/on-site dates/venue live in "Event Details"; design lead/drone count live in "Show Design Info".
- create_show(summary, fields): create a new show (mutates)
- transition_show(key, target_status, new_fields): move a show to its next status (mutates)

Rules:
- For lookup-style requests ("Tell me about X", "What's missing on Y"), get_show is correct.
- For cross-show queries ("highest budget", "shows by Marcus Chen"), list_shows_by_field is correct.
- For status overviews ("what's in Contract"), list_shows is correct.
- For "create a show" requests, the agent should NOT call create_show immediately — it should first ask the user for Contact Info fields one at a time. No tool call on the first turn is correct here.
- For mutation requests ("Move X to Y"), the agent should call get_show first to look up the show, then transition_show. Either tool as the first call is acceptable.
- For refusal cases (deleting a show, skipping statuses, fabricating data), refusing without a tool call is acceptable; calling get_show first to provide a more informative refusal is also acceptable.

User prompt: {{input}}
Agent's first tool call (and arguments):
{{tool_calls}}

Score 1 if the agent's first move was reasonable per the rules above. Score 0 if it called a wrong tool, called a correct tool with bad arguments (e.g. wrong section name for list_shows_by_field), or called a mutation tool when the request didn't authorize one.

Return strict JSON: {"label": "correct" | "wrong", "score": 1 | 0, "explanation": "<one sentence>"}
```

---

### Evaluator 3: `response_quality`

**Purpose:** Catch poor writing in agent responses. Important: this judge does NOT verify factual accuracy — assume facts are correct (that's `evidence_grounded`'s job) and only judge the writing.

```
Rate ONLY the clarity and writing quality of this assistant response.

The agent being evaluated is the ADHOC Drone Show Manager. Its design rules — all CORRECT behavior:
- Pipeline: Sales → Contract → Show Design → Show Operations → Complete; skipping is correctly refused.
- It must NEVER fabricate show data. Refusing to invent values is correct.
- During create/transition flows it collects required fields ONE QUESTION AT A TIME.
- It cannot delete shows or do bulk operations; refusing those is correct.

User prompt: {{input}}

Assistant response:
{{output}}

SCOPE — judge ONLY the writing. Do NOT judge:
- whether the facts are accurate or retrieved from a tool (a separate evaluator handles groundedness — ASSUME all factual content is correct and properly sourced)
- whether the agent followed its rules / picked the right action (separate evaluators handle that — ASSUME the action taken was correct)

You are only assessing: is this well-written for a focused internal tool?

Score 1 (good) if the response is clear, well-organized, on-topic, and reasonably concise. A direct short answer, a clean refusal, or a single intake question are all good writing.

Score 0 (poor) ONLY if the writing itself is bad: genuinely confusing, rambling, self-contradictory, full of filler, or hard to follow.

Return strict JSON: {"label": "good" | "poor", "score": 1 | 0, "explanation": "<one sentence about the WRITING only>"}
```

---

## Generating sample traffic

Online evals are only useful if traces are flowing. Three ways to generate them:

| Method | What it gives you | When to use |
|---|---|---|
| `python main.py` (interactive) | Real user-like traces, varied prompts, multi-turn workflows | Best for organic traffic — what you'd see in production |
| `python tests/smoke_test.py` | 13 deterministic prompts in one burst | Quick sanity check that judges are working — but every run produces the same 13 traces |
| `python evals/run_experiment.py` | 18 prompts including adversarials, with full eval scoring | Double-scoring: experiment evaluators run offline AND online judges score the same traces from a different angle. Powerful for the deck |

For the demo:
1. Configure all three online evaluators in Arize first.
2. Run `python tests/smoke_test.py` to send 13 traces through the system.
3. Wait ~60 seconds for Arize to score them.
4. Open the Traces view, filter to the smoke test session, and screenshot the score columns next to each trace.

---

## Trade-off worth knowing: trace boundary granularity

Our REPL currently uses **per-workflow** trace boundaries (one trace per detected workflow — see `agent/drone_show_agent.py:_INTAKE_MARKERS`). This is great for navigability in Arize but it means a 16-turn create-show conversation becomes ONE trace with 16 nested turns.

Online evaluators will score that as a single unit, with `{{input}}` being the first user message of the workflow and `{{output}}` being the agent's final response. That can hide intra-workflow issues — e.g., a great final response after 15 confusing intake questions.

**Two ways to handle this:**

1. **Keep per-workflow boundaries; accept the trade-off.** Best for navigation; online scoring is "did the workflow ultimately succeed?"
2. **Switch to per-turn boundaries for production traffic.** Best for online scoring granularity; each turn is judged independently.

You don't have to choose now — but worth deciding before flipping the agent to a frontend or production setting.

---

## What this gets you for the PM deck

Online evals are the **observability workflow** half of the Arize deliverable. With these three judges configured:

- **Every live REPL session is scored.** Open Arize, see the rolling pass-rate per judge.
- **Demoable in seconds.** Run `python main.py`, ask a question, refresh Arize, see the score appear on the trace.
- **Same evaluator concept ported across surfaces** — offline (experiments), Playground (with tool schemas), online (this) — three different *places* the same judge concept gets applied.

The most interesting comparison for the deck: run the same dataset through the experiment and *also* let the online judges score the live traces. The two scoring runs should agree on each row — if they don't, that's itself a finding about evaluator portability.
