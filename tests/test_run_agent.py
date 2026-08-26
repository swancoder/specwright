"""Tests for bin/run_agent.sh, bin/bootstrap_env.sh and bin/harness_config.py (ADR-005, ADR-006, ADR-008)."""

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
    assert env["LLM_CONTEXT_LENGTH"] == "32768" and env["LLM_MAX_OUTPUT_TOKENS"] == "8192"
    assert env["MODEL_NAME"] == "gpt-oss:20b-32k"
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

    line = res.stdout.splitlines()[-1]  # printf %q output; bash quoting is not shlex-parseable
    assert line.startswith(f"fake-agent --mcp-config {target / '.agent-harness' / 'mcp.json'} --append-system-prompt ")
    assert "SystemArchitect" in line
    assert "Implement\\ spec\\ S-01" in line and "specs/S-01/01_spec.md" in line
    assert "backend=ollama" in res.stdout and "model=gpt-oss:20b-32k" in res.stdout


def test_run_agent_default_opencode_invocation(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target)
    assert res.returncode == 0, res.stderr
    resume_line = next(l for l in res.stdout.splitlines() if l.startswith("# resume: "))
    resume = shlex.split(resume_line.removeprefix("# resume: "))
    assert resume[:2] == ["opencode", "run"] and "--continue" in resume
    assert resume[-1].startswith("Continue with the plan. Your last message was plain text instead of a tool call.")
    assert "git_commit_feature" in resume[-1]
    assert "# run marker:" in res.stdout and "max attempts: 5" in res.stdout
    cmd = shlex.split(res.stdout.splitlines()[-1])
    assert cmd[:2] == ["opencode", "run"]
    assert cmd[cmd.index("--dir") + 1] == str(target)
    assert cmd[cmd.index("-m") + 1] == "ollama/gpt-oss:20b-32k"
    assert cmd[cmd.index("--agent") + 1] == "SystemArchitect"
    assert "Implement spec S-01" in cmd[-1]

    oc = json.loads((target / ".agent-harness" / "opencode.json").read_text())
    assert oc["model"] == "ollama/gpt-oss:20b-32k"
    assert oc["provider"]["ollama"]["options"]["baseURL"] == "http://localhost:11434/v1"
    assert oc["provider"]["ollama"]["npm"] == "@ai-sdk/openai-compatible"
    limit = oc["provider"]["ollama"]["models"]["gpt-oss:20b-32k"]["limit"]
    assert limit == {"context": 32768, "output": 8192}, "missing limit => Open Code compacts every turn"
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


# ---------------------------------------------------------------- supervisor loop (ADR-008)

FAKE_AGENT = """#!/bin/bash
# Fake Open Code: records every invocation; creates the marker on the Nth call.
{ printf '%q ' "$@"; echo; } >> "$FAKE_LOG"
n=$(wc -l < "$FAKE_LOG")
if [ -n "${SUCCEED_ON:-}" ] && [ "$n" -ge "$SUCCEED_ON" ]; then
  mkdir -p "$AGENT_TARGET_DIR/.agent-harness" && : > "$AGENT_TARGET_DIR/.agent-harness/run_successful"
fi
exit 0
"""


@pytest.fixture
def fake_opencode(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A directory containing an executable named ``opencode`` plus its invocation log."""
    d = tmp_path_factory.mktemp("fake")
    exe = d / "opencode"
    exe.write_text(FAKE_AGENT)
    exe.chmod(0o755)
    log = d / "calls.log"
    return exe, log


def _calls(log: Path) -> list[list[str]]:
    return [shlex.split(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_supervisor_succeeds_on_second_attempt_with_continue(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "2"})
    assert res.returncode == 0, res.stderr
    calls = _calls(log)
    assert len(calls) == 2
    assert "--continue" not in calls[0] and calls[0][-1].startswith("Implement spec S-01")
    assert "--continue" in calls[1] and calls[1][-1].startswith("Continue with the plan.")
    assert calls[1][calls[1].index("--agent") + 1] == "SystemArchitect"
    assert "run successful" in res.stderr and "after attempt 2" in res.stderr
    assert (target / ".agent-harness" / "run_successful").exists()


def test_supervisor_gives_up_after_max_retries(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(exe), "FAKE_LOG": str(log)})
    assert res.returncode == 3, res.stderr
    calls = _calls(log)
    assert len(calls) == 5
    assert [("--continue" in c) for c in calls] == [False, True, True, True, True]
    assert "FAILED" in res.stderr and "git_commit_feature was never reached" in res.stderr


def test_supervisor_respects_max_retries_override(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "MAX_RETRIES": "1"})
    assert res.returncode == 3 and len(_calls(log)) == 1
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(exe), "MAX_RETRIES": "0"})
    assert res.returncode == 2 and "MAX_RETRIES" in res.stderr


def test_supervisor_deletes_stale_marker_at_startup(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    stale = target / ".agent-harness" / "run_successful"
    stale.parent.mkdir()
    stale.touch()
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(exe), "FAKE_LOG": str(log)})
    assert res.returncode == 3, "a stale marker must not count as success"
    assert not stale.exists()
    assert len(_calls(log)) == 5


def test_supervisor_first_attempt_success_runs_once(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "1"})
    assert res.returncode == 0 and len(_calls(log)) == 1 and "after attempt 1" in res.stderr


def test_generic_agent_cmd_runs_single_attempt(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    generic = exe.parent / "some-agent"
    generic.write_text(FAKE_AGENT)
    generic.chmod(0o755)
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(generic), "FAKE_LOG": str(log)})
    assert res.returncode == 3 and len(_calls(log)) == 1
    assert "--mcp-config" in _calls(log)[0] and "--continue" not in _calls(log)[0]
