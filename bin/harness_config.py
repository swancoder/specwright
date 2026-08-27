#!/usr/bin/env python3
"""Read config/llm_backends.yaml and config/roles.yaml for bin/run_agent.sh (ADR-005, ADR-006).

Subcommands:
  env  [backend]   print `export KEY='value'` lines for the backend (default: `default:` key)
  role [name]      print the persona's system prompt
  model [backend]  print the model name of the backend
  tools [role]     print the role's allowed harness tools, one per line
  mcptools [role]  print role's tools + gated aliases as mcp__agent-harness__<name>
  list             list backends and roles
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
HARNESS_ROOT = str(Path(__file__).resolve().parent.parent)
import sys as _sys
_sys.path.insert(0, HARNESS_ROOT)
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
BACKENDS_FILE = CONFIG_DIR / "llm_backends.yaml"
ROLES_FILE = CONFIG_DIR / "roles.yaml"


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: top level must be a mapping")
    return data


def _select(data: dict[str, Any], section: str, name: str | None, path: Path) -> tuple[str, dict[str, Any]]:
    entries = data.get(section) or {}
    name = name or data.get("default")
    if not name:
        raise SystemExit(f"{path}: no '{section}' entry requested and no 'default:' key")
    if name not in entries:
        raise SystemExit(f"{path}: unknown {section[:-1]} '{name}' (available: {', '.join(entries) or 'none'})")
    entry = entries[name] or {}
    if not isinstance(entry, dict):
        raise SystemExit(f"{path}: {section}.{name} must be a mapping")
    return name, entry


def backend_env(name: str | None, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve a backend profile into the environment variables the agent CLI expects."""
    environ = os.environ if environ is None else environ
    name, be = _select(_load(BACKENDS_FILE), "backends", name, BACKENDS_FILE)
    for key in ("base_url", "model"):
        if not be.get(key):
            raise SystemExit(f"{BACKENDS_FILE}: backends.{name}.{key} is required")
    api_key = str(be.get("api_key") or "")
    key_env = be.get("api_key_env")
    if key_env and environ.get(key_env):
        api_key = environ[key_env]
    base_url = str(be["base_url"]).rstrip("/")
    env = {
        "LLM_BACKEND": name,
        "LLM_PROVIDER": str(be.get("provider") or "openai_compatible"),
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": api_key,
        "MODEL_NAME": str(be["model"]),
        "LLM_CONTEXT_LENGTH": str(int(be.get("context_length") or 32768)),
        "LLM_MAX_OUTPUT_TOKENS": str(int(be.get("max_output_tokens") or 8192)),
    }
    for k, v in (be.get("extra_env") or {}).items():
        env[str(k)] = str(v)
    return env


def role_prompt(name: str | None) -> str:
    name, role = _select(_load(ROLES_FILE), "roles", name, ROLES_FILE)
    prompt = role.get("system_prompt")
    if not prompt:
        raise SystemExit(f"{ROLES_FILE}: roles.{name}.system_prompt is required")
    return str(prompt).rstrip("\n")


def role_tools(name: str | None) -> list[str]:
    name, role = _select(_load(ROLES_FILE), "roles", name, ROLES_FILE)
    tools = role.get("allowed_tools") or []
    return [str(t) for t in tools]


def role_mcptools(name: str | None) -> list[str]:
    """Canonical tools plus aliases whose canonical is allowed, as mcp__agent-harness__ names (ADR-014)."""
    from mcp_server.tools.aliases import ALIAS_MAP
    allowed = role_tools(name)
    names = list(allowed) + [a for a, c in ALIAS_MAP.items() if c in set(allowed)]
    return [f"mcp__agent-harness__{n}" for n in names]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness_config", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env").add_argument("backend", nargs="?")
    sub.add_parser("role").add_argument("role", nargs="?")
    sub.add_parser("model").add_argument("backend", nargs="?")
    sub.add_parser("tools").add_argument("role", nargs="?")
    sub.add_parser("mcptools").add_argument("role", nargs="?")
    sub.add_parser("list")
    ns = ap.parse_args(argv)

    if ns.cmd == "env":
        for k, v in backend_env(ns.backend).items():
            print(f"export {k}={shlex.quote(v)}")
    elif ns.cmd == "role":
        print(role_prompt(ns.role))
    elif ns.cmd == "model":
        print(backend_env(ns.backend)["MODEL_NAME"])
    elif ns.cmd == "tools":
        print("\n".join(role_tools(ns.role)))
    elif ns.cmd == "mcptools":
        print("\n".join(role_mcptools(ns.role)))
    else:
        b, r = _load(BACKENDS_FILE), _load(ROLES_FILE)
        print(f"backends (default: {b.get('default')}): {', '.join(b.get('backends') or {})}")
        print(f"roles    (default: {r.get('default')}): {', '.join(r.get('roles') or {})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
