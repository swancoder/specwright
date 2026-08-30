"""Specwright audit dashboard — read-only Observer UI (ADR-017, placeholder).

Renders the session audit logs under `.agent-harness/audit_logs/`. It only ever READS those
files, so it never interferes with agent sessions running in parallel. Launch via `./harness ui`.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

AUDIT_DIR = Path(__file__).resolve().parent.parent / ".agent-harness" / "audit_logs"

st.set_page_config(page_title="Specwright — Audit", layout="wide")
st.title("Specwright — session audit")
st.caption(f"Read-only Observer · {AUDIT_DIR}")

logs = sorted(AUDIT_DIR.glob("*.log"), reverse=True) if AUDIT_DIR.is_dir() else []

if not logs:
    st.info('No sessions yet. Start one with `./harness run "<task>" --spec <id> --target-dir <path>`.')
else:
    choice = st.sidebar.radio("Sessions", [p.name for p in logs], index=0)
    log = AUDIT_DIR / choice
    st.subheader(choice)
    try:
        st.code(log.read_text(encoding="utf-8", errors="replace"), language="text")
    except OSError as exc:  # never mutate; just report
        st.error(f"could not read {choice}: {exc}")

st.sidebar.caption("This view is read-only and never writes agent state.")
