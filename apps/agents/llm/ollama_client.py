from __future__ import annotations

import json

import ollama

from apps.agents.llm.base import (
    BaseLLMClient,
    LLMResponse,
    Message,
    ToolCall,
    TokenUsage,
)


class OllamaClient(BaseLLMClient):
    """Ollama-backed LLM client — runs models locally, zero cost.

    Supports:
    - llama3.2 (tool calling natively supported)
    - Any model installed via `ollama pull <model>`

    Tool calling uses Ollama's native function calling format,
    which mirrors the OpenAI format for compatible models.
    """

    def __init__(self, model: str = "llama3.2", host: str = "http://localhost:11434"):
        self.model = model
        self._client = ollama.AsyncClient(host=host)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        ollama_messages = [self._to_ollama_message(m) for m in messages]

        kwargs: dict = {
            "model": self.model,
            "messages": ollama_messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat(**kwargs)

        msg = response.message
        tool_calls: list[ToolCall] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = tc.function.arguments
                    if isinstance(args, str):
                        args = json.loads(args)
                except (json.JSONDecodeError, AttributeError):
                    args = {}

                tool_calls.append(ToolCall(
                    id=f"call_{tc.function.name}_{len(tool_calls)}",
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = response.usage if hasattr(response, "usage") and response.usage else None
        token_usage = TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
        )

        finish_reason = "tool_calls" if tool_calls else "stop"

        return LLMResponse(
            content=msg.content or None,
            tool_calls=tool_calls,
            usage=token_usage,
            model=self.model,
            finish_reason=finish_reason,
        )

    def _to_ollama_message(self, msg: Message) -> dict:
        result: dict = {"role": msg.role}

        if msg.content is not None:
            result["content"] = msg.content

        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                }
                for tc in msg.tool_calls
            ]

        if msg.tool_call_id:
            result["tool_call_id"] = msg.tool_call_id

        if msg.name:
            result["name"] = msg.name

        return result
