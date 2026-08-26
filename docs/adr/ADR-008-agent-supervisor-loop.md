# ADR-008: Agent Supervisor and Recovery Loop

## Status
Accepted

- **Date:** 2026-08-26
- **Stage:** 9

## Context
Local LLMs occasionally drop JSON tool-call formatting and emit plain text. Open Code interprets this plain text as a final conversational response and terminates the session prematurely. This prevents fully autonomous execution, as the agent stops before reaching the final `git_commit_feature` call.

## Decision
We will implement a supervisor loop in the orchestrator to automatically detect premature termination and resume the agent.

1. **Completion Marker (`mcp_server/tools/git_tools.py`):** 
   - The `git_commit_feature` tool is the strict endpoint of any successful spec implementation.
   - Upon a successful git commit, this tool must write an empty marker file at `.agent-harness/run_successful` inside the target directory.
2. **Supervisor Loop (`bin/run_agent.sh`):**
   - At startup, the script must ensure any old `.agent-harness/run_successful` marker is deleted from the target directory.
   - Wrap the agent execution in a `for` loop with a maximum of `MAX_RETRIES=3`.
   - On the first iteration, run the standard initiation command with `opencode run`.
   - After the command exits, check for the existence of `.agent-harness/run_successful`.
   - If the marker exists, print a success message and `exit 0`.
   - If the marker does NOT exist and `MAX_RETRIES` is not reached, resume the session with: `opencode run --dir <target> -m <model> --agent SystemArchitect --continue "Continue with the plan. Your last message was plain text instead of a tool call. You must use the appropriate MCP tool to proceed, or call git_commit_feature if you are done."`
   - If the loop exhausts all retries without seeing the marker, exit with a non-zero code.

## Implementation notes
- `git_tools.RUN_SUCCESSFUL_MARKER = ".agent-harness/run_successful"`. `git_commit_feature()`
  writes it (via `write_marker(sandbox)`, `mkdir -p` on `.agent-harness/`) only after
  `git commit` returned 0 and `HEAD` was resolved; the `"nothing to commit"` path and every
  error path leave no marker. The marker is empty; its existence is the signal. The path is
  under `.agent-harness/`, already excluded from staging by `LOCAL_ONLY_PATHSPECS`, so the
  marker can never end up in the target's history.
- `bin/run_agent.sh`: `MAX_RETRIES` is an environment override (default 3, must be ≥ 1) and is
  the total number of attempts (1 initial + up to 2 resumes). Startup deletes a stale marker
  before the first attempt. Attempt 1 runs `opencode run … "<kickoff prompt>"`; attempts 2..N
  run `opencode run --dir <target> -m <backend>/<model> --agent <role> --continue "<resume prompt>"`.
  `--continue` resumes Open Code's most recent session for that directory, preserving the tool
  transcript, so the model sees its own dropped text and the recovery instruction. The
  resume prompt is the ADR text verbatim.
- Every attempt is logged (`attempt i/N`, agent exit code). Success prints
  `run_agent.sh: run successful (marker … after attempt i)` and exits 0; exhaustion exits 3.
  The generic (non-Open Code) `AGENT_CMD` path has no `--continue` semantics and runs a single
  attempt, still gated on the marker for its exit code.
- `--dry-run` prints the initial and the resume command and never executes anything.
- Tests: `tests/test_git_tools.py` (marker written on commit; absent on nothing-to-commit,
  bad message, non-repo; stale marker not removed by the tool itself) and
  `tests/test_run_agent.py` (fake `AGENT_CMD` script counting invocations: success on attempt
  2 → exit 0 with exactly one `--continue`; never succeeds → 3 attempts, exit 3; stale marker
  deleted at startup so a no-op agent cannot pass).

## Alternatives considered
- **Parse Open Code's JSON event stream for "final message without tool call"** — couples the
  supervisor to Open Code's output format and to per-model quirks; a filesystem marker set by
  the harness's own endpoint tool is model- and CLI-agnostic.
- **Unlimited retries until the marker appears** — a confused model would loop forever;
  bounded attempts plus a non-zero exit make the failure visible to the caller.
- **Marker written by `run_tests` on green instead of by `git_commit_feature`** — passing tests
  is not the deliverable; the commit is.

## Self-critique
- The marker signals *a* successful commit, not that the whole spec is done; an agent that
  commits after milestone A ends the run "successfully". Acceptable: the plan tells the agent
  to commit per milestone, and a follow-up stage can loop on the plan rather than on the commit.
- `--continue` picks Open Code's latest session in that directory; a human running
  `opencode` in the target between attempts would be resumed instead. Unlikely in an
  autonomous run, noted.
- The resume prompt assumes the failure was a dropped tool call; if the agent stopped for a
  legitimate reason (a constitution conflict it reported), the retries nag it twice before
  giving up. The agent's own stop message remains in the transcript, so the outcome is still
  legible; a future "blocked" marker could short-circuit this.
- `MAX_RETRIES` names the total attempts, matching the ADR's `for` loop bound, even though
  "retries" strictly would mean N-1.

## Consequences
- A single dropped tool call no longer ends an autonomous run; the agent gets up to two
  nudges back into the tool loop with its context intact.
- Callers (humans, CI, a chat gateway) can rely on the exit code: 0 = committed, 3 = gave up,
  2 = usage error.
- `git_commit_feature` is now formally the run's terminal state; personas and plans should
  end with it.
- One more file lives under `.agent-harness/` in the target; it stays untracked.

## Prompt
`prompts-hist/009_supervisor_loop.txt` (local-only)
