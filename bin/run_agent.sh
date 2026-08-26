#!/bin/bash
# Launches the agent CLI against a target project for one specification (ADR-005).
#
# Usage: ./bin/run_agent.sh --spec <spec_id> --target-dir <path>
#                           [--backend <name>] [--role <name>] [--dry-run]
#
# 1. exports LLM env vars from config/llm_backends.yaml (OPENAI_BASE_URL, MODEL_NAME, ...)
# 2. writes <target>/.agent-harness/mcp.json pointing at bin/start_mcp.sh --target-dir <target>
# 3. runs the agent CLI (AGENT_CMD, default: npx @anthropic-ai/claude-code) inside <target>
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$HARNESS_DIR/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
AGENT_CMD="${AGENT_CMD:-npx @anthropic-ai/claude-code}"

SPEC_ID=""
TARGET_DIR="${TARGET_DIR:-}"
BACKEND=""
ROLE=""
DRY_RUN=0

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

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

# --- 2. MCP wiring: <target>/.agent-harness/mcp.json --------------------------
HARNESS_STATE="$TARGET_DIR/.agent-harness"
MCP_CONFIG="$HARNESS_STATE/mcp.json"
mkdir -p "$HARNESS_STATE"
HARNESS_DIR="$HARNESS_DIR" TARGET_DIR="$TARGET_DIR" MCP_CONFIG="$MCP_CONFIG" "$PYTHON" - <<'PY'
import json, os
cfg = {"mcpServers": {"agent-harness": {
    "command": os.path.join(os.environ["HARNESS_DIR"], "bin", "start_mcp.sh"),
    "args": ["--target-dir", os.environ["TARGET_DIR"]],
    "env": {"PYTHON": os.environ.get("PYTHON", "")},
}}}
with open(os.environ["MCP_CONFIG"], "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2); fh.write("\n")
PY

# --- 3. Launch the agent CLI scoped to the target ------------------------------
PROMPT="Implement spec ${SPEC_ID}. Start by calling read_constitution, then read_specification with spec_id '${SPEC_ID}' (source: ${SPEC_FILE}). Plan, implement, test and commit strictly through the MCP tools."
export AGENT_SPEC_ID="$SPEC_ID" AGENT_TARGET_DIR="$TARGET_DIR" AGENT_MCP_CONFIG="$MCP_CONFIG"

# shellcheck disable=SC2206  # AGENT_CMD is intentionally word-split
CMD=($AGENT_CMD --mcp-config "$MCP_CONFIG" --append-system-prompt "$SYSTEM_PROMPT" "$PROMPT")

if [ "$DRY_RUN" -eq 1 ]; then
  echo "# backend=$LLM_BACKEND provider=$LLM_PROVIDER model=$MODEL_NAME base_url=$OPENAI_BASE_URL"
  echo "# mcp config: $MCP_CONFIG"
  echo "# cwd: $TARGET_DIR"
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi

echo "run_agent.sh: spec=$SPEC_ID backend=$LLM_BACKEND model=$MODEL_NAME target=$TARGET_DIR" >&2
cd "$TARGET_DIR"
exec "${CMD[@]}"
