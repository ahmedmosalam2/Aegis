from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from apps.agents.llm import BaseLLMClient, LLMResponse, Message, TokenUsage
from apps.agents.tools.registry import ToolRegistry, ToolResult
from apps.core.logging import get_logger

logger = get_logger("agents.base")


# ═══════════════════════════════════════════════════════════════════
# OUTPUT STRUCTURES
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ToolCallTrace:
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    step: int


@dataclass
class AgentResult:
    success: bool
    answer: str
    traces: list[ToolCallTrace] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    steps_taken: int = 0
    stopped_reason: str = ""


# ═══════════════════════════════════════════════════════════════════
# BASE AGENT
# ═══════════════════════════════════════════════════════════════════


class BaseAgent:
    """ReAct loop agent: Reason → Act → Observe → repeat until done.

    Subclasses must implement:
        - system_prompt() → str
        - parse_result(final_response) → AgentResult  (optional override)
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        tools: ToolRegistry,
        max_steps: int = 10,
        token_budget: int = 8000,
        task: str = "default",
    ):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.task = task

    def system_prompt(self) -> str:
        raise NotImplementedError

    # ── Core Loop ────────────────────────────────────────────────────

    async def run(
        self,
        user_message: str,
        db_session: Any = None,
    ) -> AgentResult:
        messages: list[Message] = [
            Message(role="system", content=self.system_prompt()),
            Message(role="user", content=user_message),
        ]

        traces: list[ToolCallTrace] = []
        total_usage = TokenUsage()
        step = 0

        while step < self.max_steps:
            step += 1

            if total_usage.total_tokens >= self.token_budget:
                logger.warning(
                    f"[{self.task}] Token budget exhausted at step {step}: "
                    f"{total_usage.total_tokens}/{self.token_budget}"
                )
                return AgentResult(
                    success=False,
                    answer="Token budget exhausted before reaching a conclusion.",
                    traces=traces,
                    usage=total_usage,
                    steps_taken=step,
                    stopped_reason="token_budget_exhausted",
                )

            response: LLMResponse = await self.llm.chat(
                messages=messages,
                tools=self.tools.get_openai_schemas(),
            )

            total_usage.prompt_tokens += response.usage.prompt_tokens
            total_usage.completion_tokens += response.usage.completion_tokens
            total_usage.total_tokens += response.usage.total_tokens

            messages.append(response.to_message())

            if not response.tool_calls:
                logger.info(
                    f"[{self.task}] Finished at step {step} — "
                    f"tokens={total_usage.total_tokens}"
                )
                return AgentResult(
                    success=True,
                    answer=response.content or "",
                    traces=traces,
                    usage=total_usage,
                    steps_taken=step,
                    stopped_reason="done",
                )

            # ── Tool execution phase ─────────────────────────────────
            for tool_call in response.tool_calls:
                logger.info(
                    f"[{self.task}] Step {step}: calling {tool_call.name}",
                    extra={"extra_data": {"args": tool_call.arguments}},
                )

                result: ToolResult = await self.tools.execute(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    db_session=db_session,
                )

                traces.append(ToolCallTrace(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    result=result,
                    step=step,
                ))

                tool_content = (
                    json.dumps(result.data)
                    if result.success
                    else f"ERROR: {result.error}"
                )

                messages.append(Message(
                    role="tool",
                    content=tool_content,
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                ))

        logger.warning(f"[{self.task}] Max steps ({self.max_steps}) reached")
        return AgentResult(
            success=False,
            answer="Maximum investigation steps reached without a conclusion.",
            traces=traces,
            usage=total_usage,
            steps_taken=step,
            stopped_reason="max_steps_reached",
        )
