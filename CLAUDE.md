# Conventions

## Show data lives in the description

Every drone show is a Jira ticket in project `KAN`. All show fields live inside the ticket **description**, organized as markdown-style section headers with `Field: value` lines underneath. No custom Jira fields.

Always read/write description content through `backend/show_format.py` — never construct or parse description text inline. The format module is the single point that knows about both colon styles in the existing mock data (`Field: value` and `Field\n: value`) and always emits the canonical single-line form for new content.

## Schema is the source of truth for refusals

`backend/show_schema.py` lists which sections and fields are required to enter each status. **Refusal logic must use this module** — never let the LLM decide whether a show can advance. The flow is:

1. Parse description into `{section: {field: value}}`
2. Diff against `REQUIRED_SECTIONS[target_status]` + `SECTION_FIELDS[section]`
3. If anything's blank, refuse with the missing list

`N/A` is a valid populated value. Blank or absent is not.

## Never fabricate

The agent must not invent field values for shows. If a required field is missing, ask the user. If a user asks the agent to "figure out the contract info yourself," refuse. The system prompt enforces this; tools enforce it harder by failing closed when fields are blank.

## Status pipeline is linear

Sales → Contract → Show Design → Show Operations → Complete. The `transition_show` tool refuses non-adjacent moves. There's no skip.

## Adding tools

Add new tools to `tools/jira_tools.py`. Each tool is a single decorated function that:
- Has typed args (the SDK uses these for the schema the LLM sees)
- Returns plain dicts/lists (json-serializable)
- Handles its own validation; doesn't trust the agent to remember rules

Register new tools in the `tools` list of the Agent in `agent/drone_show_agent.py`.
