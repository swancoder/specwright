"""Tests for the toolchain abstraction layer (ADR-015): sanitize, truncation, resolve, MCP tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.core.context import HarnessContext
from mcp_server.core.sandbox import Sandbox
from mcp_server.core.toolchain import (
    JsonToolchain,
    PythonToolchain,
    head_tail_truncate,
    resolve_toolchain,
    sanitize,
)
from mcp_server.main import build_registry


# ---------------------------------------------------------------- §3 sanitize


def test_sanitize_strips_ansi_and_resolves_carriage_returns() -> None:
    assert sanitize("\x1b[31mERROR\x1b[0m done") == "ERROR done"
    assert sanitize("10%\r50%\r100% complete") == "100% complete"  # progress bar -> final state
    assert sanitize("\x1b]0;title\x07plain") == "plain"            # OSC sequence stripped
    assert "\x1b" not in sanitize("\x1b[1;32m\x1b[Kbuild ok")


# ---------------------------------------------------------------- §3 head/tail truncation


def test_truncate_short_text_unchanged() -> None:
    txt = "\n".join(f"line {i}" for i in range(30))
    assert head_tail_truncate(txt) == txt


def test_truncate_keeps_head_tail_rescues_errors_and_caps_4kib() -> None:
    lines = ([f"cfg{i}" for i in range(20)]
             + [f"noise{i}" for i in range(500)]
             + ["E   AssertionError: kaboom", "Fatal error: nope"]
             + [f"noise{i}" for i in range(500)]
             + [f"summary{i}" for i in range(50)])
    out = head_tail_truncate("\n".join(lines))
    assert out.startswith("cfg0")
    assert out.rstrip().endswith("summary49")
    assert "[TRUNCATED" in out
    assert "AssertionError: kaboom" in out and "Fatal error: nope" in out  # rescued
    assert len(out.encode("utf-8")) <= 4096


# ---------------------------------------------------------------- resolve / fallback


def test_resolve_falls_back_to_python_when_absent(tmp_path: Path) -> None:
    tc = resolve_toolchain(Sandbox(tmp_path))
    assert isinstance(tc, PythonToolchain) and tc.stack == "python-default"


def test_resolve_reads_toolchain_json(tmp_path: Path) -> None:
    (tmp_path / "toolchain.json").write_text(json.dumps(
        {"stack": "php", "commands": {"install": "composer install", "test": "phpunit"}}))
    tc = resolve_toolchain(Sandbox(tmp_path))
    assert isinstance(tc, JsonToolchain) and tc.stack == "php"
    assert tc.command("test") == "phpunit" and tc.command("build") is None


def test_resolve_malformed_json_falls_back(tmp_path: Path) -> None:
    (tmp_path / "toolchain.json").write_text("{not valid")
    assert isinstance(resolve_toolchain(Sandbox(tmp_path)), PythonToolchain)
    (tmp_path / "toolchain.json").write_text('{"stack":"x","commands":{}}')  # empty commands
    assert isinstance(resolve_toolchain(Sandbox(tmp_path)), PythonToolchain)


def test_json_toolchain_runs_command(tmp_path: Path) -> None:
    (tmp_path / "toolchain.json").write_text(json.dumps(
        {"stack": "node", "commands": {"lint": "echo LINT && exit 5", "test": "echo ok"}}))
    sb = Sandbox(tmp_path)
    tc = resolve_toolchain(sb)
    r = tc.run(sb, "lint")
    assert r.exit_code == 5 and "LINT" in r.stdout and r.stack == "node"
    assert tc.run(sb, "test").exit_code == 0
    assert tc.run(sb, "build").skipped  # not declared


def test_python_toolchain_skips_lint_without_venv(tmp_path: Path) -> None:
    r = PythonToolchain().run(Sandbox(tmp_path), "lint")
    assert r.skipped and r.exit_code == 0
    assert PythonToolchain().run(Sandbox(tmp_path), "build").skipped  # no build step


# ---------------------------------------------------------------- §2 MCP tool


def test_tool_is_capability_gated(tmp_path: Path) -> None:
    assert "run_toolchain_task" not in build_registry(HarnessContext.for_target(tmp_path)).names()
    ctx = HarnessContext.for_target(tmp_path, optional_tools={"run_toolchain_task"})
    try:
        assert "run_toolchain_task" in build_registry(ctx).names()
    finally:
        ctx.close()


def test_tool_returns_bounded_ansi_free_json(tmp_path: Path) -> None:
    (tmp_path / "toolchain.json").write_text(json.dumps({"stack": "demo", "commands": {
        "build": "printf '\\033[31mstart\\033[0m\\n'; for i in $(seq 1 400); do echo noise $i; done; echo 'Fatal error: boom'; exit 2"}}))
    ctx = HarnessContext.for_target(tmp_path, optional_tools={"run_toolchain_task"})
    try:
        res = build_registry(ctx).call_tool_result("run_toolchain_task", {"task": "build"})
        assert not res.is_error
        out = json.loads(res.content[0].text)
        assert set(out) >= {"task", "status", "exit_code", "output_snippet"}
        assert out["task"] == "build" and out["status"] == "failed" and out["exit_code"] == 2
        assert out["stack"] == "demo"
        assert "\x1b" not in out["output_snippet"]                       # ANSI stripped
        assert len(out["output_snippet"].encode("utf-8")) <= 4096        # bounded
        assert "Fatal error: boom" in out["output_snippet"]             # rescued/tail
        # strict schema: bad task rejected
        assert build_registry(ctx).call_tool_result("run_toolchain_task", {"task": "deploy"}).is_error
    finally:
        ctx.close()


def test_completion_checks_uses_toolchain_for_non_python(tmp_path: Path) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("cc", Path(__file__).resolve().parent.parent / "bin" / "completion_checks.py")
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)  # type: ignore[union-attr]
    (tmp_path / "toolchain.json").write_text(json.dumps({"stack": "php", "commands": {
        "install": "true", "test": "true", "lint": "echo 'phpstan: 2 errors' >&2; exit 1"}}))
    gaps = cc.run_checks(tmp_path)
    assert any("lint FAILED (php)" in g for g in gaps) and not any("hermetic" in g for g in gaps)


def test_run_tests_delegates_to_toolchain(tmp_path: Path) -> None:
    """run_tests defers to the toolchain 'test' task when a toolchain.json is present (ADR-015)."""
    import json as _json

    from mcp_server.tools.test_tools import run_tests
    (tmp_path / "tests").mkdir()
    (tmp_path / "toolchain.json").write_text(_json.dumps(
        {"stack": "node", "commands": {"test": "echo running vitest && exit 3"}}))
    out = _json.loads(run_tests(Sandbox(tmp_path), "tests"))
    assert out["runner"] == "toolchain:node" and out["exit_code"] == 3
    assert "vitest" in out["stdout"] and out["python"] is None
