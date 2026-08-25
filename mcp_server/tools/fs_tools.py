"""Filesystem and specification tools, confined to the target directory (ADR-001 §2)."""

from __future__ import annotations

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


class ReadConstitutionArgs(ToolArgs):
    """No arguments."""


class ReadSpecificationArgs(ToolArgs):
    spec_id: str = Field(description="Feature specification identifier, e.g. '001-login' or '001'.")


class FsReadArgs(ToolArgs):
    filepath: str = Field(description="Path relative to the target codebase root.")


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
            "fs_apply_patch",
            "Replace exactly one occurrence of search_string in a target file with replace_string.",
            FsApplyPatchArgs,
            lambda a: fs_apply_patch(sb, a.filepath, a.search_string, a.replace_string),  # type: ignore[attr-defined]
        ),
    ]
