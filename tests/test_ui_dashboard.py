"""Tests for the JSONL audit logger and the read-only dashboard's data helpers (ADR-018).

The Streamlit UI itself is not exercised (it renders under `streamlit run` only); these cover the
pure, importable pieces: append-only logging and robust parsing of a possibly-torn active file.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    """Import a bin/ module by path (no Streamlit import happens at module top)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


ui = _load("ui_dashboard")
audit = _load("audit_log")


# ---- parse_jsonl: robust against blank / corrupted / torn lines ----------------

def test_parse_jsonl_reads_all_valid(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text('{"actor":"harness","action":"session_start"}\n'
                 '{"actor":"Verifier","action":"session_end","exit_code":0}\n')
    events = ui.parse_jsonl(f)
    assert [e["action"] for e in events] == ["session_start", "session_end"]


def test_parse_jsonl_skips_torn_final_line(tmp_path):
    f = tmp_path / "s.jsonl"
    # valid line + a half-written trailing line (no newline, incomplete JSON) as during an append
    f.write_text('{"actor":"harness","action":"session_start"}\n{"actor":"harness","acti')
    events = ui.parse_jsonl(f)
    assert len(events) == 1 and events[0]["action"] == "session_start"


def test_parse_jsonl_skips_corrupted_middle_and_blank(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text('{"action":"a"}\n\nnot json at all\n{"action":"b"}\n')
    assert [e["action"] for e in ui.parse_jsonl(f)] == ["a", "b"]


def test_parse_jsonl_missing_file(tmp_path):
    assert ui.parse_jsonl(tmp_path / "nope.jsonl") == []


# ---- list_sessions: newest first, only .jsonl -----------------------------------

def test_list_sessions_newest_first(tmp_path):
    import os
    old = tmp_path / "20260101T000000Z-1.jsonl"; old.write_text("{}\n")
    new = tmp_path / "20260808T000000Z-2.jsonl"; new.write_text("{}\n")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    (tmp_path / "notes.log").write_text("ignore me")  # non-jsonl ignored
    result = ui.list_sessions(tmp_path)
    assert result == [new, old]


# ---- _is_ok: status / exit_code interpretation ----------------------------------

def test_is_ok_by_status_and_exit_code():
    assert ui._is_ok({"status": "success"}) is True
    assert ui._is_ok({"status": "failed"}) is False
    assert ui._is_ok({"exit_code": 0}) is True
    assert ui._is_ok({"exit_code": 7}) is False


# ---- audit_log: append-only, coercion, CLI, round-trip --------------------------

def test_emit_appends_one_line_per_event(tmp_path):
    audit.emit(tmp_path, "sid", "harness", "session_start", task="do a thing")
    audit.emit(tmp_path, "sid", "Verifier", "session_end", status="success", exit_code=0)
    lines = (tmp_path / "sid.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["actor"] == "harness" and first["task"] == "do a thing" and "ts" in first


def test_emit_drops_none_fields(tmp_path):
    audit.emit(tmp_path, "sid", "harness", "x", keep="y", drop=None)
    obj = json.loads((tmp_path / "sid.jsonl").read_text())
    assert obj["keep"] == "y" and "drop" not in obj


def test_cli_coerces_exit_code_to_int(tmp_path):
    rc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "audit_log.py"),
         str(tmp_path), "sid", "harness", "session_end", "status=failed", "exit_code=7"],
        capture_output=True, text=True, timeout=30).returncode
    assert rc == 0
    obj = json.loads((tmp_path / "sid.jsonl").read_text())
    assert obj["exit_code"] == 7 and isinstance(obj["exit_code"], int)


def test_logger_output_is_parseable_by_dashboard_even_with_torn_tail(tmp_path):
    audit.emit(tmp_path, "sid", "harness", "session_start", task="t")
    # simulate a reader catching a write mid-flight: append a partial line by hand
    with open(tmp_path / "sid.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"actor":"harness","action":"session_e')
    events = ui.parse_jsonl(tmp_path / "sid.jsonl")
    assert len(events) == 1 and events[0]["action"] == "session_start"
