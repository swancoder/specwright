# Agent Harness

Isolated orchestrator + MCP server connecting a local LLM to a separate target codebase,
with a Git-history-based temporal coupling knowledge graph (SQLite).

- Orientation and mandatory protocols: `CLAUDE.md`
- Technical reference: `docs/SPECS.md`
- Decisions: `docs/adr/`

## Quick start
```bash
./bin/bootstrap_env.sh                       # create venv, install requirements.txt
./bin/start_mcp.sh --target-dir <path>       # MCP server over stdio (+ SQLite graph)
./bin/run_agent.sh --spec <spec_id>          # run an agent session
python3 -m knowledge_graph.indexer --incremental
pytest tests/
```

## TODO
- [x] `git init` done; Stage 2 and Stage 3 committed on `main` (`git log`).
- [ ] `CLAUDE.md` references `bin/start_env.sh`; the real script is `bin/start_mcp.sh` — rename one of them.
- [x] Add `tests/` directory and a first `pytest` smoke test (`tests/test_indexer_storage.py`).
- [x] Add `.gitignore` (venv, `CLAUDE.md`, `prompts-hist/`, SQLite DB). Still TODO: `git init` + ADR-001 write-up; `.graphignore` lives in the *target* project.
- [x] ADR-002, ADR-003, ADR-004 written. TODO: ADR-001 (files linked from `CLAUDE.md` and `docs/SPECS.md` do not exist yet).
- [x] Stage 2: SQLite schema + `.graphignore` filter in `knowledge_graph/indexer.py` (ADR-002).
- [x] Stage 3: incremental indexer + self-healing + Jaccard query (ADR-003). Follow-ups: detect rewritten-but-not-pruned history via `git merge-base --is-ancestor`; rename tracking; don't wipe on transient git errors; `git log --all` includes abandoned branches.
- [x] Stage 4: MCP stdio server, 7 tools registered, `query_temporal_coupling` wired (ADR-004).
- [ ] Stage 5: implement the six stub tools (`read_constitution`, `read_specification`, `fs_read`, `fs_apply_patch`, `run_tests`, `git_commit_feature`) with target-dir sandboxing.
- [ ] Implement `bin/bootstrap_env.sh` and `bin/run_agent.sh` (agent loop).
- [ ] Fill `config/llm_backends.yaml` and `config/roles.yaml`.
- [ ] Fill `docs/SPECS.md` sections marked _TODO_.
- [ ] Rewrite the scaffold generator script later (deferred).
