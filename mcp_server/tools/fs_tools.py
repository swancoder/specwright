"""Filesystem and specification tools, confined to the target directory (ADR-001 §2, ADR-007)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Final

from pydantic import Field

from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolError, ToolSpec
from mcp_server.core.sandbox import Sandbox

CONSTITUTION_PATH: Final[str] = ".github/constitution.md"
SPECS_DIR: Final[str] = "specs"
MAX_READ_BYTES: Final[int] = 512 * 1024
SPEC_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_WRITE_BYTES: Final[int] = 1024 * 1024
MAX_LIST_ENTRIES: Final[int] = 2000
#: Directory names never listed (any depth) and never written into (ADR-007).
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


def fs_read(sandbox: Sandbox, filepath: str) -> str:
    """Read a UTF-8 text file inside the target codebase.

    Args:
        filepath: Path relative to the target codebase root.
    """
    return _read_text(sandbox, filepath)


def fs_apply_patch(sandbox: Sandbox, filepath: str, search_string: str, replace_string: str) -> str:
    """Replace exactly one occurrence of ``search_string`` in a target file.

    The file is left untouched when the search string is missing or ambiguous.

    Args:
        filepath: Path relative to the target codebase root.
        search_string: Exact text that must be present exactly once.
        replace_string: Text that will replace the matched occurrence.
    """
    path = sandbox.resolve(filepath, must_exist=True)
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


def fs_write(sandbox: Sandbox, filepath: str, content: str) -> str:
    """Create or overwrite a UTF-8 text file inside the target codebase.

    Missing parent directories are created. Writes are atomic (temp file + rename).

    Args:
        filepath: Path relative to the target root.
        content: Full file content.
    """
    path = sandbox.resolve(filepath)
    rel = sandbox.relative(path)
    if any(part in EXCLUDED_DIRS for part in rel.split("/")[:-1]):
        raise ToolError(f"refusing to write inside an excluded directory: {filepath!r}")
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
            "Read a UTF-8 text file inside the target codebase (repository-relative path).",
            FsReadArgs,
            lambda a: fs_read(sb, a.filepath),  # type: ignore[attr-defined]
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
            lambda a: fs_write(sb, a.filepath, a.content),  # type: ignore[attr-defined]
        ),
        ToolSpec(
            "fs_apply_patch",
            "Replace exactly one occurrence of search_string in a target file with replace_string.",
            FsApplyPatchArgs,
            lambda a: fs_apply_patch(sb, a.filepath, a.search_string, a.replace_string),  # type: ignore[attr-defined]
        ),
    ]
