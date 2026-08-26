"""Tests for target-environment provisioning in run_tests (ADR-009). Subprocesses are mocked."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mcp_server.core.registry import ToolError
from mcp_server.core.sandbox import CommandResult, Sandbox
from mcp_server.tools import test_tools


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    assert True\n")
    (tmp_path / "requirements.txt").write_text("fastapi\npytest\n")
    return tmp_path


@pytest.fixture
def sb(target: Path) -> Sandbox:
    return Sandbox(target)


class FakeRun:
    """Records Sandbox.run argv; creates the venv python when `-m venv` is invoked."""

    def __init__(self, sandbox: Sandbox, *, fail: dict[str, tuple[int, str]] | None = None, pytest_missing: bool = False):
        self.sandbox = sandbox
        self.calls: list[list[str]] = []
        self.fail = fail or {}          # substring of argv -> (exit_code, stderr)
        self.pytest_missing = pytest_missing

    def __call__(self, argv: list[str], *, timeout: float = 0) -> CommandResult:
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for needle, (code, err) in self.fail.items():
            if needle in joined:
                return CommandResult(argv=list(argv), exit_code=code, stdout="", stderr=err)
        if argv[1:3] == ["-m", "venv"]:
            py = self.sandbox.root / argv[3] / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/bin/sh\n")
        if argv[-2:] == ["-c", "import pytest"] and self.pytest_missing:
            self.pytest_missing = False
            return CommandResult(argv=list(argv), exit_code=1, stdout="", stderr="ModuleNotFoundError")
        if "pytest" in joined and "-q" in argv and "pip" not in argv:
            return CommandResult(argv=list(argv), exit_code=0, stdout="1 passed", stderr="")
        return CommandResult(argv=list(argv), exit_code=0, stdout="", stderr="")


def _patch(monkeypatch: pytest.MonkeyPatch, fake: FakeRun) -> None:
    monkeypatch.setattr(Sandbox, "run", lambda self, argv, *, timeout=0: fake(argv, timeout=timeout))


def _venv_py(target: Path) -> str:
    return str(target / ".venv" / "bin" / "python")


# ---------------------------------------------------------------- creation & use


def test_creates_venv_installs_requirements_then_runs_pytest_with_target_python(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(sb)
    _patch(monkeypatch, fake)
    report = json.loads(test_tools.run_tests(sb, "tests/test_x.py"))

    assert fake.calls[0] == [sys.executable, "-m", "venv", ".venv"]
    assert fake.calls[1] == [_venv_py(target), "-m", "pip", "install", "-q", "-r", "requirements.txt"]
    assert fake.calls[2] == [_venv_py(target), "-c", "import pytest"]
    assert fake.calls[3] == [_venv_py(target), "-m", "pytest", "tests/test_x.py", "-q"]
    assert sys.executable not in " ".join(fake.calls[3]), "tests must not run in the harness interpreter"
    assert report["runner"] == "pytest" and report["python"] == ".venv/bin/python"
    assert report["env_actions"] == ["created .venv", "installed requirements.txt"]
    assert report["exit_code"] == 0 and "1 passed" in report["stdout"]
    assert (target / ".venv" / ".harness-requirements.sha256").is_file()


def test_existing_venv_with_unchanged_requirements_skips_provisioning(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(sb)
    _patch(monkeypatch, fake)
    test_tools.run_tests(sb, "tests/test_x.py")
    fake.calls.clear()

    report = json.loads(test_tools.run_tests(sb, "tests/test_x.py::test_a"))
    assert [c[1:3] for c in fake.calls] == [["-c", "import pytest"], ["-m", "pytest"]]
    assert report["env_actions"] == []
    assert fake.calls[-1][3] == "tests/test_x.py::test_a"


def test_changed_requirements_triggers_reinstall(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(sb)
    _patch(monkeypatch, fake)
    test_tools.run_tests(sb, "tests/test_x.py")
    (target / "requirements.txt").write_text("fastapi\npytest\npydantic-settings\n")
    fake.calls.clear()

    report = json.loads(test_tools.run_tests(sb, "tests/test_x.py"))
    assert fake.calls[0][1:] == ["-m", "pip", "install", "-q", "-r", "requirements.txt"]
    assert report["env_actions"] == ["installed requirements.txt (changed)"]


def test_missing_requirements_still_provisions_and_installs_pytest(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (target / "requirements.txt").unlink()
    fake = FakeRun(sb, pytest_missing=True)
    _patch(monkeypatch, fake)
    report = json.loads(test_tools.run_tests(sb, "tests/test_x.py"))
    assert not any("requirements.txt" in " ".join(c) for c in fake.calls)
    assert [_venv_py(target), "-m", "pip", "install", "-q", "pytest"] in fake.calls
    assert report["env_actions"] == ["created .venv", "no requirements.txt; environment has no project dependencies", "installed pytest"]


# ---------------------------------------------------------------- failures


def test_pip_failure_surfaces_as_tool_error_and_no_stamp(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(sb, fail={"pip install -q -r": (1, "ERROR: No matching distribution found for fastapi==99")})
    _patch(monkeypatch, fake)
    with pytest.raises(ToolError, match="pip install -r requirements.txt failed.*No matching distribution"):
        test_tools.run_tests(sb, "tests/test_x.py")
    assert not (target / ".venv" / ".harness-requirements.sha256").exists()
    assert not any("-m pytest" in " ".join(c) for c in fake.calls), "tests must not run with a broken environment"


def test_venv_creation_failure_surfaces(sb: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(sb, fail={"-m venv": (1, "ensurepip is not available")})
    _patch(monkeypatch, fake)
    with pytest.raises(ToolError, match="creating .venv failed.*ensurepip"):
        test_tools.run_tests(sb, "tests/test_x.py")


def test_non_pytest_runners_do_not_provision(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (target / "package.json").write_text("{}")
    fake = FakeRun(sb)
    _patch(monkeypatch, fake)
    report = json.loads(test_tools.run_tests(sb, "tests/test_x.py"))
    assert fake.calls == [["npm", "test", "--", "tests/test_x.py"]]
    assert report["runner"] == "npm" and report["python"] is None and report["env_actions"] == []


# ---------------------------------------------------------------- CLI


def test_cli_provisions_target(sb: Sandbox, target: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeRun(sb)
    _patch(monkeypatch, fake)
    assert test_tools.main(["--target-dir", str(target)]) == 0
    out = capsys.readouterr().out
    assert "created .venv" in out and "installed requirements.txt" in out
    assert test_tools.main(["--target-dir", str(target)]) == 0
    assert "up to date" in capsys.readouterr().out
