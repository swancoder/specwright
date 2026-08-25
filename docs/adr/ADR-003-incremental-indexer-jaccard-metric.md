# ADR-003: Incremental Indexer & Jaccard Metric Logic

- **Status:** Accepted
- **Date:** 2026-08-25
- **Stage:** 3

## Context
ADR-002 defined the SQLite schema and the `.graphignore` filter but left git history
parsing unimplemented. The graph must be kept up to date cheaply on every run (only new
commits), survive history rewrites, and expose a coupling metric that is relative to each
file's own churn so that globally hot files do not dominate every query.

## Decision

### 1. Git Interfacing via Subprocess
Use Python's `subprocess` module to call `git log` directly.
Command format: `git log <latest_hash>..HEAD --pretty=format:"%x1e%H%x1f%ct%x1f%B" --name-only`
Using non-printable ASCII characters (RS `\x1e`, US `\x1f`) ensures robust parsing.

### 2. Incremental Execution & Self-Healing
Query the `commits` table for the most recent timestamp to get the latest hash.
* If no hash exists: Run full `git log --all`.
* If a hash exists: Run `git log <hash>..HEAD`.
* Self-Healing: If `git log <hash>..HEAD` fails (e.g., due to a rebase or force push), catch the exception, wipe all data from the four graph tables, and initiate a full rebuild (`git log --all`).

### 3. Spec ID Extraction
Extract the Spec/ADR ID from commit messages using the regex `\[([a-zA-Z0-9\-]+)\]`.

### 4. Transactional Aggregation
For each parsed commit, execute a single database transaction:
1. Filter out files matching the `.graphignore` rules.
2. Skip the commit entirely if the remaining file count > MASS_REFACTOR_FILE_LIMIT (50).
3. Insert the commit into `commits`.
4. Insert any new files into `files`, and increment `total_changes` for all files involved.
5. Insert mappings into `commit_files`.
6. Iterate all unique pairs in the commit's file list (using `itertools.combinations`). Order file pairs by ID (min_id, max_id), insert/ignore into `file_pairs`, and increment `co_commits`.

### 5. Jaccard Index Query
Implement this exact query in `DatabaseManager` to fetch coupled files:
```sql
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
HAVING jaccard >= ?
ORDER BY jaccard DESC;
```

## Implementation notes
- **Query deviation:** SQLite (tested on 3.46) raises `HAVING clause on a non-aggregate
  query` for the §5 SQL as written. `GROUP BY f.id` was added before `HAVING`; every
  (target, neighbour) row is unique, so results are identical.
- `knowledge_graph/indexer.py`: `GitParser` (builds the command, runs it, parses records
  into `ParsedCommit`, extracts spec IDs), `DatabaseManager.latest_commit_hash()`,
  `wipe()`, `query_coupled_files()`, `Indexer.index_history()` orchestrating
  incremental → self-heal → per-commit transactions via `Indexer.ingest_commit()`.
- Commits are processed oldest-first; `INSERT OR IGNORE` on `commits` makes reruns idempotent.
- The mass-refactoring limit is applied to the *post-`.graphignore`* file count.
- CLI: `python3 -m knowledge_graph.indexer --target-dir <repo> [--db <file>] [--incremental]`.

## Alternatives considered
- **GitPython / pygit2** — richer API but an extra native/third-party dependency for what
  is a single well-defined `git log` invocation; rejected.
- **Timestamp-based incremental (`--since`)** — simpler, but clock skew and rebases make it
  unreliable; hash ranges plus self-healing are deterministic.
- **Computing Jaccard in Python** — would require loading all pairs; SQL keeps the query
  bounded to the target file's neighbourhood.

## Self-critique
- Self-healing keys off `git log` *failing*. After `commit --amend` / rebase the old hash
  usually still exists as an unreachable object, so `old..HEAD` succeeds and the superseded
  commit stays in the graph until `git gc` prunes it (verified manually). A stricter check
  (`git merge-base --is-ancestor <hash> HEAD`) would catch this; deferred.
- `latest_commit_hash()` tie-breaks equal timestamps by insertion order (`rowid`); the
  ADR's "most recent timestamp" alone is ambiguous for commits within the same second.
- The body/file-list split relies on git's blank line before `--name-only` output; a
  message whose last paragraph looks like file names and has no changed files (e.g. an
  empty merge) could be misparsed. Merge commits list no files, which is acceptable.
- `git log --all` includes unreachable-from-HEAD branches; coupling from abandoned
  branches leaks in. Acceptable for now; revisit if noise appears.
- Self-healing wipes on *any* `git log` failure, including transient ones (repo locked,
  missing `git` binary), which triggers a needless full rebuild rather than data loss.
- Renames are not tracked; a renamed file starts a fresh history under the new path.

## Consequences
- The graph stays current with one cheap `git log` call per run.
- History rewrites cost one full rebuild instead of a corrupt graph.
- `query_temporal_coupling` (ADR-004) can be a thin wrapper over
  `DatabaseManager.query_coupled_files`.

## Prompt
`prompts-hist/002_incremental_indexer.txt` (local-only)
