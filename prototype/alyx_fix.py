"""Prototype of `ax alyx fix` — agent-native evaluator iteration.

Pulls recent test traces, asks an OpenAI model to diagnose each evaluator,
and (optionally with --apply) commits a suggested fix as a new evaluator
version on Arize. Designed for demos, not production.

Usage:
    # Analyze only
    python prototype/alyx_fix.py \\
        --project drone-show-manager \\
        --workflow "drone-show-manager-test-<timestamp>" \\
        --evaluator all \\
        --context AGENT_CONTEXT.md \\
        --wait 120

    # Analyze AND apply suggested fixes
    python prototype/alyx_fix.py ... --apply
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv(override=True)

AX = "/Users/davidkoenitzer/.local/bin/ax"
SPACE_ID = os.environ.get("ARIZE_SPACE_ID", "")
MODEL = os.environ.get("OPENAI_JUDGE_MODEL", "gpt-4.1")
EVALUATORS_DIR = Path(__file__).resolve().parent.parent / "evals" / "evaluators"


SYSTEM_PROMPT = """You are Alyx, Arize's AI assistant for iterating on live evaluators.

You are given:
1. AGENT CONTEXT — system prompt, design rationale, recent changes for the agent under test.
2. CURRENT EVALUATOR TEMPLATES — the prompts each judge currently uses.
3. RECENT TRACES — each with the agent's response and per-evaluator scores + explanations.

For each evaluator, decide whether failing scores reflect:
- AGENT is wrong: response actually violates the rule the judge cites. Fix targets the agent, not the eval. fix_template_patch should be null.
- JUDGE is wrong: explanation contradicts itself, references content not in the response, or applies a stricter standard than the template describes. fix_template_patch must be a precise old_text/new_text pair that, when applied to the evaluator's CURRENT template, would resolve the issue. The old_text must exist verbatim in the current template.
- CORRECT: judge and agent both behaved as intended. fix_template_patch is null.

Return a JSON object with this exact shape:

{
  "evaluators": [
    {
      "name": "<evaluator name>",
      "passed": <int>,
      "total": <int>,
      "diagnosis": "<one-sentence>",
      "fix_description": "<one-sentence, or 'No fix needed.'>",
      "fix_template_patch": {"old_text": "...", "new_text": "..."} or null,
      "context_needed": "<extra context for future runs, or 'No new context needed.'>"
    }
  ]
}

Only return JSON. No surrounding text.
"""


def fetch_traces(project: str, workflow: str, wait: int) -> list:
    """Fetch recent test traces with evaluations attached."""
    if wait > 0:
        print(f"  ↳ waiting {wait}s for async eval scoring...", file=sys.stderr)
        time.sleep(min(wait, 5))   # capped for demo speed

    print(f"  ↳ fetching traces for workflow '{workflow}' in project '{project}'", file=sys.stderr)

    if not SPACE_ID:
        print("  ↳ ARIZE_SPACE_ID not set; using mock traces", file=sys.stderr)
        return _mock_traces()

    try:
        result = subprocess.run(
            [AX, "spans", "export", project,
             "--space", SPACE_ID,
             "--filter", "name LIKE 'dataset_smoke:%'",
             "--days", "1", "-l", "200", "--stdout"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        spans = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f"  ↳ trace fetch failed ({e}); using mock traces", file=sys.stderr)
        return _mock_traces()

    by_trace = {}
    for s in spans:
        tid = s.get("context", {}).get("trace_id")
        if not tid or not s.get("evaluations"):
            continue
        if tid not in by_trace or s["start_time"] > by_trace[tid]["start_time"]:
            by_trace[tid] = s

    traces = sorted(by_trace.values(), key=lambda s: s["start_time"], reverse=True)[:15]
    print(f"  ↳ found {len(traces)} traces with evaluations", file=sys.stderr)
    return traces


def _mock_traces() -> list:
    """Plausible fake traces so the prototype always produces a demo run."""
    return [
        {
            "name": f"dataset_smoke:adv_{i:02d}",
            "attributes": {"output.value": f"(mocked agent response for adv_{i:02d})"},
            "evaluations": [
                {"name": "evidence_grounded", "label": "grounded", "score": 1,
                 "explanation": "Response is grounded in tool output."},
                {"name": "right_tool_chosen", "label": "correct", "score": 1,
                 "explanation": "Correct tool selection."},
                {"name": "response_quality", "label": "good", "score": 1,
                 "explanation": "Clear and concise."},
            ],
        } for i in range(1, 16)
    ]


def load_evaluator_configs(filter_name: str) -> dict:
    """Load local evaluator JSONs as the source of truth for current templates."""
    configs = {}
    for path in sorted(EVALUATORS_DIR.glob("*.json")):
        cfg = json.loads(path.read_text())
        if filter_name == "all" or cfg["name"] == filter_name:
            configs[cfg["name"]] = cfg
    return configs


def summarize_traces(traces: list, filter_name: str) -> str:
    lines = []
    for t in traces:
        name = t.get("name", "unknown")
        out = (t.get("attributes", {}).get("output.value") or "")[:300]
        evals = t.get("evaluations") or []
        if filter_name != "all":
            evals = [e for e in evals if e.get("name") == filter_name]
        if not evals:
            continue
        lines.append(f"TRACE: {name}")
        lines.append(f"  agent_output: {out!r}")
        for e in evals:
            expl = (e.get("explanation") or "")[:250]
            lines.append(f"  {e.get('name')}: score={e.get('score')} label={e.get('label')!r}")
            lines.append(f"    judge_said: {expl}")
        lines.append("")
    return "\n".join(lines)


def summarize_evaluator_configs(configs: dict) -> str:
    parts = []
    for name, cfg in configs.items():
        parts.append(f"--- {name} ---")
        parts.append(cfg.get("template", ""))
        parts.append("")
    return "\n".join(parts)


def call_alyx(context: str, eval_configs_text: str, trace_summary: str) -> dict:
    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                "AGENT CONTEXT:\n\n"
                f"{context}\n\n"
                "----\n\n"
                "CURRENT EVALUATOR TEMPLATES:\n\n"
                f"{eval_configs_text}\n\n"
                "----\n\n"
                "RECENT TRACES:\n\n"
                f"{trace_summary}\n\n"
                "Return JSON only."
            )},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def render(analyses: list) -> None:
    """Print one ▸ block per evaluator."""
    for a in analyses:
        print()
        print(f"▸ evaluator:      {a['name']}")
        print(f"▸ analysis:       Correct {a['passed']}/{a['total']} test traces. {a['diagnosis']}")
        print(f"▸ fix:            {a['fix_description']}")
        print(f"▸ context-needed: {a['context_needed']}")
    print()


def apply_fix(cfg: dict, patch: dict) -> bool:
    """Patch the local JSON and push a new evaluator version to Arize."""
    name = cfg["name"]
    path = EVALUATORS_DIR / f"{name}.json"

    old = patch.get("old_text", "")
    new = patch.get("new_text", "")
    if not old or new is None:
        print(f"  ↳ {name}: empty patch; skipping", file=sys.stderr)
        return False
    if old not in cfg["template"]:
        print(f"  ↳ {name}: patch.old_text not found in current template; skipping", file=sys.stderr)
        return False

    cfg["template"] = cfg["template"].replace(old, new)
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"  ↳ {name}: patched local {path.name}", file=sys.stderr)

    integration_id = os.environ.get("ARIZE_OPENAI_INTEGRATION_ID", "").strip()
    if not integration_id:
        try:
            r = subprocess.run(
                [AX, "ai-integrations", "list", "--space", SPACE_ID, "-o", "json"],
                capture_output=True, text=True, check=True, timeout=20,
            )
            for i in json.loads(r.stdout).get("ai_integrations", []):
                if i.get("provider") == "openAI":
                    integration_id = i["id"]
                    break
        except Exception as e:
            print(f"  ↳ {name}: couldn't auto-discover integration ID ({e})", file=sys.stderr)
            return False

    cmd = [
        AX, "evaluators", "create-template-evaluator-version", name,
        "--space", SPACE_ID,
        "--commit-message", "alyx_fix prototype: applied suggested patch",
        "--template-name", cfg["template_name"],
        "--template", cfg["template"],
        "--ai-integration-id", integration_id,
        "--model-name", cfg["model_name"],
        "--data-granularity", cfg["data_granularity"],
        "--direction", cfg["direction"],
        "--classification-choices", json.dumps(cfg["classification_choices"]),
    ]
    if cfg.get("include_explanations"):
        cmd.append("--include-explanations")
    if cfg.get("use_function_calling"):
        cmd.append("--use-function-calling")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ↳ {name}: pushed new version to Arize", file=sys.stderr)
        return True
    print(f"  ↳ {name}: push failed: {result.stderr.strip()[-300:]}", file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Prototype of ax alyx fix — agent-native evaluator iteration."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--evaluator", default="all")
    parser.add_argument("--context", required=True)
    parser.add_argument("--wait", type=int, default=0)
    parser.add_argument("--apply", action="store_true",
                        help="Apply suggested fixes by patching local JSON and pushing a new evaluator version.")
    args = parser.parse_args()

    ctx_path = Path(args.context)
    if not ctx_path.exists():
        print(f"error: context file not found: {ctx_path}", file=sys.stderr)
        sys.exit(1)
    context = ctx_path.read_text()

    configs = load_evaluator_configs(args.evaluator)
    if not configs:
        print(f"error: no evaluator configs found in {EVALUATORS_DIR}", file=sys.stderr)
        sys.exit(1)

    traces = fetch_traces(args.project, args.workflow, args.wait)
    if not traces:
        print("No traces with evaluations found. Nothing to analyze.", file=sys.stderr)
        sys.exit(1)

    trace_summary = summarize_traces(traces, args.evaluator)
    eval_configs_text = summarize_evaluator_configs(configs)
    result = call_alyx(context, eval_configs_text, trace_summary)
    analyses = result.get("evaluators", [])
    render(analyses)

    if args.apply:
        print("Applying fixes:", file=sys.stderr)
        applied = 0
        for a in analyses:
            patch = a.get("fix_template_patch")
            if patch and a["name"] in configs:
                if apply_fix(configs[a["name"]], patch):
                    applied += 1
        print(f"\nApplied {applied} fix(es).", file=sys.stderr)
    elif any(a.get("fix_template_patch") for a in analyses):
        print("Fixes available. Re-run with --apply to commit them.", file=sys.stderr)


if __name__ == "__main__":
    main()
