"""Git operations confined to the target repository (ADR-001 §4, ADR-008)."""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolError, ToolSpec
from mcp_server.core.sandbox import Sandbox

CONVENTIONAL_COMMIT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: \S.*"
)
SPEC_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: Empty file the supervisor loop in bin/run_agent.sh polls for (ADR-008). Written only
#: after a successful commit; lives under the never-staged .agent-harness/ directory.
RUN_SUCCESSFUL_MARKER: Final[str] = ".agent-harness/run_successful"

#: Paths the agent must never stage in a target repo (internal engineering notes and
#: harness state), regardless of the target's .gitignore.
LOCAL_ONLY_PATHSPECS: Final[tuple[str, ...]] = (
    ":!CLAUDE.md",
    ":!prompts-hist",
    ":!prompts-hist/**",
    ":!.agent-harness",
    ":!.agent-harness/**",
)


class GitCommitFeatureArgs(ToolArgs):
    message: str = Field(min_length=1, description="Conventional Commits message, e.g. 'feat(auth): add login'.")
    spec_id: str = Field(min_length=1, description="Spec/ADR identifier recorded in the header as [spec_id].")


def _git(sandbox: Sandbox, *args: str) -> str:
    result = sandbox.run(["git", *args], timeout=120.0)
    if result.exit_code != 0:
        raise ToolError(f"git {' '.join(args)} failed ({result.exit_code}): {result.stderr.strip()}")
    return result.stdout


def build_commit_message(message: str, spec_id: str) -> str:
    """Validate Conventional Commits format and append ``[spec_id]`` to the header."""
    if not SPEC_ID_RE.match(spec_id):
        raise ToolError(f"invalid spec id: {spec_id!r}")
    header, sep, body = message.strip().partition("\n")
    if not CONVENTIONAL_COMMIT_RE.match(header):
        raise ToolError(
            f"commit message must follow Conventional Commits (type(scope): summary): {header!r}"
        )
    if re.search(r"co-authored-by", message, re.IGNORECASE):
        raise ToolError("Co-authored-by trailers are not allowed")
    tag = f"[{spec_id}]"
    if tag not in header:
        header = f"{header} {tag}"
    return header + (sep + body if body.strip() else "")


def write_marker(sandbox: Sandbox) -> None:
    """Create the empty ``RUN_SUCCESSFUL_MARKER`` inside the target (ADR-008)."""
    marker = sandbox.resolve(RUN_SUCCESSFUL_MARKER)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def git_commit_feature(sandbox: Sandbox, message: str, spec_id: str) -> str:
    """Stage all changes (except local-only files) and commit in the target repository.

    Args:
        message: Conventional Commits message.
        spec_id: Feature specification / ADR identifier added to the header.

    Returns:
        ``"committed <short-hash>: <header>"`` or ``"nothing to commit"``.

    On a successful commit the ``.agent-harness/run_successful`` marker is written so the
    orchestrator's supervisor loop can recognise a completed run (ADR-008).
    """
    if not (sandbox.root / ".git").exists():
        raise ToolError("target directory is not a git repository")
    full_message = build_commit_message(message, spec_id)

    _git(sandbox, "add", "-A", "--", ".", *LOCAL_ONLY_PATHSPECS)
    if not _git(sandbox, "diff", "--cached", "--name-only").strip():
        return "nothing to commit"

    _git(sandbox, "commit", "-q", "--no-verify", "-m", full_message)
    short = _git(sandbox, "rev-parse", "--short", "HEAD").strip()
    write_marker(sandbox)
    return f"committed {short}: {full_message.splitlines()[0]}"


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    sb = ctx.sandbox
    return [
        ToolSpec(
            "git_commit_feature",
            "Stage all changes in the target repo (internal notes excluded) and commit with a "
            "Conventional Commits message tagged [spec_id].",
            GitCommitFeatureArgs,
            lambda a: git_commit_feature(sb, a.message, a.spec_id),  # type: ignore[attr-defined]
        ),
    ]
