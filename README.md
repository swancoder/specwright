# Agent Harness

[![CI](https://github.com/swancoder/specwright/actions/workflows/ci.yml/badge.svg)](https://github.com/swancoder/specwright/actions/workflows/ci.yml)

Isolated orchestrator + MCP server connecting a local LLM to a separate target codebase,
with a Git-history-based temporal coupling knowledge graph (SQLite).

- Orientation and mandatory protocols: `CLAUDE.md`
- Technical reference: `docs/SPECS.md`
- Decisions: `docs/adr/`

## Quick start
```bash
./bin/init_project.sh --project-dir ../my-app --stack node-typescript --backend claude  # scaffold a new target
./bin/bootstrap_env.sh                       # venv + requirements; checks node/opencode/ollama/model
./bin/bootstrap_env.sh --target-dir <path>   # also pre-provision <target>/.venv from its requirements.txt
./bin/start_mcp.sh --target-dir <path>       # MCP server over stdio (+ SQLite graph)
./bin/run_agent.sh --spec <spec_id> --target-dir <path> [--skip-preflight] [--no-completion-checks]
./bin/run_agent.sh --spec <spec_id> --target-dir <path> --phase plan   # Planner writes+commits specs/<id>/03_plan.md
#   → human reviews the plan and ticks every '- [ ]' under '## Pre-flight'
./bin/run_agent.sh --spec <spec_id> --target-dir <path>                # implement→verify loop; succeeds only when the Verifier marks the spec complete
./bin/run_agent.sh --spec <spec_id> --target-dir <path> --dry-run   # show what would run
python3 -m knowledge_graph.indexer --incremental
pytest tests/
```

## TODO
- [x] `git init` done; Stage 2 and Stage 3 committed on `main` (`git log`).
- [x] `CLAUDE.md` build commands now reference `bin/start_mcp.sh` (Stage 7).
- [x] Add `tests/` directory and a first `pytest` smoke test (`tests/test_indexer_storage.py`).
- [x] Add `.gitignore` (venv, `CLAUDE.md`, `prompts-hist/`, SQLite DB); `.graphignore` lives in the *target* project.
- [x] ADR-001..004 written. (files linked from `CLAUDE.md` and `docs/SPECS.md` do not exist yet).
- [x] Stage 2: SQLite schema + `.graphignore` filter in `knowledge_graph/indexer.py` (ADR-002).
- [x] Stage 3: incremental indexer + self-healing + Jaccard query (ADR-003). Follow-ups: detect rewritten-but-not-pruned history via `git merge-base --is-ancestor`; rename tracking; don't wipe on transient git errors; `git log --all` includes abandoned branches.
- [x] Stage 4: MCP stdio server, 7 tools registered, `query_temporal_coupling` wired (ADR-004).
- [x] Stage 5: all six tools implemented behind `mcp_server/core/sandbox.py` (ADR-001). Follow-ups: OS-level sandbox (container) for `run_tests`; configurable test runner override; `fs_write`/`fs_list` tools if the agent loop needs them.
- [x] Stage 6: `bin/run_agent.sh` + `bin/harness_config.py`, `config/llm_backends.yaml` (ollama default), `config/roles.yaml` (SystemArchitect) (ADR-005). Follow-ups: pick the real agent CLI (Claude Code needs an Anthropic-protocol proxy in front of Ollama); `AGENT_CMD` override is the placeholder.
- [x] Stage 17: `bin/init_project.sh` scaffolds a new target project — choose the stack (python|php-js|node-typescript|java-maven|java-gradle → copies the toolchain template) and the agent backend (opencode|claude + model) and directory; writes constitution/CLAUDE.md/specs skeleton/.gitignore + git baseline, prints the run commands (ADR-016).
- [x] Stage 16: toolchain abstraction (ADR-015) — a target `toolchain.json` (`{stack, commands:{install,lint,test,build}}`) overrides build/test/lint for both the agent and the gate; absent → Python default (venv/pip/mypy/ruff/pytest), unchanged. New capability-gated `run_toolchain_task` MCP tool returns ANSI-stripped, head/tail-truncated (≤4 KiB) JSON; `completion_checks.py` routes lint through it.
- [x] Stage 15: reliability hardening for weak local models (ADR-014) — model preflight (num_ctx + tool-calling probe, abort 6), dropped-tool-call recovery in the resume, tool-name + argument aliases (gated per role), mechanical completion gate (hermetic build + mypy/ruff before `spec_complete`), no-progress abort (7), shadow-dependency `fs_write` guard.
- [x] Stage 14: Claude Code backend (`AGENT_CMD=claude`, `--model sonnet` default) runs the identical plan→approve→implement→verify loop via `claude -p --output-format json`; roles applied as `--append-system-prompt` + `--allowedTools mcp__agent-harness__*` with built-ins disallowed (MCP-only); implementer resumed with `--resume <session_id>` (ADR-013). Open Code/generic paths unchanged.
- [x] Stage 13: implement phase is now an implementer→Verifier loop; success = `.agent-harness/spec_complete` (set by the read-only `Verifier` via the capability-gated `mark_spec_complete`, MCP `--enable-tool`), not the first commit; the Verifier's gap list feeds the next `--continue` (ADR-012). Follow-ups: second/stronger model for verification; `blocked` marker.
- [x] Stage 12: `--phase plan|implement`; `Planner` persona; approval gate on `03_plan.md` pre-flight checkboxes (`bin/plan_gate.py`, exit 4/5); MCP `--write-scope` enforced in `fs_write`/`fs_apply_patch`; per-role tool exposure in Open Code (ADR-011). Follow-ups: `blocked` marker; branch creation as a harness step.
- [x] Stage 11: `fs_read(start_line,end_line)`; `Sandbox.resolve` rejects `* ? [ ]`; `*.db/*.sqlite/*.sqlite3/*.db3` never staged; `run_tests(timeout_seconds=60, ≤600)` with explicit `timeout_message`; persona: milestone commits + honesty/stuck rule (ADR-010). Follow-ups: `blocked` marker so the supervisor stops nudging an honest stop.
- [x] Stage 10: `run_tests` runs `<target>/.venv/bin/python -m pytest`, creating the venv and installing `requirements.txt` (re-installs on change, self-heals pytest); `bootstrap_env.sh --target-dir`; anti-shadowing persona rule; `MAX_RETRIES=5` (ADR-009). Follow-ups: `pyproject.toml` (`pip install -e .`) support; interpreter version from the constitution.
- [x] Stage 9: supervisor loop in `run_agent.sh` — `git_commit_feature` writes `.agent-harness/run_successful`; up to `MAX_RETRIES=3` attempts, resumes with `opencode run --continue`; exit 0/3 (ADR-008). Follow-ups: a `blocked` marker so legitimate stops aren't retried; loop on plan milestones rather than first commit.
- [x] Stage 8: `fs_list` (JSON, excludes `.git`/`.venv`/`__pycache__`/`node_modules`/`.agent-harness`…) and `fs_write` (atomic, creates parents) behind `Sandbox`; 9 tools total (ADR-007). Follow-ups: configurable exclusions; optional `.gitignore` awareness; test-lock convention on the harness side.
- [x] Stage 7: `bin/bootstrap_env.sh`; Open Code is the agent CLI (`opencode run --agent SystemArchitect`, per-target `.agent-harness/opencode.json`); default model `gpt-oss:20b`; Anthropic env removed (ADR-006). Follow-ups: native Ollama adapter to separate gpt-oss reasoning; end-to-end run on a real spec.
- [ ] Fill `docs/SPECS.md` sections marked _TODO_.
- [ ] Rewrite the scaffold generator script later (deferred).
