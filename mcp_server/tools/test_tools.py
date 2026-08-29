"""Test execution inside the target sandbox, in the target's own environment (ADR-001 §3, ADR-009)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolError, ToolSpec
from mcp_server.core.sandbox import Sandbox
from mcp_server.core.toolchain import JsonToolchain, head_tail_truncate, resolve_toolchain, sanitize

DEFAULT_TEST_TIMEOUT_SECONDS: Final[int] = 60
MAX_TEST_TIMEOUT_SECONDS: Final[int] = 600
VENV_CREATE_TIMEOUT_SECONDS: Final[float] = 300.0
PIP_TIMEOUT_SECONDS: Final[float] = 900.0
MAX_OUTPUT_CHARS: Final[int] = 20_000
VENV_DIR: Final[str] = ".venv"
REQUIREMENTS_FILE: Final[str] = "requirements.txt"
REQUIREMENTS_STAMP: Final[str] = ".harness-requirements.sha256"


class RunTestsArgs(ToolArgs):
    test_target: str = Field(
        min_length=1,
        description="Repository-relative test path, optionally with a '::' selector (e.g. tests/test_x.py::test_y).",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TEST_TIMEOUT_SECONDS,
        ge=1,
        le=MAX_TEST_TIMEOUT_SECONDS,
        validation_alias=AliasChoices("timeout_seconds", "timeout", "timeout_s"),
        description=f"Kill the test run after this many seconds (default {DEFAULT_TEST_TIMEOUT_SECONDS}, max {MAX_TEST_TIMEOUT_SECONDS}).",
    )


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n[... truncated {len(text) - MAX_OUTPUT_CHARS} chars]"


# ---------------------------------------------------------------- target environment


def target_python(sandbox: Sandbox) -> Path:
    """Interpreter of the target's virtual environment (may not exist yet)."""
    return sandbox.root / VENV_DIR / "bin" / "python"


def _requirements_hash(req: Path) -> str:
    return hashlib.sha256(req.read_bytes()).hexdigest()


def _run_or_raise(sandbox: Sandbox, argv: list[str], *, timeout: float, what: str) -> None:
    result = sandbox.run(argv, timeout=timeout)
    if result.timed_out:
        raise ToolError(f"{what} timed out after {timeout:.0f}s")
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise ToolError(f"{what} failed (exit {result.exit_code}): {detail}")


def ensure_target_env(sandbox: Sandbox) -> tuple[Path, list[str]]:
    """Create/refresh ``<target>/.venv`` and return ``(python, actions)``.

    - missing venv → ``python -m venv .venv`` (harness interpreter), then install
      ``requirements.txt`` if present;
    - existing venv → re-install only when ``requirements.txt`` changed since the last install
      (SHA-256 stamp inside the venv);
    - pytest is installed into the venv if it cannot be imported.

    Raises:
        ToolError: venv creation or ``pip install`` failed; stderr is included.
    """
    actions: list[str] = []
    py = target_python(sandbox)
    req = sandbox.root / REQUIREMENTS_FILE
    stamp = sandbox.root / VENV_DIR / REQUIREMENTS_STAMP

    if not py.exists():
        _run_or_raise(
            sandbox, [sys.executable, "-m", "venv", VENV_DIR],
            timeout=VENV_CREATE_TIMEOUT_SECONDS, what=f"creating {VENV_DIR}",
        )
        if not py.exists():
            raise ToolError(f"{VENV_DIR} was created but {py} is missing")
        actions.append(f"created {VENV_DIR}")

    if req.is_file():
        current = _requirements_hash(req)
        previous = stamp.read_text().strip() if stamp.is_file() else None
        if current != previous:
            _run_or_raise(
                sandbox, [str(py), "-m", "pip", "install", "-q", "-r", REQUIREMENTS_FILE],
                timeout=PIP_TIMEOUT_SECONDS, what=f"pip install -r {REQUIREMENTS_FILE}",
            )
            stamp.write_text(current + "\n")
            actions.append(f"installed {REQUIREMENTS_FILE}" + (" (changed)" if previous else ""))
    else:
        actions.append(f"no {REQUIREMENTS_FILE}; environment has no project dependencies")

    probe = sandbox.run([str(py), "-c", "import pytest"], timeout=60.0)
    if probe.exit_code != 0:
        _run_or_raise(
            sandbox, [str(py), "-m", "pip", "install", "-q", "pytest"],
            timeout=PIP_TIMEOUT_SECONDS, what="pip install pytest",
        )
        actions.append("installed pytest")
    return py, actions


# ---------------------------------------------------------------- runner


def detect_runner(sandbox: Sandbox, test_target: str) -> tuple[str, list[str]]:
    """Pick the test runner for the target project and build its argv.

    For pytest the argv uses the target's ``.venv`` interpreter (ADR-009); call
    :func:`ensure_target_env` first so it exists.
    """
    if (sandbox.root / "gradlew").is_file():
        return "gradle", ["./gradlew", "test", "--tests", test_target]
    if (sandbox.root / "package.json").is_file():
        return "npm", ["npm", "test", "--", test_target]
    return "pytest", [str(target_python(sandbox)), "-m", "pytest", test_target, "-q"]


def run_tests(sandbox: Sandbox, test_target: str, timeout_seconds: int = DEFAULT_TEST_TIMEOUT_SECONDS) -> str:
    """Run the target's test suite (or a subset) in the target's environment; JSON report.

    Args:
        test_target: Repository-relative path, optionally followed by ``::selector``.
            The path part must exist inside the sandbox.
        timeout_seconds: Hard wall-clock limit; the process is killed when it expires
            (default 60, max 600). A timeout is reported explicitly (ADR-010).

    Returns:
        JSON: ``{"runner", "python", "env_actions", "timeout_seconds", "exit_code",
        "timed_out", "timeout_message", "stdout", "stderr"}``.
    """
    if not 1 <= timeout_seconds <= MAX_TEST_TIMEOUT_SECONDS:
        raise ToolError(f"timeout_seconds must be between 1 and {MAX_TEST_TIMEOUT_SECONDS}")
    tc = resolve_toolchain(sandbox)
    if isinstance(tc, JsonToolchain):   # ADR-015: a declared toolchain owns "test"
        r = tc.run(sandbox, "test", timeout=float(timeout_seconds))
        return json.dumps({
            "runner": f"toolchain:{tc.stack}", "python": None, "env_actions": [],
            "timeout_seconds": timeout_seconds, "exit_code": r.exit_code, "timed_out": r.timed_out,
            "timeout_message": (f"toolchain test killed after {timeout_seconds}s" if r.timed_out else None),
            "stdout": head_tail_truncate(sanitize(r.stdout)), "stderr": head_tail_truncate(sanitize(r.stderr)),
        }, indent=2)
    path_part = test_target.split("::", 1)[0]
    resolved = sandbox.resolve(path_part, must_exist=True)
    normalized = sandbox.relative(resolved) + test_target[len(path_part):]
    runner, argv = detect_runner(sandbox, normalized)
    python_used: str | None = None
    actions: list[str] = []
    if runner == "pytest":
        py, actions = ensure_target_env(sandbox)
        # lexical, not resolved: a symlinked .venv must not be reported (or rejected) by its target
        python_used = py.relative_to(sandbox.root).as_posix()
    result = sandbox.run(argv, timeout=float(timeout_seconds))
    timeout_message: str | None = None
    stderr = result.stderr
    if result.timed_out:
        timeout_message = (
            f"run_tests killed after {timeout_seconds}s (timeout_seconds={timeout_seconds}). "
            "The suite did not finish: look for a hang or an infinite loop, run a smaller "
            f"test_target, or raise timeout_seconds (max {MAX_TEST_TIMEOUT_SECONDS})."
        )
        stderr = f"[TIMEOUT] {timeout_message}\n{stderr}"
    return json.dumps(
        {
            "runner": runner,
            "python": python_used,
            "env_actions": actions,
            "timeout_seconds": timeout_seconds,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "timeout_message": timeout_message,
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(stderr),
        },
        indent=2,
    )


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    sb = ctx.sandbox
    return [
        ToolSpec(
            "run_tests",
            "Run the target project's tests (pytest in the target's own .venv — created and "
            "requirements.txt installed automatically — or gradlew / npm) for a repository-relative "
            "target; returns exit code, stdout and stderr as JSON. Killed after timeout_seconds "
            f"(default {DEFAULT_TEST_TIMEOUT_SECONDS}); a timeout is reported in timeout_message.",
            RunTestsArgs,
            lambda a: run_tests(sb, a.test_target, a.timeout_seconds),  # type: ignore[attr-defined]
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m mcp_server.tools.test_tools --target-dir <path>`` provisions the target venv."""
    ap = argparse.ArgumentParser(prog="test_tools", description="Provision a target project's .venv (ADR-009).")
    ap.add_argument("--target-dir", type=Path, required=True)
    ns = ap.parse_args(argv)
    try:
        py, actions = ensure_target_env(Sandbox(ns.target_dir))
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"python: {py}")
    for a in actions or ["environment up to date"]:
        print(f"- {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
