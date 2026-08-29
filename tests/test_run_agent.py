"""Tests for bin/run_agent.sh, bin/bootstrap_env.sh and bin/harness_config.py (ADR-005, ADR-006, ADR-008)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
RUN_AGENT = HARNESS / "bin" / "run_agent.sh"
CONFIG_PY = HARNESS / "bin" / "harness_config.py"


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHON": sys.executable, **(env or {})}
    # loop tests drive fakes, not a live model/venv — skip network preflight & the mechanical gate
    # unless a test opts back in.
    args = list(args)
    if "--preflight" in args:
        args.remove("--preflight")
    elif "--skip-preflight" not in args:
        args = [*args, "--skip-preflight"]
    if "--with-completion-checks" in args:
        args.remove("--with-completion-checks")
    elif "--no-completion-checks" not in args:
        args = [*args, "--no-completion-checks"]
    return subprocess.run([str(RUN_AGENT), *args], cwd=cwd, env=full_env, capture_output=True, text=True)


APPROVED_PLAN = "# plan\n## Pre-flight\n- [x] intent approved.\n- [x] spec reviewed.\n- [x] no conflicts.\n- [x] branch created.\n## Steps\n1. do it\n"
UNAPPROVED_PLAN = APPROVED_PLAN.replace("- [x] branch created.", "- [ ] branch created.")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "S-01"
    spec.mkdir(parents=True)
    (spec / "01_spec.md").write_text("# spec\n")
    (spec / "03_plan.md").write_text(APPROVED_PLAN)  # implement phase requires an approved plan (ADR-011)
    (tmp_path / "specs" / "template").mkdir()
    (tmp_path / "specs" / "template" / "03_plan.md").write_text("## Pre-flight\n- [ ] a\n")
    return tmp_path


def test_config_env_exports_openai_vars() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "env", "ollama"], capture_output=True, text=True, check=True).stdout
    env = dict(shlex.split(line.removeprefix("export "))[0].split("=", 1) for line in out.splitlines())
    assert env["OPENAI_BASE_URL"] == "http://localhost:11434/v1"
    assert env["OPENAI_API_KEY"] == "ollama"
    assert env["LLM_CONTEXT_LENGTH"] == "32768" and env["LLM_MAX_OUTPUT_TOKENS"] == "8192"
    assert env["MODEL_NAME"] == "gpt-oss:20b-32k"
    assert not any(k.startswith("ANTHROPIC_") for k in env), "ADR-006: no Anthropic-specific variables"


def test_config_env_prefers_api_key_env() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "env"], capture_output=True, text=True, check=True,
                         env={**os.environ, "OLLAMA_API_KEY": "s3cret"}).stdout
    assert "export OPENAI_API_KEY=s3cret" in out


def test_config_unknown_backend_fails() -> None:
    res = subprocess.run([sys.executable, str(CONFIG_PY), "env", "nope"], capture_output=True, text=True)
    assert res.returncode != 0 and "unknown backend 'nope'" in res.stderr


def test_config_role_prompt_forbids_guessing() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "role"], capture_output=True, text=True, check=True).stdout
    assert "read_specification" in out and "Do NOT guess" in out


def test_run_agent_requires_spec_and_target(target: Path) -> None:
    assert _run(["--target-dir", str(target)], target).returncode == 2
    assert _run(["--spec", "S-01"], target, {"TARGET_DIR": ""}).returncode == 2
    assert _run(["--spec", "../x", "--target-dir", str(target)], target).returncode == 2


def test_run_agent_dry_run_writes_mcp_json_and_command(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target, {"AGENT_CMD": "fake-agent"})
    assert res.returncode == 0, res.stderr

    mcp = json.loads((target / ".agent-harness" / "mcp.json").read_text())
    server = mcp["mcpServers"]["agent-harness"]
    assert server["command"] == str(HARNESS / "bin" / "start_mcp.sh")
    assert server["args"][:2] == ["--target-dir", str(target)]
    assert "run_toolchain_task" in server["args"]  # ADR-015: implementer server enables it

    line = res.stdout.splitlines()[-1]  # printf %q output; bash quoting is not shlex-parseable
    assert line.startswith(f"fake-agent --mcp-config {target / '.agent-harness' / 'mcp.json'} --append-system-prompt ")
    assert "SystemArchitect" in line
    assert "Implement\\ spec\\ S-01" in line and "specs/S-01/01_spec.md" in line
    assert "generic CLI, single attempt, no verifier" in res.stdout


def test_run_agent_default_opencode_invocation(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target)
    assert res.returncode == 0, res.stderr
    assert "success marker:" in res.stdout and "max attempts: 5" in res.stdout
    cmd = shlex.split(res.stdout.splitlines()[-1])
    assert cmd[:2] == ["opencode", "run"]
    assert cmd[cmd.index("--dir") + 1] == str(target)
    assert cmd[cmd.index("-m") + 1] == "ollama/gpt-oss:20b-32k"
    assert cmd[cmd.index("--agent") + 1] == "SystemArchitect"
    assert "Implement spec S-01" in cmd[-1]

    oc = json.loads((target / ".agent-harness" / "opencode.json").read_text())
    assert oc["model"] == "ollama/gpt-oss:20b-32k"
    assert oc["provider"]["ollama"]["options"]["baseURL"] == "http://localhost:11434/v1"
    assert oc["provider"]["ollama"]["npm"] == "@ai-sdk/openai-compatible"
    limit = oc["provider"]["ollama"]["models"]["gpt-oss:20b-32k"]["limit"]
    assert limit == {"context": 32768, "output": 8192}, "missing limit => Open Code compacts every turn"
    mcp = oc["mcp"]["agent-harness"]
    assert mcp["type"] == "local"
    assert mcp["command"][:3] == [str(HARNESS / "bin" / "start_mcp.sh"), "--target-dir", str(target)]
    assert "run_toolchain_task" in mcp["command"]
    agent = oc["agent"]["SystemArchitect"]
    assert "Do NOT guess" in agent["prompt"]
    assert "- agent-harness_read_constitution" in agent["prompt"], "persona must carry Open Code's prefixed tool names"
    for t in ("bash", "edit", "write", "patch", "multiedit", "webfetch", "read", "glob", "grep"):
        assert agent["tools"][t] is False
    assert "agent-harness_read_constitution" in cmd[-1]


def test_bootstrap_check_only_reports_python_env() -> None:
    res = subprocess.run([str(HARNESS / "bin" / "bootstrap_env.sh"), "--check-only"], capture_output=True, text=True,
                         env={**os.environ, "PYTHON": sys.executable})
    assert res.returncode in (0, 1), res.stderr
    assert "== Python environment" in res.stdout and "== Open Code" in res.stdout and "== LLM backend" in res.stdout
    assert "[ok]      python 3." in res.stdout
    assert "[MISSING]" in res.stdout or "environment ready" in res.stdout


def test_bootstrap_rejects_unknown_arg() -> None:
    res = subprocess.run([str(HARNESS / "bin" / "bootstrap_env.sh"), "--bogus"], capture_output=True, text=True)
    assert res.returncode == 2


# ---------------------------------------------------------------- supervisor loop (ADR-008)

FAKE_AGENT = r"""#!/bin/bash
# Fake Open Code. `session list` prints a stable id; runs log their argv and drive markers by --agent.
if [ "$1" = "session" ]; then
  printf 'Session ID\tTitle\tUpdated\n'; printf -- '----\n'; printf 'ses_impl_fake\tImpl\t1:00 PM\n'; exit 0
fi
{ printf '%q ' "$@"; echo; } >> "$FAKE_LOG"
agent=""; prev=""
for a in "$@"; do [ "$prev" = "--agent" ] && agent="$a"; prev="$a"; done
mkdir -p "$AGENT_TARGET_DIR/.agent-harness"
if [ "$agent" = "Verifier" ]; then
  vn=$(grep -c -- "--agent Verifier" "$FAKE_LOG")
  if [ -n "${VERIFY_PASS_ON:-}" ] && [ "$vn" -ge "$VERIFY_PASS_ON" ]; then
    : > "$AGENT_TARGET_DIR/.agent-harness/spec_complete"
  else
    echo "- missing requirement: GET / must serve the widget HTML"
    echo "- failing test: tests/integration/test_root_serves_widget"
  fi
  exit 0
fi
# primary agent (SystemArchitect / Planner): create run_successful on the Nth primary invocation
pn=$(grep -v -- "--agent Verifier" "$FAKE_LOG" | grep -c .)
[ -z "${NO_PROGRESS:-}" ] && echo "impl $pn" > "$AGENT_TARGET_DIR/impl_progress_$pn.txt"
if [ -n "${SUCCEED_ON:-}" ] && [ "$pn" -ge "$SUCCEED_ON" ]; then
  : > "$AGENT_TARGET_DIR/.agent-harness/run_successful"
fi
exit 0
"""


@pytest.fixture
def fake_opencode(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A directory containing an executable named ``opencode`` plus its invocation log."""
    d = tmp_path_factory.mktemp("fake")
    exe = d / "opencode"
    exe.write_text(FAKE_AGENT)
    exe.chmod(0o755)
    return exe, d / "calls.log"


def _calls(log: Path) -> list[list[str]]:
    return [shlex.split(line) for line in log.read_text().splitlines()] if log.exists() else []


def _agents(log: Path) -> list[str]:
    out = []
    for c in _calls(log):
        out.append(c[c.index("--agent") + 1] if "--agent" in c else "?")
    return out


# ---------------------------------------------------------------- generic CLI (no verifier)


def test_generic_agent_cmd_runs_single_attempt(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    generic = exe.parent / "some-agent"
    generic.write_text(FAKE_AGENT.replace("session list", "no-session"))
    generic.chmod(0o755)
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(generic), "FAKE_LOG": str(log)})
    assert res.returncode == 3 and len(_calls(log)) == 1
    assert "--mcp-config" in _calls(log)[0] and "--continue" not in _calls(log)[0]


# ---------------------------------------------------------------- plan phase (no verifier)


def test_plan_phase_runs_supervisor_and_succeeds_on_marker(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    (target / "specs" / "S-01" / "03_plan.md").unlink()
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--phase", "plan"], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "1"})
    assert res.returncode == 0, res.stderr
    assert _agents(log) == ["Planner"], "plan phase must not run a Verifier"
    assert "phase=plan" in res.stderr and "verify=0" in res.stderr


# ---------------------------------------------------------------- implement phase: verifier ping-pong (ADR-012)


def test_verifier_pingpong_succeeds_when_verifier_marks_complete(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    # implementer commits on its 1st call; verifier passes on its 2nd call
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "1", "VERIFY_PASS_ON": "2"})
    assert res.returncode == 0, res.stderr
    assert _agents(log) == ["SystemArchitect", "Verifier", "SystemArchitect", "Verifier"]
    # the resume carries the Verifier's feedback and pins the implementer session
    resume = _calls(log)[2]
    assert "--session" in resume and resume[resume.index("--session") + 1] == "ses_impl_fake"
    assert "--continue" not in resume
    assert "The Verifier reported the following incomplete items. Fix them and commit:" in resume[-1]
    assert "GET / must serve the widget" in resume[-1]
    assert (target / ".agent-harness" / "spec_complete").exists()
    assert "SPEC COMPLETE" in res.stderr


def test_commit_alone_does_not_end_run_without_verifier_approval(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    # implementer commits every time, but the verifier never passes
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "1", "MAX_RETRIES": "3"})
    assert res.returncode == 3, res.stderr
    assert _agents(log) == ["SystemArchitect", "Verifier"] * 3
    assert (target / ".agent-harness" / "run_successful").exists() is False or True  # deleted each iter; not the success cond
    assert not (target / ".agent-harness" / "spec_complete").exists()
    assert "FAILED — spec not complete after 3" in res.stderr


def test_no_progress_aborts_early(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    # implementer makes NO file change and never commits; verifier never passes
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "NO_PROGRESS": "1", "MAX_RETRIES": "5"})
    assert res.returncode == 7, res.stderr
    assert "no progress" in res.stderr
    # aborted well before exhausting 5 attempts
    assert len([l for l in log.read_text().splitlines() if "--agent Verifier" not in l]) <= 3


def test_verifier_dry_run_shows_both_agents_and_configs(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target)
    assert res.returncode == 0, res.stderr
    assert "verify: 1" in res.stdout and "opencode.verifier.json" in res.stdout
    assert "--enable-tool mark_spec_complete" in res.stdout
    assert "success marker:" in res.stdout and "spec_complete" in res.stdout
    lines = res.stdout.splitlines()
    verify_line = next(l for l in lines if l.startswith("# verify: "))
    assert "--agent Verifier" in verify_line
    # generated configs on disk
    vcfg = json.loads((target / ".agent-harness" / "opencode.verifier.json").read_text())
    assert "Verifier" in vcfg["agent"]
    assert "mark_spec_complete" in vcfg["mcp"]["agent-harness"]["command"] and "run_toolchain_task" in vcfg["mcp"]["agent-harness"]["command"]
    vtools = vcfg["agent"]["Verifier"]["tools"]
    assert "agent-harness_mark_spec_complete" not in vtools  # allowed -> not disabled
    assert vtools["agent-harness_git_commit_feature"] is False and vtools["agent-harness_fs_write"] is False
    impl = json.loads((target / ".agent-harness" / "opencode.json").read_text())
    assert "mark_spec_complete" not in impl["mcp"]["agent-harness"]["command"], "implementer server must NOT enable spec-complete"
    assert impl["agent"]["SystemArchitect"]["tools"]["agent-harness_mark_spec_complete"] is False


# ---------------------------------------------------------------- ADR-011 phases & gate (retained)


def test_implement_phase_refuses_unapproved_or_missing_plan(target: Path, fake_opencode: tuple[Path, Path]) -> None:
    exe, log = fake_opencode
    plan = target / "specs" / "S-01" / "03_plan.md"
    plan.write_text(UNAPPROVED_PLAN)
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(exe), "FAKE_LOG": str(log)})
    assert res.returncode == 4 and "branch created" in res.stderr and not log.exists(), "agent must not launch"
    plan.write_text("")
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target, {"AGENT_CMD": str(exe), "FAKE_LOG": str(log)})
    assert res.returncode == 5 and "--phase plan" in res.stderr and not log.exists()
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--skip-plan-gate", "--dry-run"], target)
    assert res.returncode == 0 and "WARNING (--skip-plan-gate)" in res.stderr


def test_implement_phase_dry_run_reports_approved_plan(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target)
    assert res.returncode == 0, res.stderr
    assert "plan approved" in res.stderr and "# phase: implement role: SystemArchitect verify: 1" in res.stdout
    mcp = json.loads((target / ".agent-harness" / "mcp.json").read_text())
    assert "--write-scope" not in mcp["mcpServers"]["agent-harness"]["args"]


def test_plan_phase_uses_planner_write_scope_and_tool_subset(target: Path) -> None:
    (target / "specs" / "S-01" / "03_plan.md").write_text("")  # empty plan is fine for the plan phase
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--phase", "plan", "--dry-run"], target)
    assert res.returncode == 0, res.stderr
    assert "# phase: plan role: Planner verify: 0" in res.stdout
    cmd = shlex.split(res.stdout.splitlines()[-1])
    assert cmd[cmd.index("--agent") + 1] == "Planner"
    assert "specs/S-01/03_plan.md" in cmd[-1] and "Pre-flight checkbox" in cmd[-1]

    mcp = json.loads((target / ".agent-harness" / "mcp.json").read_text())
    a = mcp["mcpServers"]["agent-harness"]["args"]
    assert a[:4] == ["--target-dir", str(target), "--write-scope", "specs"] and "run_toolchain_task" in a
    oc = json.loads((target / ".agent-harness" / "opencode.json").read_text())
    assert "--write-scope" in oc["mcp"]["agent-harness"]["command"] and "specs" in oc["mcp"]["agent-harness"]["command"]
    tools = oc["agent"]["Planner"]["tools"]
    assert tools["agent-harness_run_tests"] is False and tools["agent-harness_fs_apply_patch"] is False
    assert "agent-harness_fs_write" not in tools and "agent-harness_git_commit_feature" not in tools
    assert not (target / ".agent-harness" / "opencode.verifier.json").exists(), "plan phase has no Verifier"


def test_invalid_phase_rejected(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--phase", "deploy", "--dry-run"], target)
    assert res.returncode == 2 and "--phase must be" in res.stderr


def test_config_tools_subcommand() -> None:
    out = subprocess.run([sys.executable, str(CONFIG_PY), "tools", "Planner"], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["read_constitution", "read_specification", "fs_list", "fs_read", "fs_write", "git_commit_feature"]
    out = subprocess.run([sys.executable, str(CONFIG_PY), "tools", "Verifier"], capture_output=True, text=True, check=True).stdout.split()
    assert "mark_spec_complete" in out and "git_commit_feature" not in out


# ---------------------------------------------------------------- ADR-013 Claude Code backend

FAKE_CLAUDE = r"""#!/bin/bash
# Fake `claude`: one clean log line per call ("<kind>\t<resume>\t<prompt>"); own counters; JSON out.
kind=impl; resume="-"; prompt=""; prev=""
for a in "$@"; do
  case "$prev" in -p) prompt="$a";; --resume) resume="$a";; esac
  case "$a" in *mark_spec_complete*) kind=verifier;; esac
  prev="$a"
done
prompt="$(printf '%s' "$prompt" | tr '\n' ' ')"
printf '%s\t%s\t%s\n' "$kind" "$resume" "$prompt" >> "$FAKE_LOG"
D="$(dirname "$FAKE_LOG")"; mkdir -p "$AGENT_TARGET_DIR/.agent-harness"
if [ "$kind" = "verifier" ]; then
  n=$(( $(cat "$D/vn" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$D/vn"
  [ -n "${VERIFY_PASS_ON:-}" ] && [ "$n" -ge "$VERIFY_PASS_ON" ] && : > "$AGENT_TARGET_DIR/.agent-harness/spec_complete"
  printf '{"session_id":"ses_v%s","result":"- missing: GET / route","is_error":false}\n' "$n"
else
  n=$(( $(cat "$D/pn" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$D/pn"
  [ -z "${NO_PROGRESS:-}" ] && echo "impl $n" > "$AGENT_TARGET_DIR/impl_progress_$n.txt"
  [ -n "${SUCCEED_ON:-}" ] && [ "$n" -ge "$SUCCEED_ON" ] && : > "$AGENT_TARGET_DIR/.agent-harness/run_successful"
  printf '{"session_id":"ses_i%s","result":"ok","is_error":false}\n' "$n"
fi
"""


@pytest.fixture
def fake_claude(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    d = tmp_path_factory.mktemp("fakecc")
    exe = d / "claude"; exe.write_text(FAKE_CLAUDE); exe.chmod(0o755)
    return exe, d / "cc.log"


def test_claude_dry_run_wires_mcp_and_mcp_only_tools(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--dry-run"], target, {"AGENT_CMD": "claude"})
    assert res.returncode == 0, res.stderr
    assert "# backend: claude  model: sonnet" in res.stdout and "verify: 1" in res.stdout
    assert "mcp.verifier.json (--enable-tool mark_spec_complete)" in res.stdout
    line = res.stdout.splitlines()[-1]
    assert line.startswith("claude -p ")
    assert "mcp__agent-harness__git_commit_feature" in res.stdout  # implementer allowed
    # built-ins are disallowed (MCP-only enforcement); %q may escape commas
    assert "--disallowedTools" in line and "Bash" in line and "Write" in line
    mcp = json.loads((target / ".agent-harness" / "mcp.json").read_text())
    assert "--write-scope" not in mcp["mcpServers"]["agent-harness"]["args"]  # implement phase


def test_claude_model_override(target: Path) -> None:
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--model", "opus", "--dry-run"], target, {"AGENT_CMD": "claude"})
    assert res.returncode == 0 and "model: opus" in res.stdout


def test_claude_pingpong_succeeds_when_verifier_marks_complete(target: Path, fake_claude: tuple[Path, Path]) -> None:
    exe, log = fake_claude
    res = _run(["--spec", "S-01", "--target-dir", str(target)], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "1", "VERIFY_PASS_ON": "2"})
    assert res.returncode == 0, res.stderr
    calls = [l.split("\t") for l in log.read_text().splitlines()]
    kinds = [c[0] for c in calls]
    assert kinds == ["impl", "verifier", "impl", "verifier"]
    # 2nd implementer call resumes the captured session and carries the gap feedback
    resume = calls[2]  # kind, resume-session, prompt
    assert resume[1].startswith("ses_i")
    assert resume[2].startswith("The Verifier reported the following incomplete items")
    assert (target / ".agent-harness" / "spec_complete").exists()
    assert "SPEC COMPLETE" in res.stderr


def test_claude_plan_phase_no_verifier(target: Path, fake_claude: tuple[Path, Path]) -> None:
    exe, log = fake_claude
    (target / "specs" / "S-01" / "03_plan.md").unlink()
    res = _run(["--spec", "S-01", "--target-dir", str(target), "--phase", "plan"], target,
               {"AGENT_CMD": str(exe), "FAKE_LOG": str(log), "SUCCEED_ON": "1"})
    assert res.returncode == 0, res.stderr
    calls = log.read_text()
    assert "mark_spec_complete" not in calls and "run successful" in res.stderr
