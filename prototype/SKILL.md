---
name: prototype
description: Use this skill when iterating on live Arize evaluators against a recent test run. Wraps the prototype `alyx_fix.py` script (in this same folder), which mimics the proposed `ax alyx fix` CLI by calling an OpenAI model with project context and recent trace evaluations. Triggers on "iterate evaluators", "alyx fix", "fix the evals", "analyze test results in Arize", "why did this evaluator score wrong", or any agent-native eval-debugging task.
---

# Alyx Fix (Prototype)

This skill is the agent-facing wrapper around the prototype `alyx_fix.py` script. Use it after running smoke tests against an agent that has live Arize evaluators configured. The script returns one analysis block per evaluator — telling you whether each failing score is the agent's fault, the judge's fault, or correct.

## When to use this skill

- A user has just run a test suite and wants to know what to fix next.
- A judge scored something unexpected and you need to determine whether the agent or the evaluator is at fault.
- You're iterating on evaluator quality across many traces and want grouped output, not per-trace forensics.

## Prerequisites

- `python tests/smoke_test.py` (or equivalent) has been run recently and traces are in Arize.
- `ARIZE_SPACE_ID` is set in `.env`.
- An `AGENT_CONTEXT.md` (or similar) file exists, describing the agent under test — system prompt, design rules, recent changes. This is what makes the analysis project-specific rather than generic.

If `AGENT_CONTEXT.md` doesn't exist yet, create it before invoking the script. A short template:

```markdown
# Agent Context

## What the agent does
[one paragraph]

## Key design rules
- [rule 1]
- [rule 2]

## Recent changes
- [change 1]
- [change 2]
```

## How to invoke

```bash
# Analyze AND apply suggested fixes (default behavior)
python prototype/alyx_fix.py \
    --project drone-show-manager \
    --workflow "<workflow-tag-from-the-test-run>" \
    --evaluator all \
    --context AGENT_CONTEXT.md \
    --wait 600

# Analyze only — no writes, no new evaluator versions pushed
python prototype/alyx_fix.py ... --dry-run
```

Flags:

| Flag | Required | Notes |
|---|---|---|
| `--project` | yes | Arize project name |
| `--workflow` | yes | The workflow tag the test run used (typically a timestamp like `drone-show-manager-test-2026-05-19T03:14:00Z`) |
| `--evaluator` | no | Defaults to `all`; can narrow to one evaluator |
| `--context` | yes | Path to the agent context file |
| `--wait` | no | Seconds the script sleeps before fetching, so Arize's async pipeline has time to score the new traces. Rule of thumb: ~30-40s per evaluator per trace (e.g. `600` for 15 traces × 3 judges; `5` for a quick demo). Default `0`. |
| `--dry-run` | no | When set, prints suggested patches but writes nothing and pushes no new evaluator versions. Default behavior is to patch local `evals/evaluators/<name>.json` AND push a new evaluator version to Arize. |

### When to use --dry-run

- **Default (no flag)**: the script patches the local JSON and pushes a new evaluator version automatically. Use when you trust the analysis and want an autonomous iteration loop — the typical case for re-running an already-validated evaluator against a fresh test suite.
- **With `--dry-run`**: the script returns suggested fixes but doesn't commit. Use when you want to review fixes before applying — typical for the first iteration of a new evaluator, or any change to a judge whose calibration matters.

After a (non-dry-run) apply succeeds, the workflow becomes: re-run tests → script analyzes again → if all evaluators report "No fix needed", the loop has converged.

## Interpreting the output

The script returns one block per evaluator in this format:

```
▸ evaluator:      <name>
▸ analysis:       Correct <N>/<M> test traces. <diagnosis>.
▸ fix:            <suggested fix, or "No fix needed.">
▸ context-needed: <additional context needed, or "No new context needed.">
```

How to act on each diagnosis:

- **"Judge is wrong"** → update the evaluator template via `ax evaluators create-template-evaluator-version`. The script's `fix:` line describes what to change.
- **"Agent is wrong"** → update the agent's system prompt or tool behavior. The script's `fix:` line indicates what behavior needs adjustment.
- **"All correct"** → no action needed for that evaluator.
- **`context-needed:` non-empty** → augment `AGENT_CONTEXT.md` before the next run so the analysis sharpens.

## After invoking

1. Read the analysis.
2. Apply any suggested fixes (evaluator updates and/or agent changes).
3. If new context was requested, update `AGENT_CONTEXT.md`.
4. Re-run the test suite with a fresh workflow tag.
5. Call this skill again.

## Limitations to communicate to the user

This is a prototype, not the final feature. Things it doesn't do that the real `ax alyx fix` would:

- Apply is one-way — no diff/preview UI, no rollback. Use `--dry-run` first when in doubt.
- Doesn't filter traces by metadata.workflow attribute; uses a name pattern fallback.
- Doesn't have full platform-internal context — operates from what the `ax` CLI exposes plus the agent context file you provide.

If the user asks "can you just do all of this," explain that the prototype both suggests AND applies fixes by default (use `--dry-run` to preview), but lacks the real product's structured patch / rollback / platform-internal context.

## Failure modes

- **"context file not found"** — Create `AGENT_CONTEXT.md` first.
- **"No traces with evaluations found"** — Either the test run hasn't completed, async scoring hasn't landed yet (re-run with a larger `--wait`, e.g. `600` for 15 traces × 3 judges), or the workflow tag doesn't match any traces.
- **OpenAI API error** — Check `OPENAI_API_KEY` in `.env`; the script uses the `OPENAI_JUDGE_MODEL` env var or `gpt-4.1` as fallback.

## Example: full iteration cycle

```bash
# 1. Run tests (tags traces with a unique workflow attribute)
python tests/smoke_test.py

# 2. Ask Alyx to analyze AND apply fixes (default). Add --dry-run to preview only.
python prototype/alyx_fix.py \
    --project drone-show-manager \
    --workflow "drone-show-manager-test-2026-05-19T03:14:00Z" \
    --evaluator all \
    --context AGENT_CONTEXT.md \
    --wait 600

# 3. Re-run tests
python tests/smoke_test.py
```

Repeat until the analysis reports "all correct" or until the remaining failures are intentional trade-offs.
