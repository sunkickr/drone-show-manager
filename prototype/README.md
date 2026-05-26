# Alyx Fix — Prototype

A standalone Python script that demonstrates the proposed `ax alyx fix` flow without requiring a real Arize-side implementation.

The script accepts the same CLI shape the real command would expose, fetches recent test traces, and calls an OpenAI model behind a system prompt that approximates Alyx's role. Output matches the proposed format exactly. Designed for live demo, not production.

## The flow

```bash
# 1. Claude Code runs the tests (tags each test trace with a workflow attribute)
python tests/smoke_test.py

# 2. Claude Code asks Alyx (via the prototype script).
# --wait blocks until Arize's async pipeline has scored the new traces.
# Rule of thumb: ~30-40s per evaluator per trace once it picks the trace up,
# so 15 traces × 3 online judges (45 evaluations) lands in ~8-10 min.
# For a quick demo run on a couple of traces, drop it to --wait 5.
# By default the script patches the local JSON and pushes a new evaluator
# version. Add --dry-run to analyze only (no writes).
python prototype/alyx_fix.py \
    --project drone-show-manager \
    --workflow "drone-show-manager-test-yyyy-MM-dd'T'HH:mm:ss.SSS'Z'" \
    --evaluator all \
    --context AGENT_CONTEXT.md \
    --wait 600

# 3. Output: one analysis block per evaluator
▸ evaluator:      evidence_grounded
▸ analysis:       Correct 14/15 test traces. Judge missed cross-show copy
                  intent because rule wording was ambiguous.
▸ fix:            New evaluator version with explicit intent-to-copy rule.
                  Patch attached as diff.
▸ context-needed: No new context needed.

▸ evaluator:      response_quality
▸ analysis:       Correct 15/15 test traces. No issues with judge.
▸ fix:            No fix needed.
▸ context-needed: No new context needed.

# 4. Claude Code re-runs the tests (new workflow attribute)
python tests/smoke_test.py
```

## What the prototype does

| Concern | Real `ax alyx fix` would | The prototype does |
|---|---|---|
| Authenticate | Use Arize's CLI auth | Inherits the user's `ax` profile |
| Fetch traces | Filter by `metadata.workflow` attribute | Filters by name pattern via `ax spans export`, falls back to mock data if none found |
| Analyze | Call Alyx's internal workflows | Calls OpenAI with a system prompt approximating Alyx |
| Apply fix | Call `ax evaluators create-template-evaluator-version` automatically | Default: patches local `evals/evaluators/<name>.json` and pushes a new version. With `--dry-run`: prints the suggested patch but doesn't commit. |
| Honor `--wait` | Poll until evaluators have actually scored the new traces | Plain `time.sleep(wait)` — caller sets a value that exceeds expected scoring latency |

## Requirements

- Python deps: `openai`, `python-dotenv`. Already in the project's `.venv`.
- `OPENAI_API_KEY` set in `.env` (already present).
- `ARIZE_SPACE_ID` in `.env` (already present). If absent, the script falls back to mock traces so the demo always produces output.
- `ax` CLI installed at `/Users/davidkoenitzer/.local/bin/ax` (already installed).

## CLI flags

| Flag | Required | Description |
|---|---|---|
| `--project` | yes | Arize project name (e.g. `drone-show-manager`) |
| `--workflow` | yes | Workflow tag identifying this test run |
| `--evaluator` | no (default `all`) | Evaluator name to focus on, or `all` |
| `--context` | yes | Path to a markdown/text file describing the agent (system prompt, design rationale, recent changes) |
| `--wait` | no (default `0`) | Seconds the script sleeps before fetching, to give Arize's async pipeline time to score the new traces. Rule of thumb: ~30-40s per evaluator per trace. Pass `600` for a 15-trace × 3-judge run; pass `5` for a quick demo. |
| `--dry-run` | no | When set, prints the proposed patches but writes nothing and pushes no new evaluator versions. Default behavior is to apply each suggested fix: patches `evals/evaluators/<name>.json` in place, then pushes a new evaluator version via `ax evaluators create-template-evaluator-version`. |

## Caveats — what makes this a prototype, not the real thing

1. **No platform-internal access.** The real Alyx has direct access to Arize's evaluator definitions, variable resolution logic, and update endpoints. The prototype works from whatever the `ax` CLI exposes plus a system prompt.
2. **The apply path is a text-replacement patch, not a structured update.** The model returns an `old_text`/`new_text` pair that gets find-and-replaced into the local JSON. The real Alyx would produce a richer, structurally-aware patch. Use `--dry-run` to inspect the patch before letting it write.
3. **No multi-evaluator orchestration.** The real Alyx would understand which evaluators are configured on the project. The prototype reads local JSON files in `evals/evaluators/`.
4. **No fine-grained variable resolution diagnosis.** The real Alyx could tell you exactly which span the judge's `{output}` resolved to. The prototype reasons from explanations.
5. **No cost guardrails.** The real CLI would have quotas and pricing. The prototype spends OpenAI tokens directly.

## Example: creating an AGENT_CONTEXT.md

The context file is how the calling agent transfers project knowledge to Alyx. A useful template:

```markdown
# Agent Context

## What the agent does
Drone-show management agent for Jira (project KAN). Five tools:
list_shows, get_show, list_shows_by_field, create_show, transition_show.

## Key design rules (from system prompt)
- Pipeline is forward-only: Sales → Contract → Show Design → Show Operations → Complete
- Never fabricate field values; refuse cross-show copy requests
- One-question-at-a-time intake for create/transition flows
- Refusals don't require a get_show first

## Recent changes
- Added pronoun-resolution clause (resolves "it" / "the show" to last-named show)
- Tools now return self-describing {status, message, ...} instead of opaque sentinels
- Added workflow-span enrichment so {output} resolves deterministically
```

The agent context is the most consequential input — without it Alyx falls back to generic platform-level reasoning. With it, Alyx can produce project-specific diagnoses.

## What's not in this prototype but would matter for the real product

- A `--run` flag that auto-discovers traces by run ID (cleaner than `--workflow` pattern matching)
- Multi-trace grouping by root cause across failures
- Real apply-with-rollback (the prototype's apply path is one-way; no diff/preview, no undo)
- An MCP server wrapper for agents that prefer auto-discovery over CLI
- Telemetry on adoption, context size, and time-to-fix

See the deck (slides 14-19) for the full product proposal.
