"""Knowledge graph indexer: SQLite storage, noise filtering, and git ingestion.

* ADR-002: schema (``DatabaseManager``) and ``.graphignore`` (``GraphIgnoreFilter``).
* ADR-003: incremental git parsing (``GitParser``), self-healing rebuilds,
  transactional aggregation (``Indexer``) and the Jaccard coupling query.
"""

from __future__ import annotations

import argparse
import fnmatch
import itertools
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from knowledge_graph.schemas.graph_models import CodeGraph

#: Commits touching more files (after .graphignore filtering) than this are
#: treated as mass refactorings and skipped. See ADR-002 §3 / ADR-003 §4.
MASS_REFACTOR_FILE_LIMIT: Final[int] = 50

#: Name of the ignore file expected at the target project root.
GRAPHIGNORE_FILENAME: Final[str] = ".graphignore"

#: Regex used to extract a spec / ADR identifier from a commit message (ADR-003 §3).
SPEC_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[([a-zA-Z0-9\-]+)\]")

RS: Final[str] = "\x1e"  # record separator: one per commit
US: Final[str] = "\x1f"  # unit separator: hash / timestamp / body
GIT_LOG_FORMAT: Final[str] = f"--pretty=format:{RS}%H{US}%ct{US}%B"

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

#: ADR-003 §5. ``GROUP BY f.id`` added: SQLite rejects HAVING on non-aggregate
#: queries; each (target, neighbour) row is already unique so results are unchanged.
JACCARD_QUERY: Final[str] = """
SELECT
    f.path,
    CAST(fp.co_commits AS FLOAT) / (f_target.total_changes + f.total_changes - fp.co_commits) as jaccard
FROM file_pairs fp
JOIN files f_target ON (fp.file_a_id = f_target.id OR fp.file_b_id = f_target.id)
JOIN files f ON (
    (fp.file_a_id = f.id AND fp.file_b_id = f_target.id) OR
    (fp.file_b_id = f.id AND fp.file_a_id = f_target.id)
)
WHERE f_target.path = ?
GROUP BY f.id
HAVING jaccard >= ?
ORDER BY jaccard DESC;
"""


# --------------------------------------------------------------------------- storage


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

    def latest_commit_hash(self) -> str | None:
        """Return the hash of the most recent indexed commit, or None if empty (ADR-003 §2).

        Ties on ``timestamp`` (commits within the same second) are broken by insertion
        order, which is oldest-first, so the last inserted row wins.
        """
        row = self.connection.execute(
            "SELECT hash FROM commits ORDER BY timestamp DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return None if row is None else str(row["hash"])

    def wipe(self) -> None:
        """Delete all rows from the four graph tables (self-healing, ADR-003 §2)."""
        with self.connection:
            for table in ("file_pairs", "commit_files", "files", "commits"):
                self.connection.execute(f"DELETE FROM {table}")
            self.connection.execute("DELETE FROM sqlite_sequence WHERE name = 'files'")

    def query_coupled_files(self, path: str, min_jaccard: float) -> list[tuple[str, float]]:
        """Return ``(path, jaccard)`` for files coupled to ``path`` (ADR-003 §5).

        Args:
            path: Repository-relative path of the target file.
            min_jaccard: Inclusive lower bound on the Jaccard index (0.0–1.0).
        """
        rows = self.connection.execute(JACCARD_QUERY, (path, min_jaccard)).fetchall()
        return [(str(row["path"]), float(row["jaccard"])) for row in rows]

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


# --------------------------------------------------------------------------- noise filter


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


# --------------------------------------------------------------------------- git parsing


@dataclass(slots=True)
class ParsedCommit:
    """One commit as read from ``git log`` (ADR-003 §1)."""

    hash: str
    timestamp: int
    message: str
    files: list[str] = field(default_factory=list)
    spec_id: str | None = None


class GitLogError(RuntimeError):
    """Raised when ``git log`` cannot be executed or exits non-zero."""


class GitParser:
    """Runs ``git log`` via :mod:`subprocess` and parses its output (ADR-003 §1–§3)."""

    def __init__(self, repo_root: Path | str) -> None:
        """Bind the parser to a repository root.

        Args:
            repo_root: Directory containing the target project's ``.git``.
        """
        self.repo_root = Path(repo_root)

    @staticmethod
    def build_command(since_hash: str | None) -> list[str]:
        """Return the ``git log`` argv for a full (``--all``) or incremental run."""
        range_args = ["--all"] if since_hash is None else [f"{since_hash}..HEAD"]
        return ["git", "log", *range_args, GIT_LOG_FORMAT, "--name-only"]

    def run(self, since_hash: str | None) -> list[ParsedCommit]:
        """Execute ``git log`` and return parsed commits, oldest first.

        Args:
            since_hash: Last indexed commit hash, or None for a full history read.

        Raises:
            GitLogError: if git is unavailable or exits with a non-zero status
                (e.g. the hash no longer exists after a rebase / force push).
        """
        cmd = self.build_command(since_hash)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            raise GitLogError(f"{' '.join(cmd)} failed: {exc}") from exc
        return self.parse(result.stdout)

    @staticmethod
    def extract_spec_id(message: str) -> str | None:
        """Return the first ``[spec-id]`` token in ``message``, if any (ADR-003 §3)."""
        match = SPEC_ID_PATTERN.search(message)
        return match.group(1) if match else None

    @classmethod
    def parse(cls, output: str) -> list[ParsedCommit]:
        """Parse raw ``git log`` output into commits, oldest first.

        Each record is ``RS hash US timestamp US body [blank line] file...``.
        The ``--name-only`` file list follows the body after a blank line; commits
        without changed files (e.g. merges) have no such block.
        """
        commits: list[ParsedCommit] = []
        for record in output.split(RS):
            if not record.strip():
                continue
            parts = record.split(US, 2)
            if len(parts) != 3:
                continue
            commit_hash, raw_ts, rest = parts
            body, _, file_block = rest.rstrip("\n").rpartition("\n\n")
            if not _:  # no blank line: whole remainder is the message, no files
                body, file_block = file_block, ""
            files = [line.strip() for line in file_block.splitlines() if line.strip()]
            message = body.strip()
            commits.append(
                ParsedCommit(
                    hash=commit_hash.strip(),
                    timestamp=int(raw_ts.strip()),
                    message=message,
                    files=files,
                    spec_id=cls.extract_spec_id(message),
                )
            )
        commits.reverse()  # git log emits newest first
        return commits


# --------------------------------------------------------------------------- orchestration


@dataclass(slots=True)
class IndexReport:
    """Outcome of one :meth:`Indexer.index_history` run."""

    full_rebuild: bool
    commits_seen: int = 0
    commits_ingested: int = 0
    commits_skipped_mass_refactor: int = 0
    commits_already_indexed: int = 0


class Indexer:
    """Orchestrates git parsing, filtering, and transactional aggregation (ADR-003)."""

    def __init__(
        self,
        repo_root: Path | str,
        db: DatabaseManager,
        ignore: GraphIgnoreFilter | None = None,
        parser: GitParser | None = None,
        mass_refactor_limit: int = MASS_REFACTOR_FILE_LIMIT,
    ) -> None:
        """Initialize the indexer for the given repository root.

        Args:
            repo_root: Root of the target project's git repository.
            db: Database manager holding the knowledge graph.
            ignore: Noise filter; defaults to ``<repo_root>/.graphignore``.
            parser: Git parser; defaults to ``GitParser(repo_root)``.
            mass_refactor_limit: Max files per commit before it is skipped.
        """
        self.repo_root = Path(repo_root)
        self.db = db
        self.ignore = ignore if ignore is not None else GraphIgnoreFilter.for_target(self.repo_root)
        self.parser = parser if parser is not None else GitParser(self.repo_root)
        self.mass_refactor_limit = mass_refactor_limit

    def index_files(self) -> None:
        """Walk the source tree and register file nodes (not part of ADR-003)."""
        raise NotImplementedError

    def index_history(self) -> IndexReport:
        """Incrementally ingest new commits, self-healing on history rewrites (ADR-003 §2)."""
        since = self.db.latest_commit_hash()
        full_rebuild = since is None
        try:
            commits = self.parser.run(since)
        except GitLogError:
            if since is None:
                raise
            self.db.wipe()
            full_rebuild = True
            commits = self.parser.run(None)

        report = IndexReport(full_rebuild=full_rebuild, commits_seen=len(commits))
        for commit in commits:
            outcome = self.ingest_commit(commit)
            if outcome == "ingested":
                report.commits_ingested += 1
            elif outcome == "mass_refactor":
                report.commits_skipped_mass_refactor += 1
            else:
                report.commits_already_indexed += 1
        return report

    def ingest_commit(self, commit: ParsedCommit) -> str:
        """Apply one commit to the graph in a single transaction (ADR-003 §4).

        Returns:
            ``"ingested"``, ``"mass_refactor"`` (skipped), or ``"duplicate"``.
        """
        files = sorted({f for f in commit.files if not self.ignore.is_ignored(f)})
        if len(files) > self.mass_refactor_limit:
            return "mass_refactor"

        conn = self.db.connection
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO commits (hash, timestamp, spec_id, message) VALUES (?, ?, ?, ?)",
                (commit.hash, commit.timestamp, commit.spec_id, commit.message),
            )
            if cur.rowcount == 0:
                return "duplicate"

            file_ids: list[int] = []
            for path in files:
                conn.execute("INSERT OR IGNORE INTO files (path) VALUES (?)", (path,))
                conn.execute("UPDATE files SET total_changes = total_changes + 1 WHERE path = ?", (path,))
                row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
                file_ids.append(int(row["id"]))

            conn.executemany(
                "INSERT INTO commit_files (commit_hash, file_id) VALUES (?, ?)",
                [(commit.hash, fid) for fid in file_ids],
            )

            for a, b in itertools.combinations(sorted(file_ids), 2):
                conn.execute(
                    "INSERT OR IGNORE INTO file_pairs (file_a_id, file_b_id, co_commits) VALUES (?, ?, 0)",
                    (a, b),
                )
                conn.execute(
                    "UPDATE file_pairs SET co_commits = co_commits + 1 WHERE file_a_id = ? AND file_b_id = ?",
                    (a, b),
                )
        return "ingested"

    def build(self) -> CodeGraph:
        """Run all indexing passes and return the resulting graph (not yet implemented)."""
        raise NotImplementedError


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """Command-line entrypoint: ``python3 -m knowledge_graph.indexer --target-dir <repo>``."""
    ap = argparse.ArgumentParser(description="Update the temporal-coupling knowledge graph.")
    ap.add_argument("--target-dir", type=Path, default=Path.cwd(), help="target git repository root")
    ap.add_argument("--db", type=Path, default=None, help="SQLite file (default: <target>/.agent-harness/graph.db)")
    ap.add_argument("--incremental", action="store_true", help="only ingest commits since the last run (default behaviour)")
    args = ap.parse_args(argv)

    db_path = args.db if args.db is not None else args.target_dir / ".agent-harness" / "graph.db"
    with DatabaseManager(db_path) as db:
        report = Indexer(args.target_dir, db).index_history()
    mode = "full rebuild" if report.full_rebuild else "incremental"
    print(
        f"{mode}: {report.commits_ingested} ingested, "
        f"{report.commits_skipped_mass_refactor} skipped (mass refactor), "
        f"{report.commits_already_indexed} already indexed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
