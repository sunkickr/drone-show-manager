"""Entry point: `python main.py`"""

from dotenv import load_dotenv

# override=True makes .env the source of truth even when a shell-exported
# variable has the same name. Without this, a stale shell `export` silently
# shadows the .env file and the developer is left wondering why their changes
# don't take effect (a real bug we hit during eval iteration).
load_dotenv(override=True)

from backend.tracing import init_tracing
from agent.drone_show_agent import run


if __name__ == "__main__":
    init_tracing()
    run()
