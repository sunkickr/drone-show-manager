"""Tests for show_format parser/writer against real KAN-19 and KAN-14 description text."""

from pathlib import Path

from backend.show_format import parse_description, render_description
from backend.show_schema import (
    SECTION_FIELDS,
    is_blank,
    missing_fields_for,
    next_status,
    is_adjacent_transition,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIXTURES / name).read_text()


def test_parse_kan19_single_line_format():
    """KAN-19 uses 'Field: value' on a single line throughout."""
    parsed = parse_description(_load("kan19_single_line.txt"))

    assert parsed["Contact Info"]["Full Name"] == "Hiroshi Yamamoto"
    assert parsed["Contact Info"]["Company"] == "Sakura Tech Expo"
    assert parsed["Contact Info"]["Email"] == "h.yamamoto@sakuratech.jp"

    assert parsed["Lead Info"]["ADHOC Sales Contact"] == "Marcus Chen"
    assert parsed["Lead Info"]["Estimated Budget"] == "460,000 USD"
    assert "docs.google.com" in parsed["Lead Info"]["Active Project"]

    assert "Skyburst" in parsed["Show Design Info"]["Audio Plan"] or \
           "Sakamoto" in parsed["Show Design Info"]["Audio Plan"]

    # Drone Show Debrief is free-form prose under a single 'Debrief' field
    assert "Debrief" in parsed["Drone Show Debrief"]
    assert "35,000" in parsed["Drone Show Debrief"]["Debrief"]


def test_parse_kan14_wiki_markup_format():
    """The real Jira REST API returns descriptions in Jira wiki markup:
    'h2. Section' headers and '* *Field*: value' bulleted fields.
    Parser must normalize wiki markup before parsing."""
    parsed = parse_description(_load("kan14_wiki_markup.txt"))

    assert parsed["Contact Info"]["Full Name"] == "Diego Fernández"
    assert parsed["Contact Info"]["Company"] == "Patagonia Adventure Co"
    assert parsed["Lead Info"]["ADHOC Sales Contact"] == "David Rodriguez"
    assert parsed["Lead Info"]["Estimated Budget"] == "$165,000 USD"
    assert parsed["Show Design Info"]["Assigned Design Lead"] == "Ben Mwangi"
    assert parsed["Event Details"]["On Site Date(s)"].startswith("2026-05-15")


def test_parse_kan14_split_line_format():
    """KAN-14 has the field name on one line and ': value' on the next."""
    parsed = parse_description(_load("kan14_split_line.txt"))

    assert parsed["Contact Info"]["Full Name"] == "Diego Fernández"
    assert parsed["Contact Info"]["Company"] == "Patagonia Adventure Co"
    assert parsed["Contact Info"]["Email"] == "diego@patagoniaadventure.ar"

    assert parsed["Lead Info"]["ADHOC Sales Contact"] == "David Rodriguez"
    assert parsed["Lead Info"]["Estimated Budget"] == "$165,000 USD"

    assert parsed["Show Design Info"]["Assigned Design Lead"] == "Ben Mwangi"
    assert parsed["Show Design Info"]["Drone Count"] == "200"

    # Event Details should have on-site dates that include 2026-05-15 (2 days from today)
    assert "2026-05-15" in parsed["Event Details"]["On Site Date(s)"]


def test_round_trip_preserves_data():
    """parse(render(parsed)) should equal parsed."""
    original = parse_description(_load("kan19_single_line.txt"))
    rendered = render_description(original)
    reparsed = parse_description(rendered)
    assert reparsed == original


def test_round_trip_split_line_normalizes_to_single_line():
    """Parsing split-line then rendering produces single-line canonical form.
    The reparsed result should equal the original parsed dict."""
    original = parse_description(_load("kan14_split_line.txt"))
    rendered = render_description(original)
    # Canonical form: no split-line colons, just 'Field: value'
    assert "\n: " not in rendered
    reparsed = parse_description(rendered)
    assert reparsed == original


def test_missing_fields_for_sales_show():
    """A show currently in Sales should only need Contact + Lead Info populated."""
    parsed = parse_description(_load("kan19_single_line.txt"))
    # Strip everything past Sales to simulate a Sales-only show
    sales_only = {
        "Contact Info": parsed["Contact Info"],
        "Lead Info": parsed["Lead Info"],
    }
    assert missing_fields_for(sales_only, "Sales") == []
    # To advance to Contract it needs Contract Info — every field missing
    contract_missing = missing_fields_for(sales_only, "Contract")
    contract_fields = SECTION_FIELDS["Contract Info"]
    assert len(contract_missing) == len(contract_fields)
    assert all(section == "Contract Info" for section, _ in contract_missing)


def test_missing_fields_skip_to_complete_lists_everything():
    """A Sales show asked to jump to Complete must report every gap."""
    parsed = parse_description(_load("kan19_single_line.txt"))
    sales_only = {
        "Contact Info": parsed["Contact Info"],
        "Lead Info": parsed["Lead Info"],
    }
    missing = missing_fields_for(sales_only, "Complete")
    missing_sections = {s for s, _ in missing}
    assert "Contract Info" in missing_sections
    assert "Show Design Info" in missing_sections
    assert "Event Details" in missing_sections
    assert "Drone Show Debrief" in missing_sections


def test_is_blank_accepts_n_a_as_populated():
    """N/A is a valid populated value per PRD; blank/whitespace is not."""
    assert is_blank("") is True
    assert is_blank("   ") is True
    assert is_blank(None) is True
    assert is_blank("N/A") is False
    assert is_blank("n/a") is False
    assert is_blank("Some Value") is False


def test_status_transitions_only_adjacent_forward():
    assert next_status("Sales") == "Contract"
    assert next_status("Contract") == "Show Design"
    assert next_status("Show Operations") == "Complete"
    assert next_status("Complete") is None

    assert is_adjacent_transition("Sales", "Contract") is True
    assert is_adjacent_transition("Sales", "Complete") is False
    assert is_adjacent_transition("Contract", "Sales") is False  # no backwards


def test_render_handles_n_a_values():
    """N/A values must round-trip through render/parse."""
    parsed = {
        "Contact Info": {f: "N/A" for f in SECTION_FIELDS["Contact Info"]},
    }
    rendered = render_description(parsed)
    reparsed = parse_description(rendered)
    assert reparsed == parsed


def test_render_skips_empty_sections():
    """Sections with no populated fields should not appear in output."""
    parsed = {"Contact Info": {"Full Name": "Test"}}
    rendered = render_description(parsed)
    assert "Contact Info" in rendered
    assert "Lead Info" not in rendered
