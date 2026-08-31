# ADR-019: Auto-fix Task — Acting on Tool Recommendations

**Status:** Accepted
**Stage:** 20

## Context

In the first live gpt-oss run (branch `gpt-oss/001-feedback-widget-run2`) the mechanical gate
correctly refused to certify the spec because `ruff` reported an unused import:

```
tests/integration/test_001_feedback_api.py:1:8: F401 `json` imported but unused
Found 1 error. [*] 1 fixable with the `--fix` option.
```

The agent *tried* to fix it by hand (`fs_apply_patch`), but its search/replace didn't match exactly
(a trailing-newline difference), so the patch silently no-op'd; the model then confidently reported
"all lint checks now pass" while the import was still there. The tool output literally recommended
the remedy — **"fixable with the `--fix` option"** — and the agent had no clean way to apply it.

We want the agent to read tool output and act on such automatic-fix recommendations instead of
hand-editing what an auto-fixer resolves deterministically.

## Decision

Add a mutating **`fix`** lifecycle task to the toolchain abstraction and let the implementer invoke
it, driven by a persona rule to act on auto-fix recommendations.

- **`fix` task** (`mcp_server/core/toolchain.py`): Python default runs `ruff check --fix .`; a
  `toolchain.json` stack supplies its own (`eslint --fix`, `php-cs-fixer fix`, …). Declared in
  `TASKS`; also listed in `MUTATING_TASKS` because it changes the working tree.
- **Capability-gated** (`run_toolchain_task`): `fix` runs only when the server was started with the
  `run_toolchain_task_fix` capability. `run_agent.sh` enables it for the **implementer** in both
  backends; the read-only **Verifier** gets `run_toolchain_task` without it. A gated call raises a
  `ToolError` → the agent sees a clean tool error, never a crash.
- **Persona** (`config/roles.yaml`): SystemArchitect step 7 — when a tool's output recommends an
  automatic fix, call `run_toolchain_task` `fix`, then re-run `lint`/`run_tests` and commit; prefer
  the recommended fixer over a manual patch that must match exact text. Verifier is explicitly told
  not to run `fix`.

### Why the gate does not auto-fix

The mechanical completion gate stays read-only (install/test/lint only). If it — or the Verifier —
silently applied `fix`, it would be grading a tree it just cleaned, defeating the honest check. Only
the implementer mutates; the Verifier and the gate observe. That is why `fix` is a capability the
implementer alone holds.

## Consequences

- Auto-fixable lint/format issues (unused imports, import order, formatting) are resolved
  deterministically by the tool the agent was told to use, instead of by a brittle hand-edit.
- The fix is a normal committed agent action, so the independent gate still certifies honestly —
  it passes because the code genuinely lints clean, not because the check was lowered.
- Generalises across stacks via `toolchain.json` (`fix` added to the node-typescript and php-js
  templates; java stacks omit it — no standard safe auto-fixer).
- `fix` events appear on the dashboard as `execute_toolchain_task task=fix` with a duration.

## Alternatives considered

- **Teach the model to hand-edit better.** The failure mode (exact-match patch no-op) is inherent to
  hand-editing; a deterministic auto-fixer removes the class of bug.
- **Let the gate run `--fix` itself.** Rejected — it would make the honesty gate complicit in
  passing itself.
- **A separate `apply_fixes` tool.** Redundant with the toolchain abstraction; `fix` as a lifecycle
  task reuses `run_toolchain_task`, its auditing, and per-stack `toolchain.json` declaration.
