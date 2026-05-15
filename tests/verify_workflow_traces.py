"""Manual verification: walks the agent through a mix of single-turn and multi-turn
scenarios, prints whether each turn closed the workflow trace. Each detected
workflow is wrapped in its own Arize trace named after the first tool called.

Run with: .venv/bin/python tests/verify_workflow_traces.py
"""

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from opentelemetry import trace as otel_trace
from agents import Runner, trace as agents_trace

from backend.tracing import init_tracing
from backend import jira_client
from agent.drone_show_agent import build_agent, MUTATION_TOOLS, _looks_like_followup


# Each entry is a single user prompt. Adjacent entries belong to the same
# logical workflow if it's a multi-turn intake; we let the heuristic decide.
SCENARIOS = [
    {
        "label": "single-turn lookup (active shows)",
        "prompts": ["What's in Contract?"],
        "expect_workflow_closes_after_turn": [True],
    },
    {
        "label": "single-turn lookup that may go ambiguous → ask",
        "prompts": ["What's the status of the Spain show?", "The Costa Brava one"],
        # Spain is ambiguous → agent asks → workflow stays open
        # User picks one → agent answers → workflow closes
        "expect_workflow_closes_after_turn": [False, True],
    },
    {
        "label": "multi-turn intake (start of a create flow)",
        "prompts": [
            "Create a new show for SkyTech Berlin in Berlin, Germany",
            "Sara Johnson",   # supplying the first Contact Info field
        ],
        # Both turns should keep workflow open — agent keeps asking
        "expect_workflow_closes_after_turn": [False, False],
    },
    {
        "label": "refusal closes the workflow",
        "prompts": ["Delete the Patagonia show"],
        "expect_workflow_closes_after_turn": [True],
    },
]


def run_scenario(agent, scenario):
    print(f"\n{'=' * 72}\nSCENARIO: {scenario['label']}\n{'-' * 72}")
    history = []
    first_tool = None
    workflow_open = False
    ctx = None
    closed_after = []

    try:
        for turn_idx, prompt in enumerate(scenario["prompts"]):
            if not workflow_open:
                ctx = agents_trace(workflow_name="user request")
                ctx.__enter__()
                workflow_open = True
                first_tool = None

            print(f"  [turn {turn_idx + 1}] USER: {prompt}")
            history.append({"role": "user", "content": prompt})
            result = Runner.run_sync(agent, history)
            history = result.to_input_list()

            mutation_fired = False
            this_first_tool = None
            for item in getattr(result, "new_items", []):
                if type(item).__name__ == "ToolCallItem":
                    name = getattr(getattr(item, "raw_item", None), "name", "?")
                    if this_first_tool is None:
                        this_first_tool = name
                    if name in MUTATION_TOOLS:
                        mutation_fired = True

            # Rename the workflow on first tool seen
            if first_tool is None and this_first_tool:
                first_tool = this_first_tool
                try:
                    otel_trace.get_current_span().update_name(first_tool)
                    print(f"  [turn {turn_idx + 1}] renamed trace to '{first_tool}'")
                except Exception as e:
                    print(f"  [turn {turn_idx + 1}] rename failed: {e}")

            final = getattr(result, "final_output", "") or ""
            preview = final.replace("\n", " ")
            if len(preview) > 140:
                preview = preview[:140] + "…"
            print(f"  [turn {turn_idx + 1}] AGENT: {preview}")

            complete = mutation_fired or not _looks_like_followup(final)
            closed_after.append(complete)
            print(f"  [turn {turn_idx + 1}] workflow_complete={complete}  "
                  f"(mutation_fired={mutation_fired}, looks_followup={_looks_like_followup(final)})")

            if complete:
                ctx.__exit__(None, None, None)
                workflow_open = False
                ctx = None
    finally:
        if workflow_open and ctx is not None:
            ctx.__exit__(None, None, None)

    print(f"  expected_closes: {scenario['expect_workflow_closes_after_turn']}")
    print(f"  actual_closes:   {closed_after}")
    return closed_after == scenario["expect_workflow_closes_after_turn"]


def main():
    init_tracing()
    snap = jira_client.snapshot_board()
    print(f"snapshotted {len(snap)} tickets")

    agent = build_agent()
    results = []
    try:
        for scenario in SCENARIOS:
            ok = run_scenario(agent, scenario)
            results.append((scenario["label"], ok))
    finally:
        actions = jira_client.restore_board(snap)
        print(f"\n{'=' * 72}\nRESTORE")
        if actions:
            for a in actions:
                print(f"  {a}")
        else:
            print("  no changes detected")

    print(f"\n{'=' * 72}\nSUMMARY")
    passes = sum(1 for _, ok in results if ok)
    for label, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"\n{passes}/{len(results)} scenarios matched expected boundaries")

    time.sleep(5)  # let traces flush
    sys.exit(0 if passes == len(results) else 1)


if __name__ == "__main__":
    main()
