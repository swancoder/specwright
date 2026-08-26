#!/bin/bash
# Launches the agent CLI (Open Code) against a target project for one specification
# (ADR-005, ADR-006, ADR-008).
#
# Usage: ./bin/run_agent.sh --spec <spec_id> --target-dir <path>
#                           [--backend <name>] [--role <name>] [--dry-run]
#
# 1. exports OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME from config/llm_backends.yaml
# 2. writes <target>/.agent-harness/mcp.json  (bin/start_mcp.sh --target-dir <target>)
#    and derives <target>/.agent-harness/opencode.json (mcp + provider + persona agent)
# 3. supervisor loop (MAX_RETRIES attempts, default 3): runs
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
MAX_RETRIES="${MAX_RETRIES:-3}"
case "$MAX_RETRIES" in ''|*[!0-9]*|0) echo "run_agent.sh: MAX_RETRIES must be a positive integer (got '$MAX_RETRIES')" >&2; exit 2 ;; esac

SPEC_ID=""
TARGET_DIR="${TARGET_DIR:-}"
BACKEND=""
ROLE=""
DRY_RUN=0

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

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
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage 0 ;;
    *) echo "run_agent.sh: unknown argument '$1'" >&2; usage 2 ;;
  esac
done

[ -n "$SPEC_ID" ]    || { echo "run_agent.sh: --spec <spec_id> is required" >&2; usage 2; }
[ -n "$TARGET_DIR" ] || { echo "run_agent.sh: --target-dir <path> is required" >&2; usage 2; }
[ -d "$TARGET_DIR" ] || { echo "run_agent.sh: target dir '$TARGET_DIR' does not exist" >&2; exit 2; }
case "$SPEC_ID" in *[!A-Za-z0-9._-]*|"") echo "run_agent.sh: invalid spec id '$SPEC_ID'" >&2; exit 2 ;; esac
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

SPEC_FILE="specs/$SPEC_ID/01_spec.md"
[ -f "$TARGET_DIR/$SPEC_FILE" ] || echo "run_agent.sh: warning: $TARGET_DIR/$SPEC_FILE not found (agent will use read_specification)" >&2

# --- 1. LLM environment from config/llm_backends.yaml -------------------------
eval "$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" env ${BACKEND:+"$BACKEND"})"
SYSTEM_PROMPT="$("$PYTHON" "$HARNESS_DIR/bin/harness_config.py" role ${ROLE:+"$ROLE"})"
ROLE_NAME="${ROLE:-$("$PYTHON" -c 'import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))["default"])' "$HARNESS_DIR/config/roles.yaml")}"

# --- 2. MCP wiring: <target>/.agent-harness/{mcp.json,opencode.json} ----------
HARNESS_STATE="$TARGET_DIR/.agent-harness"
MCP_CONFIG="$HARNESS_STATE/mcp.json"
OPENCODE_CONFIG_FILE="$HARNESS_STATE/opencode.json"
mkdir -p "$HARNESS_STATE"
HARNESS_DIR="$HARNESS_DIR" TARGET_DIR="$TARGET_DIR" MCP_CONFIG="$MCP_CONFIG" \
OPENCODE_CONFIG_FILE="$OPENCODE_CONFIG_FILE" ROLE_NAME="$ROLE_NAME" SYSTEM_PROMPT="$SYSTEM_PROMPT" \
"$PYTHON" - <<'PY'
import json, os
e = os.environ
server = {
    "command": os.path.join(e["HARNESS_DIR"], "bin", "start_mcp.sh"),
    "args": ["--target-dir", e["TARGET_DIR"]],
    "env": {"PYTHON": e.get("PYTHON", "")},
}
with open(e["MCP_CONFIG"], "w", encoding="utf-8") as fh:
    json.dump({"mcpServers": {"agent-harness": server}}, fh, indent=2); fh.write("\n")

backend, model = e["LLM_BACKEND"], e["MODEL_NAME"]
HARNESS_TOOLS = ("read_constitution", "read_specification", "fs_list", "fs_read", "fs_write",
                 "fs_apply_patch", "run_tests", "git_commit_feature", "query_temporal_coupling")
# Open Code exposes MCP tools as <server>_<tool>; local models call bare names otherwise.
TOOL_NOTE = ("\n\nTool names in this session (use them EXACTLY as written, no other tools exist):\n"
             + "\n".join(f"- agent-harness_{t}" for t in HARNESS_TOOLS)
             + "\nExplore with agent-harness_fs_list, read with agent-harness_fs_read, create with agent-harness_fs_write.\n")
BUILTIN_OFF = ("bash", "edit", "write", "patch", "multiedit", "webfetch", "read", "glob", "grep", "list", "task", "skill")
opencode = {
    "$schema": "https://opencode.ai/config.json",
    "provider": {backend: {
        "npm": "@ai-sdk/openai-compatible",
        "name": f"{backend} (agent-harness)",
        "options": {"baseURL": e["OPENAI_BASE_URL"], "apiKey": e["OPENAI_API_KEY"] or "none"},
        # Without an explicit limit Open Code assumes a 0-token context and compacts every turn.
        "models": {model: {"name": model, "limit": {
            "context": int(e.get("LLM_CONTEXT_LENGTH", "32768")),
            "output": int(e.get("LLM_MAX_OUTPUT_TOKENS", "8192")),
        }}},
    }},
    "model": f"{backend}/{model}",
    "mcp": {"agent-harness": {
        "type": "local", "enabled": True,
        "command": [server["command"], *server["args"]],
        "environment": {k: v for k, v in server["env"].items() if v},
    }},
    "agent": {e["ROLE_NAME"]: {
        "description": "Agent Harness persona (config/roles.yaml)",
        "mode": "primary",
        "model": f"{backend}/{model}",
        "prompt": e["SYSTEM_PROMPT"] + TOOL_NOTE,
        "tools": {t: False for t in BUILTIN_OFF},
    }},
}
with open(e["OPENCODE_CONFIG_FILE"], "w", encoding="utf-8") as fh:
    json.dump(opencode, fh, indent=2); fh.write("\n")
PY

# --- 3. Supervisor loop: launch, check marker, resume (ADR-008) ---------------
RUN_MARKER="$HARNESS_STATE/run_successful"
rm -f "$RUN_MARKER"   # a stale marker from a previous run must never count

PROMPT="Implement spec ${SPEC_ID}. Start by calling read_constitution, then read_specification with spec_id '${SPEC_ID}' (source: ${SPEC_FILE}). Plan, implement, test and commit strictly through the MCP tools."
RESUME_PROMPT="Continue with the plan. Your last message was plain text instead of a tool call. You must use the appropriate MCP tool to proceed, or call git_commit_feature if you are done."
export AGENT_SPEC_ID="$SPEC_ID" AGENT_TARGET_DIR="$TARGET_DIR" AGENT_MCP_CONFIG="$MCP_CONFIG"

# shellcheck disable=SC2206  # AGENT_CMD is intentionally word-split
AGENT_WORDS=($AGENT_CMD)
IS_OPENCODE=0
if [ "$(basename "${AGENT_WORDS[0]}")" = "opencode" ]; then
  IS_OPENCODE=1
  export OPENCODE_CONFIG="$OPENCODE_CONFIG_FILE"
  PROMPT="Implement spec ${SPEC_ID}. Step 1: call agent-harness_read_constitution. Step 2: call agent-harness_read_specification with spec_id '${SPEC_ID}' (source: ${SPEC_FILE}). Then plan, implement, test and commit strictly through the agent-harness_* tools."
  CMD=("${AGENT_WORDS[@]}" run --dir "$TARGET_DIR" -m "$LLM_BACKEND/$MODEL_NAME" --agent "$ROLE_NAME" "$PROMPT")
  RESUME_CMD=("${AGENT_WORDS[@]}" run --dir "$TARGET_DIR" -m "$LLM_BACKEND/$MODEL_NAME" --agent "$ROLE_NAME" --continue "$RESUME_PROMPT")
else
  CMD=("${AGENT_WORDS[@]}" --mcp-config "$MCP_CONFIG" --append-system-prompt "$SYSTEM_PROMPT" "$PROMPT")
  RESUME_CMD=()
  MAX_RETRIES=1   # no --continue semantics for a generic CLI: single attempt
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "# backend=$LLM_BACKEND provider=$LLM_PROVIDER model=$MODEL_NAME base_url=$OPENAI_BASE_URL"
  echo "# mcp config: $MCP_CONFIG"
  echo "# opencode config: $OPENCODE_CONFIG_FILE"
  echo "# run marker: $RUN_MARKER (max attempts: $MAX_RETRIES)"
  echo "# cwd: $TARGET_DIR"
  [ "$IS_OPENCODE" -eq 1 ] && { printf '# resume: '; printf '%q ' "${RESUME_CMD[@]}"; echo; }
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi

echo "run_agent.sh: spec=$SPEC_ID backend=$LLM_BACKEND model=$MODEL_NAME agent=$ROLE_NAME target=$TARGET_DIR max_attempts=$MAX_RETRIES" >&2
cd "$TARGET_DIR"
for (( attempt=1; attempt<=MAX_RETRIES; attempt++ )); do
  if [ "$attempt" -eq 1 ]; then
    echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — starting session" >&2
    "${CMD[@]}" && rc=0 || rc=$?
  else
    echo "run_agent.sh: attempt $attempt/$MAX_RETRIES — no run_successful marker, resuming session with --continue" >&2
    "${RESUME_CMD[@]}" && rc=0 || rc=$?
  fi
  echo "run_agent.sh: agent exited with code $rc" >&2
  if [ -f "$RUN_MARKER" ]; then
    echo "run_agent.sh: run successful (marker $RUN_MARKER after attempt $attempt)" >&2
    exit 0
  fi
done
echo "run_agent.sh: FAILED — no run_successful marker after $MAX_RETRIES attempt(s); git_commit_feature was never reached" >&2
exit 3
