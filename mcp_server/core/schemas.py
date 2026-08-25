"""Pydantic models describing the MCP protocol messages used by the harness."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolParameter(BaseModel):
    """Describes a single input parameter of an MCP tool."""

    name: str
    type: str
    description: str = ""
    required: bool = True


class ToolDefinition(BaseModel):
    """Metadata advertised to the client for one MCP tool."""

    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)


class ToolCallRequest(BaseModel):
    """Incoming request from the LLM to invoke a tool."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None


class ToolCallResult(BaseModel):
    """Outgoing result returned to the LLM after a tool invocation."""

    request_id: Optional[str] = None
    success: bool
    output: Any = None
    error: Optional[str] = None
