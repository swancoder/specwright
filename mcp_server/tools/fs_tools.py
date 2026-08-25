"""Filesystem and specification tools exposed over MCP."""

from __future__ import annotations

from pydantic import BaseModel


class PatchResult(BaseModel):
    """Outcome of applying a search/replace patch to a file."""

    filepath: str
    applied: bool
    message: str = ""


def read_constitution() -> str:
    """Return the contents of the target project's constitution document.

    The constitution holds global architectural rules and hard constraints
    that the agent must respect in every action.
    """
    raise NotImplementedError


def read_specification(spec_id: str) -> str:
    """Return the specification documents for the given feature identifier.

    Args:
        spec_id: Identifier of the feature spec directory (e.g. "001-login").
    """
    raise NotImplementedError


def fs_read(filepath: str) -> str:
    """Read and return the contents of a file inside the target codebase.

    Args:
        filepath: Path relative to the target codebase root.
    """
    raise NotImplementedError


def fs_apply_patch(filepath: str, search_string: str, replace_string: str) -> PatchResult:
    """Replace an exact string occurrence in a file with new content.

    Args:
        filepath: Path relative to the target codebase root.
        search_string: Exact text that must be present in the file.
        replace_string: Text that will replace the matched occurrence.
    """
    raise NotImplementedError
