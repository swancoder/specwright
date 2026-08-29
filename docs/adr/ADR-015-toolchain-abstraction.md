# ADR-015: Toolchain Abstraction Layer

## Status
Accepted

- **Date:** 2026-08-29
- **Stage:** 16

## Context
The execution and validation layers were hardcoded to Python tooling — `bin/completion_checks.py`,
`run_tests`, and `bootstrap_env.sh` invoke `python -m venv`, `pip`, `mypy`, `ruff`, `pytest`
directly. Changing the target's constitution to PHP or JS steers the *agent* but not the harness's
own build/test/lint machinery, which would fail on any non-Python stack. We need to decouple the
harness from a specific stack while keeping the existing Python path byte-identical.

## Decision
1. **Project config (`toolchain.json`).** A target may declare its stack and lifecycle commands:
   `{"stack": "...", "commands": {"install": "...", "lint": "...", "test": "...", "build": "..."}}`.
   When the file is absent (or malformed / empty), the harness transparently falls back to the
   **PythonToolchain** default — the historical `venv` + `pip install -r requirements.txt` + `mypy`
   + `ruff` + `pytest` behaviour, wrapped, not removed.
2. **`run_toolchain_task` MCP tool** (capability-gated via `--enable-tool run_toolchain_task`).
   Input: a single `task` ∈ {install, lint, test, build}. It resolves the toolchain, executes the
   mapped command via the sandbox, and returns JSON `{task, stack, command, status, exit_code,
   output_snippet}`.
3. **Output sanitisation & truncation** (`mcp_server/core/toolchain.py`). Payloads never exceed
   4 KiB: ANSI/OSC escapes and `\r` progress-bar overwrites are stripped; the snippet keeps the
   first 20 and last 50 lines with the middle replaced by `... [TRUNCATED N lines] ...`; before
   dropping the middle, lines matching critical markers (`Fatal error:`, `Exception`, `Traceback`,
   `FAIL`, `Failed asserting`, `AssertionError`, …) are rescued and appended after the marker.
4. **`completion_checks.py` refactor** (ADR-014 gate). The direct `mypy`/`ruff` invocations are
   replaced by a toolchain `lint` request. Python-default targets keep the hermetic build + test
   plus `lint` (= mypy + ruff); a `toolchain.json` target runs `install → test → lint` through the
   abstraction instead.

## Implementation notes
- `mcp_server/core/toolchain.py`: `resolve_toolchain(sandbox)` → `JsonToolchain` | `PythonToolchain`;
  each exposes `.stack`, `.command(task)`, `.run(sandbox, task) -> ToolchainResult` (stdout/stderr/
  exit_code/timed_out/skipped). `JsonToolchain` runs the declared string via `bash -c` inside the
  sandbox (cwd = target, scrubbed env). `PythonToolchain` maps tasks to argv sequences run through
  the sandbox; `test`/`lint` are `skipped` (not failed) when `<target>/.venv` is absent, preserving
  the old "no venv → no gap" behaviour.
- `run_toolchain_task` lives in `mcp_server/tools/toolchain_tools.py`, gated by
  `ctx.optional_tools` (same mechanism as `mark_spec_complete`), enabled on both the implementer and
  Verifier servers by `run_agent.sh`; added to `SystemArchitect`/`Verifier` `allowed_tools` and the
  canonical tool-name lists in `gen_opencode_config.py` / `recover_hint.py`.
- `completion_checks.py` puts the harness root on `sys.path` and imports the toolchain module; its
  standalone tests and the ADR-014 gate behaviour on Python targets are unchanged.

## Alternatives considered
- **Detect the stack from marker files** (`requirements.txt`/`composer.json`/`pom.xml`) instead of a
  declared file — convenient but implicit; an explicit `toolchain.json` is auditable and overrides
  cleanly. Detection can layer on later as a default when the file is absent.
- **A YAML profile in the harness `config/`** — keeps stack knowledge in the harness; the target is
  the right owner of "how do I build this", so the file lives in the target workspace.
- **Always-on tool** — the codebase's convention is capability-gating for tools that touch execution
  policy; gating keeps a read-only role (Planner) from gaining a build runner.

## Self-critique
- `JsonToolchain` runs project-declared shell via `bash -c`; the command is trusted repo content, but
  it is arbitrary code inside the sandbox (which bounds paths and env, not what a permitted command
  does — same trust model as `run_tests`).
- The rescue markers are English/keyword heuristics; an unusual failure format may not be rescued
  from a truncated middle (head+tail still shown).
- `PythonToolchain.build` is a no-op; a Python project with a real build step would need a
  `toolchain.json`.
- Truncation counts lines then caps bytes; a single enormous line is byte-capped but not line-split.

## Consequences
- Dropping a `toolchain.json` into a PHP/JS/Java project overrides install/lint/test/build for both
  the agent (`run_toolchain_task`) and the mechanical gate — no harness code change.
- Python targets are unaffected: no `toolchain.json` → identical behaviour, all prior tests pass.
- The agent gains a single, bounded, stack-agnostic way to run its own lifecycle checks.

## Prompt
`prompts-hist/016_toolchain_abstraction.txt` (local-only)
