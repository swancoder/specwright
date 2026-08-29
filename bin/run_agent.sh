#!/bin/bash
# Launches the agent CLI (Open Code) against a target project for one specification
# (ADR-005, ADR-006, ADR-008).
#
# Usage: ./bin/run_agent.sh --spec <spec_id> --target-dir <path>
#                           [--phase plan|implement] [--backend <name>] [--role <name>]
#                           [--skip-plan-gate] [--dry-run]
#
# Phases (ADR-011): `plan` runs the Planner persona (writes under specs/ only) to produce
#   specs/<spec>/03_plan.md and commit it; `implement` (default) runs SystemArchitect and
#   refuses to start unless every Pre-flight checkbox in 03_plan.md is ticked by a human
#   (exit 4 = unapproved, 5 = plan missing/empty; --skip-plan-gate overrides).
#
# 1. exports OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME from config/llm_backends.yaml
# 2. writes <target>/.agent-harness/mcp.json  (bin/start_mcp.sh --target-dir <target>)
#    and derives <target>/.agent-harness/opencode.json (mcp + provider + persona agent)
# 3. supervisor loop (MAX_RETRIES attempts, default 5): runs
#      opencode run --dir <target> -m <backend>/<model> --agent <role> "<prompt>"
#    then checks <target>/.agent-harness/run_successful (written by git_commit_feature);
#    missing marker -> resume with `opencode run … --continue "<recovery prompt>"`.
#    exit 0 = marker seen, 3 = retries exhausted, 2 = usage error.
#    AGENT_CMD=<other> falls back to a single run of: <other> --mcp-config … --append-system-prompt … "<prompt>"
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$HARNESS_DIR/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
AGENT_CMD="${AGENT_CMD:-opencode}"
MAX_RETRIES="${MAX_RETRIES:-5}"
case "$MAX_RETRIES" in ''|*[!0-9]*|0) echo "run_agent.sh: MAX_RETRIES must be a positive integer (got '$MAX_RETRIES')" >&2; exit 2 ;; esac

SPEC_ID=""
TARGET_DIR="${TARGET_DIR:-}"
BACKEND=""
ROLE=""
PHASE="implement"
SKIP_GATE=0
DRY_RUN=0
MODEL_OVERRIDE=""
SKIP_PREFLIGHT=0
NO_COMPLETION_CHECKS=0

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --spec)        SPEC_ID="${2:-}"; shift 2 ;;
    --spec=*)      SPEC_ID="${1#*=}"; shift ;;
    --target-dir)  TARGET_DIR="${2:-}"; shift 2 ;;
    --target-dir=*) TARGET_DIR="${1#*=}"; shift ;;
    --backend)     BACKEND="${2:-}"; shift 2 ;;
    --backend=*)   BACKEND="${1#*=}"; shift ;;
    --role)        ROLE="${2:-}"; shift 2 ;;
    --role=*)      ROLE="${1#*=}"; shift ;;
    --phase)       PHASE="${2:-}"; shift 2 ;;
    --phase=*)     PHASE="${1#*=}"; shift ;;
    --skip-plan-gate) SKIP_GATE=1; shift ;;
    --model)       MODEL_OVERRIDE="${2:-}"; shift 2 ;;
    --model=*)     MODEL_OVERRIDE="${1#*=}"; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --no-completion-checks) NO_COMPLETION_CHECKS=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage 0 ;;
    *) echo "run_agent.sh: unknown argument '$1'" >&2; usage 2 ;;
  esac
done

[ -n "$SPEC_ID" ]    || { echo "run_agent.sh: --spec <spec_id> is required" >&2; usage 2; }
[ -n "$TARGET_DIR" ] || { echo "run_agent.sh: --target-dir <path> is required" >&2; usage 2; }
[ -d "$TARGET_DIR" ] || { echo "run_agent.sh: target dir '$TARGET_DIR' does not exist" >&2; exit 2; }
case "$SPEC_ID" in *[!A-Za-z0-9._-]*|"") echo "run_agent.sh: invalid spec id '$SPEC_ID'" >&2; exit 2 ;; esac
case "$PHASE" in plan|implement) ;; *) echo "run_agent.sh: --phase must be 'plan' or 'implement' (got '$PHASE')" >&2; exit 2 ;; esac
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

# --- 0. Plan location and approval gate (ADR-011) ------------------------------
PLAN_FILE="$("$PYTHON" "$HARNESS_DIR/bin/plan_gate.py" locate --target-dir "$TARGET_DIR" --spec "$SPEC_ID" 2>/dev/null || true)"
[ -n "$PLAN_FILE" ] || PLAN_FILE="$TARGET_DIR/specs/$SPEC_ID/03_plan.md"
PLAN_REL="${PLAN_FILE#"$TARGET_DIR"/}"
GATE_MSG=""
if [ "$PHASE" = "implement" ]; then
  if GATE_MSG="$("$PYTHON" "$HARNESS_DIR/bin/plan_gate.py" check "$PLAN_FILE" 2>&1)"; then
    echo "run_agent.sh: $GATE_MSG" >&2
  else
    rc=$?
    if [ "$SKIP_GATE" -eq 1 ]; then
      echo "run_agent.sh: WARNING (--skip-plan-gate): $GATE_MSG" >&2
    else
      echo "run_agent.sh: $GATE_MSG" >&2
      exit "$rc"
    fi
  fi
fi
if [ -d "$TARGET_DIR/.git" ] && [ -n "$(git -C "$TARGET_DIR" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "run_agent.sh: warning: target has uncommitted tracked changes; git_commit_feature stages -A and will include them" >&2
fi

SPEC_FILE="specs/$SPEC_ID/01_spec.md"
[ -f "$TARGET_DIR/$SPEC_FILE" ] || echo "run_agent.sh: warning: $TARGET_DIR/$SPEC_FILE not found (agent will use read_specification)" >&2

# --- 1. LLM environment from config/llm_backends.yaml -------------------------
eval "$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" env ${BACKEND:+"$BACKEND"})"
if [ -z "$ROLE" ] && [ "$PHASE" = "plan" ]; then ROLE="Planner"; fi
SYSTEM_PROMPT="$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" role ${ROLE:+"$ROLE"})"
ROLE_NAME="${ROLE:-$("$PYTHON" -c 'import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))["default"])' "$HARNESS_DIR/config/roles.yaml")}"
ROLE_TOOLS="$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" tools "$ROLE_NAME" | tr '\n' ' ')"
WRITE_SCOPE_ARGS=()
[ "$PHASE" = "plan" ] && WRITE_SCOPE_ARGS=(--write-scope specs)

# --- 2. MCP wiring: <target>/.agent-harness/{mcp.json,opencode.json[,opencode.verifier.json]} ----------
HARNESS_STATE="$TARGET_DIR/.agent-harness"
MCP_CONFIG="$HARNESS_STATE/mcp.json"
OPENCODE_CONFIG_FILE="$HARNESS_STATE/opencode.json"
VERIFIER_CONFIG_FILE="$HARNESS_STATE/opencode.verifier.json"
VERIFIER_ROLE="Verifier"
mkdir -p "$HARNESS_STATE"

# Primary persona config (+ mcp.json per the ADR-005 contract). Plan phase confines writes to specs/.
GEN_SCOPE=()
[ "$PHASE" = "plan" ] && GEN_SCOPE=(--write-scope specs)
"$PYTHON" "$HARNESS_DIR/bin/gen_opencode_config.py" \
  --target-dir "$TARGET_DIR" --role "$ROLE_NAME" \
  --out-opencode "$OPENCODE_CONFIG_FILE" --out-mcp "$MCP_CONFIG" "${GEN_SCOPE[@]}" \
  --enable-tool run_toolchain_task

# Backend detection: opencode | claude | generic (ADR-013). Open Code path unchanged.
# shellcheck disable=SC2206
AGENT_WORDS=($AGENT_CMD)
case "$(basename "${AGENT_WORDS[0]}")" in
  opencode) BACKEND_KIND=opencode ;;
  claude)   BACKEND_KIND=claude ;;
  *)        BACKEND_KIND=generic ;;
esac
VERIFY=0
[ "$PHASE" = "implement" ] && [ "$BACKEND_KIND" != "generic" ] && VERIFY=1

MCP_VERIFIER_CONFIG="$HARNESS_STATE/mcp.verifier.json"
CC_MODEL="${MODEL_OVERRIDE:-${CC_MODEL:-sonnet}}"
CC_DISALLOW="Bash,Edit,MultiEdit,Write,NotebookEdit,Read,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,BashOutput,KillShell"

if [ "$BACKEND_KIND" = "opencode" ] && [ "$VERIFY" -eq 1 ]; then
  "$PYTHON" "$HARNESS_DIR/bin/gen_opencode_config.py" \
    --target-dir "$TARGET_DIR" --role "$VERIFIER_ROLE" \
    --out-opencode "$VERIFIER_CONFIG_FILE" --enable-tool mark_spec_complete --enable-tool run_toolchain_task
fi

if [ "$BACKEND_KIND" = "claude" ]; then
  # Claude Code reads mcp.json directly; write implementer (+plan write-scope) and verifier (+enable-tool).
  _write_mcp() {  # $1=outfile  $2=extra server args (space-separated)
    OUT="$1" EXTRA="$2" HARNESS_DIR="$HARNESS_DIR" TARGET_DIR="$TARGET_DIR" PYTHON="$PYTHON" "$PYTHON" - <<'PYW'
import json, os
e = os.environ
srv = {"command": os.path.join(e["HARNESS_DIR"], "bin", "start_mcp.sh"),
       "args": ["--target-dir", e["TARGET_DIR"], *e.get("EXTRA", "").split()],
       "env": {"PYTHON": e.get("PYTHON", "")}}
with open(e["OUT"], "w", encoding="utf-8") as fh:
    json.dump({"mcpServers": {"agent-harness": srv}}, fh, indent=2); fh.write("\n")
PYW
  }
  CC_SCOPE="--enable-tool run_toolchain_task"; [ "$PHASE" = "plan" ] && CC_SCOPE="--write-scope specs"
  _write_mcp "$MCP_CONFIG" "$CC_SCOPE"
  [ "$VERIFY" -eq 1 ] && _write_mcp "$MCP_VERIFIER_CONFIG" "--enable-tool mark_spec_complete --enable-tool run_toolchain_task"
  # role tool allowlists as comma-joined mcp__agent-harness__<tool>
  ROLE_TOOLS_MCP="$(printf '%s\n' $ROLE_TOOLS | sed 's/^/mcp__agent-harness__/' | paste -sd, -)"
  VERIFIER_SYS="$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" role "$VERIFIER_ROLE")"
  VERIFIER_TOOLS_MCP="$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" tools "$VERIFIER_ROLE" | sed 's/^/mcp__agent-harness__/' | paste -sd, -)"
fi

# --- 3. Supervisor loop with Verifier handoff (ADR-008, ADR-012) ---------------
RUN_MARKER="$HARNESS_STATE/run_successful"
SPEC_MARKER="$HARNESS_STATE/spec_complete"
VERIFIER_OUT="$HARNESS_STATE/verifier_last.txt"
rm -f "$RUN_MARKER" "$SPEC_MARKER"   # stale markers must never count

RESUME_PROMPT="Continue with the plan. Your last message was plain text instead of a tool call. You must use the appropriate MCP tool to proceed, or call git_commit_feature if you are done."

# --- ADR-014 reliability helpers ---------------------------------------------
LAST_IMPL="$HARNESS_STATE/last_impl.txt"
NOPROG=0; PREV_KEY=""; GATE_GAPS=""
progress_key() { { { git -C "$TARGET_DIR" rev-parse HEAD 2>/dev/null; git -C "$TARGET_DIR" status --porcelain 2>/dev/null; find "$TARGET_DIR" -type f -not -path '*/.agent-harness/*' -not -path '*/.git/*' -not -path '*/.venv/*' -printf '%p %s %T@\n' 2>/dev/null; } | md5sum | cut -d" " -f1; } || true; }
recovery_hint() { "$PYTHON" "$HARNESS_DIR/bin/recover_hint.py" "$1" 2>/dev/null || true; }
COMPLETION_GATE=0
[ "$PHASE" = "implement" ] && [ "$NO_COMPLETION_CHECKS" -eq 0 ] && COMPLETION_GATE=1
run_completion_gate() {   # sets GATE_GAPS; returns 0 iff all mechanical checks pass
  [ "$COMPLETION_GATE" -eq 1 ] || return 0
  GATE_GAPS="$("$PYTHON" "$HARNESS_DIR/bin/completion_checks.py" --target-dir "$TARGET_DIR" 2>/dev/null)"; return $?
}
note_progress() {   # $1 = "committed" marker present? returns 0 to continue, exits 7 on stall
  local key; key="$(progress_key)"
  if [ "$key" = "$PREV_KEY" ]; then NOPROG=$((NOPROG+1)); else NOPROG=0; PREV_KEY="$key"; fi
  if [ "$NOPROG" -ge 2 ]; then echo "run_agent.sh: ABORT — no progress (no commit or file change) for 2 attempts" >&2; exit 7; fi
}

# --- model preflight (local OpenAI-compatible backends only; ADR-014 §1) -----
if [ "$DRY_RUN" -eq 0 ] && [ "$SKIP_PREFLIGHT" -eq 0 ] && [ "$BACKEND_KIND" != "claude" ]; then
  echo "run_agent.sh: model preflight ($MODEL_NAME @ ${OPENAI_BASE_URL:-?})" >&2
  if ! "$PYTHON" "$HARNESS_DIR/bin/preflight_model.py"; then
    pf=$?
    [ "$pf" -eq 4 ] && { echo "run_agent.sh: ABORT — model cannot make structured tool calls (see above); --skip-preflight to override" >&2; exit 6; }
    echo "run_agent.sh: preflight warning (rc=$pf) — continuing" >&2
  fi
fi
export AGENT_SPEC_ID="$SPEC_ID" AGENT_TARGET_DIR="$TARGET_DIR" AGENT_MCP_CONFIG="$MCP_CONFIG" AGENT_PHASE="$PHASE" AGENT_PLAN_FILE="$PLAN_FILE"

IS_OPENCODE=0
[ "$BACKEND_KIND" = "opencode" ] && IS_OPENCODE=1

if [ "$PHASE" = "plan" ]; then
  PROMPT="Create the implementation plan for spec ${SPEC_ID}. Step 1: call agent-harness_read_constitution. Step 2: call agent-harness_read_specification with spec_id '${SPEC_ID}'. Step 3: agent-harness_fs_read 'specs/template/03_plan.md' and agent-harness_fs_list '.' (recursive). Step 4: agent-harness_fs_write the complete plan to '${PLAN_REL}' — leave every Pre-flight checkbox as '- [ ]'. Step 5: agent-harness_git_commit_feature with message 'docs(spec): add implementation plan for ${SPEC_ID}' and spec_id '${SPEC_ID}'. Then stop."
else
  PROMPT="Implement spec ${SPEC_ID}. Step 1: call agent-harness_read_constitution. Step 2: call agent-harness_read_specification with spec_id '${SPEC_ID}' (source: ${SPEC_FILE}). Then plan, implement, test and commit strictly through the agent-harness_* tools. Commit at every green milestone."
fi
VERIFIER_PROMPT="Verify specification ${SPEC_ID}. Call agent-harness_read_constitution, then agent-harness_read_specification with spec_id '${SPEC_ID}', read the plan and the source with agent-harness_fs_read/agent-harness_fs_list, and run agent-harness_run_tests on 'tests'. If EVERY requirement in the spec is implemented and all tests pass, call agent-harness_mark_spec_complete. Otherwise DO NOT call it — output a bulleted list of the missing requirements and failing tests."

# Success marker: spec_complete when a Verifier runs, otherwise a commit is enough.
SUCCESS_MARKER="$RUN_MARKER"
[ "$VERIFY" -eq 1 ] && SUCCESS_MARKER="$SPEC_MARKER"

# --- Claude Code backend (ADR-013): same loop shape as Open Code, claude -p per role ---
if [ "$BACKEND_KIND" = "claude" ]; then
  IMPL_SESSION=""; CC_RESULT=""; VERIFIER_OUTPUT=""
  cc_primary() {  # $1=resume-session-id-or-empty  $2=prompt
    local sflag=(); [ -n "$1" ] && sflag=(--resume "$1")
    local out
    set +e   # a non-zero claude exit must be captured, not abort the supervisor (set -e)
    out="$("${AGENT_WORDS[@]}" -p "$2" --model "$CC_MODEL" --mcp-config "$MCP_CONFIG" \
          --append-system-prompt "$SYSTEM_PROMPT" \
          --allowedTools "$ROLE_TOOLS_MCP" --disallowedTools "$CC_DISALLOW" \
          "${sflag[@]}" --output-format json </dev/null 2>>"$HARNESS_STATE/claude.err")"
    rc=$?
    set -e
    printf '%s\n' "$out" >> "$HARNESS_STATE/claude.jsonl"
    local sid; sid="$(printf '%s' "$out" | "$PYTHON" -c 'import json,sys;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)"
    [ -n "$sid" ] && IMPL_SESSION="$sid"
    CC_RESULT="$(printf '%s' "$out" | "$PYTHON" -c 'import json,sys;print(json.load(sys.stdin).get("result",""))' 2>/dev/null || true)"
  }
  cc_verifier() {
    local out
    set +e   # a non-zero claude exit must not abort the supervisor (set -e)
    out="$("${AGENT_WORDS[@]}" -p "$VERIFIER_PROMPT" --model "$CC_MODEL" --mcp-config "$MCP_VERIFIER_CONFIG" \
          --append-system-prompt "$VERIFIER_SYS" \
          --allowedTools "$VERIFIER_TOOLS_MCP" --disallowedTools "$CC_DISALLOW" \
          --output-format json </dev/null 2>>"$HARNESS_STATE/claude.err")"
    set -e
    printf '%s\n' "$out" >> "$HARNESS_STATE/claude.jsonl"
    VERIFIER_OUTPUT="$(printf '%s' "$out" | "$PYTHON" -c 'import json,sys;print(json.load(sys.stdin).get("result",""))' 2>/dev/null || true)"
  }

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "# backend: claude  model: $CC_MODEL  phase: $PHASE  role: $ROLE_NAME  verify: $VERIFY${GATE_MSG:+  ($GATE_MSG)}"
    echo "# mcp config: $MCP_CONFIG"
    [ "$VERIFY" -eq 1 ] && echo "# verifier mcp: $MCP_VERIFIER_CONFIG (--enable-tool mark_spec_complete)"
    echo "# success marker: $([ "$VERIFY" -eq 1 ] && echo "$SPEC_MARKER" || echo "$RUN_MARKER") (max attempts: $MAX_RETRIES)"
    echo "# allowed tools: $ROLE_TOOLS_MCP"
    [ "$VERIFY" -eq 1 ] && echo "# verifier tools: $VERIFIER_TOOLS_MCP"
    printf '%q ' "${AGENT_WORDS[@]}" -p "$PROMPT" --model "$CC_MODEL" --mcp-config "$MCP_CONFIG" --append-system-prompt "<role:$ROLE_NAME>" --allowedTools "$ROLE_TOOLS_MCP" --disallowedTools "$CC_DISALLOW" --output-format json; echo
    exit 0
  fi

  echo "run_agent.sh: phase=$PHASE spec=$SPEC_ID backend=claude model=$CC_MODEL agent=$ROLE_NAME verify=$VERIFY target=$TARGET_DIR max_attempts=$MAX_RETRIES" >&2
  cd "$TARGET_DIR"
  FEEDBACK=""
  PREV_KEY="$(progress_key)"
  for (( attempt=1; attempt<=MAX_RETRIES; attempt++ )); do
    rm -f "$RUN_MARKER"
    if [ "$attempt" -eq 1 ]; then
      echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — starting claude session" >&2
      cc_primary "" "$PROMPT"
    else
      if [ -n "$FEEDBACK" ]; then
        RP="$(printf 'The Verifier reported the following incomplete items. Fix them and commit:\n%s' "$FEEDBACK")"
      else
        printf '%s' "$CC_RESULT" > "$LAST_IMPL"   # ADR-014 §2: check for a dropped tool call
        HINT="$(recovery_hint "$LAST_IMPL")"
        RP="${HINT:-$RESUME_PROMPT}"
      fi
      echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — resuming implementer (--resume ${IMPL_SESSION:0:8}…)" >&2
      cc_primary "$IMPL_SESSION" "$RP"
    fi
    echo "run_agent.sh: implementer finished (rc=$rc)" >&2
    [ -f "$RUN_MARKER" ] && echo "run_agent.sh: implementer committed this iteration" >&2
    if [ "$VERIFY" -eq 0 ]; then
      [ -f "$RUN_MARKER" ] && { echo "run_agent.sh: run successful (marker after attempt $attempt)" >&2; exit 0; }
      continue
    fi
    echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — running Verifier" >&2
    cc_verifier
    if [ -f "$SPEC_MARKER" ]; then
      if run_completion_gate; then
        echo "run_agent.sh: SPEC COMPLETE (Verifier + mechanical checks passed, attempt $attempt)" >&2
        exit 0
      fi
      echo "run_agent.sh: Verifier said complete but mechanical checks FAILED — continuing (ADR-014 §4)" >&2
      rm -f "$SPEC_MARKER"
      FEEDBACK="$(printf 'Automated checks still fail (fix and re-commit):\n%s' "$GATE_GAPS")"
      PREV_KEY="$(progress_key)"; continue
    fi
    FEEDBACK="$(printf '%s' "$VERIFIER_OUTPUT" | tail -c 4000)"
    note_progress
    echo "run_agent.sh: Verifier reported the spec is incomplete; feeding gaps back (no-progress=$NOPROG)" >&2
  done
  echo "run_agent.sh: FAILED — spec not complete after $MAX_RETRIES attempt(s)" >&2
  exit 3
fi

# non-Open Code CLI: single generic attempt, no verifier (ADR-005 fallback)
if [ "$IS_OPENCODE" -eq 0 ]; then
  CMD=("${AGENT_WORDS[@]}" --mcp-config "$MCP_CONFIG" --append-system-prompt "$SYSTEM_PROMPT" "$PROMPT")
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "# phase: $PHASE role: $ROLE_NAME (generic CLI, single attempt, no verifier)"
    printf '%q ' "${CMD[@]}"; echo; exit 0
  fi
  echo "run_agent.sh: phase=$PHASE spec=$SPEC_ID agent=$ROLE_NAME target=$TARGET_DIR (generic, 1 attempt)" >&2
  cd "$TARGET_DIR"; "${CMD[@]}" || true
  [ -f "$RUN_MARKER" ] && { echo "run_agent.sh: run successful" >&2; exit 0; }
  echo "run_agent.sh: FAILED — no run_successful marker" >&2; exit 3
fi

# Open Code path
oc() { OPENCODE_CONFIG="$OPENCODE_CONFIG_FILE" "${AGENT_WORDS[@]}" run --dir "$TARGET_DIR" -m "$LLM_BACKEND/$MODEL_NAME" --agent "$ROLE_NAME" "$@"; }
oc_verify() { OPENCODE_CONFIG="$VERIFIER_CONFIG_FILE" "${AGENT_WORDS[@]}" run --dir "$TARGET_DIR" -m "$LLM_BACKEND/$MODEL_NAME" --agent "$VERIFIER_ROLE" "$@"; }
latest_session() { OPENCODE_CONFIG="$OPENCODE_CONFIG_FILE" "${AGENT_WORDS[@]}" session list 2>/dev/null | sed -n '3p' | awk '{print $1}'; }

if [ "$DRY_RUN" -eq 1 ]; then
  echo "# phase: $PHASE role: $ROLE_NAME verify: $VERIFY plan: $PLAN_FILE${GATE_MSG:+ ($GATE_MSG)}"
  echo "# opencode config: $OPENCODE_CONFIG_FILE"
  [ "$VERIFY" -eq 1 ] && echo "# verifier config: $VERIFIER_CONFIG_FILE (--enable-tool mark_spec_complete)"
  echo "# success marker: $SUCCESS_MARKER (max attempts: $MAX_RETRIES)"
  echo "# cwd: $TARGET_DIR"
  if [ "$VERIFY" -eq 1 ]; then printf '# verify: '; printf '%q ' "${AGENT_WORDS[@]}" run --dir "$TARGET_DIR" -m "$LLM_BACKEND/$MODEL_NAME" --agent "$VERIFIER_ROLE" "$VERIFIER_PROMPT"; echo; fi
  printf '%q ' "${AGENT_WORDS[@]}" run --dir "$TARGET_DIR" -m "$LLM_BACKEND/$MODEL_NAME" --agent "$ROLE_NAME" "$PROMPT"; echo
  exit 0
fi

echo "run_agent.sh: phase=$PHASE spec=$SPEC_ID backend=$LLM_BACKEND model=$MODEL_NAME agent=$ROLE_NAME verify=$VERIFY target=$TARGET_DIR max_attempts=$MAX_RETRIES" >&2
cd "$TARGET_DIR"
FEEDBACK=""
IMPL_SESSION=""
PREV_KEY="$(progress_key)"
for (( attempt=1; attempt<=MAX_RETRIES; attempt++ )); do
  rm -f "$RUN_MARKER"
  if [ "$attempt" -eq 1 ]; then
    echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — starting session" >&2
    set +e; oc "$PROMPT" 2>&1 | tee "$LAST_IMPL"; rc=${PIPESTATUS[0]}; set -e
  else
    if [ -n "$FEEDBACK" ]; then
      RP="$(printf 'The Verifier reported the following incomplete items. Fix them and commit:\n%s' "$FEEDBACK")"
    else
      HINT="$(recovery_hint "$LAST_IMPL")"   # ADR-014 §2: was the last message a dropped tool call?
      RP="${HINT:-$RESUME_PROMPT}"
    fi
    SESS_ARGS=(--continue)
    [ -n "$IMPL_SESSION" ] && SESS_ARGS=(--session "$IMPL_SESSION")
    echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — resuming implementer (${SESS_ARGS[0]})" >&2
    set +e; oc "${SESS_ARGS[@]}" "$RP" 2>&1 | tee "$LAST_IMPL"; rc=${PIPESTATUS[0]}; set -e
  fi
  echo "run_agent.sh: implementer exited with code $rc" >&2
  [ -z "$IMPL_SESSION" ] && IMPL_SESSION="$(latest_session)"
  [ -f "$RUN_MARKER" ] && echo "run_agent.sh: implementer committed this iteration" >&2

  if [ "$VERIFY" -eq 0 ]; then
    [ -f "$SUCCESS_MARKER" ] && { echo "run_agent.sh: run successful (marker $SUCCESS_MARKER after attempt $attempt)" >&2; exit 0; }
    continue
  fi

  echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — running Verifier" >&2
  oc_verify "$VERIFIER_PROMPT" >"$VERIFIER_OUT" 2>&1 || true
  if [ -f "$SPEC_MARKER" ]; then
    if run_completion_gate; then
      echo "run_agent.sh: SPEC COMPLETE (Verifier + mechanical checks passed, attempt $attempt)" >&2
      exit 0
    fi
    echo "run_agent.sh: Verifier said complete but mechanical checks FAILED — continuing (ADR-014 §4)" >&2
    rm -f "$SPEC_MARKER"
    FEEDBACK="$(printf 'Automated checks still fail (fix and re-commit):\n%s' "$GATE_GAPS")"
    PREV_KEY="$(progress_key)"; continue
  fi
  FEEDBACK="$(tail -c 4000 "$VERIFIER_OUT" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g')"
  note_progress
  echo "run_agent.sh: Verifier reported the spec is incomplete; feeding gaps back (no-progress=$NOPROG)" >&2
done
echo "run_agent.sh: FAILED — spec not complete after $MAX_RETRIES attempt(s)" >&2
exit 3
