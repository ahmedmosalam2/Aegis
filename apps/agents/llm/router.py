"""
Model Router — picks the right model for each agent task.

Not every task needs GPT-4o. The Triage agent doing simple
severity classification can use a cheaper, faster model.
The Diagnosis agent doing multi-step reasoning needs the best.

This saves cost and reduces latency where possible.

Routing table:
    triage       → gpt-4o-mini  (simple classification)
    diagnosis    → gpt-4o       (complex reasoning + tool use)
    remediation  → gpt-4o       (critical decisions)
    verification → gpt-4o-mini  (simple comparison)
    postmortem   → gpt-4o       (detailed report writing)
"""
from __future__ import annotations

from apps.agents.llm.base import BaseLLMClient
from apps.agents.llm.openai_client import OpenAIClient


# ─── Routing Table ─────────────────────────────────────────────────
# Task type → Model name
# Adjust based on your needs, budget, and latency requirements

ROUTING_TABLE: dict[str, str] = {
    "triage":       "gpt-4o-mini",
    "diagnosis":    "gpt-4o",
    "remediation":  "gpt-4o",
    "verification": "gpt-4o-mini",
    "postmortem":   "gpt-4o",
}

DEFAULT_MODEL = "gpt-4o"


def get_llm_client(
    task: str | None = None,
    model: str | None = None,
) -> BaseLLMClient:
    """Get an LLM client for a specific task.

    The router picks the appropriate model based on task type.
    You can override with a specific model name.

    Args:
        task: Task type (triage, diagnosis, remediation, etc.).
              Used to look up the model in ROUTING_TABLE.
        model: Explicit model name override. If provided,
               ignores the routing table.

    Returns:
        Configured LLM client ready for use.

    Example:
        # Automatic routing
        llm = get_llm_client(task="diagnosis")  # → GPT-4o

        # Manual override
        llm = get_llm_client(model="gpt-4o-mini")
    """
    from apps.api.config import settings

    selected_model = model or ROUTING_TABLE.get(task or "", DEFAULT_MODEL)

    return OpenAIClient(
        model=selected_model,
        api_key=settings.OPENAI_API_KEY,
    )
