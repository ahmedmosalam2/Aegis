"""
Cost-Aware Router — Test & Demo
================================
بيثبت إن الـ router بيوفر فلوس حقيقية مقارنة باستخدام gpt-4o على طول.
"""

import sys
sys.path.insert(0, "d:/Aegis")

from apps.agents.llm.router import (
    compute_cost,
    get_llm_client,
    get_router_stats,
    reset_router_stats,
    ROUTING_TABLE,
    MODEL_PRICING,
    BASELINE_COMPARISON_MODEL,
)
from apps.agents.llm.base import TokenUsage
import pytest


# ── Unit Tests ────────────────────────────────────────────────────────

class TestModelPricing:
    def test_llama_is_free(self):
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = compute_cost("llama3.2", usage, task="triage")
        assert cost.actual_cost_usd == 0.0, "llama3.2 (Ollama) should be free"

    def test_baseline_has_cost(self):
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = compute_cost("llama3.2", usage, task="test")
        assert cost.baseline_cost_usd > 0.0, "gpt-4o baseline should have a cost"

    def test_savings_equals_baseline_when_free(self):
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = compute_cost("llama3.2", usage, task="test")
        assert cost.saved_usd == cost.baseline_cost_usd

    def test_gpt4o_cost_calculation(self):
        # 1000 input tokens @ $0.0025/1k + 500 output @ $0.010/1k
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
        cost = compute_cost("gpt-4o", usage, task="test")
        expected = (1000 / 1000 * 0.0025) + (500 / 1000 * 0.010)
        assert abs(cost.actual_cost_usd - expected) < 1e-9


class TestRoutingTable:
    def test_all_tasks_route_to_free_model(self):
        free_models = {m for m, p in MODEL_PRICING.items() if p["input_per_1k"] == 0.0}
        for task, model in ROUTING_TABLE.items():
            assert model in free_models, (
                f"Task '{task}' routes to paid model '{model}'! Expected a free model."
            )

    def test_get_llm_client_returns_client(self):
        for task in ROUTING_TABLE:
            client = get_llm_client(task=task)
            assert client is not None


class TestCumulativeStats:
    def setup_method(self):
        reset_router_stats()

    def test_stats_accumulate_correctly(self):
        usage = TokenUsage(prompt_tokens=500, completion_tokens=200, total_tokens=700)
        compute_cost("llama3.2", usage, task="triage")
        compute_cost("llama3.2", usage, task="diagnosis")

        stats = get_router_stats()
        assert stats.total_requests == 2
        assert stats.total_tokens == 1400
        assert stats.total_actual_usd == 0.0
        assert stats.total_saved_usd > 0.0

    def test_savings_percentage_100_when_all_free(self):
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
        compute_cost("llama3.2", usage, task="postmortem")
        stats = get_router_stats()
        assert stats.total_saved_pct == 100.0

    def test_by_task_breakdown(self):
        usage = TokenUsage(prompt_tokens=300, completion_tokens=100, total_tokens=400)
        compute_cost("llama3.2", usage, task="triage")
        compute_cost("llama3.2", usage, task="triage")
        compute_cost("llama3.2", usage, task="diagnosis")

        stats = get_router_stats()
        assert len(stats.by_task["triage"])    == 2
        assert len(stats.by_task["diagnosis"]) == 1


# ── Visual Demo ───────────────────────────────────────────────────────

def run_demo():
    reset_router_stats()

    print("=" * 60)
    print("  Aegis Cost-Aware Router — Live Demo")
    print("=" * 60)
    print(f"\n  Simulating a full incident resolution cycle...\n")

    # Simulate a realistic incident with typical token counts per agent
    simulated_calls = [
        ("triage",       TokenUsage(400,  150, 550)),
        ("diagnosis",    TokenUsage(1200, 600, 1800)),
        ("diagnosis",    TokenUsage(900,  400, 1300)),   # second ReAct step
        ("remediation",  TokenUsage(800,  300, 1100)),
        ("verification", TokenUsage(500,  200, 700)),
        ("postmortem",   TokenUsage(1500, 800, 2300)),
    ]

    for task, usage in simulated_calls:
        model = ROUTING_TABLE.get(task, "llama3.2")
        cost = compute_cost(model, usage, task=task)
        print(f"  {cost.summary()}")

    print()
    print(get_router_stats().summary())


if __name__ == "__main__":
    run_demo()
