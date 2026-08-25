# SPECS.md — Agent Harness Consolidated Technical Specification

Authoritative technical reference tying together `README.md`, `CLAUDE.md`, and the ADRs in `docs/adr/`.
Decision rationale is not repeated here — each section links to its ADR.

## 1. Architecture Overview
```
target repo ──git log──▶ GitParser ──ParsedCommit──▶ Indexer ──txn──▶ SQLite (DatabaseManager)
                                        ▲                                    │
                              GraphIgnoreFilter                 query_coupled_files (Jaccard)
                                                                             ▼
                                                     mcp_server.tools.query_temporal_coupling (Stage 4)
```
_TODO: MCP server / orchestrator layers (Stage 4+)._

## 2. Status Summary

| Stage | Title | Status | ADR |
|---|---|---|---|
| 1 | Untrack Internal Engineering Notes | not started | [ADR-001](adr/ADR-001-untrack-internal-engineering-notes.md) |
| 2 | Knowledge Graph SQLite Schema & .graphignore | storage + filter done; git parsing deferred to Stage 3 | [ADR-002](adr/ADR-002-knowledge-graph-sqlite-schema.md) |
| 3 | Incremental Indexer & Jaccard Metric Logic | done (tests: `tests/test_indexer_git.py`) | [ADR-003](adr/ADR-003-incremental-indexer-jaccard-metric.md) |
| 4 | MCP Server & Core Tool Stubs | not started | [ADR-004](adr/ADR-004-mcp-server-initialization.md) |

## 3. Component Specifications
### 3.1 `bin/` — lifecycle scripts
_TODO._
### 3.2 `mcp_server/` — MCP boundary
_TODO._
### 3.3 `knowledge_graph/` — indexer and temporal coupling
`indexer.py`:
- `DatabaseManager` — schema init, `latest_commit_hash()`, `wipe()`, `query_coupled_files(path, min_jaccard)` (ADR-003 §5).
- `GraphIgnoreFilter` — `.graphignore` glob filter (ADR-002 §3).
- `GitParser` — `git log` via `subprocess` with RS/US delimiters, spec-ID regex, oldest-first `ParsedCommit` list (ADR-003 §1–3).
- `Indexer.index_history()` — incremental run, self-healing full rebuild on `GitLogError`, one transaction per commit (`ingest_commit`), returns `IndexReport` (ADR-003 §2, §4).
- CLI: `python3 -m knowledge_graph.indexer --target-dir <repo> [--db <file>] [--incremental]`.
### 3.4 `config/` — backends and roles
_TODO._

## 4. Data Model (SQLite)
Tables `commits`, `files`, `commit_files`, `file_pairs` — DDL and rationale in
[ADR-002](adr/ADR-002-knowledge-graph-sqlite-schema.md). Implemented by
`knowledge_graph.indexer.DatabaseManager` (idempotent schema init, foreign keys on).
Noise reduction: `knowledge_graph.indexer.GraphIgnoreFilter` (`.graphignore`, `fnmatch`)
and `MASS_REFACTOR_FILE_LIMIT = 50`.

## 5. API Reference (MCP tools)
| Tool | Signature | Module |
|---|---|---|
| `read_constitution` | `() -> str` | `mcp_server/tools/fs_tools.py` |
| `read_specification` | `(spec_id: str) -> str` | `mcp_server/tools/fs_tools.py` |
| `fs_read` | `(filepath: str) -> str` | `mcp_server/tools/fs_tools.py` |
| `fs_apply_patch` | `(filepath: str, search_string: str, replace_string: str) -> PatchResult` | `mcp_server/tools/fs_tools.py` |
| `run_tests` | `(test_target: str) -> TestReport` | `mcp_server/tools/test_tools.py` |
| `git_commit_feature` | `(message: str, spec_id: str) -> CommitResult` | `mcp_server/tools/git_tools.py` |
| `query_temporal_coupling` | `(filepath: str, threshold_percent: int) -> TemporalCouplingResult` | `mcp_server/tools/graph_tools.py` |

## 6. Known Risks
_TODO._

## 7. Roadmap
_TODO._
