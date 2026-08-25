"""Knowledge graph indexer: SQLite storage and noise-reduction filtering.

This module implements the storage layer (ADR-002 §1) and the ``.graphignore``
filter (ADR-002 §3). Git history parsing is intentionally not implemented yet.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from knowledge_graph.schemas.graph_models import CodeGraph

#: Commits touching more files than this are treated as mass refactorings and
#: skipped by the (future) git parser. See ADR-002 §3.
MASS_REFACTOR_FILE_LIMIT: Final[int] = 50

#: Name of the ignore file expected at the target project root.
GRAPHIGNORE_FILENAME: Final[str] = ".graphignore"

SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS commits (
    hash TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    spec_id TEXT,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    total_changes INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS commit_files (
    commit_hash TEXT REFERENCES commits(hash),
    file_id INTEGER REFERENCES files(id),
    PRIMARY KEY (commit_hash, file_id)
);

CREATE TABLE IF NOT EXISTS file_pairs (
    file_a_id INTEGER REFERENCES files(id),
    file_b_id INTEGER REFERENCES files(id),
    co_commits INTEGER DEFAULT 0,
    PRIMARY KEY (file_a_id, file_b_id)
);
"""

TABLE_NAMES: Final[tuple[str, ...]] = ("commits", "files", "commit_files", "file_pairs")


class DatabaseManager:
    """Owns the SQLite connection for the knowledge graph.

    Usage::

        with DatabaseManager(Path("graph.db")) as db:
            db.connection.execute("SELECT COUNT(*) FROM commits")

    The schema from ADR-002 is created on first connect if it does not exist.
    """

    def __init__(self, db_path: Path | str) -> None:
        """Store the database location without opening a connection yet.

        Args:
            db_path: Filesystem path of the SQLite file, or ``":memory:"``.
        """
        self.db_path: str = str(db_path)
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open connection, opening it on first access."""
        if self._connection is None:
            self.connect()
        assert self._connection is not None
        return self._connection

    def connect(self) -> sqlite3.Connection:
        """Open the connection, enable foreign keys, and initialize the schema."""
        if self._connection is not None:
            return self._connection
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._connection = conn
        self.initialize_schema()
        return conn

    def initialize_schema(self) -> None:
        """Create all ADR-002 tables if they are missing. Idempotent."""
        with self.connection:
            self.connection.executescript(SCHEMA_SQL)

    def table_names(self) -> list[str]:
        """Return the names of user tables currently present in the database."""
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]

    def close(self) -> None:
        """Close the connection if it is open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class GraphIgnoreFilter:
    """Excludes files matching ``.graphignore`` glob patterns (ADR-002 §3).

    Pattern semantics:

    * Blank lines and lines starting with ``#`` are ignored.
    * A pattern is matched with :func:`fnmatch.fnmatch` against the full
      repository-relative path (POSIX separators) and against the basename,
      so both ``build/*.js`` and ``*.lock`` behave as expected.
    * A pattern ending in ``/`` matches everything below that directory.
    """

    def __init__(self, graphignore_path: Path | str | None) -> None:
        """Load patterns from the given file; a missing file yields no patterns.

        Args:
            graphignore_path: Path to the ``.graphignore`` file, or ``None``.
        """
        self.patterns: list[str] = []
        if graphignore_path is not None:
            self.patterns = self._load(Path(graphignore_path))

    @classmethod
    def from_patterns(cls, patterns: list[str]) -> GraphIgnoreFilter:
        """Build a filter directly from a list of patterns (useful for tests)."""
        instance = cls(None)
        instance.patterns = [cls._normalize(p) for p in patterns if cls._is_pattern_line(p)]
        return instance

    @classmethod
    def for_target(cls, target_root: Path | str) -> GraphIgnoreFilter:
        """Build a filter from ``<target_root>/.graphignore``."""
        return cls(Path(target_root) / GRAPHIGNORE_FILENAME)

    def is_ignored(self, filepath: str) -> bool:
        """Return True if ``filepath`` matches any loaded pattern.

        Args:
            filepath: Repository-relative path; ``\\`` separators are normalized.
        """
        path = filepath.replace("\\", "/").lstrip("./")
        name = path.rsplit("/", 1)[-1]
        for pattern in self.patterns:
            if pattern.endswith("/"):
                prefix = pattern.rstrip("/")
                if path == prefix or path.startswith(prefix + "/") or fnmatch.fnmatch(path, prefix + "/*"):
                    return True
                continue
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern):
                return True
        return False

    @staticmethod
    def _is_pattern_line(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and not stripped.startswith("#")

    @staticmethod
    def _normalize(line: str) -> str:
        pattern = line.strip().replace("\\", "/")
        return pattern[2:] if pattern.startswith("./") else pattern

    @classmethod
    def _load(cls, path: Path) -> list[str]:
        if not path.is_file():
            return []
        with path.open(encoding="utf-8") as fh:
            return [cls._normalize(line) for line in fh if cls._is_pattern_line(line)]


class Indexer:
    """Scans a repository and produces a CodeGraph (git parsing not yet implemented)."""

    def __init__(self, repo_root: Path, db: DatabaseManager, ignore: GraphIgnoreFilter | None = None) -> None:
        """Initialize the indexer for the given repository root.

        Args:
            repo_root: Root of the target project's git repository.
            db: Database manager holding the knowledge graph.
            ignore: Noise filter; defaults to ``<repo_root>/.graphignore``.
        """
        self.repo_root = repo_root
        self.db = db
        self.ignore = ignore if ignore is not None else GraphIgnoreFilter.for_target(repo_root)

    def index_files(self) -> None:
        """Walk the source tree and register file nodes."""
        raise NotImplementedError

    def index_history(self) -> None:
        """Parse git history to derive temporal coupling edges (ADR-003)."""
        raise NotImplementedError

    def build(self) -> CodeGraph:
        """Run all indexing passes and return the resulting graph."""
        raise NotImplementedError
