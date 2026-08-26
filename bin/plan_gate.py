#!/usr/bin/env python3
"""Plan location and human-approval gate for bin/run_agent.sh (ADR-011).

  locate --target-dir <path> --spec <id>   print the plan path (specs/<id>*/03_plan.md)
  check  <plan-file>                        exit 0 approved, 4 unticked pre-flight items,
                                            5 missing/empty plan or no '## Pre-flight' section
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PLAN_NAME = "03_plan.md"
# "## Pre-flight" (any heading level) or a bold "**Pre-flight**" line; ASCII or Unicode hyphen.
PREFLIGHT_HEADING = re.compile(r"^(?:#{1,6}\s+|\*\*)pre[-\u2010\u2011\u2013]flight(?:\s+checklist)?\**:?\s*$", re.IGNORECASE)
HEADING = re.compile(r"^#{1,6}\s")
CHECKBOX = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s*(.*)$")

EXIT_OK, EXIT_UNAPPROVED, EXIT_MISSING = 0, 4, 5


def locate_plan(target_dir: Path, spec_id: str) -> Path:
    """Resolve ``specs/<spec_id>*/03_plan.md`` (exact directory name wins; otherwise unique prefix)."""
    specs = target_dir / "specs"
    if not specs.is_dir():
        raise FileNotFoundError(f"no specs/ directory in {target_dir}")
    exact = specs / spec_id
    if exact.is_dir():
        return exact / PLAN_NAME
    matches = sorted(p for p in specs.iterdir() if p.is_dir() and p.name.startswith(spec_id) and p.name != "template")
    if len(matches) != 1:
        names = ", ".join(p.name for p in matches) or "none"
        raise FileNotFoundError(f"spec {spec_id!r} matches {len(matches)} directories under specs/ ({names})")
    return matches[0] / PLAN_NAME


def preflight_items(text: str) -> list[tuple[bool, str]] | None:
    """Return ``[(ticked, label), ...]`` from the Pre-flight section, or None if there is none."""
    # Tolerate a blockquote prefix on every line (a Planner formatting slip seen in run 10).
    lines = [re.sub(r"^>\s?", "", l) for l in text.splitlines()]
    start = next((i for i, l in enumerate(lines) if PREFLIGHT_HEADING.match(l.strip())), None)
    if start is None:
        return None
    items: list[tuple[bool, str]] = []
    for line in lines[start + 1:]:
        if HEADING.match(line) or line.strip().startswith("**") and line.strip().endswith(("**", "**:")):
            break
        m = CHECKBOX.match(line)
        if m:
            items.append((m.group(1).lower() == "x", m.group(2).strip()))
    return items


def check_plan(plan: Path) -> tuple[int, str]:
    """Return ``(exit_code, human message)`` for the approval state of ``plan``."""
    if not plan.is_file():
        return EXIT_MISSING, f"plan missing: {plan} — run `run_agent.sh --phase plan` first"
    text = plan.read_text(encoding="utf-8")
    if not text.strip():
        return EXIT_MISSING, f"plan is empty: {plan} — run `run_agent.sh --phase plan` first"
    items = preflight_items(text)
    if items is None:
        return EXIT_MISSING, f"plan has no '## Pre-flight' section: {plan}"
    if not items:
        return EXIT_MISSING, f"plan's Pre-flight section has no checkboxes: {plan}"
    unticked = [label for ticked, label in items if not ticked]
    if unticked:
        bullets = "\n".join(f"  - [ ] {u}" for u in unticked)
        return EXIT_UNAPPROVED, f"plan not approved — {len(unticked)} pre-flight item(s) unticked in {plan}:\n{bullets}"
    return EXIT_OK, f"plan approved: {plan} ({len(items)} pre-flight items ticked)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plan_gate", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    loc = sub.add_parser("locate")
    loc.add_argument("--target-dir", type=Path, required=True)
    loc.add_argument("--spec", required=True)
    chk = sub.add_parser("check")
    chk.add_argument("plan", type=Path)
    ns = ap.parse_args(argv)
    if ns.cmd == "locate":
        try:
            print(locate_plan(ns.target_dir.resolve(), ns.spec))
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_MISSING
        return EXIT_OK
    code, msg = check_plan(ns.plan)
    print(msg, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
