# Agent Harness

Isolated orchestrator + MCP server connecting a local LLM to a separate target codebase,
with a Git-history-based temporal coupling knowledge graph (SQLite).

- Orientation and mandatory protocols: `CLAUDE.md`
- Technical reference: `docs/SPECS.md`
- Decisions: `docs/adr/`

## Quick start
```bash
./bin/bootstrap_env.sh                       # create venv, install requirements.txt
./bin/start_env.sh --target-dir <path>       # boot MCP server + SQLite (TODO: not yet present)
./bin/run_agent.sh --spec <spec_id>          # run an agent session
python3 -m knowledge_graph.indexer --incremental
pytest tests/
```

## TODO
- [ ] `git init` the harness and make the Stage 2 and Stage 3 commits (`feat(graph): sqlite schema and .graphignore filter [ADR-002]`, `feat(graph): incremental indexer and jaccard query [ADR-003]`).
- [ ] Add `bin/start_env.sh` (CLAUDE.md references it; scaffold has `start_mcp.sh` + `bootstrap_env.sh`) — decide whether to merge or rename.
- [x] Add `tests/` directory and a first `pytest` smoke test (`tests/test_indexer_storage.py`).
- [x] Add `.gitignore` (venv, `CLAUDE.md`, `prompts-hist/`, SQLite DB). Still TODO: `git init` + ADR-001 write-up; `.graphignore` lives in the *target* project.
- [x] ADR-002, ADR-003 written. TODO: ADR-001, ADR-004 (files linked from `CLAUDE.md` and `docs/SPECS.md` do not exist yet).
- [x] Stage 2: SQLite schema + `.graphignore` filter in `knowledge_graph/indexer.py` (ADR-002).
- [x] Stage 3: incremental indexer + self-healing + Jaccard query (ADR-003). Follow-ups: detect rewritten-but-not-pruned history via `git merge-base --is-ancestor`; rename tracking; don't wipe on transient git errors; `git log --all` includes abandoned branches.
- [ ] Stage 4: wire `query_temporal_coupling` to `DatabaseManager.query_coupled_files`; real MCP server in `mcp_server/main.py`; wire the seven tool stubs.
- [ ] Fill `config/llm_backends.yaml` and `config/roles.yaml`.
- [ ] Fill `docs/SPECS.md` sections marked _TODO_.
- [ ] Rewrite the scaffold generator script later (deferred).
