"""Test execution inside the target sandbox (ADR-001 §3)."""

from __future__ import annotations

import json
import sys
from typing import Final

from pydantic import Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolSpec
from mcp_server.core.sandbox import Sandbox

TEST_TIMEOUT_SECONDS: Final[float] = 600.0
MAX_OUTPUT_CHARS: Final[int] = 20_000


class RunTestsArgs(ToolArgs):
    test_target: str = Field(
        min_length=1,
        description="Repository-relative test path, optionally with a '::' selector (e.g. tests/test_x.py::test_y).",
    )


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n[... truncated {len(text) - MAX_OUTPUT_CHARS} chars]"


def detect_runner(sandbox: Sandbox, test_target: str) -> tuple[str, list[str]]:
    """Pick the test runner for the target project and build its argv."""
    if (sandbox.root / "gradlew").is_file():
        return "gradle", ["./gradlew", "test", "--tests", test_target]
    if (sandbox.root / "package.json").is_file():
        return "npm", ["npm", "test", "--", test_target]
    return "pytest", [sys.executable, "-m", "pytest", test_target, "-q"]


def run_tests(sandbox: Sandbox, test_target: str) -> str:
    """Run the target's test suite (or a subset) and return a JSON report.

    Args:
        test_target: Repository-relative path, optionally followed by ``::selector``.
            The path part must exist inside the sandbox.

    Returns:
        JSON: ``{"runner", "exit_code", "timed_out", "stdout", "stderr"}``.
    """
    path_part = test_target.split("::", 1)[0]
    resolved = sandbox.resolve(path_part, must_exist=True)
    normalized = sandbox.relative(resolved) + test_target[len(path_part):]
    runner, argv = detect_runner(sandbox, normalized)
    result = sandbox.run(argv, timeout=TEST_TIMEOUT_SECONDS)
    return json.dumps(
        {
            "runner": runner,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
        },
        indent=2,
    )


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    sb = ctx.sandbox
    return [
        ToolSpec(
            "run_tests",
            "Run the target project's tests (pytest / gradlew / npm auto-detected) for a "
            "repository-relative target; returns exit code, stdout and stderr as JSON.",
            RunTestsArgs,
            lambda a: run_tests(sb, a.test_target),  # type: ignore[attr-defined]
        ),
    ]
