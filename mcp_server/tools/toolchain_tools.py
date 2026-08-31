"""The run_toolchain_task MCP tool (ADR-015 §2): stack-agnostic lifecycle execution for the agent."""

from __future__ import annotations

import json
import os
import time
from typing import Literal

from pydantic import Field

from mcp_server.core.audit import emit_env
from mcp_server.core.context import HarnessContext
from mcp_server.core.registry import ToolArgs, ToolSpec
from mcp_server.core.toolchain import (
    TASKS,
    head_tail_truncate,
    resolve_toolchain,
    sanitize,
)

RUN_TOOLCHAIN_TASK_TOOL = "run_toolchain_task"


class RunToolchainTaskArgs(ToolArgs):
    task: Literal["install", "lint", "test", "build"] = Field(
        description="Lifecycle task to run via the project's toolchain.json (or the Python default).",
    )


def run_toolchain_task(sandbox, task: str) -> str:
    """Run one lifecycle task through the target's toolchain and return a bounded JSON report.

    Reads ``toolchain.json`` if present (else the Python default), executes the mapped command, and
    returns ``{task, stack, command, status, exit_code, output_snippet}`` — the snippet is
    ANSI-stripped and head/tail-truncated to <= 4 KiB (ADR-015).
    """
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    tc = resolve_toolchain(sandbox)
    _t0 = time.monotonic()
    result = tc.run(sandbox, task)
    duration_ms = int((time.monotonic() - _t0) * 1000)
    combined = result.stdout
    if result.stderr.strip():
        combined = f"{combined}\n[stderr]\n{result.stderr}" if combined.strip() else result.stderr
    snippet = head_tail_truncate(sanitize(combined))
    if result.timed_out:
        status = "timeout"
    elif result.skipped:
        status = "skipped"
    else:
        status = "success" if result.exit_code == 0 else "failed"
    # audit event for the Observer dashboard (best-effort; no-op outside a harness session)
    emit_env("execute_toolchain_task", os.environ.get("HARNESS_ACTOR"),
             task=task, stack=result.stack, command=result.command,
             status=status, exit_code=result.exit_code, duration_ms=duration_ms)
    return json.dumps(
        {
            "task": task,
            "stack": result.stack,
            "command": result.command,
            "status": status,
            "exit_code": result.exit_code,
            "output_snippet": snippet,
        },
        indent=2,
    )


def build_tools(ctx: HarnessContext) -> list[ToolSpec]:
    """Capability-gated: registered only when the server is started ``--enable-tool run_toolchain_task``."""
    if RUN_TOOLCHAIN_TASK_TOOL not in ctx.optional_tools:
        return []
    sb = ctx.sandbox
    return [ToolSpec(
        RUN_TOOLCHAIN_TASK_TOOL,
        "Run a stack-agnostic lifecycle task (install | lint | test | build) via the project's "
        "toolchain.json (or the Python default). Returns JSON with status, exit_code and a "
        "truncated, ANSI-stripped output_snippet.",
        RunToolchainTaskArgs,
        lambda a: run_toolchain_task(sb, a.task),  # type: ignore[attr-defined]
    )]
