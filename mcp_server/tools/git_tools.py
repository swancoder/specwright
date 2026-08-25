"""Git tools exposed over MCP."""

from __future__ import annotations

from pydantic import BaseModel


class CommitResult(BaseModel):
    """Outcome of a commit operation."""

    commit_hash: str
    message: str


def git_commit_feature(message: str, spec_id: str) -> CommitResult:
    """Stage and commit the current changes, tagging the commit with a spec ID.

    Args:
        message: Human-readable commit message.
        spec_id: Feature specification identifier appended to the commit trailer.
    """
    raise NotImplementedError
