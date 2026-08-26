"""Tests for bin/plan_gate.py (ADR-011)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("plan_gate", Path(__file__).resolve().parent.parent / "bin" / "plan_gate.py")
plan_gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(plan_gate)

APPROVED = """# 03 — Plan
## Pre-flight
- [x] `01_intent.md` approved.
- [X] `02_spec.md` reviewed.
- [x] No constitution conflicts identified.
- [x] Branch created.

## File mapping
- [ ] this checkbox is outside pre-flight and must be ignored
"""
UNAPPROVED = APPROVED.replace("- [x] No constitution conflicts identified.", "- [ ] No constitution conflicts identified.").replace("- [x] Branch created.", "- [ ] Branch created.")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (tmp_path / "specs" / "001-feedback-widget").mkdir(parents=True)
    (tmp_path / "specs" / "template").mkdir()
    (tmp_path / "specs" / "template" / "03_plan.md").write_text("template")
    return tmp_path


def test_locate_exact_and_prefix(target: Path) -> None:
    plan = target / "specs" / "001-feedback-widget" / "03_plan.md"
    assert plan_gate.locate_plan(target, "001") == plan
    assert plan_gate.locate_plan(target, "001-feedback-widget") == plan
    (target / "specs" / "001").mkdir()
    assert plan_gate.locate_plan(target, "001") == target / "specs" / "001" / "03_plan.md", "exact dir wins"


def test_locate_ambiguous_missing_and_template_excluded(target: Path) -> None:
    (target / "specs" / "001-other").mkdir()
    with pytest.raises(FileNotFoundError, match="matches 2"):
        plan_gate.locate_plan(target, "001")
    with pytest.raises(FileNotFoundError, match="matches 0"):
        plan_gate.locate_plan(target, "t")  # 'template' never matches
    with pytest.raises(FileNotFoundError, match="no specs/"):
        plan_gate.locate_plan(target / "nope", "001")


def test_check_states(target: Path) -> None:
    plan = target / "specs" / "001-feedback-widget" / "03_plan.md"
    assert plan_gate.check_plan(plan)[0] == 5
    plan.write_text("")
    assert plan_gate.check_plan(plan)[0] == 5
    plan.write_text("# no preflight\n- [ ] x\n")
    code, msg = plan_gate.check_plan(plan)
    assert code == 5 and "Pre-flight" in msg
    plan.write_text("## Pre-flight\n\ntext only\n")
    assert plan_gate.check_plan(plan)[0] == 5
    plan.write_text(UNAPPROVED)
    code, msg = plan_gate.check_plan(plan)
    assert code == 4 and "2 pre-flight item(s) unticked" in msg and "Branch created" in msg
    plan.write_text(APPROVED)
    code, msg = plan_gate.check_plan(plan)
    assert code == 0 and "4 pre-flight items ticked" in msg, "checkboxes after the section must be ignored"


def test_tolerates_blockquote_and_bold_heading(target: Path) -> None:
    plan = target / "specs" / "001-feedback-widget" / "03_plan.md"
    quoted = "\n".join("> " + l for l in APPROVED.splitlines()) + "\n"
    assert plan_gate.check_plan(plan.write_text(quoted) and plan or plan)[0] == 0
    bold = APPROVED.replace("## Pre-flight", "**Pre‑flight**:")  # bold, unicode hyphen
    plan.write_text(bold)
    assert plan_gate.check_plan(plan)[0] == 0
    plan.write_text(bold.replace("- [x] Branch created.", "- [ ] Branch created."))
    assert plan_gate.check_plan(plan)[0] == 4


def test_cli(target: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = target / "specs" / "001-feedback-widget" / "03_plan.md"
    assert plan_gate.main(["locate", "--target-dir", str(target), "--spec", "001"]) == 0
    assert capsys.readouterr().out.strip() == str(plan)
    assert plan_gate.main(["check", str(plan)]) == 5
    plan.write_text(UNAPPROVED)
    assert plan_gate.main(["check", str(plan)]) == 4
    assert "unticked" in capsys.readouterr().err
    plan.write_text(APPROVED)
    assert plan_gate.main(["check", str(plan)]) == 0
    assert plan_gate.main(["locate", "--target-dir", str(target), "--spec", "zzz"]) == 5
