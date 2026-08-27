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
              LLM ◀──stdio (MCP)──▶ mcp_server.main (Server) ◀── ToolRegistry (9 tools)
```
```
config/llm_backends.yaml ─┐                         ┌─▶ <target>/.agent-harness/mcp.json ──▶ bin/start_mcp.sh --target-dir
config/roles.yaml ────────┴─▶ bin/run_agent.sh ─────┼─▶ <target>/.agent-harness/opencode.json (mcp + provider + persona agent; OPENCODE_CONFIG)
                              (bin/harness_config.py)├─▶ env: OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME
                                                     └─▶ supervisor loop (≤ MAX_RETRIES): opencode run --dir <target> -m <backend>/<model> --agent SystemArchitect "Implement spec <id>"
                                                           ↻ no .agent-harness/run_successful (written by git_commit_feature) → opencode run … --continue "<recovery prompt>"
```

## 2. Status Summary

| Stage | Title | Status | ADR |
|---|---|---|---|
| 1 | Sandboxing & Target Isolation (incl. local-only internal notes) | done (written in Stage 5) | [ADR-001](adr/ADR-001-sandboxing-and-target-isolation.md) |
| 2 | Knowledge Graph SQLite Schema & .graphignore | storage + filter done; git parsing deferred to Stage 3 | [ADR-002](adr/ADR-002-knowledge-graph-sqlite-schema.md) |
| 3 | Incremental Indexer & Jaccard Metric Logic | done (tests: `tests/test_indexer_git.py`) | [ADR-003](adr/ADR-003-incremental-indexer-jaccard-metric.md) |
| 4 | MCP Server & Core Tool Stubs | done — stdio server, 7 tools registered, `query_temporal_coupling` wired (tests: `tests/test_mcp_server.py`) | [ADR-004](adr/ADR-004-mcp-server-initialization.md) |
| 5 | Sandboxed Tool Implementations | done — all 7 tools functional behind `Sandbox` (tests: `tests/test_tools_sandbox.py`) | [ADR-001](adr/ADR-001-sandboxing-and-target-isolation.md) |
| 6 | Agent Orchestrator Setup | done — `run_agent.sh` generates `mcp.json`, exports LLM env, launches the agent CLI (tests: `tests/test_run_agent.py`) | [ADR-005](adr/ADR-005-agent-orchestrator-setup.md) |
| 7 | Bootstrap Environment & Open Code Integration | done — `bootstrap_env.sh`; Open Code launched with derived `opencode.json`; `gpt-oss:20b`; OpenAI-only env (tests: `tests/test_run_agent.py`) | [ADR-006](adr/ADR-006-bootstrap-and-opencode.md) |
| 8 | Greenfield File Operations | done — `fs_list` + `fs_write` in `fs_tools.py`, sandbox-enforced, exclusions for VCS/venv/cache dirs (tests: `tests/test_fs_tools.py`) | [ADR-007](adr/ADR-007-greenfield-file-operations.md) |
| 9 | Agent Supervisor & Recovery Loop | done — `run_successful` marker from `git_commit_feature`; `run_agent.sh` retries with `--continue` (tests: `tests/test_git_tools.py`, `tests/test_run_agent.py`) | [ADR-008](adr/ADR-008-agent-supervisor-loop.md) |
| 10 | Target Environment Management | done — `run_tests` provisions and uses `<target>/.venv`; `bootstrap_env.sh --target-dir`; persona anti-shadowing rule; `MAX_RETRIES=5` (tests: `tests/test_test_tools.py`) | [ADR-009](adr/ADR-009-target-environment-management.md) |
| 11 | Harness Stability & Tool Refinements | done — `fs_read` line ranges, glob-char rejection in `Sandbox`, SQLite staging exclusions, bounded/explicit `run_tests` timeout, persona milestone-commit + stuck rules (tests: `test_fs_tools.py`, `test_git_tools.py`, `test_test_tools.py`) | [ADR-010](adr/ADR-010-harness-stability-and-refinements.md) |
| 12 | Plan Phase & Human Approval Gate | done — `run_agent.sh --phase plan\|implement`, `Planner` role, `bin/plan_gate.py` (pre-flight checkboxes), `--write-scope` enforced by the MCP server, per-role tool exposure (tests: `test_plan_gate.py`, `test_run_agent.py`, `test_fs_tools.py`) | [ADR-011](adr/ADR-011-plan-phase-and-approval-gate.md) |
| 13 | Completion Criteria & Verifier Role | done — implement→verify loop; `spec_complete` marker via capability-gated `mark_spec_complete`; `Verifier` role; session-pinned resume with gap feedback (tests: `test_run_agent.py`, `test_git_tools.py`) | [ADR-012](adr/ADR-012-completion-criteria.md) |
| 14 | Claude Code Backend | done — `AGENT_CMD=claude` runs the plan/verify loop via `claude -p --output-format json`; role=`--append-system-prompt`+`--allowedTools` (MCP-only), implementer resumed with `--resume`; `--model` (default sonnet) (tests: `test_run_agent.py`) | [ADR-013](adr/ADR-013-claude-code-backend.md) |
| 15 | Reliability Hardening | done — `preflight_model.py` (num_ctx + tool-calling), `recover_hint.py` (dropped-call resume), tool/arg aliases (`aliases.py`), `completion_checks.py` (hermetic+mypy+ruff gate on `spec_complete`), no-progress abort, `fs_write` shadow-dep guard (tests: `test_reliability.py`, `test_run_agent.py`) | [ADR-014](adr/ADR-014-reliability-hardening.md) |

## 3. Component Specifications
### 3.1 `bin/` — lifecycle scripts
- `start_mcp.sh` — wraps `python -m mcp_server.main` (uses `.venv` if present).
- `bootstrap_env.sh [--python <exe>] [--check-only] [--target-dir <path>]` — `set -euo pipefail`; creates `.venv`, installs `requirements.txt`; checks node ≥ 18, npm, `opencode`, Ollama reachability at the backend `base_url` and that `MODEL_NAME` is pulled; prints `[ok]`/`[MISSING] … <fix command>`; exit 1 only on Python-env failure or `--check-only` gaps (ADR-006). `--target-dir` runs `python -m mcp_server.tools.test_tools --target-dir <path>` to pre-provision the target's `.venv` (ADR-009).
- `plan_gate.py locate --target-dir <t> --spec <id>` / `check <plan>` — resolves `specs/<id>*/03_plan.md`; approval = every checkbox under `## Pre-flight` ticked; exit 0 approved, 4 unticked (listed), 5 missing/empty/no section (ADR-011).
- `run_agent.sh --spec <id> --target-dir <path> [--phase plan|implement] [--backend] [--role] [--skip-plan-gate] [--dry-run]` — `plan`: role defaults to `Planner`, MCP server started with `--write-scope specs`, kickoff prompt names the plan path and the commit message; `implement` (default): runs `plan_gate.py check` first and exits 4/5 unless `--skip-plan-gate`; warns when the target has uncommitted tracked changes. Exports `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` (+ `LLM_BACKEND`, `LLM_PROVIDER`) via `harness_config.py env`; writes `<target>/.agent-harness/mcp.json` (`mcpServers.agent-harness` → `bin/start_mcp.sh --target-dir <target>`) and derives `<target>/.agent-harness/opencode.json` (provider `<backend>` = `@ai-sdk/openai-compatible` at `OPENAI_BASE_URL`; `mcp.agent-harness` local server; agent `<role>` with the persona prompt and built-in `bash/edit/write/patch/multiedit/webfetch` disabled); then runs the **supervisor loop**. Plan phase / generic CLI: success = `run_successful` (ADR-008). Implement phase (Open Code): an implementer→**Verifier** loop (ADR-012) — each iteration runs the implementer (initial, or resumed on its pinned `--session` with the previous Verifier's gap list as the prompt), then a fresh `Verifier` (its own `opencode.verifier.json`, MCP server started `--enable-tool mark_spec_complete`); success = `.agent-harness/spec_complete` (Verifier-set), NOT the first commit. Up to `MAX_RETRIES` (default 5). Exit 0 = complete, 3 = exhausted, 2 = usage, 4/5 = plan gate, 6 = model cannot tool-call, 7 = no progress (ADR-014). Before launch (local backends) `preflight_model.py` checks num_ctx + structured tool-calling; on `spec_complete` `completion_checks.py` runs a hermetic build + mypy/ruff and can revoke it; a dropped-tool-call in the last output is recovered into the resume prompt; `fs_write` blocks shadow-dependency packages; tool-name and argument aliases absorb common local-model guesses (ADR-014). `--skip-preflight`/`--no-completion-checks` disable those. `AGENT_CMD` defaults to `opencode`; any other value gets a single generic `--mcp-config … --append-system-prompt …` run. Exports `AGENT_SPEC_ID`, `AGENT_TARGET_DIR`, `AGENT_MCP_CONFIG` (ADR-005, ADR-006).
- `harness_config.py env|role|model|list` — PyYAML reader for `config/`; `env` prints `export KEY='v'` lines.
### 3.2 `mcp_server/` — MCP boundary
- `core/registry.py` — `ToolArgs` (strict Pydantic base, `extra="forbid"`), `ToolSpec`, `ToolRegistry` (`list_tools()`, `call()`, `call_tool_result()`); transport-independent.
- `core/context.py` — `HarnessContext(target_dir, db, sandbox)`; DB defaults to `<target>/.agent-harness/graph.db`.
- `core/sandbox.py` — `Sandbox.resolve()` (rejects absolute, `..`, glob characters `* ? [ ]` (ADR-010), symlink escapes) and `Sandbox.run()` (no shell, cwd=target, scrubbed env, timeout); `SandboxViolation`/`ToolError` surface as `is_error` results (ADR-001).
- `tools/*.py` — each exposes `build_tools(ctx)`; every tool goes through `ctx.sandbox`. See §5 for behaviour. `fs_tools.EXCLUDED_DIRS`, `MAX_LIST_ENTRIES`, `MAX_WRITE_BYTES` govern `fs_list`/`fs_write` (ADR-007).
- `main.py` — `build_registry`, `build_server` (`mcp.server.Server` with `on_list_tools`/`on_call_tool`), `serve` over `stdio_server()`, CLI `--target-dir` (required) `--db` `--write-scope <prefix>` (ADR-011) `--enable-tool <name>` (repeatable; registers capability-gated tools like `mark_spec_complete`, ADR-012).
- `gen_opencode_config.py --target-dir <t> --role <r> --out-opencode <f> [--out-mcp <f>] [--write-scope <s>...] [--enable-tool <t>...]` — builds an Open Code config for one role from `roles.yaml`; run once per role (primary + Verifier).
- **Backends (ADR-013):** `run_agent.sh` detects `opencode` | `claude` | generic from `AGENT_CMD`. Claude Code path writes plain `mcp.json`/`mcp.verifier.json`, applies a role as `--append-system-prompt <role prompt>` + `--allowedTools mcp__agent-harness__<tool>` (built-ins `--disallowedTools`, MCP-only), runs `claude -p … --output-format json` per role, captures `session_id`+`result`, resumes the implementer with `--resume`; `--model` (default `sonnet`).
- `core/context.py` — `HarnessContext.write_scopes` / `allows_write()` (ADR-011) and `optional_tools` (ADR-012); `fs_write`/`fs_apply_patch` raise `SandboxViolation` outside every scope (reads never scoped).
- Launch: `./bin/start_mcp.sh --target-dir <repo>` or `python3 -m mcp_server.main --target-dir <repo>`.
### 3.3 `knowledge_graph/` — indexer and temporal coupling
`indexer.py`:
- `DatabaseManager` — schema init, `latest_commit_hash()`, `wipe()`, `query_coupled_files(path, min_jaccard)` (ADR-003 §5).
- `GraphIgnoreFilter` — `.graphignore` glob filter (ADR-002 §3).
- `GitParser` — `git log` via `subprocess` with RS/US delimiters, spec-ID regex, oldest-first `ParsedCommit` list (ADR-003 §1–3).
- `Indexer.index_history()` — incremental run, self-healing full rebuild on `GitLogError`, one transaction per commit (`ingest_commit`), returns `IndexReport` (ADR-003 §2, §4).
- CLI: `python3 -m knowledge_graph.indexer --target-dir <repo> [--db <file>] [--incremental]`.
### 3.4 `config/` — backends and roles
- `llm_backends.yaml` — `default: <name>` + `backends.<name>: {provider, base_url, model, api_key, api_key_env?, extra_env?}`. Profiles: `ollama` (`http://localhost:11434/v1`, `gpt-oss:20b-32k`, placeholder key), `freetoken`. Exported as `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME`, `LLM_BACKEND`, `LLM_PROVIDER`, `LLM_CONTEXT_LENGTH`, `LLM_MAX_OUTPUT_TOKENS` (no Anthropic-specific variables — ADR-006); `context_length` must equal the Ollama model's `num_ctx` and is written to `opencode.json` as the model `limit` — Open Code treats a model without a limit as a 0-token context and compacts after every tool call; `api_key_env` overrides `api_key` when set in the caller's env.
- `roles.yaml` — `default: SystemArchitect` + `roles.<name>: {description, backend, allowed_tools, system_prompt}`. `allowed_tools` is enforced in the generated Open Code agent (other harness tools disabled) and in the persona's tool list (ADR-011). `Planner`: constitution → spec → template → `fs_write` plan → `git_commit_feature` → stop; never ticks pre-flight boxes, never writes outside `specs/`. `Verifier` (ADR-012): read-only + `run_tests` + `mark_spec_complete`; reads spec/plan/code, runs tests, and either marks complete or emits a gap list; no write/patch/commit tools. `SystemArchitect` mandates constitution → spec → `fs_list` exploration → coupling query → `fs_write`/`fs_apply_patch` → `run_tests` → `git_commit_feature`; forbids shadowing external dependencies with local packages (missing module ⇒ fix `requirements.txt`, ADR-009); commits at every green milestone and stops honestly after 3 failed attempts on one test — never claims a commit that did not happen (ADR-010), and forbids guessing contents/dependencies or acting outside the MCP tools (ADR-005).

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
| `fs_read` | `filepath: str, start_line?: int, end_line?: int` (1-indexed, inclusive) | UTF-8 text ≤ 512 KiB inside target; a range returns only that slice prefixed with `# <path>: lines a-b of N` (`end_line` clamped to EOF; ADR-010) | `mcp_server/tools/fs_tools.py` |
| `fs_list` | `directory_path: str = ".", recursive: bool = False` | JSON `{directory, entries:[{path, type: dir\|file\|symlink\|other, size?}], truncated}`; dirs first; skips `EXCLUDED_DIRS` (`.git`, `.venv`, `__pycache__`, `node_modules`, `.agent-harness`, caches, `dist`, `build`, IDE dirs) at every depth; symlinks listed by name, not followed; cap 2000 entries | `mcp_server/tools/fs_tools.py` |
| `fs_write` | `filepath, content: str` | creates or overwrites a UTF-8 file (≤ 1 MiB), `mkdir -p` parents, atomic write; refuses directories and excluded dirs; returns `created\|overwritten <path> (<n> bytes)` | `mcp_server/tools/fs_tools.py` |
| `fs_apply_patch` | `filepath, search_string, replace_string: str` | replaces exactly one occurrence (0 or >1 → error, file untouched); atomic write | `mcp_server/tools/fs_tools.py` |
| `run_tests` | `test_target: str` (`path[::selector]`), `timeout_seconds: int = 60` (1–600) | runner auto-detect gradlew → npm → pytest; pytest runs as `<target>/.venv/bin/python -m pytest` — venv created with the harness interpreter if missing, `requirements.txt` installed (re-installed when its SHA-256 stamp in `.venv/.harness-requirements.sha256` changes), pytest installed if not importable; provisioning errors → `ToolError` with stderr; test process killed after `timeout_seconds` with `timeout_message` + `[TIMEOUT]` stderr banner (ADR-010; provisioning: 300 s venv, 900 s pip); JSON `{runner, python, env_actions, timeout_seconds, exit_code, timed_out, timeout_message, stdout, stderr}` (ADR-009) | `mcp_server/tools/test_tools.py` |
| `git_commit_feature` | `message, spec_id: str` | validates Conventional Commits, header `… [spec_id]`, stages `-A` excluding `CLAUDE.md`, `prompts-hist/`, `.agent-harness/`, `.venv/`, `__pycache__/`, `*.db`, `*.sqlite`, `*.sqlite3`, `*.db3` (ADR-010); no co-author trailers; on success touches `.agent-harness/run_successful` (ADR-008) |
| `mark_spec_complete` | — | touches `.agent-harness/spec_complete`; registered ONLY when the server is started `--enable-tool mark_spec_complete` (Verifier's server); the loop's success condition (ADR-012) | `mcp_server/tools/git_tools.py` |
| `query_temporal_coupling` | `filepath: str, threshold_percent: int = 30 (0-100)` | JSON `{filepath, threshold_percent, coupled_files:[{filepath, coupling_percent}]}` via Jaccard query | `mcp_server/tools/graph_tools.py` |

## 6. Known Risks
- Sandbox path checks are TOCTOU-prone with symlinks (single-user local tool; accepted).
- `run_tests` bounds *which command* runs, not what the target's tests do; provisioning (`pip install`) is the one tool path with network egress (ADR-009).
- `fs_write` can overwrite any non-excluded file, including tests; the target's test-lock convention is not enforced by the harness (ADR-007).
- The `run_successful` marker means *a* commit happened; **spec completion** is a separate `spec_complete` marker set by the Verifier (ADR-012). The Verifier is the same local model as the implementer and can misjudge in either direction; a human still reviews the branch. Session-id capture parses `opencode session list` and falls back to `--continue`.
- `Sandbox` rejects `[`/`]` as glob characters, so bracketed file names are unreachable through the tools (ADR-010).
- The approval gate trusts Markdown checkboxes; anyone with write access to the plan can tick them. The Planner may still edit anything under `specs/` (reviewed before approval) (ADR-011).
- Runner detection is heuristic (npm wins over pytest when both exist).
- Self-healing re-index only triggers on `git log` failure; amended-but-unpruned history lingers (ADR-003).
- `git commit` in the target needs a git identity reachable via the scrubbed env (`HOME` is passed through).
- `run_agent.sh` `eval`s the helper's `export` lines; config files are trusted input (ADR-005).
- `OPENCODE_CONFIG` merges over the user's global Open Code config; per-agent tool toggles are honoured by Open Code but cannot stop a model from *describing* edits in text. `gpt-oss:20b` leaks reasoning into visible text via the OpenAI-compatible endpoint (ADR-006).

## 7. Roadmap
- `pyproject.toml`/`pip install -e .` provisioning; a second/stronger model for the Verifier; `blocked` marker to short-circuit retries on legitimate stops; branch creation (`feat/<NNN>-<slug>`) as a harness step; native Ollama adapter in `opencode.json` to separate gpt-oss reasoning; first end-to-end spec run and transcript review; OS-level sandbox for `run_tests`; rename tracking in the indexer.
