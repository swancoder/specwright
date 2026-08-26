# ADR-009: Target Environment Management

## Status
Accepted

- **Date:** 2026-08-26
- **Stage:** 10

## Context
During an extended autonomous run, the agent wrote valid code but failed test execution because `run_tests` executed inside the Harness's own `.venv` rather than the target project's environment. This missing dependency context caused the agent to hallucinate a shim package to satisfy imports. We need test execution to respect and utilize the target's specific virtual environment.

## Decision
1. **Target Execution Environment (`mcp_server/tools/test_tools.py`):**
   - Refactor `run_tests` to check for a `.venv` directory inside the target project.
   - If the environment does not exist, `run_tests` must proactively create it (`python3 -m venv .venv`) and install dependencies (`<target>/.venv/bin/pip install -r requirements.txt`) before running tests.
   - Test execution must use the target's python binary: `<target>/.venv/bin/python -m pytest`.
2. **Pre-provisioning (`bin/bootstrap_env.sh`):**
   - Add a `--target-dir` argument to allow manual pre-provisioning of a target's environment.
3. **Persona Guardrail (`config/roles.yaml`):**
   - Update the `SystemArchitect` prompt to explicitly forbid creating local packages that shadow external dependencies (e.g., `fastapi/`, `pydantic/`). Instruct the agent to report missing dependencies instead of hacking around them.
4. **Retry Loop (`bin/run_agent.sh`):**
   - Increase `MAX_RETRIES` from 3 to 5 to accommodate longer debugging cycles on complex greenfield tasks.

## Implementation notes
- `test_tools.ensure_target_env(sandbox) -> (python, actions)` is the single provisioning
  primitive. It creates `<target>/.venv` with the harness interpreter's `-m venv` when
  `.venv/bin/python` is missing, then installs `requirements.txt` via
  `<target>/.venv/bin/python -m pip install -r requirements.txt` (the `python -m pip` form
  is used instead of the `pip` shim so it works on fresh venvs everywhere). All commands go
  through `Sandbox.run` (cwd = target, scrubbed env, timeouts: 300 s venv, 900 s pip).
- **Requirements drift:** a SHA-256 of `requirements.txt` is stored in
  `.venv/.harness-requirements.sha256`; when the file changes (the agent may add a
  dependency mid-run) the next `run_tests` re-installs before testing. Provisioning is
  therefore idempotent and cheap on the happy path (one `stat` + one hash).
- **pytest self-heal:** if the target venv cannot `import pytest` (requirements without
  pytest, or no `requirements.txt` at all) the tool installs `pytest` into the target venv so
  the runner is always available. No `requirements.txt` yields an empty venv + pytest and an
  explicit `no requirements.txt` action in the report.
- Provisioning failures (`venv` creation or `pip install` non-zero) raise `ToolError` with the
  captured stderr, so the agent sees *"pip install failed: …"* instead of an unrelated
  `ModuleNotFoundError` that invites shims.
- `run_tests` report gains `"python"` (repo-relative interpreter used) and `"env_actions"`
  (what provisioning did on this call). gradle/npm runners are unchanged.
- `python -m mcp_server.tools.test_tools --target-dir <path>` exposes the same provisioning
  for humans; `bin/bootstrap_env.sh --target-dir <path>` calls it after the harness checks.
- `config/roles.yaml`: new prohibition — never create a local package/module that shadows an
  external dependency; a `ModuleNotFoundError` means "report the missing dependency (add it to
  `requirements.txt` if the spec/constitution allows) and re-run `run_tests`". `.venv` is already
  in `fs_tools.EXCLUDED_DIRS`, so the agent never lists or writes into the environment.
- `bin/run_agent.sh`: `MAX_RETRIES` default 5 (env override unchanged).

## Alternatives considered
- **Run tests in the harness venv with the target's requirements installed there** — pollutes
  the harness with per-target dependencies and versions; multiple targets would conflict.
- **Require the human to pre-provision `.venv` and fail otherwise** — simpler, but the ADR asks
  for autonomous runs and the first `run_tests` on a fresh clone would always fail.
- **`uv`/`poetry` instead of `venv + pip`** — faster, but adds a tool dependency to every target
  machine; the stdlib approach works everywhere Python 3.12 does.
- **Detect shim packages in the harness (`fs_write` refusing `fastapi/__init__.py`)** — would
  need a dependency list to compare against; the persona rule plus a working environment
  removes the incentive instead.

## Self-critique
- `pip install` reaches the network from inside the sandbox — the first tool that does. The
  sandbox bounds *what* runs, not egress; a pinned, reviewed `requirements.txt` is the control.
- The venv uses the harness's interpreter version (`sys.executable -m venv`), not necessarily
  the version the target's constitution names; fine while both are 3.12+.
- A target that uses `pyproject.toml` without `requirements.txt` gets only pytest; extending
  provisioning to `pip install -e .` is a follow-up.
- The venv-creation timeout (300 s) and pip timeout (900 s) are generous; on a cold network the
  first `run_tests` can take minutes, which counts against the agent's wall-clock.

## Consequences
- `run_tests` finally answers the question the agent is asking — "does my code pass?" —
  instead of "is FastAPI installed in the harness?".
- Targets gain a self-managed, untracked `.venv` (excluded from listing, staging and writes).
- Dependency additions by the agent take effect on the next `run_tests` without a human step.
- Autonomous runs get five attempts; combined with ADR-008 a long debugging cycle survives
  several dropped tool calls.

## Prompt
`prompts-hist/010_target_env.txt` (local-only)
