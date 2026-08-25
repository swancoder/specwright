"""Tool package: each module exposes ``build_tools(ctx) -> list[ToolSpec]``."""

from __future__ import annotations

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolSpec
from mcp_server.tools import fs_tools, git_tools, graph_tools, test_tools

def build_all_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Collect the tool specs from every tools module."""
    return [
        *fs_tools.build_tools(ctx),
        *test_tools.build_tools(ctx),
        *git_tools.build_tools(ctx),
        *graph_tools.build_tools(ctx),
    ]
