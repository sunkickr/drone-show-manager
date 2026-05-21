"""Per-workflow agent session with Arize trace lifecycle.

One trace per user workflow (which can span multiple turns), not per turn.
The trace closes when a mutation tool fires OR the agent's reply doesn't read
like a follow-up question. Used by both the terminal REPL and the web frontend.

Surfaces pass a workflow_prefix (e.g. "frontend:") so traces are filterable in
Arize and don't mix with smoke: / dataset_smoke: traces in evaluator analyses.
"""

from __future__ import annotations

import ast
import json

from agents import Runner, trace

from agent.drone_show_agent import (
    MUTATION_TOOLS,
    _compact_history,
    _looks_like_followup,
    build_agent,
)


class AgentSession:
    def __init__(self, *, workflow_prefix: str = ""):
        self.agent = build_agent()
        self.history: list = []
        self._ctx = None
        self._meta: dict | None = None
        self._name_set = False
        self._prefix = workflow_prefix

    def send(self, user_message: str) -> dict:
        """Run one turn. Returns {text, tool_calls, complete}.

        tool_calls is a list of {name, args, output} dicts. output is the
        parsed JSON dict when the tool returns JSON; otherwise the raw string.
        complete is True when the workflow has closed (mutation fired or no
        follow-up question detected).
        """
        self._open_if_needed(user_message)
        self.history.append({"role": "user", "content": user_message})

        result = Runner.run_sync(self.agent, self.history)

        tool_calls, mutation_fired = [], False
        for item in getattr(result, "new_items", []):
            kind = type(item).__name__
            if kind == "ToolCallItem":
                name = getattr(getattr(item, "raw_item", None), "name", "?")
                args = getattr(getattr(item, "raw_item", None), "arguments", "")
                tool_calls.append({"name": name, "args": args, "output": None})
                if not self._name_set:
                    self._rename(name)
                    self._name_set = True
                if name in MUTATION_TOOLS:
                    mutation_fired = True
            elif kind == "ToolCallOutputItem" and tool_calls:
                raw = str(getattr(item, "output", ""))
                tool_calls[-1]["output"] = _parse_tool_output(raw)

        final = getattr(result, "final_output", None) or ""
        self.history.clear()
        self.history.extend(result.to_input_list())

        complete = mutation_fired or not _looks_like_followup(final)
        if complete:
            self._close(final)

        return {"text": final, "tool_calls": tool_calls, "complete": complete}

    def close(self):
        if self._ctx is not None:
            self._close(None)

    def _open_if_needed(self, user_message: str):
        if self._ctx is not None:
            return
        # Build metadata up front so EnrichingTracingProcessor sees `input` on
        # on_trace_start. The same dict is mutated with `output` before close.
        self._meta = {"input": user_message}
        self._ctx = trace(
            workflow_name=f"{self._prefix}user request",
            metadata=self._meta,
        )
        self._ctx.__enter__()
        self._name_set = False

    def _rename(self, tool_name: str):
        try:
            from opentelemetry import trace as otel_trace
            otel_trace.get_current_span().update_name(f"{self._prefix}{tool_name}")
        except Exception:
            pass

    def _close(self, final_output: str | None):
        if self._ctx is None:
            return
        if final_output and self._meta is not None:
            self._meta["output"] = final_output
        self._ctx.__exit__(None, None, None)
        self._ctx = None
        self._meta = None
        self._name_set = False
        _compact_history(self.history)


def _parse_tool_output(raw: str):
    """Try to parse a tool output string back into a dict/list.

    Tools return dicts; the Agents SDK serializes them via str() (Python repr,
    single-quoted), not JSON. We try JSON first (in case that ever changes),
    then ast.literal_eval (safe — only evaluates literals). Returns the raw
    string on parse failure so card extraction can skip non-dict outputs.
    """
    if not raw:
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, TypeError):
        return raw
