# Playground Judge — `pg_correct_first_move`

LLM judge for the Arize **Prompt Playground** when running our agent's prompt against the dataset with tool schemas attached. Distinct from the online and offline evaluators because Playground output is a *tool call*, not a full agent trace.

## Why this judge exists

The Playground tests a prompt+model against dataset rows, but **does not execute tools**. When tool schemas are attached, the model emits a tool call (function name + arguments) instead of a final text response. The 7 offline evaluators in `evals/evaluators.py` were designed for full agent traces and don't apply cleanly to tool-call-only output. This judge grades the agent's *first move* specifically.

## Template syntax — important

Arize uses **single curly braces** for template variables: `{input}`, `{output}`, `{expected_kind}`, `{expected_tool}`. Double braces (`{{input}}`) are *not* substituted — they pass through as literal strings and the judge silently produces plausible-but-wrong scores by inferring from context.

If you've run this judge before with double braces and saw passing scores, those scores may not reflect real substitution. Re-run with the single-brace version below and verify with a debug-echo evaluator.

## Setup in Arize Playground

1. In the Playground, after configuring your prompt + model + tool schemas + dataset
2. Add an evaluator → LLM-as-Judge
3. **Name**: `pg_correct_first_move`
4. **Model**: `gpt-5.4` (or your judge model)
5. **Output schema**: JSON with `label` (string), `score` (0 or 1), `explanation` (string)
6. **Prompt template**: paste the block below

## Prompt template

```
You are evaluating a tool-using agent on its FIRST MOVE for a user prompt. The agent has access to five tools: list_shows, get_show, list_shows_by_field, create_show, transition_show. It is the ADHOC Drone Show Manager — it works only on drone-show tickets in Jira project KAN.

Agent design rules — all of these are CORRECT behavior:
- Pipeline: Sales → Contract → Show Design → Show Operations → Complete; shows move one step at a time; skipping is correctly refused.
- It must NEVER fabricate show data. Refusing to invent values is correct.
- Multi-field intake (create, transition) is collected ONE QUESTION AT A TIME — the first move for intake is NO tool call, just a question to the user.
- It cannot delete shows or do bulk operations; refusing those is correct.
- For any question about a specific show, the agent should call a tool to look it up rather than answering from memory.

User prompt: {input}
Expected behavior kind: {expected_kind}
Expected first tool (if any): {expected_tool}

Model output (this may be a tool call with arguments, or a text response, or both):
{output}

Grade the model's FIRST MOVE only — no execution happened.

Score 1 (correct) if:
- expected_kind is "lookup", "ambiguity", or "mutation" AND the model emitted a tool call to {expected_tool} (or another tool that is a reasonable first step toward the same goal) with sensible arguments
- expected_kind is "intake" AND the model did NOT call any tool — it asked the user for the first required field instead
- expected_kind is "refusal" AND the model either (a) refused directly in text without calling a mutation tool, or (b) called a lookup tool like get_show as a reasonable first step before refusing

Score 0 (wrong) if:
- the model called a wrong tool, or correct tool with nonsense arguments
- the model called a mutation tool (create_show, transition_show) on an intake or refusal prompt (this is the most dangerous failure)
- the model fabricated a specific show fact (Jira key, name, date, budget) instead of calling a tool
- the model output a generic "I don't have a tool for that" when a real tool exists

Return strict JSON: {"label": "correct" | "wrong", "score": 1 | 0, "explanation": "<one sentence naming what the model did, and whether it matches the expected first move>"}
```

## Debug echo evaluator (run this once first)

Before trusting the judge above, run a separate one-off evaluator that just echoes back what each variable resolved to. This is the only way to be sure single braces actually substitute in your Arize version.

```
Echo back exactly what you received in the variables below, with no other commentary. This is a debug evaluator.

input    : {input}
output   : {output}
expected_kind : {expected_kind}
expected_tool : {expected_tool}

Return JSON: {"label": "echo", "score": 1, "explanation": "<copy the four variable values you received verbatim, including any literal braces if substitution didn't happen>"}
```

If the explanation comes back with literal `{input}` (etc.) instead of the actual user prompt, substitution isn't working — file format the right way for your Arize version (some use `{input}`, some `{{input}}`, some `{attributes.input.value}`). If it comes back with the real prompt text, you're good.

## Related

- Offline evaluators (Python): `evals/evaluators.py`
- Offline experiment runner: `evals/run_experiment.py`
- Online (live trace) evaluators: `evals/online_evals.md`
- Tool schemas to paste in Playground: `evals/playground_tool_schemas.json`
