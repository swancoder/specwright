# ADR-013: Claude Code Backend

## Status
Accepted

- **Date:** 2026-08-27
- **Stage:** 14

## Context
The harness was built entirely around Open Code as the agent CLI (ADR-006), driving the plan
phase (ADR-011) and the implement→verify loop (ADR-012). To compare a frontier hosted model
against the local `gpt-oss:20b` on the *same* process, we need Claude Code (Anthropic) as an
alternative backend that runs the identical plan → approve → implement → verify sequence, so the
only variable is the agent (CLI + model).

## Decision
Add a **Claude Code backend** to `bin/run_agent.sh`, selected when `AGENT_CMD` resolves to
`claude`. It reuses every CLI-agnostic part of the harness — the MCP server, roles, markers,
write-scope, `--enable-tool`, the plan gate, and the supervisor loop shape — and differs only in
how a role session is launched and resumed.

1. **Backend detection.** `basename $AGENT_CMD` → `opencode` | `claude` | `generic`. The Open
   Code path (ADR-006/011/012) is unchanged; `generic` remains the single-shot fallback.
2. **MCP wiring.** Claude Code reads `--mcp-config <file>` (the same `mcpServers` JSON the harness
   already writes). Two configs: `mcp.json` (implementer; `--write-scope specs` in the plan phase)
   and `mcp.verifier.json` (`--enable-tool mark_spec_complete`). No `opencode.json`.
3. **Role = system prompt + tool allowlist.** Claude Code has no `--agent`; a role is applied as
   `--append-system-prompt "<role system_prompt>"` plus `--allowedTools` limited to that role's
   `mcp__agent-harness__<tool>` names. Built-in tools (`Bash`, `Edit`, `Write`, `Read`, …) are
   `--disallowedTools`-ed: Claude Code's own OS sandbox is often unavailable, so MCP-only is the
   security boundary — the agent can touch the target *only* through the sandboxed MCP tools.
4. **Session model.** Each role run is one `claude -p "<prompt>" --output-format json` invocation
   (a full internal agentic loop). `session_id` and the final `result` text are parsed from the
   JSON. The implementer is resumed across iterations with `--resume <session_id>`; the Verifier
   runs fresh each time. `result` from the Verifier is the gap list fed back to the implementer.
5. **Loop parity.** Same markers and success condition as ADR-008/012: `run_successful` per commit,
   `spec_complete` (Verifier-set) as the implement-phase success. `--model` (default `sonnet`)
   picks the Anthropic model; `MAX_RETRIES` unchanged.

## Implementation notes
- `--model <name>` CLI passthrough (also `CC_MODEL` env; default `sonnet`).
- `stdin` is redirected from `/dev/null` (Claude Code otherwise waits for piped input); the target
  project is trusted once in `~/.claude.json` so its `.claude/settings.json` permission entries do
  not spam warnings. The target's PreToolUse hooks match `Bash|Edit|Write`, all disallowed, so they
  never fire.
- `--allowedTools`/`--disallowedTools` are passed comma-joined in a single argument each.
- Every raw JSON result is appended to `<target>/.agent-harness/claude.jsonl`; stderr to
  `claude.err`. These are under the never-staged `.agent-harness/`.
- The Open Code and generic paths are byte-for-byte unchanged; all prior tests still pass.

## Alternatives considered
- **`--output-format stream-json`** for live tool traces — richer, but `json` gives a clean
  `session_id` + `result` in one blob, which is exactly what the loop consumes; progress is
  observed via git/markers instead.
- **Letting Claude Code use its built-in `Write`/`Bash`** — faster for the model, but bypasses the
  sandbox and write-scope entirely; MCP-only keeps the security model identical to Open Code.
- **A separate `run_agent_claude.sh`** — duplicates the loop; a backend branch keeps one supervisor.

## Self-critique
- Restricting Sonnet to MCP-only tools is unusual for it; if it insists on a built-in tool it will
  be denied and must recover via the system prompt. Observed to comply, but it is a constraint the
  model is not tuned for.
- `--resume` continues the most recent state of that session id; if the JSON parse of `session_id`
  fails, the implementer falls back to a fresh session (context carried only via the prompt).
- Cost is real and on the user's account (~$0.07 for a trivial call); a multi-iteration run is
  several dollars. The harness logs but does not cap spend.
- Claude Code's own sandbox being off means MCP-only is load-bearing, not defense-in-depth.

## Fixes (post-hoc)
- `cc_primary` / `cc_verifier` wrap the `claude` call in `set +e` … `set -e` so a non-zero
  CLI exit (e.g. a Claude Code postinstall warning) is captured into `rc`/`out` and the supervisor
  continues, instead of the `out=$(claude …)` assignment aborting the whole run under `set -euo
  pipefail`. Found during the java-gradle end-to-end validation.

## Consequences
- The full plan → approve → implement → verify process runs on Claude Code/Sonnet with one flag
  (`AGENT_CMD=claude`, optional `--model`), enabling an apples-to-apples comparison with the
  local-model run on the same spec, plan, and harness.
- The harness is now CLI-pluggable: a third backend would implement the same four operations
  (launch role, resume implementer, run verifier, capture session/result).

## Prompt
`prompts-hist/014_claude_code_backend.txt` (local-only)
