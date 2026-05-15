"""Parse and render Jira ticket descriptions for drone shows.

The parser handles three real-world formats:
    Field: value             (single-line plain text)
    Field\n: value           (newline-then-colon — legacy mock data)
    * *Field*: value         (Jira wiki markup — what the REST API actually returns)
With section headers as either plain ('Contact Info') or wiki ('h2. Contact Info').

The writer always emits the plain single-line canonical form.

The 'Drone Show Debrief' section is special: it has free-form prose rather
than Field: value pairs, so the parser captures the whole body as a single
field named 'Debrief'.
"""

import re

from backend.show_schema import SECTION_FIELDS, is_blank

SECTION_NAMES = set(SECTION_FIELDS.keys())

_WIKI_HEADER_RE = re.compile(r"^h\d+\.\s*(.+?)\s*$")
_WIKI_BOLD_FIELD_RE = re.compile(r"^\s*\*\s*\*([^*]+?)\*\s*:\s*(.*)$")
_WIKI_PLAIN_BULLET_RE = re.compile(r"^\s*\*\s+([^:*]+?)\s*:\s*(.*)$")


def _normalize_wiki_markup(text):
    """Convert Jira wiki markup lines to the plain 'Section' / 'Field: value' form."""
    out = []
    for line in text.split("\n"):
        m = _WIKI_HEADER_RE.match(line)
        if m:
            out.append(m.group(1).strip())
            continue
        m = _WIKI_BOLD_FIELD_RE.match(line)
        if m:
            out.append(f"{m.group(1).strip()}: {m.group(2).strip()}")
            continue
        m = _WIKI_PLAIN_BULLET_RE.match(line)
        if m:
            out.append(f"{m.group(1).strip()}: {m.group(2).strip()}")
            continue
        out.append(line)
    return "\n".join(out)


def parse_description(text):
    """Parse a Jira description into {section: {field: value}}."""
    if not text:
        return {}

    text = _normalize_wiki_markup(text)
    lines = text.split("\n")
    result = {}
    current_section = None
    pending_field = None
    freeform_buffer = []

    def flush_freeform():
        nonlocal freeform_buffer
        if current_section and freeform_buffer:
            fields = SECTION_FIELDS.get(current_section, [])
            if fields:
                first = fields[0]
                section_dict = result.setdefault(current_section, {})
                if is_blank(section_dict.get(first)):
                    section_dict[first] = " ".join(freeform_buffer).strip()
        freeform_buffer = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            pending_field = None
            continue

        if stripped in SECTION_NAMES:
            flush_freeform()
            current_section = stripped
            pending_field = None
            result.setdefault(current_section, {})
            continue

        if current_section is None:
            continue

        allowed = SECTION_FIELDS.get(current_section, [])

        if pending_field is not None and stripped.startswith(":"):
            value = stripped[1:].strip()
            result.setdefault(current_section, {})[pending_field] = value
            pending_field = None
            continue

        pending_field = None

        if ":" in line:
            field_part, _, value_part = line.partition(":")
            canonical = _match_field(field_part.strip(), allowed)
            if canonical:
                result.setdefault(current_section, {})[canonical] = value_part.strip()
                continue

        bare = _match_field(stripped, allowed)
        if bare:
            pending_field = bare
            continue

        freeform_buffer.append(stripped)

    flush_freeform()
    return result


def render_description(parsed):
    """Render parsed sections back to canonical description text.

    Sections are emitted in schema order. Fields use single-line 'Field: value'.
    Drone Show Debrief is rendered as free-form prose under the section header.
    Sections with no populated fields are skipped.
    """
    if not parsed:
        return ""

    blocks = []
    for section in SECTION_FIELDS:
        if section not in parsed:
            continue
        section_data = parsed[section]
        populated = [f for f in SECTION_FIELDS[section] if not is_blank(section_data.get(f))]
        if not populated:
            continue

        lines = [section]
        if section == "Drone Show Debrief":
            debrief = section_data.get("Debrief", "")
            if not is_blank(debrief):
                lines.append(debrief.strip())
        else:
            for field in SECTION_FIELDS[section]:
                if field in section_data and not is_blank(section_data[field]):
                    lines.append(f"{field}: {section_data[field]}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _match_field(candidate, allowed_fields):
    """Case-insensitive match of candidate against allowed field list.
    Returns canonical name or None."""
    if not candidate:
        return None
    norm = candidate.strip().lower()
    for f in allowed_fields:
        if f.lower() == norm:
            return f
    return None
