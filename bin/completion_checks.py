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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_server.core.audit import emit_env  # noqa: E402
from mcp_server.core.sandbox import Sandbox  # noqa: E402
from mcp_server.core.toolchain import head_tail_truncate, resolve_toolchain, sanitize  # noqa: E402


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



def _toolchain_gap(target: Path, task: str) -> str | None:
    """Run one toolchain task; return a compact gap line if it failed (ADR-015)."""
    tc = resolve_toolchain(Sandbox(target))
    res = tc.run(Sandbox(target), task)
    gate_status = "skipped" if res.skipped else ("success" if res.exit_code == 0 else "failed")
    emit_env("execute_toolchain_task", "gate", task=task, stack=tc.stack,
             command=res.command, status=gate_status, exit_code=res.exit_code)
    if res.skipped or res.exit_code == 0:
        return None
    detail = head_tail_truncate(sanitize(res.stdout + "\n" + res.stderr), head=2, tail=6, limit=800).strip()
    return f"{task} FAILED ({tc.stack}): {detail}"


def run_checks(target: Path, hermetic: bool = True) -> list[str]:
    """Mechanical gate. Python default: hermetic build/test + toolchain lint (mypy+ruff).
    A toolchain.json project runs install -> test -> lint through the abstraction (ADR-015)."""
    tc = resolve_toolchain(Sandbox(target))
    gaps: list[str] = []
    if getattr(tc, "stack", "") == "python-default":
        if hermetic:
            g = hermetic_build_and_test(target)
            if g:
                gaps.append(g)
        g = _toolchain_gap(target, "lint")   # ADR-015: mypy + ruff via the abstraction
        if g:
            gaps.append(g)
    else:
        for task in ("install", "test", "lint"):
            g = _toolchain_gap(target, task)
            if g:
                gaps.append(g)
    return gaps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="completion_checks", description=__doc__.splitlines()[0])
    ap.add_argument("--target-dir", type=Path, required=True)
    ap.add_argument("--no-hermetic", action="store_true")
    ns = ap.parse_args(argv)
    gaps = run_checks(ns.target_dir.resolve(), hermetic=not ns.no_hermetic)
    emit_env("mechanical_gate", "gate", status=("success" if not gaps else "failed"),
             gaps=len(gaps), result=("\n".join(gaps) if gaps else None))
    if not gaps:
        print("completion checks passed", file=sys.stderr)
        return 0
    for g in gaps:
        print(f"- {g}")
    return 4


if __name__ == "__main__":
    sys.exit(main())
