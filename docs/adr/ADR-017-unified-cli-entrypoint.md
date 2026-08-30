# ADR-017: Unified CLI Entry Point (`./harness`)

**Status:** Accepted
**Stage:** 18

## Context

The harness had grown to a dozen scripts under `bin/` (`bootstrap_env.sh`, `start_mcp.sh`,
`run_agent.sh`, `init_project.sh`, `completion_checks.py`, `preflight_model.py`, …). New users had
to know which script to call, in which order, with which flags. There was also no first-class way to
*observe* a run: the supervisor streams to the terminal and leaves per-commit markers, but nothing
collects a session's output in one place.

We want a single, discoverable front door for the three things a developer does day to day —
**install**, **run**, and **watch** — without rewriting the underlying scripts.

## Decision

Add a root-level executable `./harness` (bash, `set -euo pipefail`, ANSI colour, `help`) that routes
to three subcommands and owns a new per-session audit log.

- **`harness install [--no-deps]`** — create/reuse `.venv`, `pip install -r requirements.txt` **plus
  `streamlit`**, verify the MCP entry points (`bin/start_mcp.sh`, `mcp_server/main.py`) exist, and
  create `.agent-harness/audit_logs/`, adding `.agent-harness/` to `.gitignore`. `--no-deps` does the
  filesystem/venv setup but skips pip (fast path for CI/tests).
- **`harness run "<task>" [args…]`** — require a non-empty task description, generate a unique
  timestamp+pid `SESSION_ID`, **export** it (`SESSION_ID` and `HARNESS_SESSION_ID`) so downstream
  logging/MCP can tag events, then exec `bin/run_agent.sh` with the remaining args forwarded verbatim
  (`--spec <id> --target-dir <path> …`). Output is tee'd to
  `.agent-harness/audit_logs/<SESSION_ID>.log`; the supervisor's exit code is preserved via
  `PIPESTATUS[0]` — never swallowed.
- **`harness ui [--port N] [-d]`** — launch a read-only Streamlit dashboard
  (`bin/ui_dashboard.py`) that renders the audit logs. Foreground by default (reports the URL/port
  first); `-d` detaches it.

### Why the task description is a *label*, not the whole input

The harness is spec-driven: the real inputs are `--spec` and `--target-dir`. Rather than invent a
second, free-text execution path, `harness run` takes the task description as the human-readable
session label (it seeds the SESSION_ID header and the audit log) and forwards the actual flags to the
one supervisor. Validation of `--spec`/`--target-dir` stays in `run_agent.sh` — a single source of
truth.

### Observer pattern

`bin/ui_dashboard.py` only ever **reads** `.agent-harness/audit_logs/`. It never writes agent state,
so launching the UI cannot block or corrupt a session; runs proceed in parallel in another terminal.
Streamlit is installed by `harness install` but kept **out of `requirements.txt`** so the core
dependency set (and CI) stays lean.

## Consequences

- One documented entry point; `bin/*` scripts remain callable and unchanged.
- Every session now leaves a single collected transcript under `.agent-harness/audit_logs/<id>.log`.
- New heavy-ish dev dependency (streamlit), but only via `harness install`, never in CI.
- `bin/ui_dashboard.py` is a placeholder (session list + raw transcript); richer views are future work.

## Alternatives considered

- **A Python `argparse` CLI (`python -m harness`).** More portable parsing, but the subcommands are
  thin wrappers over bash scripts; a bash router keeps exit-code passthrough and `exec` trivial and
  avoids a Python process in front of every call.
- **`streamlit` in `requirements.txt`.** Simpler install, but pulls a large UI stack into every CI
  run and every target-provisioning step for a tool most invocations never use.
- **Tail the terminal instead of an audit log.** Loses history and can't be observed after the fact
  or by a second viewer.
