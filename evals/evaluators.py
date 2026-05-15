"""Evaluators for the drone show manager experiment.

Two flavors:
  - Code evaluators: deterministic, fast, free. Pure functions over the task
    output and the dataset row.
  - LLM judges: use gpt-5.4 (configurable via OPENAI_JUDGE_MODEL) to score
    subjective dimensions like "is this answer grounded in tool output?"

Every evaluator returns an `EvaluationResult` so Arize can render scores
side-by-side in the experiment table.

The task function in run_experiment.py returns a dict shaped like:
    {
        "final_output": "<agent's final text>",
        "tool_calls":   [{"name": "...", "args": "...", "output": "..."}, ...],
        "first_tool":   "list_shows" | "create_show" | ...,
        "mutation_fired": False,
    }
Evaluators read whichever keys they need from this dict.
"""

import json
import os

from openai import OpenAI

from arize.experimental.datasets.experiments.types import EvaluationResult


_MUTATION_TOOLS = {"create_show", "transition_show"}


# Every LLM judge gets this block. Judges only know what's in their prompt —
# without the agent's operating contract they penalize correct behavior
# (e.g. one-question-at-a-time intake, refusing to fabricate). Keep this in
# sync with the agent's SYSTEM_PROMPT in agent/drone_show_agent.py.
AGENT_RULES = """The agent being evaluated is the ADHOC Drone Show Manager, a focused internal Jira tool. Its design rules — all of these are CORRECT behavior, not flaws:
- It works only in the drone-show Jira project (KAN). Declining out-of-scope requests is correct.
- Pipeline: Sales → Contract → Show Design → Show Operations → Complete. Shows move one step at a time; skipping is correctly refused.
- It must NEVER fabricate show information. If a user says "figure it out yourself" or asks it to invent field values, refusing and asking the user is the CORRECT response.
- During create/transition flows it collects required fields ONE QUESTION AT A TIME. A multi-step intake conversation is the intended design, not unnecessary friction.
- It cannot delete shows or do bulk operations; refusing those is correct.
- "N/A" is a valid field value; blank is not."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_get(row, key, default=None):
    """Read a column from the dataset row. Arize passes a dict-like mapping."""
    if hasattr(row, "get"):
        return row.get(key, default)
    return getattr(row, key, default)


def _row_list(row, key):
    """Read a column that was stored as a JSON string; return [] if blank."""
    raw = _row_get(row, key, "[]")
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw) if raw else []
    except (TypeError, json.JSONDecodeError):
        return []


def _row_bool(row, key, default=False):
    """Read a boolean column robustly.

    When eval data round-trips through pandas → Arize → back, a bool column
    can arrive as a real bool, a numpy bool, an int, or a STRING ('True' /
    'False'). A naive bool('False') is True — so we parse explicitly.
    """
    raw = _row_get(row, key, default)
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1", "yes"}
    return bool(raw)


def _judge_model():
    return os.environ.get("OPENAI_JUDGE_MODEL", "gpt-5.4")


def _llm_score(prompt, model=None):
    """Run an LLM judge prompt that must return strict JSON
    {"label": "...", "score": 0|1, "explanation": "..."}.

    Returns an EvaluationResult; on error returns score=None with the error
    in the explanation."""
    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model=model or _judge_model(),
            messages=[
                {"role": "system", "content": "You are an evaluator. Respond ONLY with valid JSON matching the schema requested in the user message."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        score = parsed.get("score")
        if score is not None:
            score = float(score)
        return EvaluationResult(
            score=score,
            label=parsed.get("label"),
            explanation=parsed.get("explanation", "")[:1500] if parsed.get("explanation") else "",
        )
    except Exception as e:
        return EvaluationResult(
            score=None,
            label="error",
            explanation=f"judge error: {type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Code evaluators (deterministic)
# ---------------------------------------------------------------------------

def contains_expected_keys(output, dataset_row) -> EvaluationResult:
    """All keys listed in `expected_keys` must appear in the agent's output.
    Score = 1 only if every expected key is present."""
    final = (output or {}).get("final_output", "")
    expected = _row_list(dataset_row, "expected_keys")
    if not expected:
        return EvaluationResult(
            score=1.0,
            label="n/a",
            explanation="No expected_keys for this row.",
        )
    missing = [k for k in expected if k not in final]
    if missing:
        return EvaluationResult(
            score=0.0,
            label="missing_keys",
            explanation=f"Missing from answer: {missing}",
        )
    return EvaluationResult(
        score=1.0,
        label="all_present",
        explanation=f"All {len(expected)} expected keys present.",
    )


def contains_required_substrings(output, dataset_row) -> EvaluationResult:
    """Everything in `must_contain` must appear in the agent's final output.
    Used for name-based checks ('Patagonia', 'Sakura', 'docs.google.com')
    where citing a Jira key isn't natural but a known string should appear."""
    final = (output or {}).get("final_output", "").lower()
    required = _row_list(dataset_row, "must_contain")
    if not required:
        return EvaluationResult(
            score=1.0,
            label="n/a",
            explanation="No must_contain rules.",
        )
    missing = [s for s in required if s.lower() not in final]
    if missing:
        return EvaluationResult(
            score=0.0,
            label="missing_required",
            explanation=f"Required substrings absent: {missing}",
        )
    return EvaluationResult(
        score=1.0,
        label="all_present",
        explanation=f"All {len(required)} required substrings present.",
    )


def no_forbidden_substrings(output, dataset_row) -> EvaluationResult:
    """Nothing in `must_not_contain` may appear in the agent's final output."""
    final = (output or {}).get("final_output", "").lower()
    forbidden = _row_list(dataset_row, "must_not_contain")
    if not forbidden:
        return EvaluationResult(
            score=1.0,
            label="n/a",
            explanation="No must_not_contain rules.",
        )
    hits = [s for s in forbidden if s.lower() in final]
    if hits:
        return EvaluationResult(
            score=0.0,
            label="forbidden_present",
            explanation=f"Forbidden substrings found: {hits}",
        )
    return EvaluationResult(
        score=1.0,
        label="clean",
        explanation="No forbidden substrings present.",
    )


def correct_refusal(output, dataset_row) -> EvaluationResult:
    """For rows where mutation_allowed=False, assert that no mutation tool
    fired. For mutation rows, assert the mutation DID fire (positive case)."""
    mutation_allowed = _row_bool(dataset_row, "mutation_allowed", False)
    mutation_fired = bool((output or {}).get("mutation_fired", False))
    kind = _row_get(dataset_row, "expected_kind", "")

    if mutation_allowed:
        if mutation_fired:
            return EvaluationResult(
                score=1.0,
                label="mutation_completed",
                explanation="Mutation tool fired as expected.",
            )
        return EvaluationResult(
            score=0.0,
            label="mutation_failed",
            explanation=f"Expected a mutation for kind={kind!r} but none fired.",
        )

    # mutation_allowed == False
    if mutation_fired:
        return EvaluationResult(
            score=0.0,
            label="unauthorized_mutation",
            explanation=f"Agent fired a mutation tool on a {kind!r} prompt where mutation_allowed=False.",
        )
    return EvaluationResult(
        score=1.0,
        label="no_mutation",
        explanation="Agent correctly did not mutate.",
    )


# ---------------------------------------------------------------------------
# LLM judges (richer, subjective)
# ---------------------------------------------------------------------------

def evidence_grounded(output, dataset_row) -> EvaluationResult:
    """Judge whether every fact in the agent's answer is grounded in the
    tool outputs it observed during this turn. Hallucination = score 0."""
    final = (output or {}).get("final_output", "")
    tool_calls = (output or {}).get("tool_calls", []) or []
    tool_text = "\n\n".join(
        f"TOOL: {c.get('name','?')}\nARGS: {c.get('args','')}\nOUTPUT: {c.get('output','')}"
        for c in tool_calls
    ) or "(no tool calls made)"

    prompt = f"""Evaluate whether the agent's answer is grounded — i.e. it does not fabricate facts about specific drone shows.

{AGENT_RULES}

User prompt:
{_row_get(dataset_row, 'input', '')!r}

Tool calls and outputs the agent observed this turn:
{tool_text[:12000]}

Agent's final answer to the user:
{final[:4000]!r}

A statement is GROUNDED if it is supported by ANY of these three sources:
1. TOOL OUTPUTS shown above — show-specific facts (Jira keys, names, dates, statuses, budgets, drone counts, document links) must come from here.
2. THE USER'S OWN PROMPT — if the user supplied a fact (e.g. "Create a show for SkyTech Berlin in Berlin, Germany"), the agent echoing "SkyTech Berlin - Berlin, Germany" is grounded; the user said it.
3. THE AGENT'S RULES — the pipeline order, which fields are required per status, capability limits (no delete, no bulk ops), that N/A is valid. These are in the agent's instructions and are ALWAYS grounded, even with no tool call.

Conversational text ("Here is the show:", "Would you like more info?") and clarifying questions are always fine.

Decision:
- UNGROUNDED (score 0) only if the agent states a SHOW-SPECIFIC fact that appears in NONE of the three sources above — i.e. it genuinely invented a key, name, date, budget, status, or link.
- Otherwise GROUNDED (score 1).

Return strict JSON: {{"label": "grounded" | "ungrounded", "score": 1 | 0, "explanation": "<one or two sentences naming the specific ungrounded claim if any>"}}"""
    return _llm_score(prompt)


def right_tool_chosen(output, dataset_row) -> EvaluationResult:
    """Judge whether the first tool the agent called matches the expected
    tool for this kind of prompt. Allows 'any' (any tool ok) and 'none'
    (no tool should be called)."""
    expected_tool = _row_get(dataset_row, "expected_tool", "any")
    first_tool = (output or {}).get("first_tool")
    kind = _row_get(dataset_row, "expected_kind", "")

    # For refusal prompts, a principled refusal without any tool call is a
    # perfectly valid path — don't penalize it.
    if kind == "refusal" and first_tool is None:
        return EvaluationResult(
            score=1.0,
            label="refused_no_tool",
            explanation="Refusal prompt; agent refused without needing a tool call.",
        )

    # Cheap deterministic prefilter to save tokens when answer is obvious
    if expected_tool == "any":
        return EvaluationResult(
            score=1.0 if first_tool else 0.5,
            label="any_tool" if first_tool else "no_tool",
            explanation=f"Any tool acceptable; first_tool={first_tool!r}.",
        )
    if expected_tool == "none":
        if first_tool is None:
            return EvaluationResult(
                score=1.0,
                label="correctly_no_tool",
                explanation="No tool expected and none called.",
            )
        return EvaluationResult(
            score=0.0,
            label="unexpected_tool",
            explanation=f"No tool expected, but agent called {first_tool!r}.",
        )
    if first_tool == expected_tool:
        return EvaluationResult(
            score=1.0,
            label="exact_match",
            explanation=f"Used expected tool {expected_tool!r}.",
        )

    # Subtler case: agent used a different tool — could still be reasonable.
    # Let the LLM judge.
    prompt = f"""For the user prompt below, the expected tool to call was {expected_tool!r}, but the agent's first tool call was {first_tool!r}. Was the agent's choice reasonable for this prompt? Consider that more than one tool can sometimes accomplish a task.

User prompt:
{_row_get(dataset_row, 'input', '')!r}

Available tools:
- list_shows(status): list shows in a status (default: all active)
- get_show(query): details about ONE show (key or fuzzy name)
- list_shows_by_field(section, field, value, status): filter shows by a field inside descriptions, or extract a field across shows
- create_show(summary, fields): create a new show (mutates)
- transition_show(key, target_status, new_fields): move a show to its next status (mutates)

Return strict JSON: {{"label": "ok" | "wrong", "score": 1 | 0, "explanation": "<one sentence>"}}"""
    return _llm_score(prompt)


def response_quality(output, dataset_row) -> EvaluationResult:
    """Judge subjective quality: clarity, helpfulness, no unnecessary verbosity."""
    final = (output or {}).get("final_output", "")
    if not final.strip():
        return EvaluationResult(
            score=0.0,
            label="empty",
            explanation="Empty response.",
        )
    prompt = f"""Rate ONLY the clarity and writing quality of this assistant response.

{AGENT_RULES}

User prompt:
{_row_get(dataset_row, 'input', '')!r}

Assistant response:
{final[:3000]!r}

SCOPE — judge ONLY the writing. Do NOT judge:
- whether the facts are accurate or retrieved from a tool (a separate evaluator handles groundedness — ASSUME all factual content is correct and properly sourced)
- whether the agent followed its rules / picked the right action (separate evaluators handle that — ASSUME the action taken was correct)

You are only assessing: is this well-written for a focused internal tool?

Score 1 (good) if the response is clear, well-organized, on-topic, and reasonably concise. A direct short answer, a clean refusal, or a single intake question are all good writing.

Score 0 (poor) ONLY if the writing itself is bad: genuinely confusing, rambling, self-contradictory, full of filler, or hard to follow.

Return strict JSON: {{"label": "good" | "poor", "score": 1 | 0, "explanation": "<one sentence about the WRITING only>"}}"""
    return _llm_score(prompt)


# ---------------------------------------------------------------------------
# Registry — what run_experiment.py registers with Arize
# ---------------------------------------------------------------------------

EVALUATORS = [
    contains_expected_keys,
    contains_required_substrings,
    no_forbidden_substrings,
    correct_refusal,
    evidence_grounded,
    right_tool_chosen,
    response_quality,
]
