"""Runtime context shared by all tool handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from knowledge_graph.indexer import DatabaseManager
from mcp_server.core.sandbox import Sandbox

#: Location of the knowledge graph relative to the target project root.
DEFAULT_DB_RELPATH: str = ".agent-harness/graph.db"


@dataclass(slots=True)
class HarnessContext:
    """Everything a tool needs to act on the target project.

    Attributes:
        target_dir: Root of the target codebase the agent is allowed to touch.
        db: Open knowledge-graph database for this target.
        sandbox: Path/process confinement for ``target_dir`` (ADR-001).
    """

    target_dir: Path
    db: DatabaseManager
    sandbox: Sandbox = field(init=False)

    def __post_init__(self) -> None:
        self.sandbox = Sandbox(self.target_dir)

    @classmethod
    def for_target(cls, target_dir: Path | str, db_path: Path | str | None = None) -> HarnessContext:
        """Build a context for ``target_dir``, opening (or creating) its graph DB."""
        root = Path(target_dir).resolve()
        db = DatabaseManager(db_path if db_path is not None else root / DEFAULT_DB_RELPATH)
        db.connect()
        return cls(target_dir=root, db=db)

    def close(self) -> None:
        """Release the database connection."""
        self.db.close()
