"""The mutating `fix` toolchain task and its capability gating (ADR-019)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_graph.indexer import DatabaseManager
from mcp_server.core.context import HarnessContext
from mcp_server.core.sandbox import Sandbox
from mcp_server.core.registry import ToolError
from mcp_server.main import build_registry
from mcp_server.core.toolchain import MUTATING_TASKS, TASKS, JsonToolchain, PythonToolchain
from mcp_server.tools.toolchain_tools import RUN_TOOLCHAIN_FIX_CAP, run_toolchain_task


def test_fix_is_a_known_mutating_task():
    assert "fix" in TASKS and "fix" in MUTATING_TASKS


def test_python_default_fix_is_ruff_fix():
    assert PythonToolchain().command("fix") == "ruff check --fix ."


def test_json_toolchain_fix_from_commands(tmp_path):
    (tmp_path / "toolchain.json").write_text(json.dumps(
        {"stack": "node", "commands": {"fix": "eslint . --fix"}}))
    from mcp_server.core.toolchain import resolve_toolchain
    tc = resolve_toolchain(Sandbox(tmp_path))
    assert isinstance(tc, JsonToolchain) and tc.command("fix") == "eslint . --fix"


# ---- capability gating --------------------------------------------------------

def test_fix_refused_without_capability(tmp_path):
    # the read-only Verifier path: allow_fix defaults to False
    with pytest.raises(ToolError, match="not enabled for this role"):
        run_toolchain_task(Sandbox(tmp_path), "fix")


def test_fix_allowed_with_capability_skips_without_venv(tmp_path):
    # implementer path: allowed; python-default fix needs a provisioned .venv, so it skips (no error)
    out = json.loads(run_toolchain_task(Sandbox(tmp_path), "fix", allow_fix=True))
    assert out["task"] == "fix" and out["status"] == "skipped"


def _ctx(tmp_path, optional_tools):
    db = DatabaseManager(tmp_path / "g.db"); db.connect()
    return HarnessContext(target_dir=tmp_path, db=db, optional_tools=frozenset(optional_tools))


def test_tool_registered_with_fix_capability_runs_fix(tmp_path):
    ctx = _ctx(tmp_path, {"run_toolchain_task", RUN_TOOLCHAIN_FIX_CAP})
    try:
        res = build_registry(ctx).call_tool_result("run_toolchain_task", {"task": "fix"})
        assert not res.is_error
        out = json.loads(res.content[0].text)
        assert out["task"] == "fix" and out["status"] == "skipped"  # no venv → skipped, but permitted
    finally:
        ctx.close()


def test_tool_without_fix_capability_refuses_fix(tmp_path):
    ctx = _ctx(tmp_path, {"run_toolchain_task"})  # verifier-style: no fix capability
    try:
        res = build_registry(ctx).call_tool_result("run_toolchain_task", {"task": "fix"})
        assert res.is_error
        assert "not enabled for this role" in res.content[0].text
    finally:
        ctx.close()
