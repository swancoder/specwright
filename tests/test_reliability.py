"""Tests for ADR-014 reliability additions: aliases, shadow guard, arg aliases, recover_hint,
completion_checks, preflight."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from mcp_server.core.context import HarnessContext
from mcp_server.main import build_registry
from mcp_server.tools import fs_tools
from mcp_server.tools.aliases import ALIAS_MAP, build_alias_tools

BIN = Path(__file__).resolve().parent.parent / "bin"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BIN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "requirements.txt").write_text("fastapi\nmytoolkit==1.2\n")
    return tmp_path


# ---------------------------------------------------------------- §3 aliases


def test_aliases_delegate_and_gate(target: Path) -> None:
    reg = build_registry(HarnessContext.for_target(target))
    names = set(reg.names())
    assert {"fs_find", "open_file", "git_commit"} <= names
    assert reg.get("fs_find").handler is reg.get("fs_list").handler       # delegates
    assert reg.get("write_file").args_model is reg.get("fs_write").args_model
    # every alias maps to a registered canonical
    assert all(c in names for c in ALIAS_MAP.values())
    assert build_alias_tools([reg.get("fs_list")]) and all(
        s.name in ALIAS_MAP for s in build_alias_tools(list(reg.list_tools())[:0] + [reg.get("fs_list")]))


def test_arg_alias_line_start(target: Path) -> None:
    (target / "a.py").write_text("l1\nl2\nl3\n")
    reg = build_registry(HarnessContext.for_target(target))
    r = reg.call_tool_result("fs_read", {"filepath": "a.py", "line_start": 2, "line_end": 3})
    assert not r.is_error and "lines 2-3" in r.content[0].text
    r = reg.call_tool_result("fs_read", {"filepath": "a.py", "start_line": 2})  # canonical still works
    assert not r.is_error


# ---------------------------------------------------------------- §6 shadow guard


def test_shadow_guard_blocks_dep_packages_not_project(target: Path) -> None:
    sb = fs_tools.Sandbox(target)
    with pytest.raises(fs_tools.ToolError, match="shadow"):
        fs_tools.fs_write(sb, "fastapi/__init__.py", "x")          # builtin blocklist
    with pytest.raises(fs_tools.ToolError, match="shadow"):
        fs_tools.fs_write(sb, "mytoolkit/__init__.py", "x")        # from requirements.txt
    assert fs_tools.fs_write(sb, "src/http/__init__.py", "x").startswith("created")   # project pkg ok
    assert fs_tools.fs_write(sb, "app.py", "x").startswith("created")                 # top-level non-dep ok


# ---------------------------------------------------------------- §2 recover_hint


def test_recover_hint_detects_dropped_call() -> None:
    rh = _load("recover_hint")
    assert rh.detect('I will call {"name": "read_constitution", "arguments": {}}') == "read_constitution"
    assert rh.detect('```json\n{"name":"mcp__agent-harness__run_tests","arguments":{}}\n```') == "run_tests"
    assert rh.detect("calling agent-harness_fs_list('.')") == "fs_list"
    assert rh.detect("just prose, no tools here") is None
    assert rh.detect('{"name":"totally_made_up"}') is None


# ---------------------------------------------------------------- §4 completion_checks


def test_completion_checks_hermetic_detects_missing_dep(tmp_path: Path) -> None:
    cc = _load("completion_checks")
    # a target whose code needs a dep NOT in requirements.txt
    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / "pkg").mkdir(); (tmp_path / "pkg" / "__init__.py").write_text("import demolib_absent\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import pkg\ndef test_a():\n    assert True\n")
    gaps = cc.run_checks(tmp_path, hermetic=True)
    assert gaps and any("hermetic" in g for g in gaps)


def test_completion_checks_clean_target_passes(tmp_path: Path) -> None:
    cc = _load("completion_checks")
    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    assert 1 == 1\n")
    gaps = cc.run_checks(tmp_path, hermetic=True)
    assert gaps == []   # no .venv → mypy/ruff skipped; hermetic passes


# ---------------------------------------------------------------- §1 preflight


def test_preflight_skips_non_local(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pf = _load("preflight_model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    assert pf.main([]) == 0
    assert "non-local" in capsys.readouterr().out
    assert pf.is_local("http://localhost:11434/v1") and not pf.is_local("https://api.anthropic.com")


def test_preflight_toolcall_tristate(monkeypatch: pytest.MonkeyPatch) -> None:
    pf = _load("preflight_model")
    import urllib.request
    def fake(kind):
        def _open(req, timeout=0):
            import io, json as _j
            body = {"choices": [{"message": kind}]}
            return io.BytesIO(_j.dumps(body).encode())
        return _open
    # structured tool_calls -> ok (True)
    monkeypatch.setattr(urllib.request, "urlopen", fake({"tool_calls": [{"id": "1"}]}))
    assert pf.check_toolcall("http://x/v1", "m", "")[0] is True
    # tool call as TEXT -> hard fail (False)
    monkeypatch.setattr(urllib.request, "urlopen", fake({"content": '{"name": "read_constitution", "arguments": {}}'}))
    assert pf.check_toolcall("http://x/v1", "m", "")[0] is False
    # empty/other -> inconclusive (None), never aborts
    monkeypatch.setattr(urllib.request, "urlopen", fake({"content": ""}))
    assert pf.check_toolcall("http://x/v1", "m", "")[0] is None
