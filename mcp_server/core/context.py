"""Runtime context shared by all tool handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

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
        write_scopes: Repo-relative directory prefixes the agent may write under; empty
            means unrestricted (ADR-011).
    """

    target_dir: Path
    db: DatabaseManager
    write_scopes: tuple[str, ...] = ()
    sandbox: Sandbox = field(init=False)

    def __post_init__(self) -> None:
        self.sandbox = Sandbox(self.target_dir)
        self.write_scopes = tuple(normalize_scope(s) for s in self.write_scopes if s.strip())

    @classmethod
    def for_target(
        cls,
        target_dir: Path | str,
        db_path: Path | str | None = None,
        write_scopes: tuple[str, ...] | list[str] = (),
    ) -> HarnessContext:
        """Build a context for ``target_dir``, opening (or creating) its graph DB."""
        root = Path(target_dir).resolve()
        db = DatabaseManager(db_path if db_path is not None else root / DEFAULT_DB_RELPATH)
        db.connect()
        return cls(target_dir=root, db=db, write_scopes=tuple(write_scopes))

    def allows_write(self, relpath: str) -> bool:
        """True if ``relpath`` (POSIX, repo-relative) is inside a write scope (or none are set)."""
        if not self.write_scopes:
            return True
        parts = PurePosixPath(relpath).parts
        return any(parts[: len(PurePosixPath(s).parts)] == PurePosixPath(s).parts for s in self.write_scopes)

    def close(self) -> None:
        """Release the database connection."""
        self.db.close()


def normalize_scope(scope: str) -> str:
    """``specs/`` → ``specs``; rejects absolute/``..`` scopes."""
    s = scope.strip().replace("\\", "/").strip("/")
    if not s or s.startswith("/") or ".." in PurePosixPath(s).parts:
        raise ValueError(f"invalid write scope: {scope!r}")
    return s
