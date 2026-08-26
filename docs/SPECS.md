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
```
config/llm_backends.yaml ─┐                         ┌─▶ <target>/.agent-harness/mcp.json ──▶ bin/start_mcp.sh --target-dir
config/roles.yaml ────────┴─▶ bin/run_agent.sh ─────┼─▶ env: OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME (+ ANTHROPIC_*)
                              (bin/harness_config.py)└─▶ exec $AGENT_CMD --mcp-config … --append-system-prompt <persona> "Implement spec <id>"  (cwd = target)
```

## 2. Status Summary

| Stage | Title | Status | ADR |
|---|---|---|---|
| 1 | Sandboxing & Target Isolation (incl. local-only internal notes) | done (written in Stage 5) | [ADR-001](adr/ADR-001-sandboxing-and-target-isolation.md) |
| 2 | Knowledge Graph SQLite Schema & .graphignore | storage + filter done; git parsing deferred to Stage 3 | [ADR-002](adr/ADR-002-knowledge-graph-sqlite-schema.md) |
| 3 | Incremental Indexer & Jaccard Metric Logic | done (tests: `tests/test_indexer_git.py`) | [ADR-003](adr/ADR-003-incremental-indexer-jaccard-metric.md) |
| 4 | MCP Server & Core Tool Stubs | done — stdio server, 7 tools registered, `query_temporal_coupling` wired (tests: `tests/test_mcp_server.py`) | [ADR-004](adr/ADR-004-mcp-server-initialization.md) |
| 5 | Sandboxed Tool Implementations | done — all 7 tools functional behind `Sandbox` (tests: `tests/test_tools_sandbox.py`) | [ADR-001](adr/ADR-001-sandboxing-and-target-isolation.md) |
| 6 | Agent Orchestrator Setup | done — `run_agent.sh` generates `mcp.json`, exports LLM env, launches placeholder agent CLI (tests: `tests/test_run_agent.py`) | [ADR-005](adr/ADR-005-agent-orchestrator-setup.md) |

## 3. Component Specifications
### 3.1 `bin/` — lifecycle scripts
- `start_mcp.sh` — wraps `python -m mcp_server.main` (uses `.venv` if present).
- `run_agent.sh --spec <id> --target-dir <path> [--backend] [--role] [--dry-run]` — exports backend env via `harness_config.py env`, writes `<target>/.agent-harness/mcp.json` (`mcpServers.agent-harness` → `bin/start_mcp.sh --target-dir <target>`), then `cd <target>` and `exec $AGENT_CMD --mcp-config <mcp.json> --append-system-prompt <persona> "<prompt>"`. `AGENT_CMD` defaults to `npx @anthropic-ai/claude-code` (placeholder). Exports `AGENT_SPEC_ID`, `AGENT_TARGET_DIR`, `AGENT_MCP_CONFIG` (ADR-005).
- `harness_config.py env|role|list` — PyYAML reader for `config/`; `env` prints `export KEY='v'` lines.
- `bootstrap_env.sh` — TODO.
### 3.2 `mcp_server/` — MCP boundary
- `core/registry.py` — `ToolArgs` (strict Pydantic base, `extra="forbid"`), `ToolSpec`, `ToolRegistry` (`list_tools()`, `call()`, `call_tool_result()`); transport-independent.
- `core/context.py` — `HarnessContext(target_dir, db, sandbox)`; DB defaults to `<target>/.agent-harness/graph.db`.
- `core/sandbox.py` — `Sandbox.resolve()` (rejects absolute, `..`, symlink escapes) and `Sandbox.run()` (no shell, cwd=target, scrubbed env, timeout); `SandboxViolation`/`ToolError` surface as `is_error` results (ADR-001).
- `tools/*.py` — each exposes `build_tools(ctx)`; every tool goes through `ctx.sandbox`. See §5 for behaviour.
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
- `llm_backends.yaml` — `default: <name>` + `backends.<name>: {provider, base_url, model, api_key, api_key_env?, extra_env?}`. Profiles: `ollama` (`http://localhost:11434/v1`, `qwen2.5-coder:14b`, placeholder key), `freetoken`. Exported as `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, `LLM_BACKEND`, `LLM_PROVIDER`; `api_key_env` overrides `api_key` when set in the caller's env.
- `roles.yaml` — `default: SystemArchitect` + `roles.<name>: {description, backend, allowed_tools, system_prompt}`. `SystemArchitect` mandates constitution → spec → coupling query → patch → `run_tests` → `git_commit_feature`, and forbids guessing contents/dependencies or acting outside the MCP tools (ADR-005).

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
| `read_constitution` | — | reads `.github/constitution.md` | `mcp_server/tools/fs_tools.py` |
| `read_specification` | `spec_id: str` | concatenates `*.md` under `specs/<id>*/` (or `specs/<id>.md`); ambiguous prefix → error | `mcp_server/tools/fs_tools.py` |
| `fs_read` | `filepath: str` | UTF-8 text ≤ 512 KiB inside target | `mcp_server/tools/fs_tools.py` |
| `fs_apply_patch` | `filepath, search_string, replace_string: str` | replaces exactly one occurrence (0 or >1 → error, file untouched); atomic write | `mcp_server/tools/fs_tools.py` |
| `run_tests` | `test_target: str` (`path[::selector]`) | runner auto-detect gradlew → npm → pytest; 600 s timeout; JSON `{runner, exit_code, timed_out, stdout, stderr}` | `mcp_server/tools/test_tools.py` |
| `git_commit_feature` | `message, spec_id: str` | validates Conventional Commits, header `… [spec_id]`, stages `-A` excluding `CLAUDE.md`, `prompts-hist/`, `.agent-harness/`; no co-author trailers | `mcp_server/tools/git_tools.py` |
| `query_temporal_coupling` | `filepath: str, threshold_percent: int = 30 (0-100)` | JSON `{filepath, threshold_percent, coupled_files:[{filepath, coupling_percent}]}` via Jaccard query | `mcp_server/tools/graph_tools.py` |

## 6. Known Risks
- Sandbox path checks are TOCTOU-prone with symlinks (single-user local tool; accepted).
- `run_tests` bounds *which command* runs, not what the target's tests do.
- Runner detection is heuristic (npm wins over pytest when both exist).
- Self-healing re-index only triggers on `git log` failure; amended-but-unpruned history lingers (ADR-003).
- `git commit` in the target needs a git identity reachable via the scrubbed env (`HOME` is passed through).
- `run_agent.sh` `eval`s the helper's `export` lines; config files are trusted input. Claude Code cannot talk to raw Ollama (`ANTHROPIC_*` needs a translating proxy); the agent CLI is a placeholder (ADR-005).

## 7. Roadmap
- Choose/validate the agent CLI behind `AGENT_CMD` (or an in-repo Python loop); `bootstrap_env.sh`; OS-level sandbox for `run_tests`; rename tracking in the indexer.
