"""Transport-independent tool registry (ADR-004 §3–§4).

A ``ToolSpec`` pairs a strict Pydantic argument model with a handler. The registry
produces MCP ``Tool`` descriptors from the models' JSON schemas and validates
incoming arguments against them before dispatch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp import types
from pydantic import BaseModel, ConfigDict, ValidationError


class ToolArgs(BaseModel):
    """Base class for tool argument models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)


ToolHandler = Callable[[ToolArgs], str]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declarative description of one MCP tool."""

    name: str
    description: str
    args_model: type[ToolArgs]
    handler: ToolHandler

    def to_mcp_tool(self) -> types.Tool:
        """Render this spec as an MCP ``Tool`` with the model's JSON schema."""
        return types.Tool(
            name=self.name,
            description=self.description,
            input_schema=self.args_model.model_json_schema(),
        )


class ToolError(RuntimeError):
    """Base class for failures a tool reports to the LLM (rendered as ``is_error``)."""


class ToolNotFoundError(KeyError):
    """Raised when a tool name is not registered."""


class ToolRegistry:
    """Name-keyed collection of ``ToolSpec`` objects."""

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        """Add a tool; re-registering a name replaces the previous spec."""
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        """Registered tool names in registration order."""
        return list(self._specs)

    def get(self, name: str) -> ToolSpec:
        """Return the spec for ``name`` or raise ``ToolNotFoundError``."""
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def list_tools(self) -> list[types.Tool]:
        """MCP tool descriptors for every registered tool."""
        return [spec.to_mcp_tool() for spec in self._specs.values()]

    def call(self, name: str, arguments: dict[str, Any] | None) -> str:
        """Validate ``arguments`` against the tool's model and invoke its handler.

        Raises:
            ToolNotFoundError: unknown tool name.
            pydantic.ValidationError: arguments do not satisfy the model.
            ToolError: the tool itself refused or failed.
        """
        spec = self.get(name)
        args = spec.args_model.model_validate(arguments or {})
        return spec.handler(args)

    def call_tool_result(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        """Like ``call`` but always returns an MCP result, flagging errors instead of raising."""
        try:
            text = self.call(name, arguments)
            is_error = False
        except ToolNotFoundError:
            text, is_error = f"Unknown tool: {name}", True
        except ValidationError as exc:
            text, is_error = f"Invalid arguments for {name}: {exc}", True
        except ToolError as exc:
            text, is_error = f"{name} failed: {exc}", True
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)
