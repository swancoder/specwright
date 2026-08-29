#!/usr/bin/env python3
"""Detect a tool call a weak model emitted as PLAIN TEXT instead of calling it (ADR-014 §2).

Reads agent output (file arg or stdin). If it finds tool-call-shaped text, prints a targeted
recovery instruction naming the tool; otherwise prints nothing (exit 1). The supervisor appends
this to the resume prompt so the model actually makes the call instead of stalling.
"""

from __future__ import annotations

import json
import re
import sys

TOOLS = (
    "read_constitution", "read_specification", "fs_list", "fs_read", "fs_write",
    "fs_apply_patch", "run_tests", "git_commit_feature", "query_temporal_coupling",
    "mark_spec_complete", "run_toolchain_task",
)
_JSON_NAME = re.compile(r'\{\s*"name"\s*:\s*"(?:agent-harness_|mcp__agent-harness__)?([a-z_]+)"')
_CALL = re.compile(r'(?:agent-harness[_.]|mcp__agent-harness__)([a-z_]+)\s*\(')


def detect(text: str) -> str | None:
    for m in _JSON_NAME.finditer(text):
        if m.group(1) in TOOLS:
            return m.group(1)
    for m in _CALL.finditer(text):
        if m.group(1) in TOOLS:
            return m.group(1)
    # bare fenced json object with a name field
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            obj = json.loads(block)
            n = str(obj.get("name", "")).split("__")[-1].replace("agent-harness_", "")
            if n in TOOLS:
                return n
        except Exception:  # noqa: BLE001
            pass
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = open(argv[0], encoding="utf-8", errors="ignore").read() if argv else sys.stdin.read()
    tool = detect(text)
    if not tool:
        return 1
    print(
        f"Your previous message described a call to `{tool}` as PLAIN TEXT instead of actually "
        f"invoking the tool. Do not narrate tool calls — call `agent-harness_{tool}` now, then continue."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
