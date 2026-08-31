"""Specwright monitoring dashboard — read-only Observer UI over the JSONL audit logs (ADR-018).

Launch with ``./harness ui`` (or ``streamlit run bin/ui_dashboard.py``). It parses the append-only
``.agent-harness/audit_logs/<session>.jsonl`` files and renders a chronological timeline of agent
turns, toolchain executions, and mechanical-gate results.

Observer pattern: this script ONLY reads. It never writes a log, never spawns or signals a harness
process, and tolerates a file being appended to while it is read (the parser skips a torn final line).

The data helpers below carry no Streamlit dependency so they stay unit-testable; the UI itself is
imported lazily and only runs under ``streamlit run`` (``__name__ == "__main__"``).
"""

from __future__ import annotations

import json
from pathlib import Path

AUDIT_DIR = Path(__file__).resolve().parent.parent / ".agent-harness" / "audit_logs"

# fields large enough to hide behind an expander rather than inline in the timeline
DETAIL_FIELDS = ("payload", "prompt", "messages", "result", "output", "stdout", "stderr", "command")


def parse_jsonl(path: Path) -> list[dict]:
    """Parse one JSONL file into a list of event dicts, robustly.

    Blank lines, corrupted lines, and an incomplete trailing line (the harness appends
    asynchronously, so the last line may be mid-write) are skipped rather than raised.
    """
    events: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn / corrupted line — skip, keep going
        if isinstance(obj, dict):
            events.append(obj)
    return events


def list_sessions(audit_dir: Path = AUDIT_DIR) -> list[Path]:
    """Return the session ``*.jsonl`` files, newest first (by mtime, then name)."""
    if not audit_dir.is_dir():
        return []
    files = [p for p in audit_dir.glob("*.jsonl") if p.is_file()]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def _is_ok(event: dict) -> bool:
    """Best-effort success read from ``status`` / ``exit_code``."""
    status = str(event.get("status", "")).lower()
    if status in ("success", "ok", "pass", "passed", "complete", "green"):
        return True
    if status in ("failed", "fail", "error", "red"):
        return False
    code = event.get("exit_code")
    return code == 0 if isinstance(code, int) else True


def _render() -> None:
    import streamlit as st

    st.set_page_config(page_title="Specwright — Monitor", layout="wide")
    st.title("Specwright — session monitor")
    st.caption(f"Read-only Observer · {AUDIT_DIR}")

    with st.sidebar:
        st.header("Sessions")
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
        auto = st.toggle("Auto-refresh (5s)", value=False,
                         help="Re-reads the logs every 5 seconds. Purely read-only.")
        sessions = list_sessions()
        if not sessions:
            st.info("No sessions yet.")
            st.stop()
        choice = st.selectbox(
            "Session", sessions, index=0,
            format_func=lambda p: p.stem,  # already newest-first
        )

    events = parse_jsonl(choice)
    st.subheader(choice.stem)
    st.caption(f"{len(events)} events · read-only view")

    if not events:
        st.info("This session has no readable events yet.")
    for event in events:
        _render_event(st, event)

    if auto:
        import time
        time.sleep(5)
        st.rerun()


def _render_event(st, event: dict) -> None:
    actor = str(event.get("actor", "?"))
    action = str(event.get("action", "?"))
    ts = str(event.get("ts", ""))
    action_l = action.lower()

    if "toolchain" in action_l:  # execute_toolchain_task / run_toolchain_task
        ok = _is_ok(event)
        task = event.get("task", "")
        code = event.get("exit_code", "?")
        msg = f"🧰 **{actor}** · toolchain `{task}` — {'success' if ok else 'FAILED'} (exit {code}) · {ts}"
        (st.success if ok else st.error)(msg)
    elif "gate" in action_l:  # mechanical_gate
        ok = _is_ok(event)
        status = event.get("status", "?")
        (st.success if ok else st.error)(f"🔒 **{actor}** · mechanical gate — {status} · {ts}")
    elif action_l in ("session_start", "start"):
        st.subheader(f"▶️  session start · {actor}")
        if event.get("task"):
            st.write(event["task"])
        st.caption(ts)
    elif action_l in ("session_end", "end"):
        ok = _is_ok(event)
        code = event.get("exit_code", "?")
        (st.success if ok else st.error)(f"⏹️  session end — exit {code} · {ts}")
    elif "agent_turn" in action_l:
        phase = event.get("phase", "")
        attempt = event.get("attempt", "?")
        backend = event.get("backend", "")
        st.markdown(f"🧑‍💻 **{actor}** · turn — attempt {attempt} · {phase} · `{backend}`")
        st.caption(ts)
    else:
        st.markdown(f"**{actor}** · `{action}`")
        st.caption(ts)

    for field in DETAIL_FIELDS:
        value = event.get(field)
        if value in (None, "", [], {}):
            continue
        with st.expander(f"View Details · {field}"):
            if isinstance(value, (dict, list)):
                st.json(value)
            else:
                st.code(str(value))


if __name__ == "__main__":
    _render()
