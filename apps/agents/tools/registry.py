"""
Tool Registry — registers, validates, and executes agent tools.

Every tool the agents use goes through this registry. This gives us:
- Centralized logging of ALL tool calls (audit trail)
- Input validation before execution
- Consistent error handling (agent gets error message, not exception)
- OpenAI function calling schema generation
- Timing for observability
- The ability to swap implementations (simulated → real Prometheus)

Design:
    Agent → ToolRegistry.execute("query_metrics", {...})
                    ↓
            Validation + Logging
                    ↓
            tool.handler(**kwargs)
                    ↓
            ToolResult(success=True, data={...})
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from apps.core.logging import get_logger

logger = get_logger("agents.tools")


# ═══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ToolResult:
    """Result of executing a tool.

    Always returned — never raises exceptions to the agent.
    If a tool fails, success=False and error contains the message.
    """
    success: bool
    data: Any = None
    error: str | None = None
    execution_time_ms: float = 0.0


@dataclass
class ToolDefinition:
    """Definition of a single tool available to agents.

    Each tool has:
    - name: Used by the LLM to identify and call the tool
    - description: Tells the LLM WHEN and WHY to use this tool
    - parameters: JSON Schema defining the tool's input format
    - handler: The async function that actually executes the tool
    - requires_db: Whether the handler needs a database session
    """
    name: str
    description: str
    parameters: dict             # JSON Schema for the input
    handler: Callable[..., Awaitable[Any]]
    requires_db: bool = True


# ═══════════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════════


class ToolRegistry:
    """Manages tool registration and execution for agents.

    Usage:
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="query_metrics", ...))

        # Agent calls a tool
        result = await registry.execute("query_metrics", {"service_name": "payment-service"}, db_session)

        # Get schemas for OpenAI function calling
        schemas = registry.get_openai_schemas()
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool definition by name."""
        return self._tools.get(name)

    @property
    def tool_names(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        db_session: Any = None,
    ) -> ToolResult:
        """Execute a tool by name with the given arguments.

        All executions are:
        - Timed (for observability)
        - Logged (for the audit trail)
        - Error-handled (agent gets error message, not exception)

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments from the LLM's tool call
            db_session: Database session (injected if tool requires_db)

        Returns:
            ToolResult — always returns, never raises
        """
        tool = self._tools.get(tool_name)
        if not tool:
            logger.warning(f"Unknown tool called: {tool_name}")
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}. Available: {self.tool_names}",
            )

        start = time.perf_counter()

        try:
            # Build kwargs — inject db_session only if the tool needs it
            kwargs = dict(arguments)
            if tool.requires_db and db_session is not None:
                kwargs["session"] = db_session

            result = await tool.handler(**kwargs)

            elapsed = (time.perf_counter() - start) * 1000

            logger.info(
                f"Tool executed: {tool_name}",
                extra={"extra_data": {
                    "tool": tool_name,
                    "args": arguments,
                    "duration_ms": round(elapsed, 2),
                    "success": True,
                }},
            )

            return ToolResult(
                success=True,
                data=result,
                execution_time_ms=elapsed,
            )

        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000

            logger.error(
                f"Tool failed: {tool_name}: {e}",
                extra={"extra_data": {
                    "tool": tool_name,
                    "args": arguments,
                    "duration_ms": round(elapsed, 2),
                    "error": str(e),
                }},
            )

            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' failed: {str(e)}",
                execution_time_ms=elapsed,
            )

    def get_openai_schemas(self) -> list[dict]:
        """Convert all tools to OpenAI function calling format.

        This is what gets sent to the LLM so it knows what tools
        are available and how to call them.

        Returns a list of tool schemas like:
            [
                {
                    "type": "function",
                    "function": {
                        "name": "query_metrics",
                        "description": "...",
                        "parameters": { JSON Schema }
                    }
                },
                ...
            ]
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]
