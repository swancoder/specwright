# ADR-001: Sandboxing and Target Isolation

- **Status:** Accepted
- **Date:** 2026-08-25
- **Stage:** 1 (written during Stage 5, when the rules became enforceable in code)

## Context
The harness executes tool calls chosen by an LLM against a *separate* target codebase.
The LLM's arguments are untrusted: a path such as `../../etc/passwd`, a symlink that
points outside the repository, a test target that is really a shell command, or a commit
that sweeps in the harness's own local-only notes are all plausible failure modes. The
constitution of the target project and `CLAUDE.md` both require that harness logic never
leaks into the target and that internal engineering notes stay untracked.

## Decision

### 1. Single containment primitive
All filesystem and process access goes through `mcp_server.core.sandbox.Sandbox`,
constructed once per server from `--target-dir` (resolved to an absolute real path).

- `Sandbox.resolve(relpath)`:
  - rejects empty paths, absolute paths, Windows drive/UNC forms, and any `..` segment
    *before* touching the filesystem;
  - joins with the target root and calls `Path.resolve()` so symlinks are followed;
  - accepts the result only if `resolved.is_relative_to(target_root)`; otherwise raises
    `SandboxViolation`.
  - The root itself is never a valid *file* target.
- `Sandbox.run(argv, timeout)`: `subprocess.run` with `shell=False`, `cwd=target_root`,
  a minimal environment (`PATH`, `HOME`, `LANG`, plus an explicit allow-list), captured
  output, and a hard timeout. Argv elements that are paths are resolved through
  `Sandbox.resolve` by the caller before being passed in.

### 2. Filesystem tools
- `fs_read(filepath)` — resolve, require a regular file, cap at 512 KiB, return text.
- `fs_apply_patch(filepath, search, replace)` — resolve, require exactly **one**
  occurrence of `search` (zero → not found; more than one → ambiguous, no change);
  write atomically (temp file + `os.replace`).
- `read_constitution()` — fixed path `.github/constitution.md`.
- `read_specification(spec_id)` — `spec_id` must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`
  (no separators); matches `specs/<spec_id>*/` directories or `specs/<spec_id>.md`;
  returns all `*.md` files concatenated with `## <relative path>` headers.

### 3. Test execution
- `run_tests(test_target)` — the path part of the target (everything before the first
  `::`) must resolve inside the sandbox and exist. Runner detection, in order:
  `gradlew` present → `./gradlew test --tests <target>`; `package.json` present →
  `npm test -- <target>`; otherwise `<python> -m pytest <target> -q`.
- Timeout 600 s; stdout/stderr each truncated to 20 000 characters; result is a
  JSON object `{runner, exit_code, timed_out, stdout, stderr}`.

### 4. Git operations
- `git_commit_feature(message, spec_id)` — every `git` call runs through
  `Sandbox.run` inside the target repo; the target must contain `.git`.
- `message` must satisfy Conventional Commits:
  `^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: \S.+`.
- The header becomes `<message> [<spec_id>]` unless `[<spec_id>]` is already present.
- **Local-only rule:** staging uses `git add -A -- . ':!CLAUDE.md' ':!prompts-hist'
  ':!prompts-hist/**' ':!.agent-harness' ':!.agent-harness/**'` so internal engineering
  notes and the knowledge-graph database can never be committed by the agent, even if
  the target's `.gitignore` forgets them.
- No `Co-authored-by` or other AI-attribution trailers are ever added.
- If nothing is staged the tool reports that and does not create an empty commit.

### 5. Error surface
Sandbox violations and tool failures raise `ToolError` subclasses; the MCP layer converts
them into `is_error=true` text results, so a hostile argument never crashes the server.

## Alternatives considered
- **OS-level sandbox (containers, seccomp, chroot)** — stronger, but heavyweight for a
  local developer tool; can be layered on later without changing tool code.
- **`os.path.commonpath` string checks without `resolve()`** — defeated by symlinks.
- **Allowing absolute paths that happen to be inside the target** — rejected: forces every
  caller to think in repository-relative terms and simplifies auditing.
- **Free-form test command from the LLM** — rejected; runner detection keeps the LLM from
  executing arbitrary programs.

## Self-critique
- Symlink checks are TOCTOU-prone: a link changed between `resolve()` and the read could
  escape. Acceptable for a single-user local tool.
- `run_tests` still executes whatever the target's own test suite does; the sandbox
  bounds *where* and *what command*, not the target code's behaviour.
- Runner detection is heuristic; a monorepo with both `package.json` and `pytest` will
  pick npm.

## Consequences
- Every tool is safe to expose to an LLM by construction; adding a tool means using
  `Sandbox`, not re-implementing checks.
- Tests can prove containment with `tmp_path` fixtures and hostile inputs.
- Internal files (`CLAUDE.md`, `prompts-hist/`) remain untracked in both the harness repo
  (via `.gitignore`) and any target the agent commits to (via the staging exclusion).

## Prompt
`prompts-hist/004_sandboxed_tools.txt` (local-only)
