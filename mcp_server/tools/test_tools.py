"""Test execution tools exposed over MCP."""

from __future__ import annotations

from pydantic import BaseModel


class TestReport(BaseModel):
    """Summary of a test run."""

    target: str
    passed: int = 0
    failed: int = 0
    output: str = ""


def run_tests(test_target: str) -> TestReport:
    """Run the test suite (or a subset) in the target codebase and report results.

    Args:
        test_target: Test path, module, or selector expression to execute.
    """
    raise NotImplementedError
