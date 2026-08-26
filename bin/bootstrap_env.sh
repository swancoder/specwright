#!/bin/bash
# Prepares the Agent Harness environment (ADR-006).
#
# Usage: ./bin/bootstrap_env.sh [--python <exe>] [--check-only] [--target-dir <path>]
#
# 1. creates .venv (if missing) and installs requirements.txt
# 2. checks Open Code prerequisites: node >= 18, npm, opencode
# 3. checks the default LLM backend: Ollama reachable, configured model pulled
# 4. --target-dir: pre-provisions <target>/.venv from the target's requirements.txt
#    (same code path run_tests uses; ADR-009)
# Missing optional tooling is reported with the command that fixes it.
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HARNESS_DIR"
PY_EXE="${PYTHON:-python3}"
CHECK_ONLY=0
TARGET_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --python)   PY_EXE="${2:-}"; shift 2 ;;
    --python=*) PY_EXE="${1#*=}"; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --target-dir)   TARGET_DIR="${2:-}"; shift 2 ;;
    --target-dir=*) TARGET_DIR="${1#*=}"; shift ;;
    -h|--help)  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "bootstrap_env.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

MISSING=0
ok()   { printf '  [ok]      %s\n' "$*"; }
miss() { printf '  [MISSING] %s\n' "$*"; MISSING=1; }
warn() { printf '  [warn]    %s\n' "$*"; }
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }

# --- 1. Python environment -----------------------------------------------------
echo "== Python environment"
command -v "$PY_EXE" >/dev/null || { echo "bootstrap_env.sh: python interpreter '$PY_EXE' not found" >&2; exit 1; }
PYV="$("$PY_EXE" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ver_ge "$PYV" "3.12" || { echo "bootstrap_env.sh: Python >= 3.12 required, found $PYV ($PY_EXE)" >&2; exit 1; }
ok "python $PYV ($(command -v "$PY_EXE"))"

VENV="$HARNESS_DIR/.venv"
VPY="$VENV/bin/python"
if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ -x "$VPY" ]; then ok ".venv present"; else miss ".venv — run ./bin/bootstrap_env.sh"; fi
else
  if [ ! -x "$VPY" ]; then
    echo "  creating $VENV"
    "$PY_EXE" -m venv "$VENV"
  fi
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -r requirements.txt
  ok ".venv ready ($("$VPY" -m pip list --format=freeze 2>/dev/null | grep -ci -E '^(mcp|pydantic|pyyaml|pytest)=' ) core packages)"
fi
[ -x "$VPY" ] && "$VPY" -c 'import mcp, pydantic, yaml' 2>/dev/null && ok "imports: mcp, pydantic, yaml" || miss "python deps — run ./bin/bootstrap_env.sh"

# --- 2. Open Code prerequisites --------------------------------------------------
echo "== Open Code (agent CLI)"
if command -v node >/dev/null; then
  NV="$(node --version | sed 's/^v//')"
  if ver_ge "$NV" "18.0.0"; then ok "node $NV"; else miss "node >= 18 (found $NV) — https://nodejs.org or nvm install 22"; fi
else
  miss "node — install Node.js >= 18 (https://nodejs.org or nvm install 22)"
fi
command -v npm >/dev/null && ok "npm $(npm --version)" || miss "npm — ships with Node.js"
if command -v opencode >/dev/null; then
  ok "opencode $(opencode --version 2>/dev/null | tail -1) ($(command -v opencode))"
else
  miss "opencode — npm install -g opencode-ai   (or: curl -fsSL https://opencode.ai/install | bash)"
fi

# --- 3. LLM backend ---------------------------------------------------------------
echo "== LLM backend (config/llm_backends.yaml)"
CFG_PY="$VPY"; [ -x "$CFG_PY" ] || CFG_PY="$PY_EXE"
if ENV_OUT="$("$CFG_PY" bin/harness_config.py env 2>/dev/null)"; then
  eval "$ENV_OUT"
  ok "backend '$LLM_BACKEND' → $OPENAI_BASE_URL model $MODEL_NAME"
  if command -v curl >/dev/null; then
    if TAGS="$(curl -sf --max-time 5 "${OPENAI_BASE_URL%/v1}/api/tags" 2>/dev/null)"; then
      ok "ollama reachable at ${OPENAI_BASE_URL%/v1}"
      if printf '%s' "$TAGS" | grep -q "\"name\":\"$MODEL_NAME\""; then
        ok "model $MODEL_NAME pulled"
      else
        miss "model $MODEL_NAME — ollama pull $MODEL_NAME"
      fi
    elif curl -sf --max-time 5 "$OPENAI_BASE_URL/models" >/dev/null 2>&1; then
      ok "OpenAI-compatible endpoint reachable at $OPENAI_BASE_URL (non-Ollama; model not verified)"
    else
      miss "no server at $OPENAI_BASE_URL — start Ollama (ollama serve) or edit config/llm_backends.yaml"
    fi
  else
    warn "curl not found; skipped endpoint check"
  fi
else
  miss "config/llm_backends.yaml unreadable (run with .venv or install pyyaml)"
fi

# --- 4. Target environment (optional) -----------------------------------------
if [ -n "$TARGET_DIR" ]; then
  echo "== Target environment ($TARGET_DIR)"
  if [ ! -d "$TARGET_DIR" ]; then
    miss "target dir '$TARGET_DIR' does not exist"
  elif [ ! -x "$VPY" ]; then
    miss "harness .venv required to provision a target — run ./bin/bootstrap_env.sh first"
  elif [ "$CHECK_ONLY" -eq 1 ]; then
    if [ -x "$TARGET_DIR/.venv/bin/python" ]; then ok "target .venv present"; else miss "target .venv — run ./bin/bootstrap_env.sh --target-dir $TARGET_DIR"; fi
  else
    if OUT="$(PYTHONPATH="$HARNESS_DIR" "$VPY" -c 'import sys; from mcp_server.tools.test_tools import main; sys.exit(main(sys.argv[1:]))' --target-dir "$TARGET_DIR" 2>&1)"; then
      printf '%s\n' "$OUT" | sed 's/^/  /'; ok "target .venv provisioned"
    else
      printf '%s\n' "$OUT" | sed 's/^/  /'; miss "target provisioning failed (see above)"
    fi
  fi
fi

echo
if [ "$MISSING" -eq 0 ]; then
  echo "bootstrap_env.sh: environment ready — ./bin/run_agent.sh --spec <id> --target-dir <path>"
  exit 0
fi
echo "bootstrap_env.sh: some prerequisites are missing (see [MISSING] above)."
[ "$CHECK_ONLY" -eq 1 ] && exit 1
exit 0
