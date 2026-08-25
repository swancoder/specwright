"""Test execution tools exposed over MCP (stub, ADR-004 §3)."""

from __future__ import annotations

from pydantic import Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolSpec

NOT_IMPLEMENTED: str = "Not implemented yet"


class RunTestsArgs(ToolArgs):
    test_target: str = Field(description="Test path, module, or selector expression to execute.")


def run_tests(test_target: str) -> str:
    """Run the test suite (or a subset) in the target codebase and report results.

    Args:
        test_target: Test path, module, or selector expression to execute.
    """
    return NOT_IMPLEMENTED


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    return [
        ToolSpec(
            "run_tests",
            "Run the target project's test suite (or a subset) and report results.",
            RunTestsArgs,
            lambda a: run_tests(a.test_target),  # type: ignore[attr-defined]
        ),
    ]
