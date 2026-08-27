"""Tool-name aliases for common hallucinations by weak local models (ADR-014 §3).

A small model often invents plausible tool names (`fs_find`, `open_file`, `git_commit`).
Rather than let Open Code reject them and cost the agent a turn, we register thin aliases
that delegate to the real, sandbox-enforced handler. Aliases are gated exactly like their
canonical tool by the config generators, so a read-only role never gains a write alias.
"""

from __future__ import annotations

from typing import Final

from mcp_server.core.registry import ToolSpec

#: alias name -> canonical tool name
ALIAS_MAP: Final[dict[str, str]] = {
    "fs_find": "fs_list", "list_files": "fs_list", "list_directory": "fs_list",
    "open_file": "fs_read", "read_file": "fs_read", "cat_file": "fs_read",
    "write_file": "fs_write", "create_file": "fs_write",
    "apply_patch": "fs_apply_patch", "edit_file": "fs_apply_patch",
    "run_test": "run_tests", "pytest": "run_tests",
    "git_commit": "git_commit_feature", "commit": "git_commit_feature",
}


def build_alias_tools(real_specs: list[ToolSpec]) -> list[ToolSpec]:
    """Return delegating alias ToolSpecs for every alias whose canonical tool is present."""
    by_name = {s.name: s for s in real_specs}
    out: list[ToolSpec] = []
    for alias, canonical in ALIAS_MAP.items():
        target = by_name.get(canonical)
        if target is not None:
            out.append(ToolSpec(
                alias,
                f"Alias of {canonical}: {target.description}",
                target.args_model,
                target.handler,
            ))
    return out
