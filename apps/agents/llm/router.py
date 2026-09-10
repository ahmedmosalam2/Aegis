from __future__ import annotations

from apps.agents.llm.base import BaseLLMClient


ROUTING_TABLE: dict[str, str] = {
    "triage":       "llama3.2",
    "diagnosis":    "llama3.2",
    "remediation":  "llama3.2",
    "verification": "llama3.2",
    "postmortem":   "llama3.2",
}

DEFAULT_MODEL = "llama3.2"


def get_llm_client(
    task: str | None = None,
    model: str | None = None,
) -> BaseLLMClient:
    """Get an LLM client for a specific task.

    Uses Ollama (local, free) with llama3.2.
    To switch to OpenAI: replace OllamaClient with OpenAIClient.

    Args:
        task: Task type used to look up the model in ROUTING_TABLE.
        model: Explicit model override.
    """
    from apps.agents.llm.ollama_client import OllamaClient

    selected_model = model or ROUTING_TABLE.get(task or "", DEFAULT_MODEL)
    return OllamaClient(model=selected_model)
