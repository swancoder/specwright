#!/usr/bin/env python3
"""Append-only structured JSONL audit log for Specwright sessions (ADR-018).

Each event is one JSON object on its own line under ``.agent-harness/audit_logs/<session_id>.jsonl``:

    {"ts": "...Z", "session_id": "...", "actor": "...", "action": "...", ...extra}

Durability model: every event is a *single* ``os.write`` of one already-terminated line to an
``O_APPEND`` file descriptor. Concurrent writers never interleave partial lines, and a concurrent
reader (the dashboard) sees at worst a torn *final* line while a write is in flight — which the
reader's parser skips. There is never a corrupt line in the middle of the file.

CLI (used by ``./harness run`` and any future instrumentation):
    audit_log.py <audit_dir> <session_id> <actor> <action> [key=value ...]
``exit_code`` values are coerced to int when numeric; everything else stays a string.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def utc_now() -> str:
    """UTC timestamp, second resolution, ISO-8601 with a trailing Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def emit(audit_dir: str | os.PathLike[str], session_id: str, actor: str, action: str,
         ts: str | None = None, **fields: object) -> Path:
    """Append one event line; returns the log path. ``None`` fields are dropped."""
    directory = Path(audit_dir)
    directory.mkdir(parents=True, exist_ok=True)
    event: dict[str, object] = {"ts": ts or utc_now(), "session_id": session_id,
                                "actor": actor, "action": action}
    for key, value in fields.items():
        if value is not None:
            event[key] = value
    line = json.dumps(event, ensure_ascii=False) + "\n"
    path = directory / f"{session_id}.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))  # one atomic append of a whole line
    finally:
        os.close(fd)
    return path


def _parse_kv(args: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, value = arg.split("=", 1)
        if key == "exit_code":
            try:
                out[key] = int(value)
                continue
            except ValueError:
                pass
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4:
        print("usage: audit_log.py <audit_dir> <session_id> <actor> <action> [key=value ...]",
              file=sys.stderr)
        return 2
    audit_dir, session_id, actor, action, *rest = args
    emit(audit_dir, session_id, actor, action, **_parse_kv(rest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
