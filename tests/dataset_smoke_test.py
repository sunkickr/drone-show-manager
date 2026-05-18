"""Smoke test driven by the Arize dataset `drone_show_manager_v2`.

Pulls every prompt from the dataset, runs it through the live agent in its
own Arize trace (named `dataset_smoke:<id>`), then snapshot-restores Jira.

The point isn't local pass/fail — there are no rubric checks here. We just
want each prompt to fire a real trace so the three online evaluators
(`evidence_grounded`, `right_tool_chosen`, `response_quality`) score them
automatically in Arize within a minute or two.

Run with:
    .venv/bin/python tests/dataset_smoke_test.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

from agents import Runner, trace

from backend.tracing import init_tracing
from backend import jira_client
from agent.drone_show_agent import build_agent


DEFAULT_DATASET_NAME = "drone_show_manager_v2"
SPACE_ID = "U3BhY2U6NDQ0MDY6alVPYw=="
AX_BIN = "/Users/davidkoenitzer/.local/bin/ax"

# Allow override via CLI arg: `python tests/dataset_smoke_test.py <dataset_name>`
DATASET_NAME = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATASET_NAME


def fetch_dataset_prompts():
    """Pull dataset examples via ax and return list of (id, prompt) tuples."""
    result = subprocess.run(
        [AX_BIN, "datasets", "export", DATASET_NAME, "--space", SPACE_ID, "--stdout"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    examples = data if isinstance(data, list) else data.get("examples", [])
    out = []
    for ex in examples:
        props = ex.get("additional_properties") or {}
        prompt = props.get("input")
        # New datasets use platform-managed example ids; the meaningful
        # test id lives in additional_properties.test_id. Older datasets
        # (drone_show_manager_v2) put it directly on top-level id.
        test_id = props.get("test_id") or ex.get("id")
        if prompt:
            out.append((test_id, prompt))
    return out


def run_one(agent, test_id, prompt):
    with trace(workflow_name=f"dataset_smoke:{test_id}"):
        try:
            result = Runner.run_sync(agent, [{"role": "user", "content": prompt}])
        except Exception as e:
            return f"<exception: {type(e).__name__}: {e}>"
    return (getattr(result, "final_output", "") or "")


def main():
    init_tracing()
    agent = build_agent()

    print("\nFetching dataset…")
    tests = fetch_dataset_prompts()
    print(f"  got {len(tests)} prompts")

    print("\nSnapshotting board state…")
    snap = jira_client.snapshot_board()
    print(f"  captured {len(snap)} tickets")

    try:
        for i, (test_id, prompt) in enumerate(tests, 1):
            print(f"\n{'=' * 72}\n[{i:2d}/{len(tests)}] {test_id}\nPROMPT: {prompt}\n{'-' * 72}")
            answer = run_one(agent, test_id, prompt)
            preview = answer.replace("\n", " ")
            if len(preview) > 240:
                preview = preview[:240] + "…"
            print(f"ANSWER: {preview}")
    finally:
        print(f"\n{'=' * 72}\nRestoring board state…")
        time.sleep(2)
        actions = jira_client.restore_board(snap)
        if actions:
            for a in actions:
                print(f"  {a}")
        else:
            print("  no changes detected")

    print(f"\n{'=' * 72}\nDone. {len(tests)} traces sent to Arize.")
    print("Online evaluators should score them within ~1-2 minutes.")
    print("View at: https://app.arize.com → drone-show-manager project → Traces")
    time.sleep(5)  # let trace exporter flush


if __name__ == "__main__":
    main()
