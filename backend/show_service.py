"""Show read / update / transition operations shared by the agent tools and the
web form.

All description I/O goes through show_format; all validation through show_schema
(per CLAUDE.md — never parse/construct description text inline, never let the
caller decide whether a transition is allowed).

The agent tools (tools/jira_tools.py) reuse `summarize` / `parsed_show` for their
return shapes. The web form (backend/web.py) additionally calls
`update_show_fields`, `transition_show_status`, and `form_sections` — field
editing is a UI capability that the agent deliberately does not have.
"""

from backend import jira_client
from backend.show_format import parse_description, render_description
from backend.show_schema import (
    SECTION_FIELDS,
    STATUSES,
    all_required_sections_through,
    is_adjacent_transition,
    is_blank,
    missing_fields_for,
    next_status,
)


def summarize(issue):
    """Return {key, summary, status} from a raw Jira issue dict."""
    f = jira_client.issue_fields(issue)
    return {"key": f["key"], "summary": f["summary"], "status": f["status"]}


def parsed_show(issue):
    """Return {key, summary, status, sections, next_status, missing_for_next_status}."""
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


def fetch_show(key):
    """Return parsed_show for a key, or None if the issue doesn't exist."""
    try:
        issue = jira_client.get_issue(key)
    except Exception:
        return None
    return parsed_show(issue)


def form_sections(show):
    """Build the web form model for a parsed show.

    Returns a list of sections, each with every field (from the schema), its
    current value, and whether that field is blank-and-required to reach the
    next status. A section is included if it is already populated OR required
    to advance — so the form stays focused on what matters now instead of
    dumping all nine sections.
    """
    sections = show.get("sections", {})
    nxt = show.get("next_status")
    required = set(all_required_sections_through(nxt)) if nxt else set()
    missing_set = {
        (m["section"], m["field"]) for m in show.get("missing_for_next_status", [])
    }

    model = []
    for section in SECTION_FIELDS:  # schema order
        if section not in sections and section not in required:
            continue
        data = sections.get(section, {})
        fields = []
        has_missing = False
        for name in SECTION_FIELDS[section]:
            value = data.get(name, "")
            miss = (section, name) in missing_set
            if miss:
                has_missing = True
            fields.append({
                "name": name,
                "value": "" if is_blank(value) else value,
                "missing": miss,
            })
        model.append({
            "section": section,
            "required_for_next": section in required,
            "has_missing": has_missing,
            "fields": fields,
        })
    return model


def update_show_fields(key, fields):
    """Merge user-supplied field values into a show's description and persist.

    `fields` is {section: {field: value}}. Validates section/field names against
    the schema; rejects unknown ones. Blank values are skipped (the convention
    is `N/A` to populate, blank means 'leave unset'). Does NOT transition — the
    form must save edits before a separate transition call.

    Returns {"updated": parsed_show} or {"error": ..., "valid_fields"?: [...]}.
    Raises if the issue doesn't exist (caller maps to 404).
    """
    issue = jira_client.get_issue(key)
    f = jira_client.issue_fields(issue)
    parsed = parse_description(f["description"])

    for section, fld_map in (fields or {}).items():
        if section not in SECTION_FIELDS:
            return {"error": f"Unknown section '{section}'."}
        for field_name, value in fld_map.items():
            if field_name not in SECTION_FIELDS[section]:
                return {
                    "error": f"Unknown field '{field_name}' in section '{section}'.",
                    "valid_fields": SECTION_FIELDS[section],
                }
            if is_blank(value):
                continue
            parsed.setdefault(section, {})[field_name] = value

    jira_client.update_description(key, render_description(parsed))
    return {"updated": parsed_show(jira_client.get_issue(key))}


def transition_show_status(key, target_status):
    """Advance a show exactly one step forward. Takes NO field values — fields
    must be saved via update_show_fields first; this enforces that by refusing
    when required fields for the target are still blank.

    Returns {"transitioned": {key, from, to}, "show": parsed_show} on success,
    or {"error": ..., "missing"?: [...], "next_allowed_status"?: ...}.
    Raises if the issue doesn't exist (caller maps to 404).
    """
    if target_status not in STATUSES:
        return {"error": f"Unknown status '{target_status}'."}

    issue = jira_client.get_issue(key)
    f = jira_client.issue_fields(issue)
    current = f["status"]

    if not is_adjacent_transition(current, target_status):
        return {
            "error": (
                f"Cannot move {key} from '{current}' to '{target_status}'. "
                f"Only the next step is allowed."
            ),
            "next_allowed_status": next_status(current),
        }

    parsed = parse_description(f["description"])
    missing = missing_fields_for(parsed, target_status)
    if missing:
        return {
            "error": (
                f"Cannot transition {key} to '{target_status}' — required fields are blank."
            ),
            "missing": [{"section": s, "field": fld} for s, fld in missing],
        }

    jira_client.transition(key, target_status)
    return {
        "transitioned": {"key": key, "from": current, "to": target_status},
        "show": parsed_show(jira_client.get_issue(key)),
    }
