from __future__ import annotations

from dataclasses import dataclass, field
from apps.agents.llm.base import BaseLLMClient, TokenUsage


# ── Model Pricing (USD per 1,000 tokens) ─────────────────────────────
# Ollama local models: effectively free (only electricity cost)
# OpenAI: official API pricing as of 2025
# ─────────────────────────────────────────────────────────────────────

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Local / Free
    "llama3.2": {
        "input_per_1k":  0.0,
        "output_per_1k": 0.0,
        "provider":      "ollama",
    },
    # OpenAI
    "gpt-4o": {
        "input_per_1k":  0.0025,
        "output_per_1k": 0.010,
        "provider":      "openai",
    },
    "gpt-4o-mini": {
        "input_per_1k":  0.000150,
        "output_per_1k": 0.000600,
        "provider":      "openai",
    },
    "gpt-4-turbo": {
        "input_per_1k":  0.010,
        "output_per_1k": 0.030,
        "provider":      "openai",
    },
}

# Baseline model used for savings comparison
# (what we WOULD pay if we used a premium cloud model for everything)
BASELINE_COMPARISON_MODEL = "gpt-4o"

# ── Routing Table ─────────────────────────────────────────────────────
# Maps each task to the most appropriate (cheapest capable) model.
# Criteria: use local (free) Ollama for everything unless a task
# explicitly requires cloud-scale reasoning.
# ─────────────────────────────────────────────────────────────────────

ROUTING_TABLE: dict[str, str] = {
    "triage":       "llama3.2",   # Fast classification — local is enough
    "diagnosis":    "llama3.2",   # ReAct loop with tools — local handles it
    "remediation":  "llama3.2",   # Action planning — local handles it
    "verification": "llama3.2",   # Health check analysis — local is enough
    "postmortem":   "llama3.2",   # Report writing — local is enough
}

DEFAULT_MODEL = "llama3.2"


# ── Cost Tracking ─────────────────────────────────────────────────────

@dataclass
class RequestCost:
    """Cost breakdown for a single LLM request."""
    model:           str
    prompt_tokens:   int
    completion_tokens: int
    total_tokens:    int
    actual_cost_usd: float   # What we actually paid
    baseline_cost_usd: float # What gpt-4o would have cost
    saved_usd:       float   # baseline - actual
    task:            str = ""

    def summary(self) -> str:
        return (
            f"[{self.task or self.model}] "
            f"tokens={self.total_tokens} | "
            f"actual=${self.actual_cost_usd:.6f} | "
            f"vs gpt-4o=${self.baseline_cost_usd:.6f} | "
            f"saved=${self.saved_usd:.6f}"
        )


@dataclass
class RouterStats:
    """Cumulative cost stats across all requests routed."""
    total_requests:     int   = 0
    total_tokens:       int   = 0
    total_actual_usd:   float = 0.0
    total_baseline_usd: float = 0.0
    total_saved_usd:    float = 0.0
    by_task:            dict[str, list[RequestCost]] = field(default_factory=dict)

    @property
    def total_saved_pct(self) -> float:
        """Percentage saved vs always using the baseline model."""
        if self.total_baseline_usd == 0:
            return 100.0  # Everything was free
        return (self.total_saved_usd / self.total_baseline_usd) * 100

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "  Aegis Cost-Aware Router — Savings Report",
            "=" * 60,
            f"  Total requests : {self.total_requests}",
            f"  Total tokens   : {self.total_tokens:,}",
            f"  Actual cost    : ${self.total_actual_usd:.4f}",
            f"  vs gpt-4o cost : ${self.total_baseline_usd:.4f}",
            f"  Total saved    : ${self.total_saved_usd:.4f}  ({self.total_saved_pct:.1f}%)",
            "=" * 60,
        ]
        if self.by_task:
            lines.append("  Breakdown by task:")
            for task, costs in self.by_task.items():
                task_saved = sum(c.saved_usd for c in costs)
                task_tokens = sum(c.total_tokens for c in costs)
                lines.append(
                    f"    {task:<14} {len(costs):>3} calls  "
                    f"{task_tokens:>8,} tokens  saved=${task_saved:.4f}"
                )
        lines.append("=" * 60)
        return "\n".join(lines)


# ── Global Stats Singleton ────────────────────────────────────────────

_router_stats = RouterStats()


def get_router_stats() -> RouterStats:
    """Return the global cumulative cost stats."""
    return _router_stats


def reset_router_stats() -> None:
    """Reset stats (useful for testing)."""
    global _router_stats
    _router_stats = RouterStats()


# ── Core Functions ────────────────────────────────────────────────────

def compute_cost(model: str, usage: TokenUsage, task: str = "") -> RequestCost:
    """Compute the USD cost of a request and savings vs baseline.

    Args:
        model:  The model that was actually used.
        usage:  Token usage from the LLM response.
        task:   The task name for breakdown reporting.

    Returns:
        RequestCost with actual cost, baseline cost, and savings.
    """
    pricing    = MODEL_PRICING.get(model, MODEL_PRICING["llama3.2"])
    baseline_p = MODEL_PRICING[BASELINE_COMPARISON_MODEL]

    actual = (
        usage.prompt_tokens     / 1000 * pricing["input_per_1k"]
        + usage.completion_tokens / 1000 * pricing["output_per_1k"]
    )
    baseline = (
        usage.prompt_tokens     / 1000 * baseline_p["input_per_1k"]
        + usage.completion_tokens / 1000 * baseline_p["output_per_1k"]
    )

    cost = RequestCost(
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        actual_cost_usd=round(actual, 8),
        baseline_cost_usd=round(baseline, 8),
        saved_usd=round(baseline - actual, 8),
        task=task,
    )

    # Update global stats
    _router_stats.total_requests     += 1
    _router_stats.total_tokens       += usage.total_tokens
    _router_stats.total_actual_usd   += cost.actual_cost_usd
    _router_stats.total_baseline_usd += cost.baseline_cost_usd
    _router_stats.total_saved_usd    += cost.saved_usd
    _router_stats.by_task.setdefault(task or model, []).append(cost)

    return cost


def get_llm_client(
    task: str | None = None,
    model: str | None = None,
) -> BaseLLMClient:
    """Get an LLM client for a specific task.

    Routes to the cheapest capable model based on ROUTING_TABLE.
    Falls back to Ollama llama3.2 (free) if task is unknown.

    Args:
        task:  Task type (triage, diagnosis, remediation, etc.)
        model: Explicit model override — skips routing table.

    Returns:
        A configured LLM client ready to use.
    """
    from apps.agents.llm.ollama_client import OllamaClient

    selected_model = model or ROUTING_TABLE.get(task or "", DEFAULT_MODEL)
    return OllamaClient(model=selected_model)
