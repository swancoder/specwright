# ADR-014: Reliability Hardening for Weak Local Models

## Status
Accepted

- **Date:** 2026-08-27
- **Stage:** 15

## Context
Runs 1–12 on `gpt-oss:20b` (local) surfaced recurring failure modes a hosted model (Sonnet)
never hit: a 4096-token context truncating the whole run, models that emit tool calls as plain
text, hallucinated tool names/arguments, a shim package written to satisfy an import, milestone
commits mistaken for completion, and packaging/type defects the (also-local) Verifier could not
see. This stage adds six model-agnostic guards, most valuable under weak local models.

## Decision
1. **Model pre-flight (`bin/preflight_model.py`)** — before an opencode/generic run against a
   local endpoint: assert the model's `num_ctx` ≥ declared `LLM_CONTEXT_LENGTH`, and that it
   returns a STRUCTURED `tool_calls` for a trivial request. Broken tool-calling aborts the run
   (exit 6); low context warns. `--skip-preflight` bypasses; skipped for Claude/non-local.
2. **Dropped-tool-call recovery (`bin/recover_hint.py`)** — when an implementer attempt makes no
   commit, scan its last output for tool-call-shaped text (`{"name":…}`, fenced JSON, `tool(…)`);
   if found, the resume prompt names the tool and tells the model to actually call it, instead of
   the generic nudge.
3. **Tool-interface tolerance** — (a) alias tools (`mcp_server/tools/aliases.py`) for common
   hallucinations (`fs_find→fs_list`, `open_file→fs_read`, `git_commit→git_commit_feature`, …),
   delegating to the real sandboxed handler and gated exactly like their canonical (a read-only
   role gets no write alias); (b) argument aliases via Pydantic `AliasChoices` for the variants a
   model guesses (`line_start`/`start` for `fs_read`, `timeout` for `run_tests`).
4. **Mechanical completion gate (`bin/completion_checks.py`)** — after the Verifier sets
   `spec_complete`, run model-independent checks in the target: a HERMETIC build (fresh venv from
   `requirements.txt`, fresh CWD, then pytest — catches a missing runtime dependency and CWD
   pollution the in-process Verifier hides), plus `mypy` and `ruff` when present. Any failure
   revokes `spec_complete` and feeds the gaps back. `--no-completion-checks` disables it.
5. **No-progress abort** — the loop hashes git HEAD + working tree before/after each implementer
   attempt; two consecutive attempts with no commit and no file change abort with exit 7 rather
   than burning every retry on a spinning model.
6. **Shadow-dependency write guard** — `fs_write` refuses to create a REPO-ROOT package whose name
   matches an installed dependency (builtin blocklist + names parsed from `requirements.txt`), so
   a model that hits `ModuleNotFoundError` cannot fake e.g. a `fastapi/` package (run 8). Project
   packages under `src/` (e.g. `src/http`) are unaffected.

## Implementation notes
- New scripts: `bin/preflight_model.py`, `bin/recover_hint.py`, `bin/completion_checks.py`
  (stdlib-only, each independently testable). `bin/harness_config.py` gains `mcptools <role>`
  (canonical + gated aliases as `mcp__agent-harness__…`), consumed by the Claude backend and the
  Open Code config generator so aliases inherit role gating.
- `run_agent.sh` flags: `--skip-preflight`, `--no-completion-checks`. New exit codes: 6 (model
  cannot tool-call), 7 (no progress). Both backend loops (Open Code, Claude) share the helpers
  `progress_key`, `recovery_hint`, `run_completion_gate`, `note_progress`.
- `ToolArgs` gains `populate_by_name=True` so a field's canonical name and its aliases both parse.
- Alias tools are registered by `build_all_tools` after the real tools; `EXPECTED_TOOLS` in the
  server tests is now a subset check.

## Alternatives considered
- **Server-side unknown-tool suggestions** — the client (Open Code/Claude) rejects an unknown tool
  before it reaches our server, so hints must be delegating alias *tools*, not error messages.
- **`extra="ignore"` on all args** — would silently accept garbage; targeted `AliasChoices` keeps
  the strict schema while absorbing the specific guesses observed.
- **Hermetic server launch** (start `uvicorn` and probe HTTP) — highest fidelity but needs a
  declared entrypoint/health route; the hermetic *build+test* is generic and already catches the
  missing-dependency and CWD-pollution blockers. Full launch remains a follow-up.

## Self-critique
- The mechanical gate runs a full `pip install` in a throwaway venv on every completion — seconds
  to minutes and network egress; `--no-completion-checks` exists for offline/cost-sensitive runs.
- `num_ctx` detection reads Ollama's `/api/show` `parameters`; a model whose context is set another
  way is reported as "unknown" (fails safe by warning).
- Alias tools enlarge the tool list the model sees; kept to a curated, high-frequency set.
- No-progress uses a working-tree hash; a change entirely inside an excluded dir would read as no
  progress. Excluded dirs are harness state, so this is intended.

## Consequences
- A local model that cannot tool-call, or a mis-loaded context, fails in seconds with a clear
  message instead of after five empty attempts.
- Dropped tool calls, hallucinated names/args, and shim packages are recovered or blocked at the
  harness, not left to chew through retries.
- `spec_complete` now means "Verifier agreed AND the code builds/types/lints in a clean env" —
  closing the packaging blind spot both audits found. Costlier per completion, disable-able.

## Prompt
`prompts-hist/015_reliability_hardening.txt` (local-only)
