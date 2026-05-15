"""Drone Show Manager — OpenAI Agents SDK agent definition and terminal run loop."""

import os
import sys
from datetime import date

from agents import Agent, Runner, trace

from tools.jira_tools import TOOLS

SYSTEM_PROMPT = """You are the ADHOC Drone Show Manager — a Jira assistant for the drone-show company ADHOC. You help the team see status, surface what's blocking shows, create new shows, and advance shows through the pipeline.

# Greeting & sample prompts
If — and only if — the user opens the conversation with a generic greeting ("Hello", "Hi", "Hey"), asks what you can do, or otherwise has no specific request, introduce yourself as the ADHOC Drone Show Manager and offer these sample flows:
  • "What active shows do we have?" or "Which shows are in Contract?"
  • "Tell me about the Toronto show" or "What's missing on the Auckland show?"
  • "List all shows by Marcus Chen" or "Which complete show had the highest budget?"
  • "Create a new show"
  • "Move Reykjavik to Show Design"
Then ask which one they'd like to do.

If the user's message is already a specific request (a status question, a show lookup, a create or transition request, a refusal-worthy ask, etc.), DO NOT greet — address their request directly. The greeting only fires when the user has not yet expressed an intent.

# Scope
You only work in the ADHOC drone-show Jira project (key: KAN). If asked anything outside that scope (other projects, code, general chitchat), politely decline.

# The pipeline
Drone shows move forward through these statuses in order, with no skipping:
    Sales → Contract → Show Design → Show Operations → Complete

"Active" means any status except Complete.

# Evidence enforcement — non-negotiable
Never fabricate information about a show. If a tool says a show doesn't exist, tell the user it doesn't exist. If a field is missing, ask the user — do NOT invent values. If a user says "just figure out the contract info yourself" or similar, refuse and ask them to provide it.

`N/A` is a valid value if the user supplies it. Blank/empty is not.

# Sequencing
Ask one question at a time during intake and transition flows. Don't batch multiple questions in a single message. After the user answers, ask the next one.

# Your tools
You have exactly 5 tools. Pick the one that matches the user's intent:

1. list_shows(status=None)
   - Status overviews. Default returns active shows. Pass a status name to filter.

2. get_show(query)
   - Details about ONE show, plus "what's missing to advance to the next status".
   - Accepts a Jira key (e.g. KAN-9) or a fuzzy name (e.g. "Toronto", "Bariloche", "Spain").
   - If the result is `ambiguous`, ask the user which one they meant — DO NOT guess.
   - If the result is `none`, tell the user the show doesn't exist.

3. list_shows_by_field(section, field, value=None, status=None)
   - Cross-show queries:
     • "Shows by Marcus Chen" → section='Lead Info', field='ADHOC Sales Contact', value='Marcus Chen'
     • "Highest-budget complete show" → field='Estimated Budget', status='Complete', value=None, then pick the max from the returned values
     • "Project doc links for Show Operations shows" → field='Active Project', status='Show Operations', value=None

4. create_show(summary, fields)
   - Make a new show. Summary format: 'Company - City, Country'.
   - Required up-front: every field of Contact Info and Lead Info. Collect these from the user ONE QUESTION AT A TIME before calling. `N/A` allowed, blank not.
   - Tool will refuse if any required field is blank.

5. transition_show(key, target_status, new_fields=None)
   - Move a show exactly one step forward.
   - Required fields per status:
     • To enter Contract: Contract Info section (Link to Upstream Contract, Link to Downstream Contracts)
     • To enter Show Design: Show Design Info section (8 fields)
     • To enter Show Operations: Event Details + Permits + Gear List + Media Capture Plan (full set)
     • To enter Complete: Drone Show Debrief (single field 'Debrief')
   - Collect missing fields ONE AT A TIME from the user before calling.
   - Refuses if target is not the immediate next step, or any required field is blank after merge.

# What you do NOT do
You cannot delete shows. You cannot do bulk moves across many shows at once. You cannot edit arbitrary fields outside the create/transition flows. If asked any of these, say plainly: "That's not something I can do in this MVP."

# Today's date
For any time-based question, today is {today}.

# Output style
Show the user your reasoning at a high level as you go (the harness streams your tool calls). For final answers — status summaries, "what's missing" lists, show details — use clear plain text. Numbers and dates from Jira must be quoted verbatim from tool output; never paraphrase or estimate.
"""


def build_agent():
    instructions = SYSTEM_PROMPT.format(today=date.today().isoformat())
    return Agent(
        name="ADHOC Drone Show Manager",
        instructions=instructions,
        model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        tools=TOOLS,
    )


MUTATION_TOOLS = {"create_show", "transition_show"}

# Tight markers indicating the agent is demanding STRUCTURED field input from
# the user — i.e. we're in an intake or clarification turn and the workflow
# should stay open. Deliberately excludes conversational closings like
# "would you like more information" or "anything else" which appear at the
# end of many lookup answers but don't represent a workflow continuing.
_INTAKE_MARKERS = (
    "please provide",
    "could you provide",
    "could you tell me",
    "what is the",
    "what's the",
    "i need the",
    "i need you to provide",
    "tell me the",
    "which one would",
    "which show would",
    "which one are",
    "which show are",
)


def run():
    """Terminal REPL with per-workflow tracing.

    Each user-facing workflow (a single lookup, or a multi-turn create/transition
    flow) is wrapped in its own Arize trace. The trace closes when either:
      (a) the agent successfully calls a mutation tool (create_show / transition_show), or
      (b) the agent gives a final answer that doesn't read like a follow-up question.

    The workflow's name is set dynamically from the first tool the agent calls in
    the workflow (e.g. `create_show`, `get_show`). If no tool is called, the name
    falls back to the user's intent (`chat`).
    """
    agent = build_agent()
    history = []
    workflow_ctx = None
    workflow_name_set = False
    print("ADHOC Drone Show Manager (type 'exit' to quit)\n")

    def open_workflow_if_needed():
        nonlocal workflow_ctx, workflow_name_set
        if workflow_ctx is None:
            workflow_ctx = trace(workflow_name="user request")
            workflow_ctx.__enter__()
            workflow_name_set = False

    def close_workflow():
        nonlocal workflow_ctx, workflow_name_set
        if workflow_ctx is not None:
            workflow_ctx.__exit__(None, None, None)
            workflow_ctx = None
            workflow_name_set = False

    def rename_workflow(name):
        """Set the workflow span's name on the first tool call of this trace."""
        nonlocal workflow_name_set
        if workflow_name_set or workflow_ctx is None:
            return
        # Look up the current span and update its name attribute. Works across
        # OTel SDK versions because span.update_name is the standard API.
        try:
            from opentelemetry import trace as otel_trace
            otel_trace.get_current_span().update_name(name)
        except Exception:
            pass
        workflow_name_set = True

    try:
        # Opening greeting
        open_workflow_if_needed()
        history.append({"role": "user", "content": "Hello"})
        completed, _ = _run_turn(agent, history, rename_workflow)
        if completed:
            close_workflow()

        while True:
            try:
                user_input = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                return

            open_workflow_if_needed()
            history.append({"role": "user", "content": user_input})
            completed, _ = _run_turn(agent, history, rename_workflow)
            if completed:
                close_workflow()
    finally:
        # Make sure any open trace closes cleanly on exit / exception.
        close_workflow()


def _run_turn(agent, history, on_first_tool=None):
    """Run one turn; print tool activity and final answer.

    Returns (workflow_complete, first_tool_name).
    workflow_complete is True when a mutation tool fired OR the agent's final
    answer doesn't look like a follow-up question.
    """
    result = Runner.run_sync(agent, history)

    mutation_fired = False
    first_tool = None
    for item in getattr(result, "new_items", []):
        kind = type(item).__name__
        if kind == "ToolCallItem":
            tool_name = getattr(getattr(item, "raw_item", None), "name", "?")
            args = getattr(getattr(item, "raw_item", None), "arguments", "")
            args_preview = (args[:120] + "…") if isinstance(args, str) and len(args) > 120 else args
            print(f"  → tool: {tool_name}({args_preview})", file=sys.stderr)
            if first_tool is None:
                first_tool = tool_name
                if on_first_tool:
                    on_first_tool(tool_name)
            if tool_name in MUTATION_TOOLS:
                mutation_fired = True
        elif kind == "ToolCallOutputItem":
            output = str(getattr(item, "output", ""))
            preview = output[:120] + ("…" if len(output) > 120 else "")
            print(f"  ← {preview}", file=sys.stderr)

    final = getattr(result, "final_output", None) or ""
    print("\n" + "─" * 60)
    print(final)
    print("─" * 60)

    new_input = result.to_input_list()
    history.clear()
    history.extend(new_input)

    # A workflow is "done" if a mutation succeeded, OR the agent's reply
    # doesn't look like it's still asking the user for something.
    if mutation_fired:
        # Confirm tool didn't return an error; treat any successful call as done
        complete = True
    else:
        complete = not _looks_like_followup(final)
    return complete, first_tool


def _looks_like_followup(text):
    """True if the agent's last paragraph is demanding structured input from
    the user (intake, ambiguity resolution, missing-field question).

    We only inspect the last paragraph because the agent often echoes the
    user's question mid-response. The actual follow-up demand always sits at
    the end of the message.
    """
    if not text:
        return False
    last_paragraph = text.strip().split("\n\n")[-1].strip().lower()
    return any(marker in last_paragraph for marker in _INTAKE_MARKERS)
