"""FastAPI web frontend for the ADHOC Drone Show Manager.

Serves a chat UI from /, with /api/session + /api/chat routes that drive the
agent. Each session wraps one AgentSession; one Arize trace covers each user
workflow (which can span multiple turns), not each HTTP request.

Run locally:  uvicorn backend.web:app --reload
Run on Replit: see .replit
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(override=True)

from agent.session import AgentSession  # noqa: E402  (after load_dotenv)
from backend.tracing import init_tracing  # noqa: E402


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# The opening greeting and four example chips. Hardcoded to match the system
# prompt's bullets — parsing the agent's prose would be fragile, and rendering
# a constant lets the UI show the greeting instantly without an agent round-trip
# on page load. If the system prompt's greeting changes, update this to match.
GREETING = "I'm the ADHOC Drone Show Manager. What would you like to do?"

EXAMPLES = [
    "Which shows are in Contract?",
    "Tell me about the Toronto show",
    "Create a new show",
    "Move the Bariloche show to Show Design",
]

sessions: dict[str, AgentSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_tracing()
    yield
    for session in sessions.values():
        session.close()
    sessions.clear()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request, call_next):
    # The frontend is iterated frequently; without this, browsers serve a
    # stale cached index.html / app.js / style.css and UI changes silently
    # don't appear. no-cache forces a revalidation on every load.
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/session")
def create_session():
    # Mint a session and return the greeting from a constant — no agent call,
    # so the UI paints the greeting + example chips immediately. The agent runs
    # for the first time on the user's first real message (one trace per
    # workflow, starting at that message rather than a throwaway "Hello").
    session_id = uuid.uuid4().hex
    sessions[session_id] = AgentSession(workflow_prefix="frontend:")
    return {
        "session_id": session_id,
        "text": GREETING,
        "examples": EXAMPLES,
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    session = sessions.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    turn = session.send(req.message)
    return {
        "text": turn["text"],
        "cards": extract_cards(turn["tool_calls"]),
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def extract_cards(tool_calls: list[dict]) -> list[dict]:
    """Shape tool results into renderable cards for the UI.

    Card kinds:
      - "small": {key, summary, status, highlight?}  — list rows and post-mutation confirmations
      - "big":   the full `show` payload from get_show (sections, next_status, missing_for_next_status)
    """
    cards: list[dict] = []
    for call in tool_calls:
        name = call.get("name")
        out = call.get("output")
        if not isinstance(out, dict):
            continue
        if name in ("list_shows", "list_shows_by_field"):
            for show in out.get("shows", []):
                cards.append({"kind": "small", "show": show})
        elif name == "get_show" and out.get("status") == "found":
            cards.append({"kind": "big", "show": out["show"]})
        elif name == "create_show" and "created" in out:
            cards.append({"kind": "small", "show": out["created"], "highlight": "created"})
        elif name == "transition_show" and "transitioned" in out:
            t = out["transitioned"]
            cards.append({
                "kind": "small",
                "show": {"key": t.get("key", ""), "summary": "", "status": t.get("to", "")},
                "highlight": "transitioned",
            })
    return cards
