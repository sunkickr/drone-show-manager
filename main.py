"""Entry point: `python main.py`"""

from dotenv import load_dotenv

load_dotenv()

from backend.tracing import init_tracing
from agent.drone_show_agent import run


if __name__ == "__main__":
    init_tracing()
    run()
