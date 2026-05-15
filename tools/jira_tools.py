"""The 5 tools the drone show agent uses.

Each tool is a pure function decorated with @function_tool. The decorator turns
the type hints and docstring into the JSON schema the model sees.
"""

import re
from typing import Optional

from agents import function_tool

from backend import jira_client
from backend.show_format import parse_description, render_description
from backend.show_schema import (
    STATUSES,
    SECTION_FIELDS,
    REQUIRED_SECTIONS,
    is_blank,
    missing_fields_for,
    next_status,
    is_adjacent_transition,
)

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def _summarize(issue):
    """Return {key, summary, status} from a raw Jira issue dict."""
    f = jira_client.issue_fields(issue)
    return {"key": f["key"], "summary": f["summary"], "status": f["status"]}


def _parsed_show(issue):
    """Return {key, summary, status, sections, missing_for_next}."""
    f = jira_client.issue_fields(issue)
    sections = parse_description(f["description"])
    nxt = next_status(f["status"])
    missing = missing_fields_for(sections, nxt) if nxt else []
    return {
        "key": f["key"],
        "summary": f["summary"],
        "status": f["status"],
        "sections": sections,
        "next_status": nxt,
        "missing_for_next_status": [
            {"section": s, "field": fld} for s, fld in missing
        ],
    }


# ---------------------------------------------------------------------------
# Tool 1: list_shows
# ---------------------------------------------------------------------------

@function_tool
def list_shows(status: Optional[str] = None) -> dict:
    """List drone shows in the project.

    If `status` is omitted, returns every ACTIVE show (excludes Complete).
    If `status` is given, returns only shows in that status. Valid statuses:
    Sales, Contract, Show Design, Show Operations, Complete.

    Returns: {"shows": [{"key", "summary", "status"}, ...]}
    """
    if status:
        if status not in STATUSES:
            return {"error": f"Unknown status '{status}'. Valid: {STATUSES}"}
        jql = f'status = "{status}" ORDER BY key'
    else:
        jql = 'status != "Complete" ORDER BY status, key'
    issues = jira_client.search(jql, fields="summary,status")
    return {"shows": [_summarize(i) for i in issues]}


# ---------------------------------------------------------------------------
# Tool 2: get_show
# ---------------------------------------------------------------------------

@function_tool
def get_show(query: str) -> dict:
    """Look up a single drone show by Jira key (e.g. 'KAN-9') or fuzzy text
    matched against the ticket summary (e.g. 'Toronto', 'Bariloche').

    Returns one of:
        {"show": {key, summary, status, sections, next_status, missing_for_next_status}}
        {"ambiguous": [{key, summary, status}, ...]}   when 2+ summaries match
        {"none": query}                                when no show matches

    The agent must ask the user to disambiguate when the result is 'ambiguous',
    and must report 'no show found' when the result is 'none'. Never fabricate
    information about a show that doesn't exist.
    """
    q = (query or "").strip()
    if not q:
        return {"none": query}

    if ISSUE_KEY_RE.match(q):
        try:
            issue = jira_client.get_issue(q)
        except Exception:
            return {"none": query}
        return {"show": _parsed_show(issue)}

    # Fuzzy: pull all tickets, substring match on summary (case-insensitive).
    all_issues = jira_client.search_all(fields="summary,status,description")
    needle = q.lower()
    matches = [i for i in all_issues if needle in (i.get("fields", {}).get("summary", "") or "").lower()]

    if not matches:
        return {"none": query}
    if len(matches) > 1:
        return {"ambiguous": [_summarize(i) for i in matches]}
    return {"show": _parsed_show(matches[0])}


# ---------------------------------------------------------------------------
# Tool 3: list_shows_by_field
# ---------------------------------------------------------------------------

@function_tool
def list_shows_by_field(
    section: str,
    field: str,
    value: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    """Find shows by a value inside the description, OR pull a field across shows.

    Parameters:
        section: Section name from the schema (e.g. 'Lead Info', 'Event Details').
        field:   Field name within that section (e.g. 'ADHOC Sales Contact', 'Estimated Budget').
        value:   If given, return only shows where the field substring-matches this
                 (case-insensitive). If omitted, return ALL shows that have the field
                 populated, including the field's current value — useful for
                 'highest budget' or 'all project doc links'.
        status:  If given, filter to shows in this status only.

    Use this for cross-show queries like:
      - "Shows by Marcus Chen"  → section='Lead Info', field='ADHOC Sales Contact', value='Marcus Chen'
      - "Highest budget complete show" → section='Lead Info', field='Estimated Budget', status='Complete' (then pick max)
      - "Project doc links for Show Operations" → section='Lead Info', field='Active Project', status='Show Operations'

    Returns: {"shows": [{"key", "summary", "status", "value"}, ...]}
    """
    if section not in SECTION_FIELDS:
        return {"error": f"Unknown section '{section}'. Valid: {list(SECTION_FIELDS.keys())}"}
    if field not in SECTION_FIELDS[section]:
        return {
            "error": f"Unknown field '{field}' in section '{section}'. "
                     f"Valid fields: {SECTION_FIELDS[section]}"
        }
    if status and status not in STATUSES:
        return {"error": f"Unknown status '{status}'. Valid: {STATUSES}"}

    if status:
        issues = jira_client.search(f'status = "{status}"', fields="summary,status,description")
    else:
        issues = jira_client.search_all(fields="summary,status,description")

    results = []
    needle = value.lower() if value else None
    for issue in issues:
        f = jira_client.issue_fields(issue)
        sections = parse_description(f["description"])
        actual = sections.get(section, {}).get(field)
        if is_blank(actual):
            continue
        if needle and needle not in str(actual).lower():
            continue
        results.append({
            "key": f["key"],
            "summary": f["summary"],
            "status": f["status"],
            "value": actual,
        })
    return {"shows": results}


# ---------------------------------------------------------------------------
# Tool 4: create_show
# ---------------------------------------------------------------------------

@function_tool(strict_mode=False)
def create_show(summary: str, fields: dict) -> dict:
    """Create a new drone show ticket in the project.

    Parameters:
        summary: Ticket title. PRD convention: 'Company/Organization - Location'
                 where Location is 'City, Country' (e.g. 'SkyTech Berlin - Berlin, Germany').
        fields:  A dict of {section_name: {field_name: value}}. Must include
                 every field of Contact Info and Lead Info, populated with a
                 real value or 'N/A'. Blank values are rejected.

    Refuses to create the show if any required field is blank. The agent should
    have collected all fields from the user (one at a time) before calling.

    Returns: {"created": {"key", "summary", "status"}} on success
             {"error": "...", "missing": [...]} on validation failure
    """
    if is_blank(summary):
        return {"error": "Summary is required."}

    parsed = fields or {}
    missing = missing_fields_for(parsed, "Sales")
    if missing:
        return {
            "error": "Cannot create show — required fields are blank.",
            "missing": [{"section": s, "field": fld} for s, fld in missing],
        }

    description = render_description(parsed)
    created = jira_client.create_issue(summary=summary, description=description)
    # atlassian-python-api returns a dict with key + self URL
    new_key = created.get("key")
    if not new_key:
        return {"error": "Jira did not return a key for the created issue.", "raw": created}
    issue = jira_client.get_issue(new_key)
    return {"created": _summarize(issue)}


# ---------------------------------------------------------------------------
# Tool 5: transition_show
# ---------------------------------------------------------------------------

@function_tool(strict_mode=False)
def transition_show(
    key: str,
    target_status: str,
    new_fields: Optional[dict] = None,
) -> dict:
    """Advance a show to the next status in the pipeline, with required-field
    validation.

    Pipeline (forward only, no skipping):
        Sales -> Contract -> Show Design -> Show Operations -> Complete

    Parameters:
        key:           Jira issue key (e.g. 'KAN-9').
        target_status: The status to enter. Must be exactly one step forward
                       from the show's current status.
        new_fields:    {section_name: {field_name: value}} — fields collected
                       from the user for the target status. Merged into the
                       existing description before validation.

    Refuses (with details) if:
      - target_status is not the immediate next step
      - any required field for target_status is blank after merge
      - the Jira transition itself fails

    Returns:
        {"transitioned": {"key", "from", "to"}} on success
        {"error": "...", "missing": [...]} on validation failure
    """
    if target_status not in STATUSES:
        return {"error": f"Unknown status '{target_status}'. Valid: {STATUSES}"}

    issue = jira_client.get_issue(key)
    f = jira_client.issue_fields(issue)
    current = f["status"]

    if not is_adjacent_transition(current, target_status):
        nxt = next_status(current)
        return {
            "error": (
                f"Cannot move {key} from '{current}' to '{target_status}'. "
                f"Only the next step is allowed."
            ),
            "current_status": current,
            "next_allowed_status": nxt,
        }

    parsed = parse_description(f["description"])
    if new_fields:
        for section, fld_map in new_fields.items():
            if section not in SECTION_FIELDS:
                return {"error": f"Unknown section '{section}' in new_fields."}
            for field_name, value in fld_map.items():
                if field_name not in SECTION_FIELDS[section]:
                    return {
                        "error": f"Unknown field '{field_name}' in section '{section}'.",
                        "valid_fields": SECTION_FIELDS[section],
                    }
                parsed.setdefault(section, {})[field_name] = value

    missing = missing_fields_for(parsed, target_status)
    if missing:
        return {
            "error": (
                f"Cannot transition {key} to '{target_status}' — required fields are blank."
            ),
            "missing": [{"section": s, "field": fld} for s, fld in missing],
        }

    jira_client.update_description(key, render_description(parsed))
    jira_client.transition(key, target_status)
    return {"transitioned": {"key": key, "from": current, "to": target_status}}


# Exported list for the Agent
TOOLS = [list_shows, get_show, list_shows_by_field, create_show, transition_show]
