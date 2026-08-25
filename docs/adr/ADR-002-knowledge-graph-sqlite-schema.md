# ADR-002: Knowledge Graph SQLite Schema & .graphignore

- **Status:** Accepted
- **Date:** 2026-08-25
- **Stage:** 2

## Context
The knowledge graph must store git history compactly enough for incremental updates
(ADR-003) and fast pairwise coupling queries, while excluding files that generate noise.

## Decision

### 1. SQLite Schema Design
A normalized schema optimized for fast graph traversal and incremental updates.

```sql
-- Represents individual Git commits
CREATE TABLE commits (
    hash TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    spec_id TEXT,
    message TEXT NOT NULL
);

-- Represents files tracked in the repository
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    total_changes INTEGER DEFAULT 0
);

-- Junction table mapping files modified in a specific commit
CREATE TABLE commit_files (
    commit_hash TEXT REFERENCES commits(hash),
    file_id INTEGER REFERENCES files(id),
    PRIMARY KEY (commit_hash, file_id)
);

-- Edges representing temporal coupling between two files
CREATE TABLE file_pairs (
    file_a_id INTEGER REFERENCES files(id),
    file_b_id INTEGER REFERENCES files(id),
    co_commits INTEGER DEFAULT 0,
    PRIMARY KEY (file_a_id, file_b_id)
);
```

### 3. Noise Reduction and Anomaly Filtering
- **.graphignore Integration:** The target project contains a `.graphignore` file at its
  root. Standard glob matching (Python `fnmatch`) is used; any matching path is excluded.
- **Mass Refactoring Filter:** Commits modifying > 50 files are ignored.

## Implementation notes
- `knowledge_graph/indexer.py`: `DatabaseManager` (connection + idempotent
  `CREATE TABLE IF NOT EXISTS`, `PRAGMA foreign_keys = ON`, context-manager),
  `GraphIgnoreFilter` (comment/blank stripping; patterns matched against full relative
  path and basename; trailing `/` means "whole directory"), constant
  `MASS_REFACTOR_FILE_LIMIT = 50` for the future parser.
- Git parsing is deliberately absent (ADR-003).

## Alternatives considered
- **Denormalized `file_pairs(path_a, path_b)`** — simpler joins but duplicates paths and
  makes renames costly; rejected.
- **`pathspec` / gitignore-compatible matcher** — richer semantics (negation, `**`), but
  adds a dependency; ADR mandates `fnmatch`. Revisit if `.graphignore` files grow complex.

## Self-critique
- `fnmatch` `*` matches `/`, so `*.lock` also matches `a/b/c.lock` — intended, but it is
  not gitignore semantics; users may be surprised. Negation (`!pattern`) is unsupported.
- `file_pairs` has no `CHECK (file_a_id < file_b_id)`; the parser must canonicalize pair
  order or pairs will be double-counted.
- Foreign keys are declared but `commit_files` rows are not `ON DELETE CASCADE`; deleting
  commits requires manual cleanup.

## Consequences
- Storage layer is testable in isolation (`tests/test_indexer_storage.py`).
- ADR-003 must: order pair ids canonically, honor `MASS_REFACTOR_FILE_LIMIT`, and consult
  `GraphIgnoreFilter` before inserting into `files`.

## Prompt
`prompts-hist/001_kg_sqlite_schema_graphignore.txt` (local-only)
