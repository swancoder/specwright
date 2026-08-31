"""Behavioural tests for the unified ./harness CLI (ADR-017).

Routing/validation run against the real script; install/run/ui run against an isolated temp
copy of the harness (stub entry points + a fake run_agent.sh) so no test mutates the repo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "harness"
ENV = {**os.environ, "NO_COLOR": "1"}


def _run(args, cwd=ROOT):
    return subprocess.run([str(cwd / "harness"), *args], cwd=cwd, env=ENV,
                          capture_output=True, text=True, timeout=120)


# ---- routing / validation (real script, no side effects) --------------------

def test_help_lists_commands():
    r = _run(["help"])
    assert r.returncode == 0
    for cmd in ("install", "run", "ui"):
        assert cmd in r.stdout


def test_no_args_shows_help():
    assert _run([]).returncode == 0


def test_unknown_command_exits_2():
    r = _run(["frobnicate"])
    assert r.returncode == 2
    assert "unknown command" in r.stderr


def test_run_without_task_exits_2():
    r = _run(["run"])
    assert r.returncode == 2
    assert "task description is required" in r.stderr


def test_ui_rejects_unknown_arg():
    assert _run(["ui", "--bogus"]).returncode == 2


# ---- isolated harness copy for the stateful commands ------------------------

@pytest.fixture
def sandbox_harness(tmp_path):
    h = tmp_path / "hx"
    (h / "bin").mkdir(parents=True)
    (h / "mcp_server").mkdir()
    shutil.copy(HARNESS, h / "harness")
    shutil.copy(ROOT / "bin" / "ui_dashboard.py", h / "bin" / "ui_dashboard.py")
    shutil.copy(ROOT / "bin" / "audit_log.py", h / "bin" / "audit_log.py")
    (h / "requirements.txt").write_text("")
    (h / "bin" / "start_mcp.sh").write_text("#!/usr/bin/env bash\n")
    (h / "mcp_server" / "main.py").write_text("")
    # the audit_log.py shim imports mcp_server.core.audit — mirror that minimal package
    (h / "mcp_server" / "__init__.py").write_text("")
    core = h / "mcp_server" / "core"; core.mkdir()
    (core / "__init__.py").write_text("")
    shutil.copy(ROOT / "mcp_server" / "core" / "audit.py", core / "audit.py")
    fake = h / "bin" / "run_agent.sh"
    fake.write_text('#!/usr/bin/env bash\necho "args: $*"\n'
                    'echo "sid=${SESSION_ID:-UNSET}"\nexit 7\n')
    fake.chmod(0o755)
    return h


def _run_in(h, args):
    return subprocess.run([str(h / "harness"), *args], cwd=h, env=ENV,
                          capture_output=True, text=True, timeout=120)


def test_install_no_deps_bootstraps(sandbox_harness):
    h = sandbox_harness
    r = _run_in(h, ["install", "--no-deps"])
    assert r.returncode == 0
    assert (h / ".venv" / "bin" / "python").exists()
    assert (h / ".agent-harness" / "audit_logs").is_dir()
    assert ".agent-harness/" in (h / ".gitignore").read_text()


def test_install_fails_on_missing_entrypoint(sandbox_harness):
    (sandbox_harness / "mcp_server" / "main.py").unlink()
    assert _run_in(sandbox_harness, ["install", "--no-deps"]).returncode == 1


def test_run_exports_session_id_logs_and_passes_exit_code(sandbox_harness):
    h = sandbox_harness
    r = _run_in(h, ["run", "build a widget", "--spec", "001", "--target-dir", "../x"])
    assert r.returncode == 7  # exit code of run_agent.sh is preserved, not swallowed
    logs = list((h / ".agent-harness" / "audit_logs").glob("*.log"))
    assert len(logs) == 1
    body = logs[0].read_text()
    assert "task:       build a widget" in body
    assert "sid=" in body and "UNSET" not in body      # SESSION_ID was exported to the child
    assert "args: --spec 001 --target-dir ../x" in body  # remaining args forwarded verbatim
    assert "exit:       7" in body
    # structured JSONL companion for the dashboard: session_start + session_end
    import json
    jsonl = list((h / ".agent-harness" / "audit_logs").glob("*.jsonl"))
    assert len(jsonl) == 1
    events = [json.loads(x) for x in jsonl[0].read_text().splitlines() if x.strip()]
    actions = [e["action"] for e in events]
    assert actions == ["session_start", "session_end"]
    assert events[0]["task"] == "build a widget"
    assert events[1]["status"] == "failed" and events[1]["exit_code"] == 7


def test_run_requires_nonempty_task(sandbox_harness):
    assert _run_in(sandbox_harness, ["run", ""]).returncode == 2


def test_ui_without_streamlit_errors(sandbox_harness):
    # no .venv and streamlit not importable from bare python3 in the sandbox
    r = _run_in(sandbox_harness, ["ui"])
    assert r.returncode == 1
    assert "streamlit is not installed" in r.stderr
