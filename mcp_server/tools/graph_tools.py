"""Knowledge-graph query tools exposed over MCP."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CoupledFile(BaseModel):
    """A file that historically changes together with the queried file."""

    filepath: str
    coupling_percent: int


class TemporalCouplingResult(BaseModel):
    """Result of a temporal coupling query."""

    filepath: str
    threshold_percent: int
    coupled_files: list[CoupledFile] = Field(default_factory=list)


def query_temporal_coupling(filepath: str, threshold_percent: int) -> TemporalCouplingResult:
    """Return files that co-change with the given file above a coupling threshold.

    Args:
        filepath: Path relative to the target codebase root.
        threshold_percent: Minimum co-change percentage (0-100) to include a file.
    """
    raise NotImplementedError
