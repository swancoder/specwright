"""Pydantic models for knowledge graph nodes and edges."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileNode(BaseModel):
    """A source file in the target codebase."""

    filepath: str
    language: str = ""
    commit_count: int = 0


class CouplingEdge(BaseModel):
    """A temporal coupling relationship between two files."""

    source: str
    target: str
    co_change_count: int = 0
    coupling_percent: int = 0


class CodeGraph(BaseModel):
    """Container for all nodes and edges of the knowledge graph."""

    nodes: list[FileNode] = Field(default_factory=list)
    edges: list[CouplingEdge] = Field(default_factory=list)
