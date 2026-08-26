"""Containment primitives for all target-project access (ADR-001)."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from mcp_server.core.registry import ToolError

#: Environment variables passed through to sandboxed subprocesses.
ENV_ALLOWLIST: Final[tuple[str, ...]] = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM", "USER",
    "VIRTUAL_ENV", "PYTHONPATH", "JAVA_HOME", "GRADLE_USER_HOME", "NODE_PATH",
)

_WINDOWS_ABS: Final[re.Pattern[str]] = re.compile(r"^(?:[A-Za-z]:|\\\\|//)")
#: Glob metacharacters; paths must be exact (ADR-010).
GLOB_CHARS: Final[frozenset[str]] = frozenset("*?[]")


class SandboxViolation(ToolError):
    """A path or command would escape the target directory."""


@dataclass(slots=True)
class CommandResult:
    """Captured outcome of a sandboxed subprocess."""

    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Sandbox:
    """Resolves repository-relative paths and runs commands confined to ``root``."""

    def __init__(self, root: Path | str) -> None:
        self.root: Path = Path(root).resolve()
        if not self.root.is_dir():
            raise SandboxViolation(f"target directory does not exist: {self.root}")

    # ---------------------------------------------------------------- paths

    def resolve(self, relpath: str, *, must_exist: bool = False) -> Path:
        """Return the absolute path for ``relpath`` if it stays inside the sandbox.

        Raises:
            SandboxViolation: empty/absolute path, ``..`` segment, glob characters
                (``* ? [ ]``), or a symlink that resolves outside the target root.
        """
        candidate = relpath.replace("\\", "/").strip()
        if not candidate:
            raise SandboxViolation("empty path")
        if candidate.startswith("/") or _WINDOWS_ABS.match(candidate):
            raise SandboxViolation(f"absolute paths are not allowed: {relpath!r}")
        if GLOB_CHARS & set(candidate):
            raise SandboxViolation(f"glob characters are not allowed; give an exact path: {relpath!r}")
        if any(part == ".." for part in PurePosixPath(candidate).parts):
            raise SandboxViolation(f"path traversal is not allowed: {relpath!r}")

        resolved = (self.root / candidate).resolve()
        if not resolved.is_relative_to(self.root):
            raise SandboxViolation(f"path escapes the target directory: {relpath!r}")
        if must_exist and not resolved.exists():
            raise ToolError(f"no such file or directory: {relpath!r}")
        return resolved

    def relative(self, path: Path) -> str:
        """POSIX-style path of ``path`` relative to the root."""
        return path.resolve().relative_to(self.root).as_posix()

    # ---------------------------------------------------------------- processes

    def run(self, argv: list[str], *, timeout: float = 600.0) -> CommandResult:
        """Run ``argv`` (no shell) with ``cwd`` at the sandbox root and a scrubbed env."""
        if not argv:
            raise SandboxViolation("empty command")
        env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
        try:
            proc = subprocess.run(
                argv,
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                argv=list(argv),
                exit_code=-1,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr) + f"\n[timed out after {timeout:.0f}s]",
                timed_out=True,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"command not found: {argv[0]}") from exc
        return CommandResult(argv=list(argv), exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data
