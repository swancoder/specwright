# ADR-016: Project Initialization Script

## Status
Accepted

- **Date:** 2026-08-29
- **Stage:** 17

## Context
Starting a new target project meant hand-creating the exact structure the harness expects — a
constitution, `CLAUDE.md`, a `specs/<feature>` skeleton, a stack-appropriate `toolchain.json`
(ADR-015), a `.gitignore`, and a git baseline. That is error-prone and the earlier Python-vs-stack
constitution mismatches showed how a wrong scaffold derails a run. With the toolchain templates
(ADR-015) in place, a one-command initializer can produce a runnable project for any supported stack.

## Decision
Add `bin/init_project.sh` — scaffolds a new target the harness can drive, with a choice of:
- **`--project-dir`** — where the project is created (required; prompted interactively otherwise).
- **`--stack`** — the toolchain the harness builds/tests with: `python` (default) | `php-js` |
  `node-typescript` | `java-maven` | `java-gradle`. Non-Python stacks copy the matching
  `templates/toolchains/<stack>.toolchain.json`; Python uses the built-in default (no file).
- **`--backend`** — the agent "harness" that will drive it: `opencode` (local, default model
  `gpt-oss:20b-32k`) | `claude` (default `sonnet`), with an optional `--model` override.

It writes: `.github/constitution.md` and `CLAUDE.md` (both parameterised by the stack's
language/test/lint rows), a `specs/template/` and a first `specs/<slug>/` with `01_intent.md`,
`02_spec.md`, and an **empty `03_plan.md`** (filled by the plan phase, approved by a human), a
`src/` placeholder, a stack-aware `.gitignore` (always excluding `.agent-harness/`), and a git
baseline commit. It finishes by printing the exact `run_agent.sh` plan and implement commands for
the chosen backend/model.

Flags: `--name`, `--spec-slug` (validated), `--yes` (non-interactive, accept defaults), `--force`
(allow a non-empty directory). Missing values are prompted only on a tty; `--yes` or a pipe makes
it fully non-interactive (and testable).

## Implementation notes
- Pure Bash, `set -euo pipefail`; no writes outside `--project-dir`.
- The generated constitution keeps the "constitution wins / changing a stack row needs an ADR"
  contract so the agent still refuses genuine conflicts.
- The baseline commit uses fallback git identity env vars so it works in a fresh CI/container.
- `tests/test_init_project.py` drives the script for every stack and checks the tracked file set,
  the copied/absent `toolchain.json`, the empty-plan gate state (exit 5), slug/name handling, and
  the guard rails (bad stack/backend/slug, non-empty dir without `--force`).

## Alternatives considered
- **A Python CLI (`python -m harness.init`)** — more portable string handling, but the scaffold is
  file/`git` plumbing that Bash does directly, matching the other `bin/` entry points.
- **Copy a fixed example project** — hard to keep in step with five stacks; parameterised generation
  keeps one source of truth.
- **Generate `01_intent`/`02_spec` content too** — deliberately left as stubs: the spec is the human's
  input to the run, not something to fabricate.

## Self-critique
- The generated constitution is intentionally minimal; a real project will tighten it. It is a
  starting point, not a finished governance document.
- Stack profiles are hard-coded in the script; a new stack needs an entry here and a matching
  toolchain template. Acceptable — both live in the same repo.
- The script does not install any toolchain (composer/npm/maven); it only scaffolds. Provisioning
  happens on the first `run_tests` / `run_toolchain_task`.

## Consequences
- `./bin/init_project.sh --project-dir <p> --stack <s> --backend <b>` yields a project that the
  harness can plan → implement → verify immediately, in any supported language.
- The two experiment axes from this project — stack and agent backend — are now first-class,
  explicit choices at project creation.

## Prompt
`prompts-hist/017_init_project.txt` (local-only)
