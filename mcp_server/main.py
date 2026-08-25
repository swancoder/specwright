"""Entrypoint that initializes and serves the harness MCP server."""

from __future__ import annotations

from mcp_server.core.schemas import ToolDefinition
from mcp_server.tools import fs_tools, git_tools, graph_tools, test_tools


def register_tools() -> list[ToolDefinition]:
    """Build the list of tool definitions advertised to MCP clients."""
    raise NotImplementedError


def create_server() -> object:
    """Construct the MCP server instance with all tools registered."""
    raise NotImplementedError


def main() -> None:
    """Start the MCP server and block until shutdown."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
