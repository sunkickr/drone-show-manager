"""Thin Jira REST wrapper. Loads credentials from env at import time."""

import os

from atlassian import Jira


def _client():
    url = os.environ["JIRA_URL"]
    email = os.environ["JIRA_USER_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    return Jira(url=url, username=email, password=token, cloud=True)


def project_key():
    return os.environ.get("JIRA_PROJECT_KEY", "KAN")


def search(jql, fields="summary,status,description"):
    """Run a JQL search restricted to the configured project.
    `jql` may include filters and an ORDER BY clause; the project filter is
    prepended with AND."""
    full_jql = f"project = {project_key()} AND {jql}" if jql else f"project = {project_key()}"
    response = _client().jql(full_jql, fields=fields, limit=100)
    return response.get("issues", [])


def search_all(fields="summary,status,description"):
    """Return every ticket in the project."""
    response = _client().jql(
        f"project = {project_key()} ORDER BY created DESC",
        fields=fields,
        limit=100,
    )
    return response.get("issues", [])


def get_issue(key):
    """Fetch one issue by key. Returns the raw Jira issue dict."""
    return _client().get_issue(key)


def create_issue(summary, description):
    """Create a Task in the configured project. Returns the new issue dict
    (includes the assigned key)."""
    fields = {
        "project": {"key": project_key()},
        "summary": summary,
        "description": description,
        "issuetype": {"name": "Task"},
    }
    return _client().create_issue(fields=fields)


def update_description(key, description):
    _client().update_issue_field(key, fields={"description": description})


def delete_issue(key):
    _client().delete_issue(key)


def snapshot_board():
    """Capture the current state of every ticket in the project.

    Returns: {key: {status, description}} for every existing ticket.
    """
    snap = {}
    for issue in search_all(fields="summary,status,description"):
        f = issue_fields(issue)
        snap[f["key"]] = {
            "status": f["status"],
            "description": f["description"],
        }
    return snap


def restore_board(snapshot):
    """Restore the board to the captured snapshot.

    - Deletes any ticket that did not exist in the snapshot
    - Reverts status (transitions) for tickets whose status changed
    - Restores the original description for tickets whose description changed

    Returns a list of action strings describing what was done (for logging).
    """
    actions = []
    current = {}
    for issue in search_all(fields="summary,status,description"):
        f = issue_fields(issue)
        current[f["key"]] = {
            "status": f["status"],
            "description": f["description"],
        }

    new_keys = sorted(set(current) - set(snapshot))
    for key in new_keys:
        try:
            delete_issue(key)
            actions.append(f"deleted {key} (created during tests)")
        except Exception as e:
            actions.append(f"FAILED to delete {key}: {e}")

    for key in sorted(set(snapshot) & set(current)):
        orig = snapshot[key]
        now = current[key]
        if now["status"] != orig["status"]:
            try:
                transition(key, orig["status"])
                actions.append(f"reverted {key}: {now['status']} -> {orig['status']}")
            except Exception as e:
                actions.append(f"FAILED to revert {key} status: {e}")
        if now["description"] != orig["description"]:
            try:
                update_description(key, orig["description"])
                actions.append(f"restored {key} description")
            except Exception as e:
                actions.append(f"FAILED to restore {key} description: {e}")

    return actions


def transition(key, target_status):
    """Move an issue to `target_status` by looking up the matching transition ID.

    atlassian-python-api's `get_issue_transitions` returns dicts shaped like
    {'name': 'Done', 'id': 51, 'to': 'Complete'} — the 'to' key holds the
    destination status name. Matches against both 'to' (preferred) and 'name'
    so we work either way.
    """
    target = target_status.lower()
    c = _client()
    transitions = c.get_issue_transitions(key)
    for t in transitions:
        to_status = str(t.get("to") or "").lower()
        name = str(t.get("name") or "").lower()
        if to_status == target or name == target:
            c.set_issue_status_by_transition_id(key, t["id"])
            return
    available = [(t.get("name"), t.get("to")) for t in transitions]
    raise ValueError(
        f"No Jira transition available from current status to '{target_status}'. "
        f"Available (name, to): {available}"
    )


def issue_fields(issue):
    """Extract the fields we care about from a raw Jira issue dict."""
    f = issue.get("fields", {})
    status = f.get("status", {}).get("name") if isinstance(f.get("status"), dict) else f.get("status")
    return {
        "key": issue.get("key"),
        "summary": f.get("summary"),
        "status": status,
        "description": f.get("description") or "",
    }
