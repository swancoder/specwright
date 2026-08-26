"""Tests for the run_successful completion marker written by git_commit_feature (ADR-008)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mcp_server.core.registry import ToolError
from mcp_server.core.sandbox import Sandbox
from mcp_server.tools import git_tools


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


@pytest.fixture
def sb(repo: Path) -> Sandbox:
    return Sandbox(repo)


def marker(repo: Path) -> Path:
    return repo / git_tools.RUN_SUCCESSFUL_MARKER


def test_marker_written_on_successful_commit(sb: Sandbox, repo: Path) -> None:
    assert not marker(repo).exists()
    (repo / "src" / "b.py").write_text("b = 2\n")
    out = git_tools.git_commit_feature(sb, "feat(core): add b", "001")
    assert out.startswith("committed ")
    m = marker(repo)
    assert m.is_file() and m.stat().st_size == 0
    assert m.parent.name == ".agent-harness"
    # marker lives in a never-staged directory
    assert ".agent-harness/run_successful" not in git(repo, "ls-files").split()
    assert git(repo, "status", "--porcelain", "--", ".agent-harness").strip().startswith("??")


def test_marker_absent_when_nothing_to_commit(sb: Sandbox, repo: Path) -> None:
    assert git_tools.git_commit_feature(sb, "chore: noop", "001") == "nothing to commit"
    assert not marker(repo).exists()


def test_marker_absent_on_invalid_message_or_non_repo(sb: Sandbox, repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    (repo / "src" / "c.py").write_text("c\n")
    with pytest.raises(ToolError):
        git_tools.git_commit_feature(sb, "not conventional", "001")
    assert not marker(repo).exists()

    plain = tmp_path_factory.mktemp("plain")
    with pytest.raises(ToolError):
        git_tools.git_commit_feature(Sandbox(plain), "chore: x", "001")
    assert not (plain / git_tools.RUN_SUCCESSFUL_MARKER).exists()


def test_marker_idempotent_and_directory_created(sb: Sandbox, repo: Path) -> None:
    assert not (repo / ".agent-harness").exists()
    git_tools.write_marker(sb)
    git_tools.write_marker(sb)
    assert marker(repo).is_file()


def test_tool_only_writes_marker_after_commit_via_registry(repo: Path) -> None:
    from mcp_server.core.context import HarnessContext
    from mcp_server.main import build_registry

    ctx = HarnessContext.for_target(repo)
    try:
        reg = build_registry(ctx)
        (repo / "src" / "d.py").write_text("d\n")
        res = reg.call_tool_result("git_commit_feature", {"message": "feat: d", "spec_id": "001"})
        assert not res.is_error and marker(repo).exists()
    finally:
        ctx.close()


def test_target_venv_and_pycache_are_never_staged(sb: Sandbox, repo: Path) -> None:
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")
    (repo / "src" / "__pycache__").mkdir()
    (repo / "src" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
    (repo / "src" / "e.py").write_text("e\n")
    assert git_tools.git_commit_feature(sb, "feat: e", "001").startswith("committed ")
    tracked = git(repo, "ls-files").split()
    assert "src/e.py" in tracked
    assert not any(p.startswith(".venv") or "__pycache__" in p for p in tracked)
