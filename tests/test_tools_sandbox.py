"""Sandboxed tool tests (ADR-001): traversal protection, patching, tests, git."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server.core.registry import ToolError
from mcp_server.core.sandbox import Sandbox, SandboxViolation
from mcp_server.tools import fs_tools, git_tools, test_tools


@pytest.fixture
def target(tmp_path: Path) -> Path:
    root = tmp_path / "target"
    (root / ".github").mkdir(parents=True)
    (root / ".github" / "constitution.md").write_text("# Constitution\nrule 1\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def f():\n    return 1\n")
    (root / "specs" / "001-login").mkdir(parents=True)
    (root / "specs" / "001-login" / "01_intent.md").write_text("intent\n")
    (root / "specs" / "001-login" / "02_spec.md").write_text("spec\n")
    (root / "specs" / "002-logout").mkdir()
    (root / "specs" / "002-logout" / "01_intent.md").write_text("logout\n")
    (tmp_path / "outside.txt").write_text("secret\n")
    return root


@pytest.fixture
def sb(target: Path) -> Sandbox:
    return Sandbox(target)


# ---------------------------------------------------------------- Sandbox.resolve


@pytest.mark.parametrize(
    "bad",
    ["../outside.txt", "src/../../outside.txt", "/etc/passwd", "", "   ", "C:\\Windows\\x", "..", "src/..\\..\\outside.txt"],
)
def test_resolve_rejects_traversal_and_absolute(sb: Sandbox, bad: str) -> None:
    with pytest.raises(SandboxViolation):
        sb.resolve(bad)


def test_resolve_rejects_symlink_escape(sb: Sandbox, target: Path, tmp_path: Path) -> None:
    (target / "link").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(SandboxViolation):
        sb.resolve("link")
    (target / "dirlink").symlink_to(tmp_path)
    with pytest.raises(SandboxViolation):
        sb.resolve("dirlink/outside.txt")


def test_resolve_accepts_inside_paths(sb: Sandbox, target: Path) -> None:
    assert sb.resolve("src/app.py") == (target / "src" / "app.py").resolve()
    assert sb.resolve("./src/./app.py") == (target / "src" / "app.py").resolve()
    (target / "inlink").symlink_to(target / "src")
    assert sb.resolve("inlink/app.py").is_relative_to(target.resolve())


def test_resolve_must_exist(sb: Sandbox) -> None:
    with pytest.raises(ToolError):
        sb.resolve("missing.py", must_exist=True)


def test_run_scrubs_env_and_uses_root_cwd(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPER_SECRET", "x")
    res = sb.run([sys.executable, "-c", "import os;print(os.getcwd());print('SUPER_SECRET' in os.environ)"])
    assert res.exit_code == 0
    out = res.stdout.splitlines()
    assert Path(out[0]).resolve() == target.resolve()
    assert out[1] == "False"


def test_run_timeout(sb: Sandbox) -> None:
    res = sb.run([sys.executable, "-c", "import time;time.sleep(5)"], timeout=0.5)
    assert res.timed_out and res.exit_code == -1 and "timed out" in res.stderr


# ---------------------------------------------------------------- fs tools


def test_fs_read_and_traversal(sb: Sandbox) -> None:
    assert fs_tools.fs_read(sb, "src/app.py") == "def f():\n    return 1\n"
    with pytest.raises(SandboxViolation):
        fs_tools.fs_read(sb, "../outside.txt")
    with pytest.raises(ToolError):
        fs_tools.fs_read(sb, "src")  # directory
    with pytest.raises(ToolError):
        fs_tools.fs_read(sb, "nope.py")


def test_fs_read_rejects_binary_and_huge(sb: Sandbox, target: Path) -> None:
    (target / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ToolError, match="UTF-8"):
        fs_tools.fs_read(sb, "bin.dat")
    (target / "big.txt").write_bytes(b"x" * (fs_tools.MAX_READ_BYTES + 1))
    with pytest.raises(ToolError, match="too large"):
        fs_tools.fs_read(sb, "big.txt")


def test_fs_apply_patch_success(sb: Sandbox, target: Path) -> None:
    msg = fs_tools.fs_apply_patch(sb, "src/app.py", "return 1", "return 2")
    assert "1 occurrence" in msg
    assert (target / "src" / "app.py").read_text() == "def f():\n    return 2\n"
    assert not list((target / "src").glob(".app.py.*.tmp"))  # temp file cleaned up


def test_fs_apply_patch_not_found_and_ambiguous(sb: Sandbox, target: Path) -> None:
    with pytest.raises(ToolError, match="not found"):
        fs_tools.fs_apply_patch(sb, "src/app.py", "return 9", "x")
    (target / "src" / "dup.py").write_text("a = 1\na = 1\n")
    with pytest.raises(ToolError, match="2 times"):
        fs_tools.fs_apply_patch(sb, "src/dup.py", "a = 1", "a = 2")
    assert (target / "src" / "dup.py").read_text() == "a = 1\na = 1\n"  # untouched
    with pytest.raises(SandboxViolation):
        fs_tools.fs_apply_patch(sb, "../outside.txt", "secret", "x")


def test_read_constitution(sb: Sandbox, target: Path) -> None:
    assert fs_tools.read_constitution(sb).startswith("# Constitution")
    (target / ".github" / "constitution.md").unlink()
    with pytest.raises(ToolError):
        fs_tools.read_constitution(sb)


def test_read_specification_matching(sb: Sandbox, target: Path) -> None:
    text = fs_tools.read_specification(sb, "001-login")
    assert "## specs/001-login/01_intent.md" in text and "## specs/001-login/02_spec.md" in text
    assert "intent" in text and "spec" in text
    assert "logout" in fs_tools.read_specification(sb, "002")  # prefix match
    (target / "specs" / "003.md").write_text("single\n")
    assert "single" in fs_tools.read_specification(sb, "003")


def test_read_specification_errors(sb: Sandbox, target: Path) -> None:
    with pytest.raises(ToolError, match="invalid spec id"):
        fs_tools.read_specification(sb, "../.github")
    with pytest.raises(ToolError, match="invalid spec id"):
        fs_tools.read_specification(sb, "a/b")
    with pytest.raises(ToolError, match="no specification"):
        fs_tools.read_specification(sb, "999")
    (target / "specs" / "001-signup").mkdir()
    with pytest.raises(ToolError, match="ambiguous"):
        fs_tools.read_specification(sb, "001")
    assert "intent" in fs_tools.read_specification(sb, "001-login")  # exact still wins


# ---------------------------------------------------------------- run_tests


def test_run_tests_pytest_captures_result(sb: Sandbox, target: Path) -> None:
    (target / "tests").mkdir()
    (target / "tests" / "test_ok.py").write_text("def test_a():\n    assert True\n\ndef test_b():\n    assert False\n")
    report = json.loads(test_tools.run_tests(sb, "tests/test_ok.py::test_a"))
    assert report["runner"] == "pytest" and report["exit_code"] == 0 and not report["timed_out"]
    assert "1 passed" in report["stdout"]
    report = json.loads(test_tools.run_tests(sb, "tests/test_ok.py"))
    assert report["exit_code"] != 0 and "1 failed" in report["stdout"]


def test_run_tests_rejects_escape_and_missing(sb: Sandbox) -> None:
    with pytest.raises(SandboxViolation):
        test_tools.run_tests(sb, "../outside.txt")
    with pytest.raises(ToolError):
        test_tools.run_tests(sb, "tests/nothing.py")


def test_run_tests_runner_detection(sb: Sandbox, target: Path) -> None:
    (target / "package.json").write_text("{}")
    assert test_tools.detect_runner(sb, "t")[0] == "npm"
    (target / "gradlew").write_text("#!/bin/sh\n")
    assert test_tools.detect_runner(sb, "t")[0] == "gradle"


# ---------------------------------------------------------------- git


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def repo(target: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(target, "init", "-q", "-b", "main")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "chore: init")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    import mcp_server.core.sandbox as sandbox_mod

    monkeypatch.setattr(
        sandbox_mod,
        "ENV_ALLOWLIST",
        (*sandbox_mod.ENV_ALLOWLIST, "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"),
    )
    return target


def test_build_commit_message() -> None:
    assert git_tools.build_commit_message("feat(auth): add login", "001-login") == "feat(auth): add login [001-login]"
    assert git_tools.build_commit_message("fix: x [ADR-9]\n\nbody", "ADR-9") == "fix: x [ADR-9]\n\nbody"
    for bad in ["add login", "feature: x", "feat:x", "Feat: x"]:
        with pytest.raises(ToolError, match="Conventional"):
            git_tools.build_commit_message(bad, "s")
    with pytest.raises(ToolError, match="Co-authored"):
        git_tools.build_commit_message("feat: x\n\nCo-authored-by: bot", "s")
    with pytest.raises(ToolError, match="invalid spec id"):
        git_tools.build_commit_message("feat: x", "../x")


def test_git_commit_feature_commits_and_excludes_local_only(sb: Sandbox, repo: Path) -> None:
    (repo / "src" / "new.py").write_text("x = 1\n")
    (repo / "CLAUDE.md").write_text("internal\n")
    (repo / "prompts-hist").mkdir()
    (repo / "prompts-hist" / "001.txt").write_text("prompt\n")
    (repo / ".agent-harness").mkdir()
    (repo / ".agent-harness" / "graph.db").write_bytes(b"db")

    out = git_tools.git_commit_feature(sb, "feat(core): add new module", "001-login")
    assert out.startswith("committed ")
    assert git(repo, "log", "-1", "--pretty=%s").strip() == "feat(core): add new module [001-login]"
    tracked = git(repo, "ls-files").split()
    assert "src/new.py" in tracked
    assert "CLAUDE.md" not in tracked
    assert not any(p.startswith("prompts-hist") or p.startswith(".agent-harness") for p in tracked)
    assert "Co-authored" not in git(repo, "log", "-1", "--pretty=%B")


def test_git_commit_feature_nothing_to_commit_and_not_repo(sb: Sandbox, repo: Path, tmp_path: Path) -> None:
    assert git_tools.git_commit_feature(sb, "chore: noop", "s1") == "nothing to commit"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ToolError, match="not a git repository"):
        git_tools.git_commit_feature(Sandbox(plain), "chore: x", "s1")


def test_git_commit_feature_rejects_bad_message_without_staging(sb: Sandbox, repo: Path) -> None:
    (repo / "src" / "n.py").write_text("y\n")
    with pytest.raises(ToolError):
        git_tools.git_commit_feature(sb, "bad message", "s1")
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""
