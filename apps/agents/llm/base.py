"""
LLM Client Abstraction Layer.

Defines the interface that ALL LLM providers must implement.
The agents interact ONLY with this interface — they never know
which provider (OpenAI, Anthropic, local) is behind the scenes.

This separation means we can swap providers without touching
a single line in any agent.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ToolCall:
    """A tool call requested by the LLM.

    When the LLM decides it needs to use a tool, it returns one
    or more ToolCall objects. Each has a unique ID (for matching
    results back), a tool name, and arguments.
    """
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class TokenUsage:
    """Token consumption for a single LLM call.

    Tracked for:
    - Cost monitoring (token budget enforcement)
    - Observability (Prometheus metrics)
    - Model routing optimization
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class Message:
    """A single message in the agent's conversation.

    Supports all four roles:
    - system:    Agent identity and instructions
    - user:      Investigation context or follow-up
    - assistant: LLM response (may include tool_calls)
    - tool:      Result of executing a tool (matched by tool_call_id)
    """
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None   # For tool result messages
    name: str | None = None           # Tool name for tool results


@dataclass
class LLMResponse:
    """Response from an LLM provider.

    Contains either:
    - A text response (content) when the LLM is done thinking
    - Tool calls (tool_calls) when the LLM wants to use tools
    - Both (some providers do this)
    """
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    finish_reason: str = ""  # "stop" | "tool_calls" | "length"

    def to_message(self) -> Message:
        """Convert this response to an assistant Message.

        Used to add the LLM's response back into the conversation
        history before adding tool results.
        """
        return Message(
            role="assistant",
            content=self.content,
            tool_calls=self.tool_calls if self.tool_calls else None,
        )


# ═══════════════════════════════════════════════════════════════════
# ABSTRACT BASE CLIENT
# ═══════════════════════════════════════════════════════════════════


class BaseLLMClient(ABC):
    """Abstract base for all LLM providers.

    Every provider (OpenAI, Anthropic, Azure, local) must implement
    the `chat()` method. The agents only ever call this interface.

    Design contract:
    - chat() is async (LLM calls are I/O-bound)
    - tools use the OpenAI function calling JSON schema format
    - temperature defaults to 0.0 (deterministic for SRE decisions)
    - Token usage is always tracked and returned
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send messages to the LLM and get a response.

        Args:
            messages: Conversation history (system + user + assistant + tool)
            tools: Tool schemas in OpenAI function calling format.
                   Pass None to force a text-only response.
            temperature: 0.0 = deterministic (recommended for SRE).
                         Higher values = more creative (not what we want).
            max_tokens: Maximum response length.

        Returns:
            LLMResponse with content and/or tool_calls.
        """
        ...
