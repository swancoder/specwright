"""Entrypoint that initializes and serves the harness MCP server over stdio (ADR-004)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolRegistry
from mcp_server.tools import build_all_tools

SERVER_NAME: str = "agent-harness"
SERVER_VERSION: str = "0.1.0"


def build_registry(ctx: HarnessContext) -> ToolRegistry:
    """Register every harness tool against the given context."""
    return ToolRegistry(build_all_tools(ctx))


def build_server(registry: ToolRegistry) -> Server:
    """Wrap a registry into an ``mcp.server.Server`` with list/call handlers."""

    async def on_list_tools(ctx: object, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=registry.list_tools())

    async def on_call_tool(ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
        return registry.call_tool_result(params.name, params.arguments)

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions="Tools for spec-driven development on the configured target project.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve(target_dir: Path, db_path: Path | None = None, write_scopes: tuple[str, ...] = (), optional_tools: tuple[str, ...] = ()) -> None:
    """Run the MCP server on stdio until the client disconnects."""
    ctx = HarnessContext.for_target(target_dir, db_path, write_scopes, frozenset(optional_tools))
    try:
        server = build_server(build_registry(ctx))
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        ctx.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments (``--target-dir`` is required)."""
    ap = argparse.ArgumentParser(prog="mcp_server", description="Agent Harness MCP server (stdio).")
    ap.add_argument("--target-dir", type=Path, required=True, help="root of the target codebase")
    ap.add_argument("--db", type=Path, default=None, help="knowledge-graph SQLite file (default: <target>/.agent-harness/graph.db)")
    ap.add_argument(
        "--write-scope", action="append", default=[], metavar="PREFIX",
        help="repo-relative directory the agent may write under (repeatable; default: unrestricted) [ADR-011]",
    )
    ap.add_argument(
        "--enable-tool", action="append", default=[], metavar="NAME",
        help="register a capability-gated tool (e.g. mark_spec_complete); repeatable [ADR-012]",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start the MCP server and block until shutdown."""
    args = parse_args(argv)
    if not args.target_dir.is_dir():
        print(f"error: --target-dir {args.target_dir} is not a directory", file=sys.stderr)
        return 2
    anyio.run(serve, args.target_dir, args.db, tuple(args.write_scope), tuple(args.enable_tool))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
