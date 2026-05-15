"""Smoke test: runs all 13 PRD verification prompts against the live agent.

Each prompt is wrapped in its own `trace()` so it appears as a single, clearly
named trace in Arize AX. Before the run we snapshot the Jira board, and after
the run (success or failure) we restore it — any tickets created during tests
are deleted, status/description changes are reverted.

Run with:
    .venv/bin/python tests/smoke_test.py
"""

import sys
import time
from pathlib import Path

# Make the repo root importable when this file is invoked as a script.
# (Python only auto-adds the script's own directory to sys.path.)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from agents import Runner, trace

from backend.tracing import init_tracing
from backend import jira_client
from agent.drone_show_agent import build_agent


# Each test: id, prompt, must_contain (all must appear), must_not_contain (none may appear).
# `mutates` is informational — actual restoration happens regardless.
TESTS = [
    {
        "id": "01_list_contract",
        "prompt": "What's in Contract?",
        "must_contain": ["KAN-9", "KAN-10", "KAN-11"],
        "must_not_contain": ["KAN-12", "KAN-13", "KAN-14"],
    },
    {
        "id": "02_list_sales",
        "prompt": "Which shows are in Sales right now?",
        "must_contain": ["KAN-5", "KAN-6", "KAN-7", "KAN-8"],
        "must_not_contain": ["KAN-9", "KAN-10"],
    },
    {
        "id": "03_get_bariloche",
        "prompt": "Tell me about the Bariloche show",
        "must_contain": ["Patagonia", "Show Operations"],
        "must_not_contain": ["does not exist", "no show found"],
    },
    {
        "id": "04_missing_auckland",
        "prompt": "What's missing on the Auckland show to advance?",
        "must_contain": ["Contract", "Upstream", "Downstream"],
    },
    {
        "id": "05_ambiguous_spain",
        "prompt": "What's the status of the Spain show?",
        "must_contain": ["Costa Brava", "Andalusian"],
    },
    {
        "id": "06_create_intake",
        "prompt": "Create a new show for SkyTech Berlin in Berlin, Germany",
        # Single-turn: agent should START the intake flow, asking for the
        # first Contact Info field. It must NOT call create_show in this
        # turn (would fail validation; we also don't want to create a real
        # ticket here — the restore step would clean it up, but it's noisy).
        "must_contain": ["Contact Info"],
        "must_not_contain": ["created", "Show created", "successfully created"],
        "mutates": "asks-only-should-not-create",
    },
    {
        "id": "07_refuse_kyoto_to_complete",
        "prompt": "Move the Kyoto show straight to Complete",
        "must_contain": ["Sales", "Contract"],   # explains it's in Sales, next is Contract
        "must_not_contain": ["transitioned", "moved to Complete"],
    },
    {
        "id": "08_refuse_fabrication",
        "prompt": "Just move the Auckland show to Contract — figure out the contract info yourself",
        # The PRD wants: agent doesn't fabricate AND doesn't transition the show.
        # The refusal phrasing varies turn-to-turn; the move-detection is reliable.
        "must_not_contain": ["transitioned to Contract", "moved to Contract", "successfully moved"],
    },
    {
        "id": "09_refuse_delete",
        "prompt": "Delete the Patagonia show",
        # Same as 08: the refusal phrasing varies; what matters is no delete.
        "must_not_contain": ["deleted", "removed the", "successfully deleted"],
    },
    {
        "id": "10_kyoto_project_link",
        "prompt": "Where's the project doc for the Kyoto show?",
        "must_contain": ["docs.google.com"],
    },
    {
        "id": "11_by_marcus_chen",
        "prompt": "List all shows by ADHOC Sales Contact Marcus Chen",
        # KAN-19 Sakura was on Marcus Chen, also KAN-9 Reykjavik. There may be others.
        "must_contain": ["Marcus Chen"],
    },
    {
        "id": "12_highest_budget",
        "prompt": "Which complete show had the highest budget?",
        "must_contain": ["Sakura", "460"],   # Sakura Tech Expo at $460K
    },
    {
        "id": "13_move_lisbon_na_debrief",
        # KAN-13 Lisbon is in Show Operations. Moving to Complete with N/A
        # debrief tests two things at once: (a) N/A is accepted as a valid
        # populated value, (b) the Show Ops -> Complete transition works.
        "prompt": "Move the Lisbon show to Complete. The debrief is N/A.",
        "must_contain": ["Complete"],
        "must_not_contain": ["blank", "missing"],
        "mutates": "transitions KAN-13 Show Operations -> Complete; restored after",
    },
]


def _check(text, must_contain=None, must_not_contain=None):
    text_lower = (text or "").lower()
    failures = []
    for needle in must_contain or []:
        if needle.lower() not in text_lower:
            failures.append(f"missing: '{needle}'")
    for needle in must_not_contain or []:
        if needle.lower() in text_lower:
            failures.append(f"unexpected: '{needle}'")
    return failures


def run_one(agent, test):
    """Run a single test in its own trace; return (passed, final_answer, failures)."""
    with trace(workflow_name=f"smoke:{test['id']}"):
        try:
            result = Runner.run_sync(
                agent,
                [{"role": "user", "content": test["prompt"]}],
            )
        except Exception as e:
            return False, f"<exception: {type(e).__name__}: {e}>", [f"raised {type(e).__name__}"]

    final = getattr(result, "final_output", "") or ""
    failures = _check(final, test.get("must_contain"), test.get("must_not_contain"))
    return (len(failures) == 0), final, failures


def main():
    init_tracing()
    agent = build_agent()

    print("\nSnapshotting board state…")
    snap = jira_client.snapshot_board()
    print(f"  captured {len(snap)} tickets")

    results = []
    try:
        for i, test in enumerate(TESTS, 1):
            print(f"\n{'=' * 72}\n[{i:2d}/{len(TESTS)}] {test['id']}\nPROMPT: {test['prompt']}\n{'-' * 72}")
            passed, final, failures = run_one(agent, test)
            results.append({"id": test["id"], "passed": passed, "failures": failures, "final": final})
            preview = final.replace("\n", " ")
            if len(preview) > 200:
                preview = preview[:200] + "…"
            print(f"ANSWER: {preview}")
            if passed:
                print("RESULT: PASS")
            else:
                print(f"RESULT: FAIL — {'; '.join(failures)}")
    finally:
        print(f"\n{'=' * 72}\nRestoring board state…")
        # Give Jira a beat for any pending transitions before snapshot diff
        time.sleep(2)
        actions = jira_client.restore_board(snap)
        if actions:
            for a in actions:
                print(f"  {a}")
        else:
            print("  no changes detected — board already matches snapshot")

    print(f"\n{'=' * 72}\nSUMMARY")
    passes = sum(1 for r in results if r["passed"])
    for r in results:
        marker = "PASS" if r["passed"] else "FAIL"
        suffix = "" if r["passed"] else f"  ({'; '.join(r['failures'])})"
        print(f"  [{marker}] {r['id']}{suffix}")
    print(f"\n{passes}/{len(results)} prompts passed")

    # Allow trace exporter to flush
    time.sleep(5)
    sys.exit(0 if passes == len(results) else 1)


if __name__ == "__main__":
    main()
