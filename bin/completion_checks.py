#!/usr/bin/env python3
"""Mechanical completion gate run AFTER the Verifier marks spec_complete (ADR-014 §4).

The Verifier judges through pytest and can't see packaging/type/lint defects. This runs
model-independent checks in the target and returns a bulleted gap list (exit 4) or passes (0):
  - hermetic: fresh venv from requirements.txt in a throwaway CWD, then pytest — catches a
    missing runtime dependency and CWD-pollution the in-process TestClient hides;
  - mypy (if installed in the target venv);
  - ruff (if installed in the target venv).
Best-effort per check; a check whose tool is absent is reported as skipped, not failed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _run(argv: list[str], cwd: Path, timeout: float = 900.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def hermetic_build_and_test(target: Path) -> str | None:
    """Fresh venv from requirements.txt, then pytest FROM the project root. Returns a gap line or None.

    Dependency isolation comes from the throwaway venv (built only from requirements.txt); the working
    directory stays the project root so the project's own pytest config (rootdir, ``pythonpath``) and
    ``python -m``'s CWD-on-sys.path both resolve as they do for a normal ``pytest`` invocation — running
    from a temp CWD would spuriously break every src-layout project (ADR-014).
    """
    req = target / "requirements.txt"
    if not req.is_file():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="harness-hermetic-"))
    try:
        v = tmp / "venv"
        venv.EnvBuilder(with_pip=True).create(v)
        py = v / "bin" / "python"
        pip = _run([str(py), "-m", "pip", "install", "-q", "-r", str(req)], cwd=tmp)
        if pip.returncode != 0:
            return f"hermetic build FAILED: `pip install -r requirements.txt` errored — {pip.stderr.strip()[-300:]}"
        _run([str(py), "-m", "pip", "install", "-q", "pytest"], cwd=tmp)
        before = {p.name for p in target.glob("*.db")} | {p.name for p in target.glob("*.sqlite*")}
        run = _run([str(py), "-m", "pytest", "-q", "tests"], cwd=target)
        if run.returncode != 0:
            tail = (run.stdout + run.stderr).strip().splitlines()[-6:]
            return ("hermetic tests FAILED in a clean venv (a runtime dependency is likely missing "
                    "from requirements.txt): " + " / ".join(tail))
        leaked = ({p.name for p in target.glob("*.db")} | {p.name for p in target.glob("*.sqlite*")}) - before
        if leaked:
            return f"isolation: tests wrote a database into the project directory ({', '.join(sorted(leaked))}) — the DB path is not configurable."
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def venv_tool(target: Path, module: str, args: list[str]) -> str | None:
    py = target / ".venv" / "bin" / "python"
    if not py.exists():
        return None
    probe = _run([str(py), "-c", f"import {module}"], cwd=target, timeout=30)
    if probe.returncode != 0:
        return f"{module}: not installed in the target .venv — cannot run the mandated check"
    res = _run([str(py), "-m", module, *args], cwd=target)
    if res.returncode != 0:
        tail = (res.stdout + res.stderr).strip().splitlines()[-4:]
        return f"{module} FAILED: " + " / ".join(tail)
    return None


def run_checks(target: Path, hermetic: bool = True) -> list[str]:
    gaps: list[str] = []
    if hermetic:
        g = hermetic_build_and_test(target)
        if g:
            gaps.append(g)
    for g in (venv_tool(target, "mypy", ["src"]), venv_tool(target, "ruff", ["check", "."])):
        if g:
            gaps.append(g)
    return gaps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="completion_checks", description=__doc__.splitlines()[0])
    ap.add_argument("--target-dir", type=Path, required=True)
    ap.add_argument("--no-hermetic", action="store_true")
    ns = ap.parse_args(argv)
    gaps = run_checks(ns.target_dir.resolve(), hermetic=not ns.no_hermetic)
    if not gaps:
        print("completion checks passed", file=sys.stderr)
        return 0
    for g in gaps:
        print(f"- {g}")
    return 4


if __name__ == "__main__":
    sys.exit(main())
