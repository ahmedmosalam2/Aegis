"""
OpenAI LLM Client Implementation.

Translates the abstract BaseLLMClient interface into actual
OpenAI API calls. Handles:
- Message format conversion (our format → OpenAI format)
- Tool/function calling
- Token usage tracking
- Error handling with graceful fallbacks

This file is the ONLY place that imports `openai`.
No agent code ever touches the OpenAI SDK directly.
"""
from __future__ import annotations

import json
import os

from openai import AsyncOpenAI

from apps.agents.llm.base import (
    BaseLLMClient,
    LLMResponse,
    Message,
    ToolCall,
    TokenUsage,
)


class OpenAIClient(BaseLLMClient):
    """OpenAI-backed LLM client.

    Supports:
    - GPT-4o (complex reasoning, tool use)
    - GPT-4o-mini (fast classification, simple tasks)
    - Any OpenAI-compatible model
    """

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self.model = model
        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send messages to OpenAI and parse the response.

        Converts our Message format to OpenAI's expected format,
        makes the API call, and converts back to our LLMResponse.
        """

        # Convert our Message format → OpenAI format
        oai_messages = [self._to_openai_message(m) for m in messages]

        # Build request kwargs
        kwargs: dict = {
            "model": self.model,
            "messages": oai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Call OpenAI API
        response = await self._client.chat.completions.create(**kwargs)

        # Parse response
        choice = response.choices[0]
        message = choice.message

        # Extract tool calls if any
        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                ))

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            ),
            model=response.model,
            finish_reason=choice.finish_reason or "",
        )

    def _to_openai_message(self, msg: Message) -> dict:
        """Convert our Message → OpenAI message dict.

        Handles all four message roles and their specific fields:
        - system/user: just role + content
        - assistant: role + content + optional tool_calls
        - tool: role + content + tool_call_id
        """
        result: dict = {"role": msg.role}

        if msg.content is not None:
            result["content"] = msg.content

        # Assistant messages with tool calls
        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in msg.tool_calls
            ]
            # OpenAI requires content field even if null
            if "content" not in result:
                result["content"] = None

        # Tool result messages
        if msg.tool_call_id:
            result["tool_call_id"] = msg.tool_call_id

        if msg.name:
            result["name"] = msg.name

        return result
