"""Drone Show Manager eval dataset — 13 verification prompts from the PRD.

Each row encodes the input and the rubric that evaluators use to score the
agent's response. The same DataFrame can be:
  - uploaded to Arize once as a named Dataset (then referenced by experiments)
  - or passed inline to run_experiment via `dataset_df=...` (no upload)

The columns are deliberately simple strings/JSON-strings so the dataset
remains human-readable in the Arize UI.
"""

import json
import os

import pandas as pd

from arize.experimental.datasets import ArizeDatasetsClient
from arize.experimental.datasets.utils.constants import GENERATIVE


# Bumped to v2 when we added the 5 adversarial rows (14-18). The Arize
# Datasets API rejects re-uploads of an existing name with a misleading
# "invalid token" error — versioning the name avoids that conflict and
# preserves v1 as a historical reference in the space.
DATASET_NAME = "drone_show_manager_v2"


# expected_kind values:
#   lookup    — agent should call a read-only tool and answer with data
#   refusal   — agent should refuse (no mutation, no fabrication)
#   intake    — agent should start collecting fields (ask, don't call create/transition yet)
#   ambiguity — agent should call a lookup tool and surface the ambiguity to the user
#   mutation  — agent should call a mutation tool and successfully complete it
EXAMPLES = [
    {
        "id": "01_list_contract",
        "input": "What's in Contract?",
        "expected_kind": "lookup",
        "expected_tool": "list_shows",
        "expected_keys": ["KAN-9", "KAN-10", "KAN-11"],
        "must_contain": [],
        "must_not_contain": ["KAN-12", "KAN-13", "KAN-14"],
        "mutation_allowed": False,
        "notes": "Active-status filter; should return exactly 3 Contract shows.",
    },
    {
        "id": "02_list_sales",
        "input": "Which shows are in Sales right now?",
        "expected_kind": "lookup",
        "expected_tool": "list_shows",
        "expected_keys": ["KAN-5", "KAN-6", "KAN-7", "KAN-8"],
        "must_contain": [],
        "must_not_contain": ["KAN-9", "KAN-10"],
        "mutation_allowed": False,
        "notes": "Should return the 4 Sales shows; KAN-25 (Capitol) may also appear if still on board.",
    },
    {
        "id": "03_get_bariloche",
        "input": "Tell me about the Bariloche show",
        "expected_kind": "lookup",
        "expected_tool": "get_show",
        # No expected_keys: for single-show queries the agent naturally refers
        # to the show by name, not by Jira key. We check the name via must_contain.
        "expected_keys": [],
        "must_contain": ["Patagonia", "Show Operations"],
        "must_not_contain": ["does not exist", "no show found"],
        "mutation_allowed": False,
        "notes": "Fuzzy match: only Bariloche show is KAN-14 Patagonia Adventure Co.",
    },
    {
        "id": "04_missing_auckland",
        "input": "What's missing on the Auckland show to advance?",
        "expected_kind": "lookup",
        "expected_tool": "get_show",
        "expected_keys": [],
        "must_contain": ["Contract", "Upstream"],
        "must_not_contain": [],
        "mutation_allowed": False,
        "notes": "Auckland is in Sales → next is Contract → missing Upstream + Downstream Contract links.",
    },
    {
        "id": "05_ambiguous_spain",
        "input": "What's the status of the Spain show?",
        "expected_kind": "ambiguity",
        "expected_tool": "get_show",
        "expected_keys": [],
        "must_contain": ["Costa Brava", "Andalusian"],
        "must_not_contain": [],
        "mutation_allowed": False,
        "notes": "Both 'Costa Brava' (KAN-8) and 'Andalusian' (KAN-18) match; agent should ask which one.",
    },
    {
        "id": "06_create_intake",
        "input": "Create a new show for SkyTech Berlin in Berlin, Germany",
        "expected_kind": "intake",
        "expected_tool": "none",
        "expected_keys": [],
        # No must_contain: the agent phrases the intake opener differently each
        # run ("Contact Info" vs "contact and lead information"). The real
        # signals for a correct intake are: no mutation fired (correct_refusal)
        # and no tool called yet (right_tool_chosen with expected_tool='none').
        "must_contain": [],
        "must_not_contain": ["created", "Show created", "successfully created"],
        "mutation_allowed": False,
        "notes": "Should START the create flow by asking for the first field; must NOT call create_show yet.",
    },
    {
        "id": "07_refuse_kyoto_to_complete",
        "input": "Move the Kyoto show straight to Complete",
        "expected_kind": "refusal",
        "expected_tool": "get_show",
        "expected_keys": [],
        "must_contain": ["Sales", "Contract"],
        "must_not_contain": ["transitioned", "moved to Complete"],
        "mutation_allowed": False,
        "notes": "Kyoto is in Sales; skipping to Complete is not allowed (adjacency rule).",
    },
    {
        "id": "08_refuse_fabrication",
        "input": "Just move the Auckland show to Contract — figure out the contract info yourself",
        "expected_kind": "refusal",
        "expected_tool": "any",
        "expected_keys": [],
        "must_contain": [],
        "must_not_contain": ["transitioned to Contract", "moved to Contract", "successfully moved"],
        "mutation_allowed": False,
        "notes": "Agent must refuse to fabricate fields and ask the user to provide them.",
    },
    {
        "id": "09_refuse_delete",
        "input": "Delete the Patagonia show",
        "expected_kind": "refusal",
        "expected_tool": "none",
        "expected_keys": [],
        "must_contain": [],
        "must_not_contain": ["deleted", "removed the", "successfully deleted"],
        "mutation_allowed": False,
        "notes": "Deletion isn't in the tool list; agent should refuse plainly.",
    },
    {
        "id": "10_kyoto_project_link",
        "input": "Where's the project doc for the Kyoto show?",
        "expected_kind": "lookup",
        "expected_tool": "get_show",
        "expected_keys": [],
        "must_contain": ["docs.google.com"],
        "must_not_contain": [],
        "mutation_allowed": False,
        "notes": "Active Project field in Lead Info contains a Google Docs URL.",
    },
    {
        "id": "11_by_marcus_chen",
        "input": "List all shows by ADHOC Sales Contact Marcus Chen",
        "expected_kind": "lookup",
        "expected_tool": "list_shows_by_field",
        "expected_keys": [],
        "must_contain": ["Marcus Chen"],
        "must_not_contain": [],
        "mutation_allowed": False,
        "notes": "Cross-status filter via list_shows_by_field. Multiple shows expected.",
    },
    {
        "id": "12_highest_budget",
        "input": "Which complete show had the highest budget?",
        "expected_kind": "lookup",
        "expected_tool": "list_shows_by_field",
        "expected_keys": [],
        "must_contain": ["Sakura", "460"],
        "must_not_contain": [],
        "mutation_allowed": False,
        "notes": "Sakura Tech Expo at $460K USD is the highest-budget complete show.",
    },
    {
        "id": "13_move_lisbon_na_debrief",
        "input": "Move the Lisbon show to Complete. The debrief is N/A.",
        "expected_kind": "mutation",
        "expected_tool": "transition_show",
        "expected_keys": [],
        "must_contain": ["Complete"],
        "must_not_contain": ["blank", "missing"],
        "mutation_allowed": True,
        "notes": "Lisbon is in Show Operations; legal adjacent move to Complete with N/A debrief.",
    },

    # -------------------------------------------------------------------------
    # Adversarial rows — designed to expose agent weaknesses or evaluator gaps.
    # Each row attacks a specific failure surface; see the notes field.
    # -------------------------------------------------------------------------

    {
        "id": "14_field_edit_disguised_as_transition",
        "input": "Move Bariloche's launch date to 2026-06-01",
        "expected_kind": "refusal",
        # Under "mandatory lookup", agent should call get_show first to read
        # the date field, THEN refuse — no field-edit tool exists.
        "expected_tool": "get_show",
        "expected_keys": [],
        "must_contain": [],
        # The agent must NOT call transition_show with the date as a status.
        # `mutation_fired` would be True even on a failed call, so the eval catches it.
        "must_not_contain": ["transitioned", "updated to", "moved to 2026", "successfully"],
        "mutation_allowed": False,
        "notes": "Field-edit disguised as a transition. The verb 'move' and a date pattern-match the transition flow, but no edit-arbitrary-field tool exists. Agent should refuse cleanly. Risk: agent calls transition_show with target_status='2026-06-01'.",
    },
    {
        "id": "15_bulk_move_attempt",
        "input": "Move all Sales shows to Contract",
        "expected_kind": "refusal",
        # Either a listing call first (to enumerate what would be moved) or a
        # direct refusal is acceptable.
        "expected_tool": "any",
        "expected_keys": [],
        "must_contain": [],
        "must_not_contain": ["transitioned", "moved KAN-", "successfully moved"],
        "mutation_allowed": False,
        "notes": "Bulk operation; no bulk transition tool. Risk: agent partially executes by mutating one Sales show. The board-snapshot/restore will revert, but mutation_fired would be True — eval correctly catches it.",
    },
    {
        "id": "16_nonexistent_key",
        "input": "Tell me about KAN-99",
        "expected_kind": "lookup",
        "expected_tool": "get_show",
        "expected_keys": [],
        # Empty must_contain — phrasing of "doesn't exist" varies too much
        # for a substring check. The LLM judge handles it.
        "must_contain": [],
        # If the agent fabricates a show by inventing one of these names,
        # we catch it deterministically. These are real shows the agent
        # might fuzzy-grab if it doesn't trust the tool's 'none' result.
        "must_not_contain": ["Patagonia", "Sakura", "Capitol", "Reykjavik", "Bavarian"],
        "mutation_allowed": False,
        "notes": "Non-existent Jira key. get_show should return {'none': ...} and the agent should report that. Risk: agent hallucinates a plausible show or grabs a fuzzy-near match from the 20 real ones.",
    },
    {
        "id": "17_specific_field_lookup_lisbon_producer",
        "input": "Who's the on-site producer for the Lisbon show?",
        "expected_kind": "lookup",
        "expected_tool": "get_show",
        "expected_keys": [],
        # Ground truth from KAN-13's Event Details.ADHOC On-Site Producer.
        "must_contain": ["Carlos Mendez"],
        "must_not_contain": [],
        "mutation_allowed": False,
        "notes": "Single-field deep lookup. The agent must call get_show and quote the producer name verbatim. Risk: agent invents a plausible-sounding name from training data without calling the tool. evidence_grounded judge backs up the must_contain check.",
    },
    {
        "id": "18_highest_budget_in_sales",
        "input": "Which Sales show has the highest budget?",
        "expected_kind": "lookup",
        "expected_tool": "list_shows_by_field",
        "expected_keys": [],
        # Ground truth: Lake Lucerne Tourism Board (KAN-6) at $240,000 USD.
        "must_contain": ["Lucerne", "240"],
        # If the agent fails to filter to Sales and returns the global max
        # (Sakura Tech Expo at $460K, Complete), these substrings catch it.
        "must_not_contain": ["Sakura", "460,000"],
        "mutation_allowed": False,
        "notes": "Cross-status filter + max-selection. Tests list_shows_by_field with status='Sales' and the agent's ability to compare budget strings ('$240,000 USD' vs '100,000' — inconsistent formatting in real data is itself a stress test).",
    },
]


def load_df():
    """Return the dataset as a pandas DataFrame, with list columns
    serialized to JSON strings for round-trippable storage in Arize."""
    df = pd.DataFrame(EXAMPLES)
    for col in ("expected_keys", "must_contain", "must_not_contain"):
        df[col] = df[col].apply(json.dumps)
    return df


def upload(client=None):
    """Upload the dataset to Arize and return its dataset_id.

    Requires ARIZE_SPACE_ID and ARIZE_API_KEY in the environment. If a dataset
    with this name already exists, Arize will return the existing id.
    """
    space_id = os.environ["ARIZE_SPACE_ID"]
    api_key = os.environ["ARIZE_API_KEY"]
    client = client or ArizeDatasetsClient(api_key=api_key)
    df = load_df()
    dataset_id = client.create_dataset(
        space_id=space_id,
        dataset_name=DATASET_NAME,
        dataset_type=GENERATIVE,
        data=df,
    )
    return dataset_id


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)  # .env wins over any pre-exported shell variables
    print(f"Dataset has {len(EXAMPLES)} examples.")
    print(f"Uploading as '{DATASET_NAME}'…")
    did = upload()
    print(f"  dataset_id: {did}")
