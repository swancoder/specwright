"""Git tools exposed over MCP (stub, ADR-004 §3)."""

from __future__ import annotations

from pydantic import Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolSpec

NOT_IMPLEMENTED: str = "Not implemented yet"


class GitCommitFeatureArgs(ToolArgs):
    message: str = Field(description="Conventional Commits message.")
    spec_id: str = Field(description="Feature specification identifier recorded in the commit.")


def git_commit_feature(message: str, spec_id: str) -> str:
    """Stage and commit the current changes, tagging the commit with a spec ID.

    Args:
        message: Human-readable commit message.
        spec_id: Feature specification identifier appended to the commit trailer.
    """
    return NOT_IMPLEMENTED


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    return [
        ToolSpec(
            "git_commit_feature",
            "Stage and commit current changes in the target repo, tagged with a spec ID.",
            GitCommitFeatureArgs,
            lambda a: git_commit_feature(a.message, a.spec_id),  # type: ignore[attr-defined]
        ),
    ]
