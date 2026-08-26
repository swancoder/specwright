"""Tests for bin/run_agent.sh, bin/bootstrap_env.sh and bin/harness_config.py (ADR-005, ADR-006)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
RUN_AGENT = HARNESS / "bin" / "run_agent.sh"
CONFIG_PY = HARNESS / "bin" / "harness_config.py"


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHON": sys.executable, **(env or {})}
    return subprocess.run([str(RUN_AGENT), *args], cwd=cwd, env=full_env, capture_output=True, text=True)


@pytest.fixture
def target(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "S-01"
    spec.mkdir(parents=True)
    (spec / "01_spec.md").write_text("# spec\n")
    return tmp_path


def test_config_env_exports_openai_vars() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "env", "ollama"], capture_output=True, text=True, check=True).stdout
    env = dict(shlex.split(line.removeprefix("export "))[0].split("=", 1) for line in out.splitlines())
    assert env["OPENAI_BASE_URL"] == "http://localhost:11434/v1"
    assert env["OPENAI_API_KEY"] == "ollama"
    assert env["MODEL_NAME"] == "qwen2.5-coder:14b-32k"
    assert not any(k.startswith("ANTHROPIC_") for k in env), "ADR-006: no Anthropic-specific variables"


def test_config_env_prefers_api_key_env() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "env"], capture_output=True, text=True, check=True,
                         env={**os.environ, "OLLAMA_API_KEY": "s3cret"}).stdout
    assert "export OPENAI_API_KEY=s3cret" in out


def test_config_unknown_backend_fails() -> None:
    res = subprocess.run([sys.executable, str(CONFIG_PY), "env", "nope"], capture_output=True, text=True)
    assert res.returncode != 0 and "unknown backend 'nope'" in res.stderr


def test_config_role_prompt_forbids_guessing() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "role"], capture_output=True, text=True, check=True).stdout
    assert "read_specification" in out and "Do NOT guess" in out


def test_run_agent_requires_spec_and_target(target: Path) -> None:
    assert _run(["--target-dir", str(target)], target).returncode == 2
    assert _run(["--spec", "S-01"], target, {"TARGET_DIR": ""}).returncode == 2
    assert _run(["--spec", "../x", "--target-dir", str(target)], target).returncode == 2


def test_run_agent_dry_run_writes_mcp_json_and_command(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target, {"AGENT_CMD": "fake-agent"})
    assert res.returncode == 0, res.stderr

    mcp = json.loads((target / ".agent-harness" / "mcp.json").read_text())
    server = mcp["mcpServers"]["agent-harness"]
    assert server["command"] == str(HARNESS / "bin" / "start_mcp.sh")
    assert server["args"] == ["--target-dir", str(target)]

    cmd = shlex.split(res.stdout.splitlines()[-1])
    assert cmd[0] == "fake-agent"
    assert cmd[cmd.index("--mcp-config") + 1] == str(target / ".agent-harness" / "mcp.json")
    assert "SystemArchitect" in cmd[cmd.index("--append-system-prompt") + 1]
    assert "Implement spec S-01" in cmd[-1] and "specs/S-01/01_spec.md" in cmd[-1]
    assert "backend=ollama" in res.stdout and "model=qwen2.5-coder:14b-32k" in res.stdout


def test_run_agent_default_opencode_invocation(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target)
    assert res.returncode == 0, res.stderr
    cmd = shlex.split(res.stdout.splitlines()[-1])
    assert cmd[:2] == ["opencode", "run"]
    assert cmd[cmd.index("--dir") + 1] == str(target)
    assert cmd[cmd.index("-m") + 1] == "ollama/qwen2.5-coder:14b-32k"
    assert cmd[cmd.index("--agent") + 1] == "SystemArchitect"
    assert "Implement spec S-01" in cmd[-1]

    oc = json.loads((target / ".agent-harness" / "opencode.json").read_text())
    assert oc["model"] == "ollama/qwen2.5-coder:14b-32k"
    assert oc["provider"]["ollama"]["options"]["baseURL"] == "http://localhost:11434/v1"
    assert oc["provider"]["ollama"]["npm"] == "@ai-sdk/openai-compatible"
    mcp = oc["mcp"]["agent-harness"]
    assert mcp["type"] == "local"
    assert mcp["command"] == [str(HARNESS / "bin" / "start_mcp.sh"), "--target-dir", str(target)]
    agent = oc["agent"]["SystemArchitect"]
    assert "Do NOT guess" in agent["prompt"]
    assert "- agent-harness_read_constitution" in agent["prompt"], "persona must carry Open Code's prefixed tool names"
    for t in ("bash", "edit", "write", "patch", "multiedit", "webfetch", "read", "glob", "grep"):
        assert agent["tools"][t] is False
    assert "agent-harness_read_constitution" in cmd[-1]


def test_bootstrap_check_only_reports_python_env() -> None:
    res = subprocess.run([str(HARNESS / "bin" / "bootstrap_env.sh"), "--check-only"], capture_output=True, text=True,
                         env={**os.environ, "PYTHON": sys.executable})
    assert res.returncode in (0, 1), res.stderr
    assert "== Python environment" in res.stdout and "== Open Code" in res.stdout and "== LLM backend" in res.stdout
    assert "[ok]      python 3." in res.stdout
    assert "[MISSING]" in res.stdout or "environment ready" in res.stdout


def test_bootstrap_rejects_unknown_arg() -> None:
    res = subprocess.run([str(HARNESS / "bin" / "bootstrap_env.sh"), "--bogus"], capture_output=True, text=True)
    assert res.returncode == 2
