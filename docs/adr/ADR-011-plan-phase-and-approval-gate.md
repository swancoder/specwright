# ADR-011: Plan Phase and Human Approval Gate

## Status
Accepted

- **Date:** 2026-08-26
- **Stage:** 12

## Context
The target project's own rules (constitution rule 1, `CLAUDE.md` workflow #1, the header of
`specs/template/03_plan.md`) say the agent derives `03_plan.md` from `02_spec.md`, then
**stops for human approval**, and implementation starts only afterwards. The harness had a
single mode — "Implement spec N" — so an empty plan led the implementer either to skip the
plan or to write it and continue without any human in the loop. Run 9 also showed the value
of a reviewed plan: the agent followed `03_plan.md` almost file-for-file.

## Decision
1. **Two phases in `bin/run_agent.sh --phase plan|implement`** (default `implement`).
   - `plan`: launches the `Planner` persona with a kickoff prompt to read constitution, spec
     and `specs/template/03_plan.md`, write `specs/<feature>/03_plan.md`, commit it with
     `docs(spec): add implementation plan for <id> [<id>]`, and stop. The commit produces the
     ADR-008 `run_successful` marker, so the supervisor loop is reused unchanged.
   - `implement`: unchanged behaviour, but guarded by the approval gate below.
2. **Approval signal = the plan's pre-flight checkboxes.** A plan is approved when its
   `## Pre-flight` section exists and every `- [ ]` item is ticked (`- [x]`). Humans tick the
   boxes after reviewing; the Planner is instructed to leave them unticked.
3. **Gate enforcement (`bin/plan_gate.py`)** — `locate` resolves `specs/<id>*/03_plan.md`
   (unique directory match) and `check` returns 0 = approved, 4 = unticked items (listed),
   5 = missing/empty plan or no pre-flight section. `run_agent.sh --phase implement` refuses
   to launch on 4/5 (`--skip-plan-gate` overrides, printing a warning).
4. **Write scope enforced by the harness, not just the prompt.** `mcp_server.main` accepts
   repeatable `--write-scope <prefix>`; `fs_write` and `fs_apply_patch` raise
   `SandboxViolation` for paths outside every scope. The plan phase generates `mcp.json` with
   `--write-scope specs`, so a Planner cannot touch `src/` or `tests/` even if the model tries.
5. **Per-role tool exposure.** `roles.yaml → allowed_tools` is honoured in the generated
   Open Code agent: harness tools not in the list are disabled (`agent-harness_<tool>: false`).
   The Planner has no `run_tests`, `fs_apply_patch` or `query_temporal_coupling`.

## Implementation notes
- `HarnessContext.write_scopes: tuple[str, ...]` (empty = unrestricted) with
  `check_write(relpath)`; `fs_tools.fs_write/fs_apply_patch` take `write_scopes` and call
  `enforce_write_scope()` after sandbox resolution (so traversal is rejected first). Scopes are
  repo-relative directory prefixes compared lexically on POSIX paths.
- `harness_config.py tools <role>` prints the role's `allowed_tools`; `run_agent.sh` feeds it
  to the `opencode.json` generator.
- Phase-specific prompts live in `run_agent.sh`; the plan prompt names the exact plan path.
  The Planner persona (`config/roles.yaml`) forbids ticking pre-flight boxes, writing outside
  `specs/`, and inventing requirements — open questions go into the plan's Risks/Open
  questions instead.
- Exit codes of `run_agent.sh`: 0 success, 2 usage, 3 retries exhausted, 4 plan not approved,
  5 plan missing/empty.
- The target may hold staged-but-uncommitted human edits; `git_commit_feature` stages `-A`, so
  the plan commit would sweep them in. `run_agent.sh` warns when the target has uncommitted
  changes at launch (both phases).

## Alternatives considered
- **`Status: approved` line in the plan** — a second signal to maintain; the template already
  carries pre-flight boxes and humans naturally tick them.
- **One persona doing plan + implement in a single session** — no approval gate; contradicts
  the project's own workflow.
- **Prompt-only write restriction for the Planner** — run 8/9 showed prompts are advisory for
  a 20B model; a harness-enforced scope makes the gate real.

## Self-critique
- All four template boxes must be ticked, including "Branch created" — a human step the
  harness does not perform. Intentional (it is the approval act), but easy to forget.
- The Planner can still write anything under `specs/` (e.g. edit the spec itself). Acceptable:
  it is reviewed before approval, and git history shows it.
- The gate parses Markdown checkboxes; a plan with a differently named section is treated as
  unapproved (exit 5) rather than silently passing.

## Consequences
- The workflow is now: `--phase plan` → human reviews/edits and ticks pre-flight → `--phase
  implement`. Two runs, one human step in between.
- Write scope is a reusable primitive; future roles (reviewer, docs) can be confined the same
  way.
- Implementation runs can no longer start from an empty or unreviewed plan.

## Prompt
`prompts-hist/012_plan_phase.txt` (local-only)
