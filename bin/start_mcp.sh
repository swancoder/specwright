#!/bin/bash
# Starts the MCP server that exposes harness tools to the local LLM over stdio.
# Usage: ./bin/start_mcp.sh --target-dir <path> [--db <sqlite-file>]
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" -m mcp_server.main "$@"
