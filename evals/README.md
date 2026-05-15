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

## Extending

- Add a new dataset row → append to `EXAMPLES` in `dataset.py`, re-run
- Add a new evaluator → write a function returning `EvaluationResult`, add to `EVALUATORS` in `evaluators.py`
- Use a different judge model → set `OPENAI_JUDGE_MODEL` in your `.env`

## Prompt Playground (no-code)

Once `dataset.py` has uploaded `drone_show_manager_v1`, you can open it in Arize's Prompt Playground, paste a candidate system prompt, choose a model, and run it against the dataset row-by-row — without leaving the UI. Useful for quick prompt iteration before promoting a change to the codebase.
