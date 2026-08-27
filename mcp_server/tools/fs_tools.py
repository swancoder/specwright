"""Filesystem and specification tools, confined to the target directory (ADR-001 §2, ADR-007)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolError, ToolSpec
from mcp_server.core.sandbox import Sandbox, SandboxViolation

CONSTITUTION_PATH: Final[str] = ".github/constitution.md"
SPECS_DIR: Final[str] = "specs"
MAX_READ_BYTES: Final[int] = 512 * 1024
SPEC_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_WRITE_BYTES: Final[int] = 1024 * 1024
MAX_LIST_ENTRIES: Final[int] = 2000
#: Directory names never listed (any depth) and never written into (ADR-007).
#: Third-party packages a top-level dir must never shadow (ADR-014 §6); a weak model that
#: hits an ImportError sometimes writes a fake package (run 8 wrote a fake fastapi/).
SHADOW_DEPS: Final[frozenset[str]] = frozenset({
    "fastapi", "starlette", "pydantic", "pydantic_core", "uvicorn", "httpx", "anyio",
    "sqlalchemy", "sqlmodel", "flask", "django", "requests", "numpy", "pandas", "click",
    "pytest", "mcp", "yaml", "pyyaml",
})
EXCLUDED_DIRS: Final[frozenset[str]] = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "dist", "build", ".agent-harness", ".idea", ".vscode",
})


class ReadConstitutionArgs(ToolArgs):
    """No arguments."""


class ReadSpecificationArgs(ToolArgs):
    spec_id: str = Field(description="Feature specification identifier, e.g. '001-login' or '001'.")


class FsReadArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root.")
    start_line: int | None = Field(default=None, ge=1, validation_alias=AliasChoices("start_line", "line_start", "start", "from_line"), description="First line to return (1-indexed, inclusive). Omit for whole file.")
    end_line: int | None = Field(default=None, ge=1, validation_alias=AliasChoices("end_line", "line_end", "end", "to_line"), description="Last line to return (1-indexed, inclusive; clamped to EOF).")


class FsListArgs(ToolArgs):
    directory_path: str = Field(default=".", description="Directory relative to the target codebase root ('.' = root).")
    recursive: bool = Field(default=False, description="Walk subdirectories (excluded dirs are always skipped).")


class FsWriteArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root; parent directories are created.")
    content: str = Field(description="Full UTF-8 text content of the file (overwrites an existing file).")


class FsApplyPatchArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root.")
    search_string: str = Field(min_length=1, description="Exact text that must occur exactly once in the file.")
    replace_string: str = Field(description="Text that replaces the matched occurrence.")


# ---------------------------------------------------------------- helpers


def _read_text(sandbox: Sandbox, relpath: str) -> str:
    path = sandbox.resolve(relpath, must_exist=True)
    if not path.is_file():
        raise ToolError(f"not a regular file: {relpath!r}")
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise ToolError(f"file too large ({size} bytes > {MAX_READ_BYTES}): {relpath!r}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"not a UTF-8 text file: {relpath!r}") from exc


def _write_atomic(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- tools


def enforce_write_scope(sandbox: Sandbox, write_scopes: tuple[str, ...], path: Path, filepath: str) -> None:
    """Raise ``SandboxViolation`` if ``path`` is outside every write scope (ADR-011)."""
    if not write_scopes:
        return
    rel = path.relative_to(sandbox.root).as_posix()
    parts = Path(rel).parts
    for scope in write_scopes:
        sparts = Path(scope).parts
        if parts[: len(sparts)] == sparts:
            return
    raise SandboxViolation(
        f"writes are restricted to {', '.join(s + '/' for s in write_scopes)} in this phase: {filepath!r}"
    )


def read_constitution(sandbox: Sandbox) -> str:
    """Return the target project's constitution (`.github/constitution.md`).

    The constitution holds global architectural rules and hard constraints
    that the agent must respect in every action.
    """
    return _read_text(sandbox, CONSTITUTION_PATH)


def read_specification(sandbox: Sandbox, spec_id: str) -> str:
    """Return every Markdown document of the feature spec matching ``spec_id``.

    Matches ``specs/<spec_id>*/`` directories (e.g. ``001`` → ``001-login``) or a single
    ``specs/<spec_id>.md`` file. Files are concatenated with ``## <path>`` headers.

    Args:
        spec_id: Plain identifier; path separators and ``..`` are rejected.
    """
    if not SPEC_ID_RE.match(spec_id):
        raise ToolError(f"invalid spec id: {spec_id!r}")
    specs_root = sandbox.resolve(SPECS_DIR)
    if not specs_root.is_dir():
        raise ToolError(f"no {SPECS_DIR}/ directory in target")

    candidates = sorted(p for p in specs_root.iterdir() if p.is_dir() and p.name.startswith(spec_id))
    exact = [p for p in candidates if p.name == spec_id]
    if exact:
        candidates = exact
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise ToolError(f"ambiguous spec id {spec_id!r}: matches {names}")

    files: list[Path]
    if candidates:
        files = sorted(candidates[0].rglob("*.md"))
    else:
        single = specs_root / f"{spec_id}.md"
        files = [single] if single.is_file() else []
    if not files:
        raise ToolError(f"no specification found for {spec_id!r}")

    sections = []
    for f in files:
        rel = sandbox.relative(f)
        sections.append(f"## {rel}\n\n{_read_text(sandbox, rel)}")
    return "\n\n".join(sections)


def fs_read(sandbox: Sandbox, filepath: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Read a UTF-8 text file inside the target codebase, optionally a line range.

    Args:
        filepath: Path relative to the target codebase root.
        start_line: First line (1-indexed, inclusive); defaults to 1.
        end_line: Last line (1-indexed, inclusive); defaults to EOF, clamped to EOF.

    A sliced result is prefixed with ``# <path>: lines a-b of N`` (ADR-010).
    """
    text = _read_text(sandbox, filepath)
    if start_line is None and end_line is None:
        return text
    if (start_line is not None and start_line < 1) or (end_line is not None and end_line < 1):
        raise ToolError("start_line and end_line must be >= 1")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    start = start_line if start_line is not None else 1
    end = end_line if end_line is not None else total
    if start > end:
        raise ToolError(f"start_line ({start}) must not exceed end_line ({end})")
    if start > total:
        raise ToolError(f"start_line ({start}) is beyond the end of {filepath!r} ({total} lines)")
    end = min(end, total)
    body = "".join(lines[start - 1:end])
    return f"# {filepath}: lines {start}-{end} of {total}\n{body}"


def fs_apply_patch(sandbox: Sandbox, filepath: str, search_string: str, replace_string: str, write_scopes: tuple[str, ...] = ()) -> str:
    """Replace exactly one occurrence of ``search_string`` in a target file.

    The file is left untouched when the search string is missing or ambiguous.

    Args:
        filepath: Path relative to the target codebase root.
        search_string: Exact text that must be present exactly once.
        replace_string: Text that will replace the matched occurrence.
    """
    path = sandbox.resolve(filepath, must_exist=True)
    enforce_write_scope(sandbox, write_scopes, path, filepath)
    original = _read_text(sandbox, filepath)
    count = original.count(search_string)
    if count == 0:
        raise ToolError(f"search string not found in {filepath!r}")
    if count > 1:
        raise ToolError(f"search string occurs {count} times in {filepath!r}; make it unique")
    _write_atomic(path, original.replace(search_string, replace_string, 1))
    return f"patched {filepath}: 1 occurrence replaced"


def _entry(sandbox: Sandbox, path: Path) -> dict[str, object]:
    if path.is_symlink():
        kind = "symlink"
    elif path.is_dir():
        kind = "dir"
    elif path.is_file():
        kind = "file"
    else:
        kind = "other"
    # Lexical (unresolved) relative path: a symlink is reported by its own name, never
    # by the target it points to (which may lie outside the sandbox).
    entry: dict[str, object] = {"path": path.relative_to(sandbox.root).as_posix(), "type": kind}
    if kind == "file":
        entry["size"] = path.stat().st_size
    return entry


def _iter_children(directory: Path) -> list[Path]:
    children = [c for c in directory.iterdir() if c.name not in EXCLUDED_DIRS]
    return sorted(children, key=lambda c: (not c.is_dir(), c.name))


def fs_list(sandbox: Sandbox, directory_path: str = ".", recursive: bool = False) -> str:
    """List files and directories inside the target codebase as JSON.

    Excluded directories (``EXCLUDED_DIRS``: VCS, virtualenvs, caches, ``node_modules``,
    ``.agent-harness`` …) are skipped at every depth to keep the listing small.

    Args:
        directory_path: Directory relative to the target root; ``"."`` is the root.
        recursive: Walk subdirectories; output is capped at ``MAX_LIST_ENTRIES``.
    """
    root = sandbox.resolve(directory_path, must_exist=True)
    if not root.is_dir():
        raise ToolError(f"not a directory: {directory_path!r}")
    if any(part in EXCLUDED_DIRS for part in sandbox.relative(root).split("/") if part):
        raise ToolError(f"directory is excluded from listing: {directory_path!r}")

    entries: list[dict[str, object]] = []
    truncated = False
    stack = [root]
    while stack:
        current = stack.pop(0)
        for child in _iter_children(current):
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                stack.clear()
                break
            entries.append(_entry(sandbox, child))
            if recursive and child.is_dir() and not child.is_symlink():
                stack.append(child)
    return json.dumps(
        {"directory": sandbox.relative(root) or ".", "entries": entries, "truncated": truncated},
        indent=2,
    )


def _shadow_deps(sandbox: Sandbox) -> frozenset[str]:
    """Builtin blocklist plus top-level names parsed from the target's requirements.txt."""
    names = set(SHADOW_DEPS)
    req = sandbox.root / "requirements.txt"
    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkg = re.split(r"[<>=!~;\[ ]", line, 1)[0].strip().lower().replace("-", "_")
                if pkg:
                    names.add(pkg)
    return frozenset(names)


def _reject_shadow_package(sandbox: Sandbox, rel: str) -> None:
    """Reject creating a REPO-ROOT package whose name shadows a dependency (ADR-014 §6).

    Only top-level dirs are blocked; project packages under ``src/`` (e.g. ``src/http``) are fine.
    """
    parts = rel.split("/")
    if len(parts) > 1 and parts[0].lower().replace("-", "_") in _shadow_deps(sandbox):
        raise ToolError(
            f"refusing to create a top-level package '{parts[0]}/' that shadows the installed "
            f"dependency '{parts[0]}'. A ModuleNotFoundError means the dependency is missing from "
            f"requirements.txt — add it there and re-run tests; do not fake the package."
        )


def fs_write(sandbox: Sandbox, filepath: str, content: str, write_scopes: tuple[str, ...] = ()) -> str:
    """Create or overwrite a UTF-8 text file inside the target codebase.

    Missing parent directories are created. Writes are atomic (temp file + rename).

    Args:
        filepath: Path relative to the target root.
        content: Full file content.
    """
    path = sandbox.resolve(filepath)
    enforce_write_scope(sandbox, write_scopes, path, filepath)
    rel = sandbox.relative(path)
    if any(part in EXCLUDED_DIRS for part in rel.split("/")[:-1]):
        raise ToolError(f"refusing to write inside an excluded directory: {filepath!r}")
    _reject_shadow_package(sandbox, rel)
    if path.is_dir():
        raise ToolError(f"path is a directory: {filepath!r}")
    size = len(content.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise ToolError(f"content too large ({size} bytes > {MAX_WRITE_BYTES}): {filepath!r}")
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, content)
    return f"{'overwritten' if existed else 'created'} {rel} ({size} bytes)"


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Tool specs for this module."""
    sb = ctx.sandbox
    scopes = tuple(ctx.write_scopes)
    return [
        ToolSpec(
            "read_constitution",
            "Return the target project's constitution: global architectural rules and hard constraints.",
            ReadConstitutionArgs,
            lambda a: read_constitution(sb),
        ),
        ToolSpec(
            "read_specification",
            "Return the intent/spec/plan documents for a feature specification ID.",
            ReadSpecificationArgs,
            lambda a: read_specification(sb, a.spec_id),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_read",
            "Read a UTF-8 text file inside the target codebase (repository-relative path); "
            "optional start_line/end_line (1-indexed, inclusive) return only that slice.",
            FsReadArgs,
            lambda a: fs_read(sb, a.filepath, a.start_line, a.end_line),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_list",
            "List files and directories (JSON) inside the target codebase; VCS/venv/cache dirs are excluded.",
            FsListArgs,
            lambda a: fs_list(sb, a.directory_path, a.recursive),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_write",
            "Create or overwrite a UTF-8 text file in the target codebase; parent directories are created.",
            FsWriteArgs,
            lambda a: fs_write(sb, a.filepath, a.content, scopes),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_apply_patch",
            "Replace exactly one occurrence of search_string in a target file with replace_string.",
            FsApplyPatchArgs,
            lambda a: fs_apply_patch(sb, a.filepath, a.search_string, a.replace_string, scopes),  # type: ignore[attr-defined]
        ),
    ]
