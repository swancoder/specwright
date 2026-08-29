"""Tests for bin/init_project.sh — new-project scaffolding (ADR-016)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
INIT = HARNESS / "bin" / "init_project.sh"


def _init(dest: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(INIT), "--project-dir", str(dest), "--yes", *args],
                          capture_output=True, text=True)


def _tracked(dest: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(dest), "ls-files"], capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_python_default_scaffold_has_no_toolchain(tmp_path: Path) -> None:
    d = tmp_path / "proj"
    res = _init(d, "--stack", "python", "--backend", "opencode")
    assert res.returncode == 0, res.stderr
    tracked = _tracked(d)
    assert {".github/constitution.md", "CLAUDE.md", ".gitignore",
            "specs/001-feature/01_intent.md", "specs/001-feature/02_spec.md",
            "specs/001-feature/03_plan.md", "specs/template/03_plan.md", "src/.gitkeep"} <= tracked
    assert "toolchain.json" not in tracked  # Python uses the default
    assert (d / "specs" / "001-feature" / "03_plan.md").read_text() == ""  # empty until planned
    assert "Python 3.12+" in (d / ".github" / "constitution.md").read_text()
    assert "AGENT_CMD=opencode" in res.stderr and "--phase plan" in res.stderr


@pytest.mark.parametrize("stack", ["php-js", "node-typescript", "java-maven", "java-gradle"])
def test_non_python_stack_copies_toolchain(tmp_path: Path, stack: str) -> None:
    d = tmp_path / "proj"
    res = _init(d, "--stack", stack, "--backend", "claude", "--model", "haiku")
    assert res.returncode == 0, res.stderr
    tc = json.loads((d / "toolchain.json").read_text())
    assert tc["stack"] == stack and set(tc["commands"]) == {"install", "lint", "test", "build"}
    assert "toolchain.json" in _tracked(d)
    assert "AGENT_CMD=claude" in res.stderr and "--model haiku" in res.stderr


def test_slug_and_name_and_git_baseline(tmp_path: Path) -> None:
    d = tmp_path / "widget"
    res = _init(d, "--stack", "python", "--spec-slug", "004-login", "--name", "Login Service")
    assert res.returncode == 0, res.stderr
    assert (d / "specs" / "004-login" / "01_intent.md").is_file()
    log = subprocess.run(["git", "-C", str(d), "log", "--oneline"], capture_output=True, text=True, check=True).stdout
    assert "initialise Login Service (python)" in log


def test_refuses_nonempty_dir_without_force(tmp_path: Path) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    (d / "existing.txt").write_text("x")
    assert _init(d, "--stack", "python").returncode == 2
    assert _init(d, "--stack", "python", "--force").returncode == 0  # --force overrides


def test_rejects_bad_stack_slug_backend(tmp_path: Path) -> None:
    assert _init(tmp_path / "a", "--stack", "cobol").returncode == 2
    assert _init(tmp_path / "b", "--stack", "python", "--backend", "aider").returncode == 2
    assert _init(tmp_path / "c", "--stack", "python", "--spec-slug", "../evil").returncode == 2


def test_scaffold_plan_gate_is_unapproved(tmp_path: Path) -> None:
    d = tmp_path / "proj"
    _init(d, "--stack", "node-typescript")
    gate = subprocess.run([sys.executable, str(HARNESS / "bin" / "plan_gate.py"), "check",
                           str(d / "specs" / "001-feature" / "03_plan.md")], capture_output=True, text=True)
    assert gate.returncode == 5  # empty plan → run the plan phase first
