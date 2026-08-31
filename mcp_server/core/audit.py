"""Canonical structured JSONL audit log for Specwright sessions (ADR-018).

One JSON object per line under ``<audit_dir>/<session_id>.jsonl``:

    {"ts": "...Z", "session_id": "...", "actor": "...", "action": "...", ...extra}

Durability: every event is a single ``os.write`` of one already-terminated line to an ``O_APPEND``
descriptor, so concurrent writers never interleave and a concurrent reader (the dashboard) sees at
worst a torn *final* line — never a corrupt middle.

``emit_env`` is the one-liner instrumentation entry point: it reads ``HARNESS_AUDIT_DIR`` /
``HARNESS_SESSION_ID`` from the environment (exported by ``./harness run``) and is a safe no-op when
they are unset, so instrumenting a code path can never break it. Actor defaults to ``HARNESS_ACTOR``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

AUDIT_DIR_ENV = "HARNESS_AUDIT_DIR"
SESSION_ENV = "HARNESS_SESSION_ID"
ACTOR_ENV = "HARNESS_ACTOR"


def utc_now() -> str:
    """UTC timestamp, second resolution, ISO-8601 with a trailing Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def emit(audit_dir: str | os.PathLike[str], session_id: str, actor: str, action: str,
         ts: str | None = None, **fields: object) -> Path:
    """Append one event line to ``<audit_dir>/<session_id>.jsonl`` and return the path.

    ``None`` fields are dropped. The write is a single append of a whole, newline-terminated line.
    """
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


def emit_env(action: str, actor: str | None = None, **fields: object) -> Path | None:
    """Emit using the ambient session env; no-op (and never raises) when it is absent.

    Reads the audit dir and session id from ``HARNESS_AUDIT_DIR`` / ``HARNESS_SESSION_ID``; ``actor``
    falls back to ``HARNESS_ACTOR`` then ``"agent"``. Best-effort: any failure is swallowed so a
    logging problem can never break an agent turn, a toolchain run, or the gate.
    """
    audit_dir = os.environ.get(AUDIT_DIR_ENV)
    session_id = os.environ.get(SESSION_ENV)
    if not audit_dir or not session_id:
        return None
    resolved_actor = actor or os.environ.get(ACTOR_ENV) or "agent"
    try:
        return emit(audit_dir, session_id, resolved_actor, action, **fields)
    except OSError:
        return None


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
    """CLI: ``audit_log.py <audit_dir> <session_id> <actor> <action> [key=value ...]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4:
        print("usage: audit_log.py <audit_dir> <session_id> <actor> <action> [key=value ...]",
              file=sys.stderr)
        return 2
    audit_dir, session_id, actor, action, *rest = args
    emit(audit_dir, session_id, actor, action, **_parse_kv(rest))
    return 0
