# SPECS.md — Agent Harness Consolidated Technical Specification

Authoritative technical reference tying together `README.md`, `CLAUDE.md`, and the ADRs in `docs/adr/`.
Decision rationale is not repeated here — each section links to its ADR.

## 1. Architecture Overview
```
target repo ──git log──▶ GitParser ──ParsedCommit──▶ Indexer ──txn──▶ SQLite (DatabaseManager)
                                        ▲                                    │
                              GraphIgnoreFilter                 query_coupled_files (Jaccard)
                                                                             ▼
                                                     mcp_server.tools.query_temporal_coupling
                                                                             ▼
              LLM ◀──stdio (MCP)──▶ mcp_server.main (Server) ◀── ToolRegistry (7 tools)
```
_TODO: orchestrator / agent loop (`bin/run_agent.sh`)._

## 2. Status Summary

| Stage | Title | Status | ADR |
|---|---|---|---|
| 1 | Untrack Internal Engineering Notes | not started | [ADR-001](adr/ADR-001-untrack-internal-engineering-notes.md) |
| 2 | Knowledge Graph SQLite Schema & .graphignore | storage + filter done; git parsing deferred to Stage 3 | [ADR-002](adr/ADR-002-knowledge-graph-sqlite-schema.md) |
| 3 | Incremental Indexer & Jaccard Metric Logic | done (tests: `tests/test_indexer_git.py`) | [ADR-003](adr/ADR-003-incremental-indexer-jaccard-metric.md) |
| 4 | MCP Server & Core Tool Stubs | done — stdio server, 7 tools registered, `query_temporal_coupling` wired (tests: `tests/test_mcp_server.py`) | [ADR-004](adr/ADR-004-mcp-server-initialization.md) |

## 3. Component Specifications
### 3.1 `bin/` — lifecycle scripts
- `start_mcp.sh` — wraps `python -m mcp_server.main` (uses `.venv` if present).
- `bootstrap_env.sh`, `run_agent.sh` — TODO.
### 3.2 `mcp_server/` — MCP boundary
- `core/registry.py` — `ToolArgs` (strict Pydantic base, `extra="forbid"`), `ToolSpec`, `ToolRegistry` (`list_tools()`, `call()`, `call_tool_result()`); transport-independent.
- `core/context.py` — `HarnessContext(target_dir, db)`; DB defaults to `<target>/.agent-harness/graph.db`.
- `tools/*.py` — each exposes `build_tools(ctx)`; `graph_tools.query_temporal_coupling` is wired to `DatabaseManager.query_coupled_files`, the other six return `"Not implemented yet"`.
- `main.py` — `build_registry`, `build_server` (`mcp.server.Server` with `on_list_tools`/`on_call_tool`), `serve` over `stdio_server()`, CLI `--target-dir` (required) `--db`.
- Launch: `./bin/start_mcp.sh --target-dir <repo>` or `python3 -m mcp_server.main --target-dir <repo>`.
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
Argument schemas are the Pydantic `*Args` models in each module (strict; unknown fields rejected). All tools return text content.

| Tool | Arguments | Behaviour | Module |
|---|---|---|---|
| `read_constitution` | — | stub | `mcp_server/tools/fs_tools.py` |
| `read_specification` | `spec_id: str` | stub | `mcp_server/tools/fs_tools.py` |
| `fs_read` | `filepath: str` | stub | `mcp_server/tools/fs_tools.py` |
| `fs_apply_patch` | `filepath, search_string, replace_string: str` | stub | `mcp_server/tools/fs_tools.py` |
| `run_tests` | `test_target: str` | stub | `mcp_server/tools/test_tools.py` |
| `git_commit_feature` | `message, spec_id: str` | stub | `mcp_server/tools/git_tools.py` |
| `query_temporal_coupling` | `filepath: str, threshold_percent: int = 30 (0-100)` | JSON `{filepath, threshold_percent, coupled_files:[{filepath, coupling_percent}]}` via Jaccard query | `mcp_server/tools/graph_tools.py` |

## 6. Known Risks
_TODO._

## 7. Roadmap
_TODO._
