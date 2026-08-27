#!/usr/bin/env python3
"""Generate an Open Code config (and optionally mcp.json) for one harness role.

Reads the role's prompt and allowed_tools from config/roles.yaml and the LLM settings from
the environment (as exported by run_agent.sh). Used for both the primary persona and the
Verifier (ADR-005, ADR-011, ADR-012).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_config  # noqa: E402
from mcp_server.tools.aliases import ALIAS_MAP  # noqa: E402

HARNESS_DIR = Path(__file__).resolve().parent.parent
HARNESS_TOOLS = (
    "read_constitution", "read_specification", "fs_list", "fs_read", "fs_write",
    "fs_apply_patch", "run_tests", "git_commit_feature", "query_temporal_coupling",
    "mark_spec_complete",
)
BUILTIN_OFF = ("bash", "edit", "write", "patch", "multiedit", "webfetch", "read", "glob", "grep", "list", "task", "skill")


def build_opencode(role: str, allowed: list[str], prompt: str, server_cmd: str, server_args: list[str]) -> dict:
    backend = os.environ["LLM_BACKEND"]
    model = os.environ["MODEL_NAME"]
    allowed_set = set(allowed) or set(HARNESS_TOOLS)
    role_off = tuple(f"agent-harness_{t}" for t in HARNESS_TOOLS if t not in allowed_set)
    # ADR-014 §3: aliases enabled iff their canonical is allowed; disable the rest
    alias_off = tuple(f"agent-harness_{a}" for a, c in ALIAS_MAP.items() if c not in allowed_set)
    note = (
        "\n\nTool names in this session (use them EXACTLY as written, no other tools exist):\n"
        + "\n".join(f"- agent-harness_{t}" for t in HARNESS_TOOLS if t in allowed_set)
        + "\nExplore with agent-harness_fs_list, read with agent-harness_fs_read.\n"
    )
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {backend: {
            "npm": "@ai-sdk/openai-compatible",
            "name": f"{backend} (agent-harness)",
            "options": {"baseURL": os.environ["OPENAI_BASE_URL"], "apiKey": os.environ.get("OPENAI_API_KEY") or "none"},
            "models": {model: {"name": model, "limit": {
                "context": int(os.environ.get("LLM_CONTEXT_LENGTH", "32768")),
                "output": int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "8192")),
            }}},
        }},
        "model": f"{backend}/{model}",
        "mcp": {"agent-harness": {
            "type": "local", "enabled": True,
            "command": [server_cmd, *server_args],
            "environment": {"PYTHON": os.environ.get("PYTHON", "")} if os.environ.get("PYTHON") else {},
        }},
        "agent": {role: {
            "description": f"Agent Harness {role} persona (config/roles.yaml)",
            "mode": "primary",
            "model": f"{backend}/{model}",
            "prompt": prompt + note,
            "tools": {t: False for t in (*BUILTIN_OFF, *role_off, *alias_off)},
        }},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gen_opencode_config", description=__doc__.splitlines()[0])
    ap.add_argument("--target-dir", type=Path, required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--out-opencode", type=Path, required=True)
    ap.add_argument("--out-mcp", type=Path, default=None, help="also write an mcp.json (ADR-005 contract)")
    ap.add_argument("--write-scope", action="append", default=[])
    ap.add_argument("--enable-tool", action="append", default=[])
    ns = ap.parse_args(argv)

    server_cmd = str(HARNESS_DIR / "bin" / "start_mcp.sh")
    server_args = ["--target-dir", str(ns.target_dir)]
    for s in ns.write_scope:
        server_args += ["--write-scope", s]
    for t in ns.enable_tool:
        server_args += ["--enable-tool", t]

    prompt = harness_config.role_prompt(ns.role)
    allowed = harness_config.role_tools(ns.role)
    cfg = build_opencode(ns.role, allowed, prompt, server_cmd, server_args)
    ns.out_opencode.parent.mkdir(parents=True, exist_ok=True)
    ns.out_opencode.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    if ns.out_mcp is not None:
        server = {"command": server_cmd, "args": server_args, "env": {"PYTHON": os.environ.get("PYTHON", "")}}
        ns.out_mcp.write_text(json.dumps({"mcpServers": {"agent-harness": server}}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
