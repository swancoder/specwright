"""Tests for fs_list and fs_write (ADR-007)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mcp_server.core.registry import ToolError
from mcp_server.core.sandbox import Sandbox, SandboxViolation
from mcp_server.tools import fs_tools


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# readme\n")
    for noise in (".git", ".venv", "__pycache__", "node_modules", ".agent-harness"):
        (tmp_path / noise).mkdir()
        (tmp_path / noise / "junk").write_text("x")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00")
    return tmp_path


@pytest.fixture
def sb(target: Path) -> Sandbox:
    return Sandbox(target)


def _listing(sb: Sandbox, *args, **kw) -> dict:
    return json.loads(fs_tools.fs_list(sb, *args, **kw))


# ---------------------------------------------------------------- fs_list


def test_fs_list_root_excludes_noise_and_sorts_dirs_first(sb: Sandbox) -> None:
    out = _listing(sb)
    assert out["directory"] == "."
    assert out["truncated"] is False
    paths = [e["path"] for e in out["entries"]]
    assert paths == ["src", "README.md"]
    assert out["entries"][0]["type"] == "dir"
    assert out["entries"][1] == {"path": "README.md", "type": "file", "size": 9}


def test_fs_list_subdirectory_and_recursive(sb: Sandbox) -> None:
    sub = _listing(sb, "src")
    assert [e["path"] for e in sub["entries"]] == ["src/pkg", "src/app.py"]

    rec = _listing(sb, ".", recursive=True)
    paths = [e["path"] for e in rec["entries"]]
    assert "src/pkg/__init__.py" in paths and "src/app.py" in paths
    assert not any("__pycache__" in p or ".git" in p or "node_modules" in p for p in paths)


def test_fs_list_default_argument_is_root(sb: Sandbox) -> None:
    assert fs_tools.fs_list(sb) == fs_tools.fs_list(sb, ".")


def test_fs_list_rejects_traversal_absolute_and_excluded(sb: Sandbox, target: Path) -> None:
    with pytest.raises(SandboxViolation):
        fs_tools.fs_list(sb, "../")
    with pytest.raises(SandboxViolation):
        fs_tools.fs_list(sb, str(target.parent))
    with pytest.raises(ToolError, match="excluded"):
        fs_tools.fs_list(sb, ".git")
    with pytest.raises(ToolError, match="not a directory"):
        fs_tools.fs_list(sb, "README.md")
    with pytest.raises(ToolError, match="no such file"):
        fs_tools.fs_list(sb, "missing")


def test_fs_list_symlink_escape_is_blocked(sb: Sandbox, target: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret").write_text("s")
    os.symlink(outside, target / "link")
    with pytest.raises(SandboxViolation):
        fs_tools.fs_list(sb, "link")
    rec = _listing(sb, ".", recursive=True)
    assert {"path": "link", "type": "symlink"} in rec["entries"]
    assert not any(e["path"].startswith("link/") for e in rec["entries"])


def test_fs_list_truncates(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_tools, "MAX_LIST_ENTRIES", 2)
    out = _listing(sb, ".", recursive=True)
    assert out["truncated"] is True and len(out["entries"]) == 2


# ---------------------------------------------------------------- fs_write


def test_fs_write_creates_file_and_parents(sb: Sandbox, target: Path) -> None:
    msg = fs_tools.fs_write(sb, "src/feedback/routes.py", "ROUTES = 1\n")
    assert msg == "created src/feedback/routes.py (11 bytes)"
    assert (target / "src" / "feedback" / "routes.py").read_text() == "ROUTES = 1\n"
    assert (target / "src" / "feedback").is_dir()


def test_fs_write_overwrites_existing(sb: Sandbox, target: Path) -> None:
    msg = fs_tools.fs_write(sb, "README.md", "# new\n")
    assert msg.startswith("overwritten README.md")
    assert (target / "README.md").read_text() == "# new\n"
    assert not list(target.glob(".README.md.*.tmp")), "atomic write leaves no temp files"


def test_fs_write_roundtrip_with_fs_read_and_utf8(sb: Sandbox) -> None:
    fs_tools.fs_write(sb, "docs/ünïcode.md", "héllo — 世界\n")
    assert fs_tools.fs_read(sb, "docs/ünïcode.md") == "héllo — 世界\n"


def test_fs_write_rejects_traversal_absolute_dir_excluded_and_large(sb: Sandbox, target: Path) -> None:
    with pytest.raises(SandboxViolation):
        fs_tools.fs_write(sb, "../escape.txt", "x")
    with pytest.raises(SandboxViolation):
        fs_tools.fs_write(sb, "/etc/passwd", "x")
    with pytest.raises(SandboxViolation):
        fs_tools.fs_write(sb, "", "x")
    with pytest.raises(ToolError, match="is a directory"):
        fs_tools.fs_write(sb, "src", "x")
    with pytest.raises(ToolError, match="excluded"):
        fs_tools.fs_write(sb, ".git/hooks/pre-commit", "x")
    with pytest.raises(ToolError, match="excluded"):
        fs_tools.fs_write(sb, ".agent-harness/mcp.json", "{}")
    with pytest.raises(ToolError, match="too large"):
        fs_tools.fs_write(sb, "big.txt", "x" * (fs_tools.MAX_WRITE_BYTES + 1))
    assert not (target.parent / "escape.txt").exists()


def test_fs_write_symlinked_parent_escape_is_blocked(sb: Sandbox, target: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, target / "link")
    with pytest.raises(SandboxViolation):
        fs_tools.fs_write(sb, "link/evil.txt", "x")
    assert not (outside / "evil.txt").exists()


# ---------------------------------------------------------------- registry


def test_tools_registered_with_strict_schemas(target: Path) -> None:
    from mcp_server.core.context import HarnessContext
    from mcp_server.main import build_registry

    ctx = HarnessContext.for_target(target)
    try:
        reg = build_registry(ctx)
        names = {t.name for t in reg.list_tools()}
        assert {"fs_list", "fs_write"} <= names
        schema = reg.get("fs_list").to_mcp_tool().input_schema
        assert schema["properties"]["directory_path"]["default"] == "."
        assert schema.get("additionalProperties") is False
        res = reg.call_tool_result("fs_write", {"filepath": "new/a.txt", "content": "a", "extra": 1})
        assert res.is_error
        res = reg.call_tool_result("fs_write", {"filepath": "new/a.txt", "content": "a"})
        assert not res.is_error and (target / "new" / "a.txt").read_text() == "a"
        res = reg.call_tool_result("fs_list", {})
        assert not res.is_error and '"new"' in res.content[0].text
    finally:
        ctx.close()
