"""Knowledge-graph query tools exposed over MCP (wired to Stage 3, ADR-004 §3)."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from knowledge_graph.indexer import DatabaseManager
from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolSpec


class QueryTemporalCouplingArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root.")
    threshold_percent: int = Field(
        default=30, ge=0, le=100, description="Minimum Jaccard coupling, as a percentage (0-100)."
    )


class CoupledFile(BaseModel):
    """A file that historically changes together with the queried file."""

    filepath: str
    coupling_percent: float


class TemporalCouplingResult(BaseModel):
    """Result of a temporal coupling query."""

    filepath: str
    threshold_percent: int
    coupled_files: list[CoupledFile] = Field(default_factory=list)


def query_temporal_coupling(db: DatabaseManager, filepath: str, threshold_percent: int) -> TemporalCouplingResult:
    """Return files that co-change with the given file above a coupling threshold.

    Args:
        db: Knowledge-graph database built by ``knowledge_graph.indexer``.
        filepath: Path relative to the target codebase root.
        threshold_percent: Minimum Jaccard index (0-100) to include a file.
    """
    rows = db.query_coupled_files(filepath, threshold_percent / 100)
    return TemporalCouplingResult(
        filepath=filepath,
        threshold_percent=threshold_percent,
        coupled_files=[CoupledFile(filepath=p, coupling_percent=round(j * 100, 2)) for p, j in rows],
    )


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""

    def _handler(a: ToolArgs) -> str:
        args = a  # QueryTemporalCouplingArgs
        result = query_temporal_coupling(ctx.db, args.filepath, args.threshold_percent)  # type: ignore[attr-defined]
        return json.dumps(result.model_dump(), indent=2)

    return [
        ToolSpec(
            "query_temporal_coupling",
            "Files that historically change together with the given file "
            "(Jaccard temporal coupling from git history), above a percentage threshold.",
            QueryTemporalCouplingArgs,
            _handler,
        ),
    ]
