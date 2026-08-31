# ADR-018: Read-Only Monitoring Dashboard & JSONL Audit Log

**Status:** Accepted
**Stage:** 19

## Context

ADR-017 gave `./harness run` a per-session transcript (`.agent-harness/audit_logs/<id>.log`) and a
placeholder `./harness ui`. That transcript is a flat human tee — fine to read in a terminal, but not
structured enough to visualize (which actor did what, which toolchain task passed, whether the gate
went green). We want a lightweight, read-only monitoring UI, and a structured event stream for it to
read, without pulling a heavy stack into the core dependency set or violating the Observer boundary.

## Decision

Two pieces:

### 1. `bin/audit_log.py` — structured JSONL event log

Append-only. Each event is one JSON object on its own line in `<id>.jsonl`:

```json
{"ts":"…Z","session_id":"…","actor":"harness|Planner|SystemArchitect|Verifier|toolchain|gate","action":"session_start|execute_toolchain_task|mechanical_gate|session_end|…", "status":"…","exit_code":0, "task":"…","payload":"…","result":"…"}
```

Every event is a single `os.write` of one already-newline-terminated line to an `O_APPEND` fd. This
gives the durability property the reader depends on: **writers never interleave, and a concurrent
reader sees at worst a torn *final* line — never a corrupt middle.** `./harness run` emits
`session_start` / `session_end` (best-effort, `|| true`, so logging can never break a run); richer
per-action events (agent turns, each toolchain execution, gate result) are appended by the harness /
MCP tools as that instrumentation lands — the dashboard already renders the whole schema.

### 2. `bin/ui_dashboard.py` — Streamlit Observer

- **Data helpers carry no Streamlit import** (`parse_jsonl`, `list_sessions`, `_is_ok`) so they stay
  unit-testable and the module imports cleanly without Streamlit installed; the UI (`_render`) is
  imported lazily and runs only under `streamlit run` (`__name__ == "__main__"`).
- **Robust parsing:** blank, corrupted, and torn-trailing lines are skipped, never raised — the file
  is safe to read while it is being appended to.
- **Sidebar:** `st.selectbox` over the `*.jsonl` sessions, newest first; a `🔄 Refresh` button and a
  native `time.sleep + st.rerun` auto-refresh toggle (no third-party `st_autorefresh` dependency).
- **Timeline:** one row per event; toolchain executions and gate results are colored via
  `st.success` / `st.error` from `status` / `exit_code`; large `payload` / `prompt` / `result` /
  `output` fields go inside `st.expander("View Details")` with `st.json` / `st.code`.
- **Strictly read-only:** no control writes a log or touches a harness process.

Streamlit is installed by `./harness install`, deliberately **not** in `requirements.txt`, so CI and
target provisioning stay lean.

## Consequences

- Sessions now have a machine-readable event stream and a real dashboard, alongside the human `.log`.
- A new event type is additive: emit it from `audit_log.py`; the dashboard renders known actions
  specially and anything else as a generic row.
- The Streamlit render path is not exercised in CI (no Streamlit there); the tested surface is the
  parser, the session listing, and the logger. The UI is kept thin to make that boundary safe.

## Alternatives considered

- **Keep parsing the flat `.log`.** No structure to color or filter; regex-scraping a human tee is
  brittle.
- **`streamlit-autorefresh` dependency.** Rejected — a `time.sleep`+`st.rerun` loop is native and
  dependency-free, and this is a low-frequency monitor.
- **A logging daemon / socket.** Overkill; append-only JSONL files are crash-safe, greppable, and
  need no running service to read after the fact.

## Follow-up: real per-action instrumentation

The initial cut emitted only session-level events. Follow-up wired the three real event sources so the
timeline reflects an actual run, keeping the logging module as the single dependency direction
(`bin/` and `mcp_server/tools/` → `mcp_server/core/audit.py`; `bin/audit_log.py` is now a thin CLI shim).

- **`mcp_server/core/audit.py`** — the logic moved here (canonical). `emit_env(action, actor=None, **fields)`
  reads `HARNESS_AUDIT_DIR` / `HARNESS_SESSION_ID` (and `HARNESS_ACTOR`) from the environment and is a
  safe no-op when unset, so any code path can be instrumented without a hard dependency on a session.
- **Env propagation** — `./harness run` exports `HARNESS_AUDIT_DIR` + `HARNESS_SESSION_ID`;
  `run_agent.sh` additionally exports `HARNESS_ACTOR` (the acting role) so a toolchain event emitted
  deep inside the MCP server is attributed correctly. Everything flows down the process tree
  (`harness → run_agent.sh → agent CLI → MCP server → completion gate`).
- **Sources:**
  - *Agent turns* — `run_agent.sh` emits `agent_turn` at the top of every implementer attempt and
    before each Verifier run, in both the Open Code and Claude Code loops (`actor` = role).
  - *Toolchain executions* — the `run_toolchain_task` MCP tool emits `execute_toolchain_task`
    (`actor` = `HARNESS_ACTOR`), and the completion gate emits one per lifecycle task (`actor` = `gate`).
  - *Mechanical gate* — `completion_checks.py` emits a `mechanical_gate` verdict (`status`, `gaps`,
    and the gap list as `result`).

All emissions are best-effort (`|| true` in bash, swallowed `OSError` in Python): a logging failure
can never break an agent turn, a toolchain run, or the gate. The dashboard renders `agent_turn`,
`execute_toolchain_task`, and `mechanical_gate` specially; any future action falls back to a generic row.
