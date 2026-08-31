"""Tests for emit_env and the three audit-event sources: agent turns, toolchain runs, the gate (ADR-018)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.core import audit
from mcp_server.core.sandbox import Sandbox


def _events(audit_dir: Path, sid: str) -> list[dict]:
    return [json.loads(x) for x in (audit_dir / f"{sid}.jsonl").read_text().splitlines() if x.strip()]


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    d = tmp_path / "audit"
    monkeypatch.setenv("HARNESS_AUDIT_DIR", str(d))
    monkeypatch.setenv("HARNESS_SESSION_ID", "sid1")
    monkeypatch.delenv("HARNESS_ACTOR", raising=False)
    return d


# ---- emit_env -----------------------------------------------------------------

def test_emit_env_noop_without_session(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUDIT_DIR", raising=False)
    monkeypatch.delenv("HARNESS_SESSION_ID", raising=False)
    assert audit.emit_env("agent_turn", "SystemArchitect") is None


def test_emit_env_writes_with_session(session_env):
    audit.emit_env("agent_turn", "SystemArchitect", attempt="1")
    ev = _events(session_env, "sid1")
    assert ev[0]["actor"] == "SystemArchitect" and ev[0]["action"] == "agent_turn"


def test_emit_env_actor_defaults_to_harness_actor(session_env, monkeypatch):
    monkeypatch.setenv("HARNESS_ACTOR", "Verifier")
    audit.emit_env("agent_turn")
    assert _events(session_env, "sid1")[0]["actor"] == "Verifier"


# ---- source A: run_toolchain_task MCP tool ------------------------------------

def test_toolchain_tool_emits_execute_event(session_env, tmp_path, monkeypatch):
    from mcp_server.tools.toolchain_tools import run_toolchain_task
    monkeypatch.setenv("HARNESS_ACTOR", "SystemArchitect")
    target = tmp_path / "proj"; target.mkdir()  # no toolchain.json + no venv -> python-default "test" skips
    run_toolchain_task(Sandbox(target), "test")
    ev = _events(session_env, "sid1")
    assert len(ev) == 1
    e = ev[0]
    assert e["action"] == "execute_toolchain_task"
    assert e["actor"] == "SystemArchitect"
    assert e["task"] == "test" and e["stack"] == "python-default"
    assert "status" in e and "exit_code" in e
    assert isinstance(e["duration_ms"], int) and e["duration_ms"] >= 0


# ---- source C: mechanical gate ------------------------------------------------

def test_completion_gate_emits_toolchain_and_gate_events(session_env, tmp_path):
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("completion_checks", root / "bin" / "completion_checks.py")
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
    target = tmp_path / "proj"; target.mkdir()  # no requirements.txt, no venv -> checks skip, gate passes
    rc = cc.main(["--target-dir", str(target)])
    assert rc == 0
    ev = _events(session_env, "sid1")
    actions = [e["action"] for e in ev]
    assert "mechanical_gate" in actions
    gate = next(e for e in ev if e["action"] == "mechanical_gate")
    assert gate["actor"] == "gate" and gate["status"] == "success" and gate["gaps"] == 0
    assert isinstance(gate["duration_ms"], int) and gate["duration_ms"] >= 0
    # the lint toolchain task also emits an execute event, attributed to the gate, timed
    tool = [e for e in ev if e["action"] == "execute_toolchain_task"]
    assert tool and all(e["actor"] == "gate" for e in tool)
    assert all(isinstance(e["duration_ms"], int) for e in tool)
