# ADR-010: Harness Stability and Tool Refinements

## Status
Accepted

- **Date:** 2026-08-26
- **Stage:** 11

## Context
Analysis of an extended autonomous run (Run 9) revealed minor inefficiencies and edge cases in the toolset: context bloat from reading entire large files, glob-character hallucinations in paths, unintentional staging of database files, and the agent getting stuck in loops without yielding.

## Decision
We will implement the following refinements across the Harness:

1. **`fs_read` Line Ranges:** 
   - Add optional `start_line` and `end_line` (1-indexed) integer arguments to `fs_read`.
   - If provided, return only the specified slice of the file.
2. **Glob-char Path Rejection:** 
   - Update the `Sandbox` primitive or individual tool validation to explicitly reject paths containing `*` or `?`. Tools must enforce exact pathing.
3. **Staging Exclusions (`git_commit_feature`):** 
   - Prevent staging of `.db`, `.sqlite`, or `*.db3` files to avoid polluting the repository with local state.
4. **`run_tests` Timeout Refinement:** 
   - Ensure the timeout is strictly enforced (e.g., 60 seconds default) and explicitly communicated to the agent in the tool's return payload if it triggers.
5. **Persona Rules Update (`config/roles.yaml`):** 
   - **Milestone Commits:** Encourage the agent to call `git_commit_feature` to save progress when a major milestone in the plan is reached, rather than waiting for the entire spec to be done.
   - **Honesty/Stuck Rule:** If the agent fails to fix a test after 3 attempts (e.g., due to a framework quirk), it must state this honestly in a final message and stop, rather than hallucinating fake packages or wild syntax changes.

## Implementation notes
- **`fs_read(filepath, start_line=None, end_line=None)`** — both 1-indexed and inclusive; either
  may be omitted (`start_line` alone reads to EOF, `end_line` alone reads from line 1).
  Validation: integers ≥ 1, `start_line ≤ end_line`, `start_line ≤ number of lines`;
  `end_line` beyond EOF is clamped. A slice is prefixed with a one-line header
  `# <path>: lines a-b of N` so the model never mistakes a slice for the whole file. The
  512 KiB size cap still applies to the file as a whole.
- **Glob rejection lives in `Sandbox.resolve()`** — a single choke point every tool passes
  through (`fs_read`, `fs_write`, `fs_apply_patch`, `fs_list`, `run_tests`, spec/constitution
  readers). Rejected characters: `*`, `?`, `[`, `]` (all four are glob metacharacters; the
  ADR names the first two). Raises `SandboxViolation("glob characters are not allowed …")`.
  Run 9 produced a real file literally named `src/???`; this makes that impossible.
- **Staging** — `LOCAL_ONLY_PATHSPECS` gains `:!*.db`, `:!**/*.db`, `:!*.sqlite`, `:!**/*.sqlite`,
  `:!*.sqlite3`, `:!**/*.sqlite3`, `:!*.db3`, `:!**/*.db3` (git pathspec globs, top level and
  nested). A `feedback.db` created by the app's default `DATABASE_PATH` can no longer land in
  a commit.
- **`run_tests(test_target, timeout_seconds=60)`** — the model repeatedly guessed a `timeout`
  argument in run 9, so it now exists: default **60 s**, range 1–600. Enforcement is
  `subprocess.run(timeout=…)` inside `Sandbox.run` (the process is killed). On expiry the
  report carries `"timed_out": true`, a top-level `"timeout_message"` ("run_tests killed after
  60s — …"), the same banner at the head of `stderr`, and `"exit_code": -1`. The report also
  echoes `"timeout_seconds"` so the agent can see the budget it used. Provisioning timeouts
  from ADR-009 (venv 300 s, pip 900 s) are unchanged and separate.
- **Persona** — workflow step 7 now reads "commit at every green milestone of the plan"
  (progress in run 9 evaporated when context compaction hit after 150 turns); a new
  "stuck rule": after three failed attempts at the same failing test, stop with an honest
  report of what was tried and the exact error — never fake packages, never rewrite syntax
  at random, never describe work as committed unless `git_commit_feature` returned
  `committed …`.

## Alternatives considered
- **Glob rejection per tool** — duplicated logic; `Sandbox.resolve()` already owns path policy.
- **Return slices as JSON `{lines: [...]}`** — heavier for the model to reason over than plain
  text with a header line.
- **Timeout only via `TEST_TIMEOUT_SECONDS`** — hiding the budget from the agent was exactly
  what made it guess an argument; exposing it (bounded) is cheaper than fighting it.
- **Git `.gitignore` in the target for `*.db`** — the target's ignore file is the project's
  business; the harness must never commit local state regardless of it.

## Self-critique
- A 60 s default is tight for real integration suites; the agent can raise it to 600 s, and
  the default is a constant one line away.
- `[`/`]` rejection excludes legitimately named files with brackets — rare in source trees,
  and the ADR's intent (exact pathing) outweighs it.
- Line-range reads shift responsibility to the model to ask for the right window; the header
  mitigates but cannot prevent a model editing based on a partial view.
- The stuck rule is prompt-only; the supervisor loop (ADR-008) will still nudge a stopped
  agent up to `MAX_RETRIES` times. A `blocked` marker remains the roadmap item.

## Fixes (post-hoc)
- `git_commit_feature` now stages with `git add -A` and then unstages `NEVER_COMMIT_PATHS`
  (`CLAUDE.md`, `prompts-hist`, `.agent-harness`, `.venv`, `__pycache__`, `*.db*`) via `git reset`,
  instead of `git add -A -- . :!<pathspec>`. The old form errored (`paths are ignored … use -f`)
  when the target's `.gitignore` already excluded `.agent-harness` — which `bin/init_project.sh`
  (ADR-016) correctly adds. Nested paths use `:(glob)` pathspecs. (commit tagged `[ADR-001]`.)

## Consequences
- Every path the agent supplies is exact; glob hallucinations fail fast with a clear message.
- Large files can be read in windows, keeping tool output (and context) small.
- Repository history cannot acquire local SQLite state through the harness.
- Test timeouts are visible and bounded; a hung suite costs at most the agent's chosen budget.
- Progress is banked per milestone, and honest stops replace hallucinated completions.

## Prompt
`prompts-hist/011_harness_stability.txt` (local-only)
