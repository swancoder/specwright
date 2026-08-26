# Agent Harness

Isolated orchestrator + MCP server connecting a local LLM to a separate target codebase,
with a Git-history-based temporal coupling knowledge graph (SQLite).

- Orientation and mandatory protocols: `CLAUDE.md`
- Technical reference: `docs/SPECS.md`
- Decisions: `docs/adr/`

## Quick start
```bash
./bin/bootstrap_env.sh                       # venv + requirements; checks node/opencode/ollama/gpt-oss:20b
./bin/start_mcp.sh --target-dir <path>       # MCP server over stdio (+ SQLite graph)
./bin/run_agent.sh --spec <spec_id> --target-dir <path>   # Open Code + MCP + gpt-oss:20b via Ollama
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
- [x] Stage 7: `bin/bootstrap_env.sh`; Open Code is the agent CLI (`opencode run --agent SystemArchitect`, per-target `.agent-harness/opencode.json`); default model `gpt-oss:20b`; Anthropic env removed (ADR-006). Follow-ups: native Ollama adapter to separate gpt-oss reasoning; end-to-end run on a real spec.
- [ ] Fill `docs/SPECS.md` sections marked _TODO_.
- [ ] Rewrite the scaffold generator script later (deferred).
