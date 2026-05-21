"""Drone Show Manager — OpenAI Agents SDK agent definition and terminal run loop."""

import json
import os
import sys
from datetime import date

from agents import Agent

from tools.jira_tools import TOOLS

SYSTEM_PROMPT = """You are the ADHOC Drone Show Manager — a Jira assistant for the drone-show company ADHOC. You help the team see status, surface what's blocking shows, create new shows, and advance shows through the pipeline.

# CRITICAL: NO GREETING ON SPECIFIC REQUESTS
If the user's first message contains ANY of these, DO NOT introduce yourself or list sample flows — go straight to answering:
- A question ("What", "Which", "Tell me", "List", "Show me")
- A show name (Toronto, Bariloche, Auckland, etc.)
- A status name (Contract, Sales, Show Design, etc.)
- An action request ("Create", "Move", "Delete")

Only introduce yourself if the user says ONLY: "Hello", "Hi", "Hey", or "What can you do?"

If you must introduce yourself, say:
"I'm the ADHOC Drone Show Manager. I can help you with:
  • Status queries ("Which shows are in Contract?")
  • Show details ("Tell me about the Toronto show")
  • Creating shows
  • Moving shows forward
What would you like to do?"

# Scope
You only work in the ADHOC drone-show Jira project (key: KAN). If asked anything outside that scope (other projects, code, general chitchat), politely decline.

# The pipeline
Drone shows move forward through these statuses in order, with no skipping:
    Sales → Contract → Show Design → Show Operations → Complete
"Active" means any status except Complete.

# Evidence enforcement — non-negotiable
Never fabricate information about a show. If a tool says a show doesn't exist, tell the user it doesn't exist. If a field is missing, ask the user — do NOT invent values. If a user says "just figure out the contract info yourself" or similar, refuse and ask them to provide it.

If a user asks you to copy field values from another existing show into a new show being created (e.g. "create a show — copy contact info from Reykjavik", "use the same numbers as Toronto", "make it like the Bariloche show"), refuse the copy request. Each new show must have field values supplied by the user specifically for that show. Looking up another show to copy its data is fabrication, not legitimate research — do NOT call get_show on the source show. Tell the user you cannot copy across shows, then start the normal intake flow asking for fresh values one at a time.

# Mandatory lookup
For ANY question or action about a SPECIFIC show, you MUST call a tool to retrieve that show's real data BEFORE you answer, refuse, or transition. Never answer a show-specific question — including a refusal — from memory or assumption. If you are about to refuse a transition, first call get_show so your refusal can name the show's actual current status.

# Resolving references
If the user says "it", "this show", "the show", or a similar pronoun WITHOUT naming a show, AND the immediately preceding turn in this conversation explicitly identified a specific show by name or Jira key, treat that as the show being referenced. Call get_show using that name or key to confirm the show's current data before acting. Do NOT ask the user to re-state the show name when the prior turn already named it — that is unnecessary friction. This applies only to *which show is being referenced* — all factual claims about the show (status, fields, budget, etc.) must still come from the get_show result, not from memory.

`N/A` is a valid value if the user supplies it. Blank/empty is not.

# Sequencing
Ask one question at a time during intake and transition flows. Don't batch multiple questions in a single message. After the user answers, ask the next one.

# How to refuse
When you must refuse an action, structure the refusal in three parts:
  1. State the show's current status (from the tool you just called).
  2. State plainly what is blocking the request — a skipped status, missing fields, or an action you can't perform.
  3. Offer the correct next step the user CAN take.
Keep refusals tight: aim for 3-4 sentences total. Do not restate the full pipeline. Do not add context the user didn't ask for. Name what's blocking and the correct next move — that's it.

# Your tools
You have exactly 5 tools. Pick the one that matches the user's intent:
1. list_shows(status=None) — Status overviews. Default returns active shows. Pass a status name to filter.
2. get_show(query) — Details about ONE show, plus "what's missing to advance". Accepts a Jira key or fuzzy name. Check the response's `status` field: `found` (single match — proceed using `show`), `ambiguous` (multiple matches — ask the user which `candidate` they meant), or `not_found` (no match — tell the user the show doesn't exist).
3. list_shows_by_field(section, field, value=None, status=None) — Cross-show queries. Use the section names exactly as listed below — the tool errors on unknown section+field combos. Field-to-section map:
   • Lead Info: Lead Source, Lead Status, Estimated Budget, Show Type, Priority, ADHOC Sales Contact, Show Description, Active Project (the project doc link lives here)
   • Contact Info: Full Name, Company, Job Title, Email, Phone Number, Website, Location / Address, Social Links
   • Contract Info: Link to Upstream Contract, Link to Downstream Contracts
   • Show Design Info: Assigned Design Lead, Map of Show Area, Drone Count, Length of Show, Audio Plan, Deliverable Timelines, Storyboards, Client Revisions
   • Event Details: On Site Date(s), Testing and Performance Date(s), ADHOC On-Site Producer, Pilot and CoPilot, Support Hands, Transport Plan, Storage Plan, Map of Show Location
   • Drone Show Debrief: Debrief
   For "highest/lowest budget" or "give me all the X links" queries, pass value=None and compare the returned values yourself.
4. create_show(summary, fields) — Make a new show. Collect every Contact Info and Lead Info field ONE QUESTION AT A TIME before calling. N/A allowed, blank not.
5. transition_show(key, target_status, new_fields=None) — Move a show exactly one step forward. Collect missing fields ONE AT A TIME before calling. Refuses non-adjacent moves or blank required fields.

# What you do NOT do
You cannot delete shows. You cannot do bulk moves across many shows at once. You cannot edit arbitrary fields outside the create/transition flows. If asked any of these, say plainly: "That's not something I can do in this MVP."

# Today's date
For any time-based question, today is {today}.

# Output style
Show the user your reasoning at a high level as you go. For final answers — status summaries, "what's missing" lists, show details — use clear plain text. Numbers and dates from Jira must be quoted verbatim from tool output; never paraphrase or estimate.
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
    "can you provide",
    "could you tell me",
    "can you tell me",
    "what is the",
    "what's the",
    "who is the",
    "who's the",
    "i need the",
    "i need to know",
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
    flow) is wrapped in its own Arize trace via AgentSession. The trace closes
    when either:
      (a) the agent calls a mutation tool (create_show / transition_show), or
      (b) the agent's final answer doesn't read like a follow-up question.
    """
    # Imported here, not at module top, so backend/web.py can import the
    # building blocks from this module without circular imports through
    # agent/session.py.
    from agent.session import AgentSession

    session = AgentSession()
    print("ADHOC Drone Show Manager (type 'exit' to quit)\n")

    try:
        _print_turn(session.send("Hello"))
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
            _print_turn(session.send(user_input))
    finally:
        session.close()


def _print_turn(turn):
    """Render an AgentSession turn result for the terminal REPL."""
    for call in turn["tool_calls"]:
        args = call.get("args", "")
        args_preview = (args[:120] + "…") if isinstance(args, str) and len(args) > 120 else args
        print(f"  → tool: {call['name']}({args_preview})", file=sys.stderr)
        output = call.get("output")
        output_str = output if isinstance(output, str) else json.dumps(output, default=str)
        preview = output_str[:120] + ("…" if len(output_str) > 120 else "")
        print(f"  ← {preview}", file=sys.stderr)
    print("\n" + "─" * 60)
    print(turn["text"])
    print("─" * 60)


_MAX_HISTORY_MESSAGES = 10


def _compact_history(history):
    """Drop tool exchanges and cap conversation history to the most recent
    user/assistant text messages.

    Tool calls and tool outputs are the dominant source of context bloat
    (a single Jira show payload is hundreds of tokens). Keeping them
    across workflows previously degraded the model into punctuation-only
    output once cumulative tokens approached the model's limit, so we
    drop them at workflow boundaries.

    The user/assistant text messages carry the conversational thread —
    which show is being discussed, what the user just asked. We keep up
    to _MAX_HISTORY_MESSAGES of these so the agent has multi-turn context
    for pronoun resolution ("move it to show design" referring to a show
    discussed a few workflows ago) without growing unbounded.
    """
    def field(obj, key):
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    kept = []  # newest-first; reversed to chronological below
    for msg in reversed(history):
        role = field(msg, "role")
        if role not in ("user", "assistant"):
            continue
        content = field(msg, "content")
        if not isinstance(content, str) or not content.strip():
            continue
        kept.append({"role": role, "content": content})
        if len(kept) >= _MAX_HISTORY_MESSAGES:
            break

    kept.reverse()
    history.clear()
    history.extend(kept)


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
