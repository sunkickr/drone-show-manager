# Evals

The development workflow for the drone show agent — datasets, experiments, and scoring.

This sits in `evals/` rather than `tests/` because it does something different from tests: instead of asserting "did the code do the right thing?" with hard pass/fail, it runs the agent against a set of inputs and *scores* the outputs with both deterministic checks and LLM judges. Each run is captured as an **experiment** in Arize so you can compare runs across prompt / model / agent changes.

## Files

| File | Purpose |
|---|---|
| `dataset.py` | The 13-row eval dataset and upload helper. `load_df()` returns a pandas DataFrame; `upload()` pushes it to Arize. |
| `evaluators.py` | 3 code evaluators + 3 LLM judges. Each returns an `EvaluationResult` with score/label/explanation. |
| `run_experiment.py` | Orchestrator: snapshots the Jira board, builds the agent, runs the dataset through it, scores with all evaluators, restores the board. |

## Evaluators

| Name | Type | Cost | What it scores |
|---|---|---|---|
| `contains_expected_keys` | Code | free | All `expected_keys` (e.g. `KAN-9`, `KAN-10`) must appear in the answer |
| `no_forbidden_substrings` | Code | free | None of `must_not_contain` (e.g. `"deleted"`, `"transitioned"`) may appear |
| `correct_refusal` | Code | free | Mutation rows must succeed; refusal/lookup/intake rows must NOT mutate |
| `evidence_grounded` | LLM judge | ~$0.005/row | Every factual claim in the answer must be traceable to tool output |
| `right_tool_chosen` | LLM judge (partly code-shortcut) | ~$0.002/row | Did the agent pick the expected tool? |
| `response_quality` | LLM judge | ~$0.003/row | Clarity, conciseness, on-topic |

Judges use `OPENAI_JUDGE_MODEL` (default `gpt-5.4`) — chosen so the judge is sharper than the agent it's evaluating.

## Usage

### Run an experiment (uploads to Arize)

```bash
.venv/bin/python evals/run_experiment.py
```

This will:
1. Snapshot all 21 Jira tickets
2. Upload the dataset to Arize (no-op if it already exists)
3. Run every dataset row through the agent (concurrency=3)
4. Score every output with all 6 evaluators
5. Restore any board changes (e.g. test 13 mutates KAN-13; it gets reverted)
6. Push the experiment results to Arize and print a score summary

The experiment is visible at `app.arize.com` under the `drone-show-manager` project's "Experiments" page.

### Iterate locally without spending Arize quota

```bash
.venv/bin/python evals/run_experiment.py --dry-run
```

Runs evaluators, prints scores, doesn't upload the experiment. Useful while tuning evaluator prompts.

> **Note:** the Arize SDK intentionally runs `--dry-run` on only the **first 10 rows** of the dataset (`input_df.head(10)` in the SDK). A full run (no `--dry-run`) processes all 13 rows.

### Override the model under test

```bash
OPENAI_MODEL=gpt-4.1 .venv/bin/python evals/run_experiment.py --experiment-name model_4_1_full
```

Then compare in Arize's experiments table side-by-side with the gpt-4.1-mini run.

### Just upload the dataset

```bash
.venv/bin/python evals/dataset.py
```

Useful when you want the dataset ready for use in the Arize Prompt Playground UI.

## Dataset schema

Each row encodes both the input and the rubric:

| Column | Type | Example |
|---|---|---|
| `id` | string | `01_list_contract` |
| `input` | string | `"What's in Contract?"` |
| `expected_kind` | string enum | `lookup` / `refusal` / `intake` / `ambiguity` / `mutation` |
| `expected_tool` | string | `list_shows`, `get_show`, `none`, `any` |
| `expected_keys` | JSON list | `["KAN-9", "KAN-10", "KAN-11"]` |
| `must_contain` | JSON list | `["Patagonia", "Show Operations"]` |
| `must_not_contain` | JSON list | `["deleted", "transitioned"]` |
| `mutation_allowed` | bool | `false` for everything except `mutation` kind |
| `notes` | string | Free-form context (helpful for the LLM judges) |

## Comparing runs

In Arize, each experiment shows up as a row in the project's experiments table with mean scores per evaluator. To compare:

1. Run experiment A (e.g. baseline)
2. Change something — prompt, model, tool
3. Run experiment B with a new `--experiment-name`
4. Open the experiments table → select both → diff view shows per-row score deltas

## Iterating on live evaluators

The online evaluators in `online_evals.md` are configured in the Arize UI and score every live trace as it arrives. When you tighten one, the next agent run usually surfaces mis-scores you have to diagnose and resolve. The loop below is the workflow we used to do that.

### The loop

1. **Run live smoke tests.** Each prompt fires as its own trace named `dataset_smoke:<test_id>`.

   ```bash
   .venv/bin/python tests/dataset_smoke_test.py <dataset_name>
   ```

2. **Wait for async eval scoring.** Arize scores new traces asynchronously — typically 1-2 minutes after the trace lands. If a trace shows zero entries in `evaluations`, the scoring hasn't run yet.

3. **Fetch traces + eval results.** Use `ax spans export` and parse the `evaluations` array on the root span.

   ```bash
   ax spans export "drone-show-manager" \
     --space "$ARIZE_SPACE_ID" \
     --filter "name = 'dataset_smoke:<test_id>'" \
     --days 1 -l 5 --stdout | python3 -c "
   import sys, json
   spans = sorted(json.load(sys.stdin), key=lambda s: s['start_time'], reverse=True)
   for e in spans[0].get('evaluations', []):
       print(f'{e[\"name\"]:<20} {e[\"label\"]:<12} ({e[\"score\"]})')
       print(f'  {e[\"explanation\"][:200]}\n')
   "
   ```

4. **Decide if the agent or the judge is wrong.** Compare the trace's `output.value` to the judge's `explanation`:
   - Does the agent's response actually violate the rule the judge cites? → **agent bug** (fix the system prompt or tool behavior).
   - Does the judge's explanation contradict itself, or quote content not in the response? → **judge bug** (often instrumentation; see the prerequisite below).
   - Does the judge apply a stricter standard than its prompt describes? → **judge prompt needs tightening**.

5. **Update the evaluator.** Edit `evals/evaluators/<name>.json` and push a new version:

   ```bash
   python3 -c "
   import json, subprocess
   cfg = json.load(open('evals/evaluators/<name>.json'))
   subprocess.run(['ax', 'evaluators', 'create-template-evaluator-version', cfg['name'],
       '--space', '$ARIZE_SPACE_ID',
       '--commit-message', 'describe what changed',
       '--template-name', cfg['template_name'],
       '--template', cfg['template'],
       '--ai-integration-id', '$ARIZE_OPENAI_INTEGRATION_ID',
       '--model-name', cfg['model_name'],
       '--data-granularity', cfg['data_granularity'],
       '--direction', cfg['direction'],
       '--classification-choices', json.dumps(cfg['classification_choices']),
       *(['--include-explanations'] if cfg['include_explanations'] else []),
       *(['--use-function-calling'] if cfg['use_function_calling'] else []),
   ])"
   ```

   Versions are immutable — the latest becomes active automatically. Past versions stay queryable via `ax evaluators list-versions`. Find your AI integration ID via `ax ai-integrations list --space "$ARIZE_SPACE_ID"`.

Then re-run the smoke test and repeat until scores stabilize.

### Prerequisite: workflow-span enrichment

This loop assumes the `EnrichingTracingProcessor` in `backend/tracing.py` is active. Without it, trace-scoped `{output}` resolution falls back to inconsistent descendant spans — producing what look like judge bugs but are actually instrumentation issues. If you're seeing self-contradictory judge explanations, or judges referencing data the agent never produced, verify `attributes.output.value` is set on the workflow root span before iterating on the judge prompt.

### Tips

- The judge's `explanation` is the strongest debug signal. Internal contradictions ("the agent claimed X is true... the agent's response omitted X entirely") almost always mean the judge saw different data than you expected.
- Patterns across tests (one judge scoring 0 on many traces) usually point at the judge; one-off failures usually point at the agent.
- Keep `evals/evaluators/<name>.json` as the source of truth. Commit JSON changes alongside the `create-template-evaluator-version` push so the repo and Arize stay in sync.

## Extending

- Add a new dataset row → append to `EXAMPLES` in `dataset.py`, re-run
- Add a new evaluator → write a function returning `EvaluationResult`, add to `EVALUATORS` in `evaluators.py`
- Use a different judge model → set `OPENAI_JUDGE_MODEL` in your `.env`

## Prompt Playground (no-code)

Once `dataset.py` has uploaded `drone_show_manager_v1`, you can open it in Arize's Prompt Playground, paste a candidate system prompt, choose a model, and run it against the dataset row-by-row — without leaving the UI. Useful for quick prompt iteration before promoting a change to the codebase.
