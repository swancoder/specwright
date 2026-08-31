"""Toolchain abstraction: decouple build/test/lint/install from a specific tech stack (ADR-015).

A target may drop a ``toolchain.json`` in its root to declare its stack and the shell commands for
the standard lifecycle tasks. When absent, the harness transparently falls back to the historical
Python strategy (venv + pip + mypy/ruff + pytest), so existing Python targets are unaffected.

    resolve_toolchain(sandbox) -> Toolchain          # JsonToolchain or PythonToolchain
    toolchain.run(sandbox, task) -> ToolchainResult  # task in TASKS

Output helpers (sanitize / head_tail_truncate) keep tool payloads small and ANSI-free.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from mcp_server.core.sandbox import CommandResult, Sandbox

TOOLCHAIN_FILE: Final[str] = "toolchain.json"
TASKS: Final[tuple[str, ...]] = ("install", "lint", "test", "build", "fix")
#: Tasks that MODIFY the working tree (auto-fixers). Gated: only the implementer may run them,
#: never the read-only Verifier — otherwise the completion gate could be silently pre-cleaned.
MUTATING_TASKS: Final[tuple[str, ...]] = ("fix",)
DEFAULT_TIMEOUT: Final[float] = 900.0

# ---------------------------------------------------------------- output sanitization (ADR-015 §3)

MAX_SNIPPET_BYTES: Final[int] = 4096
HEAD_LINES: Final[int] = 20
TAIL_LINES: Final[int] = 50
MAX_RESCUED: Final[int] = 15
#: Substrings that mark a critical line worth rescuing from a truncated middle section.
ERROR_MARKERS: Final[tuple[str, ...]] = (
    "Fatal error:", "Exception", "Traceback", "FAIL", "Failed asserting", "AssertionError",
    "Error:", "error:", "ERROR", "error[", "panic:", "SyntaxError", "not found", "No such",
)
# OSC (\x1b]…\x07) and CSI (\x1b[…) tried before the single-char C1 alt, whose range covers ']'.
_ANSI_RE: Final[re.Pattern[str]] = re.compile(r"\x1b(?:\][^\x07]*\x07|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")


def sanitize(text: str) -> str:
    """Strip ANSI/control sequences and resolve ``\\r`` progress-bar overwrites to their final state."""
    text = _ANSI_RE.sub("", text)
    out: list[str] = []
    for line in text.split("\n"):
        if "\r" in line:
            line = line.split("\r")[-1]  # a carriage-return overwrite keeps only the last segment
        out.append(line)
    return "\n".join(out)


def head_tail_truncate(text: str, head: int = HEAD_LINES, tail: int = TAIL_LINES,
                       limit: int = MAX_SNIPPET_BYTES) -> str:
    """Keep the first ``head`` and last ``tail`` lines; rescue error lines from the dropped middle.

    Produces ``<head>\\n... [TRUNCATED N lines] ...\\n<rescued errors>\\n<tail>`` and hard-caps the
    result at ``limit`` bytes so it never blows the LLM context.
    """
    lines = text.split("\n")
    if len(lines) > head + tail:
        middle = lines[head:len(lines) - tail]
        seen: set[str] = set()
        rescued: list[str] = []
        for ln in middle:
            s = ln.strip()
            if s and s not in seen and any(m in ln for m in ERROR_MARKERS):
                seen.add(s)
                rescued.append(ln)
            if len(rescued) >= MAX_RESCUED:
                break
        parts = lines[:head] + ["", f"... [TRUNCATED {len(middle)} lines] ..."]
        if rescued:
            parts += ["[rescued errors]", *rescued]
        parts += ["", *lines[len(lines) - tail:]]
        text = "\n".join(parts)
    data = text.encode("utf-8")
    if len(data) > limit:
        text = data[:limit - 32].decode("utf-8", "ignore") + "\n... [snippet capped at 4 KiB]"
    return text


# ---------------------------------------------------------------- toolchain strategies


@dataclass(slots=True)
class ToolchainResult:
    """Outcome of one toolchain task (already sanitized fields are the tool's responsibility)."""

    task: str
    stack: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    skipped: bool = False


@dataclass(slots=True)
class JsonToolchain:
    """A stack declared by ``toolchain.json``: ``{stack, commands:{install,lint,test,build}}``."""

    stack: str
    commands: dict[str, str]

    def command(self, task: str) -> str | None:
        return self.commands.get(task)

    def run(self, sandbox: Sandbox, task: str, timeout: float = DEFAULT_TIMEOUT) -> ToolchainResult:
        cmd = self.commands.get(task)
        if not cmd:
            return ToolchainResult(task, self.stack, "", 0, stdout=f"(no '{task}' command declared for stack '{self.stack}')", skipped=True)
        res = sandbox.run(["bash", "-c", cmd], timeout=timeout)  # project-declared shell command
        return ToolchainResult(task, self.stack, cmd, res.exit_code, res.stdout, res.stderr, res.timed_out)


@dataclass(slots=True)
class PythonToolchain:
    """Default strategy — the historical Python behaviour, wrapped (ADR-009/010/014)."""

    stack: str = "python-default"

    def _venv_py(self, sandbox: Sandbox) -> Path:
        return sandbox.root / ".venv" / "bin" / "python"

    def _steps(self, sandbox: Sandbox, task: str) -> list[list[str]]:
        py = str(self._venv_py(sandbox))
        if task == "install":
            return [[sys.executable, "-m", "venv", ".venv"],
                    [py, "-m", "pip", "install", "-q", "-r", "requirements.txt"]]
        if task == "test":
            return [[py, "-m", "pytest", "-q", "tests"]]
        if task == "lint":
            return [[py, "-m", "mypy", "src"], [py, "-m", "ruff", "check", "."]]
        if task == "fix":
            return [[py, "-m", "ruff", "check", "--fix", "."]]  # apply ruff's own auto-fixes
        return []  # build: no compile step for the Python default

    def command(self, task: str) -> str | None:
        return {"install": "python -m venv .venv && pip install -r requirements.txt",
                "test": "pytest -q tests", "lint": "mypy src && ruff check .",
                "fix": "ruff check --fix .", "build": "(no build step)"}.get(task)

    def run(self, sandbox: Sandbox, task: str, timeout: float = DEFAULT_TIMEOUT) -> ToolchainResult:
        display = self.command(task) or ""
        steps = self._steps(sandbox, task)
        if not steps:
            return ToolchainResult(task, self.stack, display, 0, stdout=f"(no '{task}' step for {self.stack})", skipped=True)
        # test/lint need a provisioned .venv; if missing, skip (install provisions it).
        if task in ("test", "lint", "fix") and not self._venv_py(sandbox).exists():
            return ToolchainResult(task, self.stack, display, 0, stdout="(skipped: target .venv not provisioned — run the install task)", skipped=True)
        stdout, stderr, code = "", "", 0
        for argv in steps:
            r: CommandResult = sandbox.run(argv, timeout=timeout)
            stdout += r.stdout
            stderr += r.stderr
            if r.exit_code != 0:
                code = r.exit_code
            if r.timed_out:
                return ToolchainResult(task, self.stack, display, -1, stdout, stderr, timed_out=True)
        return ToolchainResult(task, self.stack, display, code, stdout, stderr)


def resolve_toolchain(sandbox: Sandbox) -> JsonToolchain | PythonToolchain:
    """Return the target's declared toolchain, or the Python default when ``toolchain.json`` is absent."""
    cfg = sandbox.root / TOOLCHAIN_FILE
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return PythonToolchain()
        commands = data.get("commands") or {}
        if isinstance(data, dict) and isinstance(commands, dict) and commands:
            stack = str(data.get("stack") or "custom")
            return JsonToolchain(stack, {k: str(v) for k, v in commands.items() if v})
    return PythonToolchain()
