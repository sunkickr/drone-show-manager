"""Drone show schema — single source of truth for sections, fields, and status gating."""

STATUSES = ["Sales", "Contract", "Show Design", "Show Operations", "Complete"]

REQUIRED_SECTIONS = {
    "Sales": ["Contact Info", "Lead Info"],
    "Contract": ["Contract Info"],
    "Show Design": ["Show Design Info"],
    "Show Operations": [
        "Event Details",
        "Permit Requirements and Timeline",
        "Gear List",
        "Media Capture Plan",
    ],
    "Complete": ["Drone Show Debrief"],
}

SECTION_FIELDS = {
    "Contact Info": [
        "Full Name",
        "Company",
        "Job Title",
        "Email",
        "Phone Number",
        "Website",
        "Location / Address",
        "Social Links",
    ],
    "Lead Info": [
        "Lead Source",
        "Lead Status",
        "Estimated Budget",
        "Show Type",
        "Priority",
        "ADHOC Sales Contact",
        "Show Description",
        "Active Project",
    ],
    "Contract Info": [
        "Link to Upstream Contract",
        "Link to Downstream Contracts",
    ],
    "Show Design Info": [
        "Assigned Design Lead",
        "Map of Show Area",
        "Drone Count",
        "Length of Show",
        "Audio Plan",
        "Deliverable Timelines",
        "Storyboards",
        "Client Revisions",
    ],
    "Event Details": [
        "On Site Date(s)",
        "Testing and Performance Date(s)",
        "ADHOC On-Site Producer",
        "Pilot and CoPilot",
        "Support Hands",
        "Company Providing Drones/Batteries/Support Gear",
        "Transport Plan",
        "Storage Plan",
        "Map of Show Location",
    ],
    "Permit Requirements and Timeline": [
        "Aviation Liability Insurance",
        "FAA",
        "Pyrodrone Fire Marshal Permit",
        "Local Fire Department Notice",
        "Local Police Notice",
        "Venue Requirements",
        "City Requirements",
    ],
    "Gear List": [
        "Drones",
        "Batteries",
        "Battery Chargers",
        "Power Source for Battery Chargers",
        "Base Station",
    ],
    "Media Capture Plan": [
        "ADHOC Responsible Party",
        "Content Handoff Plan",
    ],
    "Drone Show Debrief": [
        "Debrief",
    ],
}


def is_blank(value):
    """A field is blank if it's None, missing, or an empty/whitespace string.
    `N/A` (any case) is a valid populated value per PRD."""
    if value is None:
        return True
    return str(value).strip() == ""


def all_required_sections_through(status):
    """All sections that must be populated for a show currently AT `status`
    (accumulates everything from Sales up through `status`)."""
    sections = []
    for s in STATUSES:
        sections.extend(REQUIRED_SECTIONS.get(s, []))
        if s == status:
            break
    return sections


def missing_fields_for(parsed_sections, target_status):
    """Return list of (section, field) tuples that are blank or missing
    for a show to ENTER `target_status` (i.e. requires sections for every
    status from Sales through `target_status`)."""
    missing = []
    for section in all_required_sections_through(target_status):
        section_data = parsed_sections.get(section, {})
        for field in SECTION_FIELDS.get(section, []):
            if is_blank(section_data.get(field)):
                missing.append((section, field))
    return missing


def next_status(current_status):
    """Return the next status in the pipeline, or None if at Complete."""
    if current_status not in STATUSES:
        return None
    idx = STATUSES.index(current_status)
    if idx + 1 >= len(STATUSES):
        return None
    return STATUSES[idx + 1]


def is_adjacent_transition(current_status, target_status):
    """Only allow forward, single-step transitions."""
    return next_status(current_status) == target_status
