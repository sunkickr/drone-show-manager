"""Run the drone show manager experiment against the Arize dataset.

For each row of the dataset:
  1. Snapshot the Jira board (once, around the whole run)
  2. Build a fresh agent
  3. Send the row's input as a single user turn
  4. Capture the agent's final output, the tool calls it made, and whether
     a mutation tool fired
  5. Return that dict so all evaluators in evaluators.EVALUATORS can score it

Arize uploads the experiment table to the dashboard automatically.

Usage:
    .venv/bin/python evals/run_experiment.py
    .venv/bin/python evals/run_experiment.py --dry-run   # skip upload to Arize
    .venv/bin/python evals/run_experiment.py --no-upload-dataset   # use inline DataFrame
"""

import argparse
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from agents import Runner

from arize.experimental.datasets import ArizeDatasetsClient

from backend.tracing import init_tracing
from backend import jira_client
from agent.drone_show_agent import build_agent, MUTATION_TOOLS

from evals import dataset as eval_dataset
from evals.evaluators import EVALUATORS


def make_task():
    """Returns an ASYNC task function bound to a freshly-built agent.

    Arize's run_experiment manages its own asyncio loop and dispatches tasks
    concurrently. Calling `Runner.run_sync` from inside that loop would error
    (asyncio.run inside an existing loop), so the task awaits `Runner.run`
    instead. A single Agent instance is reused — Agents SDK agents are
    stateless plus thread-safe; per-turn state lives in the input history.

    The parameter MUST be named `dataset_row` — Arize's _bind_task_signature
    pattern-matches the parameter name to decide what to pass.
    """
    agent = build_agent()

    async def run_one(dataset_row):
        row = dataset_row  # Arize passes the row dict directly
        prompt = row.get("input", "")

        try:
            result = await Runner.run(
                agent,
                [{"role": "user", "content": prompt}],
            )
        except Exception as e:
            return {
                "final_output": "",
                "tool_calls": [],
                "first_tool": None,
                "mutation_fired": False,
                "error": f"{type(e).__name__}: {e}",
            }

        tool_calls = []
        first_tool = None
        mutation_fired = False

        items = list(getattr(result, "new_items", []))
        i = 0
        while i < len(items):
            item = items[i]
            kind = type(item).__name__
            if kind == "ToolCallItem":
                raw = getattr(item, "raw_item", None)
                name = getattr(raw, "name", "?")
                args = getattr(raw, "arguments", "")
                output = ""
                if i + 1 < len(items) and type(items[i + 1]).__name__ == "ToolCallOutputItem":
                    # Keep the full tool output — the evidence_grounded judge
                    # needs to see everything the agent saw. Truncating here
                    # silently makes grounded answers look ungrounded.
                    output = str(getattr(items[i + 1], "output", ""))[:15000]
                tool_calls.append({"name": name, "args": str(args)[:2000], "output": output})
                if first_tool is None:
                    first_tool = name
                if name in MUTATION_TOOLS:
                    mutation_fired = True
            i += 1

        return {
            "final_output": getattr(result, "final_output", "") or "",
            "tool_calls": tool_calls,
            "first_tool": first_tool,
            "mutation_fired": mutation_fired,
        }

    return run_one


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Run evaluators locally; don't upload experiment to Arize.")
    parser.add_argument("--no-upload-dataset", action="store_true",
                        help="Use the inline DataFrame instead of uploading dataset to Arize.")
    parser.add_argument("--experiment-name", default=None,
                        help="Override the experiment name (default: drone_show_agent_<timestamp>).")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Number of dataset rows to run in parallel.")
    args = parser.parse_args()

    init_tracing()

    space_id = os.environ["ARIZE_SPACE_ID"]
    api_key = os.environ["ARIZE_API_KEY"]
    client = ArizeDatasetsClient(api_key=api_key)

    print("Snapshotting Jira board (mutations will be reverted at end)…")
    snap = jira_client.snapshot_board()
    print(f"  captured {len(snap)} tickets\n")

    experiment_name = args.experiment_name or f"drone_show_agent_{int(time.time())}"
    print(f"Experiment name: {experiment_name}")
    print(f"Model: {os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini')}")
    print(f"Judge model: {os.environ.get('OPENAI_JUDGE_MODEL', 'gpt-5.4')}")
    print(f"Dry run: {args.dry_run}\n")

    df = eval_dataset.load_df()
    print(f"Dataset: {len(df)} rows ({list(df['id'])})")
    if not args.no_upload_dataset:
        print(f"Uploading dataset '{eval_dataset.DATASET_NAME}' to Arize…")
        try:
            did = client.create_dataset(
                space_id=space_id,
                dataset_name=eval_dataset.DATASET_NAME,
                dataset_type=eval_dataset.GENERATIVE,
                data=df,
            )
            print(f"  dataset_id: {did}\n")
        except Exception as e:
            print(f"  (skipping upload — likely exists already: {e})\n")

    task = make_task()

    try:
        print("Running experiment…")
        result = client.run_experiment(
            space_id=space_id,
            experiment_name=experiment_name,
            task=task,
            dataset_df=df,
            dataset_name=eval_dataset.DATASET_NAME,
            evaluators=EVALUATORS,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
            exit_on_error=False,
        )
    finally:
        print("\nRestoring Jira board…")
        actions = jira_client.restore_board(snap)
        if actions:
            for a in actions:
                print(f"  {a}")
        else:
            print("  no changes detected")

    if result is None:
        print("\nExperiment returned None (likely no rows processed).")
        sys.exit(1)

    experiment_id, result_df = result
    print(f"\nExperiment complete.")
    print(f"  experiment_id: {experiment_id or '(dry run)'}")
    print(f"  rows processed: {len(result_df)}")

    # Dump the full result table so failures can be inspected offline.
    out_path = _PROJECT_ROOT / "evals" / f"result_{experiment_name}.csv"
    try:
        result_df.to_csv(out_path, index=False)
        print(f"  result table: {out_path}")
    except Exception as e:
        print(f"  (could not write result CSV: {e})")

    # Summarize evaluator scores
    print("\nScore summary:")
    eval_score_cols = [c for c in result_df.columns if c.startswith("eval.") and c.endswith(".score")]
    for col in eval_score_cols:
        scores = result_df[col].dropna()
        if len(scores):
            name = col[len("eval."):-len(".score")]
            mean = scores.mean()
            n_pass = (scores >= 1.0).sum()
            # Identify which rows failed (score < 1.0)
            failed_mask = result_df[col] < 1.0
            failed_ids = []
            if "id" in result_df.columns:
                failed_ids = result_df.loc[failed_mask, "id"].dropna().tolist()
            elif "dataset_row" in result_df.columns:
                failed_ids = [
                    (r or {}).get("id", "?") for r in result_df.loc[failed_mask, "dataset_row"]
                ]
            suffix = f"  failed: {failed_ids}" if failed_ids else ""
            print(f"  {name:30s}  mean={mean:.2f}  pass={n_pass}/{len(scores)}{suffix}")

    if not args.dry_run and experiment_id:
        print(f"\nView in Arize: search experiments page for '{experiment_name}'.")

    time.sleep(5)  # let traces flush


if __name__ == "__main__":
    main()
