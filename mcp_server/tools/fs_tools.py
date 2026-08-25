"""Filesystem and specification tools exposed over MCP (stubs, ADR-004 §3)."""

from __future__ import annotations

from pydantic import Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolSpec

NOT_IMPLEMENTED: str = "Not implemented yet"


class ReadConstitutionArgs(ToolArgs):
    """No arguments."""


class ReadSpecificationArgs(ToolArgs):
    spec_id: str = Field(description="Feature specification identifier, e.g. '001-login'.")


class FsReadArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root.")


class FsApplyPatchArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root.")
    search_string: str = Field(description="Exact text that must be present in the file.")
    replace_string: str = Field(description="Text that replaces the matched occurrence.")


def read_constitution() -> str:
    """Return the contents of the target project's constitution document.

    The constitution holds global architectural rules and hard constraints
    that the agent must respect in every action.
    """
    return NOT_IMPLEMENTED


def read_specification(spec_id: str) -> str:
    """Return the specification documents for the given feature identifier.

    Args:
        spec_id: Identifier of the feature spec directory (e.g. "001-login").
    """
    return NOT_IMPLEMENTED


def fs_read(filepath: str) -> str:
    """Read and return the contents of a file inside the target codebase.

    Args:
        filepath: Path relative to the target codebase root.
    """
    return NOT_IMPLEMENTED


def fs_apply_patch(filepath: str, search_string: str, replace_string: str) -> str:
    """Replace an exact string occurrence in a file with new content.

    Args:
        filepath: Path relative to the target codebase root.
        search_string: Exact text that must be present in the file.
        replace_string: Text that will replace the matched occurrence.
    """
    return NOT_IMPLEMENTED


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    return [
        ToolSpec(
            "read_constitution",
            "Return the target project's constitution: global architectural rules and hard constraints.",
            ReadConstitutionArgs,
            lambda a: read_constitution(),
        ),
        ToolSpec(
            "read_specification",
            "Return the intent/spec/plan documents for a feature specification ID.",
            ReadSpecificationArgs,
            lambda a: read_specification(a.spec_id),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_read",
            "Read a file inside the target codebase.",
            FsReadArgs,
            lambda a: fs_read(a.filepath),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_apply_patch",
            "Replace an exact string occurrence in a target file with new content.",
            FsApplyPatchArgs,
            lambda a: fs_apply_patch(a.filepath, a.search_string, a.replace_string),  # type: ignore[attr-defined]
        ),
    ]
