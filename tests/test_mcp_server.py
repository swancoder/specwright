"""Tests for the MCP server wiring (ADR-004)."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from mcp import types
from mcp.server import Server

from knowledge_graph.indexer import DatabaseManager
from mcp_server.core.context import HarnessContext
from mcp_server.main import build_registry, build_server, parse_args
from mcp_server.tools.aliases import ALIAS_MAP

EXPECTED_TOOLS = {
    "read_constitution",
    "read_specification",
    "fs_read",
    "fs_list",
    "fs_write",
    "fs_apply_patch",
    "run_tests",
    "git_commit_feature",
    "query_temporal_coupling",
}


class RecordingDB(DatabaseManager):
    """DatabaseManager whose Jaccard query is recorded and canned."""

    def __init__(self) -> None:
        super().__init__(":memory:")
        self.calls: list[tuple[str, float]] = []

    def query_coupled_files(self, path: str, min_jaccard: float) -> list[tuple[str, float]]:
        self.calls.append((path, min_jaccard))
        return [("src/b.py", 0.5), ("src/c.py", 0.25)]


@pytest.fixture
def ctx(tmp_path: Path) -> HarnessContext:
    return HarnessContext(target_dir=tmp_path, db=RecordingDB())


def test_all_seven_tools_registered_with_schemas(ctx: HarnessContext) -> None:
    registry = build_registry(ctx)
    assert EXPECTED_TOOLS <= set(registry.names())  # canonical tools present
    assert set(ALIAS_MAP) <= set(registry.names())  # ADR-014 §3 aliases registered
    assert registry.get("fs_find").args_model is registry.get("fs_list").args_model  # alias delegates
    for tool in registry.list_tools():
        assert isinstance(tool, types.Tool)
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema.get("additionalProperties") is False  # strict models
    schema = registry.get("fs_apply_patch").to_mcp_tool().input_schema
    assert set(schema["required"]) == {"filepath", "search_string", "replace_string"}


def test_query_temporal_coupling_calls_database_manager(ctx: HarnessContext) -> None:
    registry = build_registry(ctx)
    text = registry.call("query_temporal_coupling", {"filepath": "src/a.py", "threshold_percent": 30})
    assert ctx.db.calls == [("src/a.py", 0.3)]  # type: ignore[attr-defined]
    payload = json.loads(text)
    assert payload["filepath"] == "src/a.py"
    assert payload["coupled_files"] == [
        {"filepath": "src/b.py", "coupling_percent": 50.0},
        {"filepath": "src/c.py", "coupling_percent": 25.0},
    ]


def test_query_temporal_coupling_default_threshold(ctx: HarnessContext) -> None:
    build_registry(ctx).call("query_temporal_coupling", {"filepath": "x.py"})
    assert ctx.db.calls == [("x.py", 0.3)]  # type: ignore[attr-defined]


def test_sandboxed_tools_report_errors_via_registry(ctx: HarnessContext) -> None:
    registry = build_registry(ctx)
    res = registry.call_tool_result("fs_read", {"filepath": "../etc/passwd"})
    assert res.is_error and "fs_read failed" in res.content[0].text  # type: ignore[union-attr]
    (ctx.target_dir / "hello.txt").write_text("hi\n")
    assert registry.call("fs_read", {"filepath": "hello.txt"}) == "hi\n"
    res = registry.call_tool_result("read_constitution", {})
    assert res.is_error  # no constitution in an empty target


def test_invalid_arguments_are_reported_not_raised(ctx: HarnessContext) -> None:
    registry = build_registry(ctx)
    bad = registry.call_tool_result("query_temporal_coupling", {"filepath": "a", "threshold_percent": 150})
    assert bad.is_error and "Invalid arguments" in bad.content[0].text  # type: ignore[union-attr]
    extra = registry.call_tool_result("fs_read", {"filepath": "a", "bogus": 1})
    assert extra.is_error
    unknown = registry.call_tool_result("nope", {})
    assert unknown.is_error and "Unknown tool" in unknown.content[0].text  # type: ignore[union-attr]


def test_mcp_server_handlers_expose_tools(ctx: HarnessContext) -> None:
    server = build_server(build_registry(ctx))
    assert isinstance(server, Server)
    list_entry = server.get_request_handler("tools/list")
    call_entry = server.get_request_handler("tools/call")
    assert list_entry is not None and call_entry is not None

    async def run() -> tuple[types.ListToolsResult, types.CallToolResult]:
        listed = await list_entry.handler(None, None)
        called = await call_entry.handler(
            None, types.CallToolRequestParams(name="query_temporal_coupling", arguments={"filepath": "src/a.py"})
        )
        return listed, called

    listed, called = anyio.run(run)
    assert EXPECTED_TOOLS <= {t.name for t in listed.tools}
    assert not called.is_error
    assert json.loads(called.content[0].text)["filepath"] == "src/a.py"  # type: ignore[union-attr]


def test_real_db_end_to_end(tmp_path: Path) -> None:
    ctx = HarnessContext.for_target(tmp_path)
    try:
        assert (tmp_path / ".agent-harness" / "graph.db").exists()
        text = build_registry(ctx).call("query_temporal_coupling", {"filepath": "nothing.py", "threshold_percent": 0})
        assert json.loads(text)["coupled_files"] == []
    finally:
        ctx.close()


def test_parse_args_requires_target_dir() -> None:
    assert parse_args(["--target-dir", "/tmp/x"]).target_dir == Path("/tmp/x")
    with pytest.raises(SystemExit):
        parse_args([])
