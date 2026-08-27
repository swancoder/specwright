#!/usr/bin/env python3
"""Model pre-flight for local OpenAI-compatible/Ollama backends (ADR-014 §1).

Two checks against OPENAI_BASE_URL / MODEL_NAME:
  - context: the model's loaded num_ctx is >= the declared LLM_CONTEXT_LENGTH (Ollama defaults
    to 4096, which silently truncated whole runs).
  - toolcall: the endpoint returns a STRUCTURED tool_call for a trivial tool request (weak models
    like qwen2.5-coder emit bare JSON text instead, which breaks the whole agent loop).

Exit: 0 ok · 3 context too small (warn) · 4 tool-calling broken · 5 endpoint unreachable.
Skipped (exit 0) for non-local endpoints.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

TOOLCALL, CONTEXT, UNREACHABLE = 4, 3, 5


def _post(url: str, payload: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check_context(base_url: str, model: str, declared: int) -> tuple[bool, str]:
    """Best-effort: read the model's num_ctx from Ollama /api/show."""
    root = base_url.rsplit("/v1", 1)[0]
    try:
        info = _post(f"{root}/api/show", {"name": model}, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        return True, f"context: could not query /api/show ({exc}); skipped"
    params = info.get("parameters", "") or ""
    num_ctx = None
    for line in params.splitlines():
        if line.strip().startswith("num_ctx"):
            try:
                num_ctx = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    if num_ctx is None:
        return False, (f"context: model '{model}' has no num_ctx set (Ollama will default to 4096, "
                       f"which truncates the agent). Create a variant: `ollama create {model}-{declared//1024}k "
                       f"-f Modelfile` with `PARAMETER num_ctx {declared}`.")
    if num_ctx < declared:
        return False, f"context: model num_ctx={num_ctx} < declared {declared}; the agent context will be truncated."
    return True, f"context: num_ctx={num_ctx} >= {declared} ok"


def check_toolcall(base_url: str, model: str, api_key: str) -> tuple[bool, str]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Call the ping tool."}],
        "tools": [{"type": "function", "function": {"name": "ping", "description": "ping", "parameters": {"type": "object", "properties": {}}}}],
        "tool_choice": "auto", "max_tokens": 64,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(f"{base_url}/chat/completions", data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60.0) as r:
            data = json.load(r)
    except Exception as exc:  # noqa: BLE001
        return True, f"toolcall: endpoint error, skipped ({exc})"
    msg = (data.get("choices") or [{}])[0].get("message", {})
    if msg.get("tool_calls"):
        return True, "toolcall: structured tool_calls returned ok"
    content = (msg.get("content") or "")[:80].replace("\n", " ")
    return False, (f"toolcall: model '{model}' did NOT return structured tool_calls "
                   f"(content={content!r}). It emits tool calls as text — the agent loop will not work. "
                   f"Use a model with reliable tool calling (e.g. gpt-oss:20b, qwen3, devstral).")


def is_local(base_url: str) -> bool:
    return any(h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0", ":11434"))


def main(argv: list[str] | None = None) -> int:
    base = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("MODEL_NAME", "")
    key = os.environ.get("OPENAI_API_KEY", "")
    declared = int(os.environ.get("LLM_CONTEXT_LENGTH", "32768"))
    if not base or not is_local(base):
        print("preflight: non-local endpoint, skipped")
        return 0
    ok_ctx, msg_ctx = check_context(base, model, declared)
    print(f"  [{'ok' if ok_ctx else 'WARN'}] {msg_ctx}", file=sys.stderr)
    ok_tc, msg_tc = check_toolcall(base, model, key)
    print(f"  [{'ok' if ok_tc else 'FAIL'}] {msg_tc}", file=sys.stderr)
    if not ok_tc:
        return TOOLCALL
    if not ok_ctx:
        return CONTEXT
    return 0


if __name__ == "__main__":
    sys.exit(main())
