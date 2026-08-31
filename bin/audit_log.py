#!/usr/bin/env python3
"""CLI shim for the structured JSONL audit log — the logic lives in ``mcp_server.core.audit``.

Usage: audit_log.py <audit_dir> <session_id> <actor> <action> [key=value ...]
Used by ``./harness run`` (bash) and any other non-Python caller; Python code should import
``mcp_server.core.audit`` directly. Re-exports ``emit`` / ``emit_env`` / ``utc_now`` for convenience.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_server.core.audit import emit, emit_env, main, utc_now  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
