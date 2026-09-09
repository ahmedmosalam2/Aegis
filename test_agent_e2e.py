"""
End-to-end test: Diagnosis Agent on Ollama (llama3.2)

يشغّل الـ Diagnosis Agent على incident حقيقي ويطبع النتيجة.
"""
import asyncio
import sys
sys.path.insert(0, 'd:/Aegis')

from apps.agents.llm.ollama_client import OllamaClient
from apps.agents.tools.registry import ToolRegistry, ToolDefinition
from apps.agents.diagnosis_agent import DiagnosisAgent


# ── Mock tools — لا تحتاج DB ──────────────────────────────────────

async def mock_query_metrics(service_name: str, **kwargs) -> dict:
    """Simulates a service_crash failure on payment-service."""
    if service_name == "payment-service":
        return {
            "service_name": service_name,
            "status": "down",
            "metrics": {
                "error_rate": 100.0,
                "latency_ms": 0,
                "cpu_percent": 0,
                "memory_percent": 95.0,
            },
            "active_failure_count": 1,
        }
    return {"service_name": service_name, "status": "healthy", "metrics": {}, "active_failure_count": 0}


async def mock_get_active_failures(service_name: str | None = None, **kwargs) -> list:
    return [
        {
            "failure_id": "test-001",
            "service_name": "payment-service",
            "failure_type": "service_crash",
            "severity": "critical",
            "description": "Process killed by OOM",
            "injected_at": "2026-09-09T10:00:00Z",
        }
    ]


async def mock_search_logs(service_name: str, level: str = "error", **kwargs) -> list:
    return [
        {
            "timestamp": "2026-09-09T10:00:01Z",
            "service": service_name,
            "level": "error",
            "message": "Process exited with code 137 (OOM Killed)",
            "failure_type": "service_crash",
        },
        {
            "timestamp": "2026-09-09T10:00:05Z",
            "service": service_name,
            "level": "error",
            "message": "Service health check failed — no response",
            "failure_type": "service_crash",
        },
    ]


async def mock_get_service_dependencies(service_name: str, **kwargs) -> dict:
    return {
        "service_name": service_name,
        "depends_on": ["auth-service", "postgres"],
        "depended_by": ["api-gateway", "order-service"],
        "cascade_risk": True,
        "full_blast_radius": ["api-gateway", "order-service"],
        "blast_radius_size": 2,
    }


async def mock_check_system_health(**kwargs) -> dict:
    return {
        "overall_status": "down",
        "total_services": 5,
        "services": [
            {"service_name": "payment-service", "status": "down", "active_failures": 1, "degraded_dependencies": []},
            {"service_name": "api-gateway", "status": "degraded", "active_failures": 0, "degraded_dependencies": ["payment-service"]},
            {"service_name": "order-service", "status": "degraded", "active_failures": 0, "degraded_dependencies": ["payment-service"]},
            {"service_name": "auth-service", "status": "healthy", "active_failures": 0, "degraded_dependencies": []},
            {"service_name": "postgres", "status": "healthy", "active_failures": 0, "degraded_dependencies": []},
        ],
    }


QUERY_METRICS_SCHEMA = {
    "type": "object",
    "properties": {"service_name": {"type": "string"}},
    "required": ["service_name"],
}

EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}

SERVICE_SCHEMA = {
    "type": "object",
    "properties": {"service_name": {"type": "string"}},
    "required": ["service_name"],
}

FAILURES_SCHEMA = {
    "type": "object",
    "properties": {"service_name": {"type": "string", "description": "Optional filter"}},
    "required": [],
}

LOGS_SCHEMA = {
    "type": "object",
    "properties": {
        "service_name": {"type": "string"},
        "level": {"type": "string", "enum": ["error", "warning", "info", "all"]},
        "minutes": {"type": "integer"},
    },
    "required": ["service_name"],
}


def build_mock_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="query_metrics", description="Query health metrics for a service.", parameters=QUERY_METRICS_SCHEMA, handler=mock_query_metrics, requires_db=False))
    registry.register(ToolDefinition(name="get_active_failures", description="Get active failure injections.", parameters=FAILURES_SCHEMA, handler=mock_get_active_failures, requires_db=False))
    registry.register(ToolDefinition(name="search_logs", description="Search logs for errors.", parameters=LOGS_SCHEMA, handler=mock_search_logs, requires_db=False))
    registry.register(ToolDefinition(name="get_service_dependencies", description="Get dependency graph.", parameters=SERVICE_SCHEMA, handler=mock_get_service_dependencies, requires_db=False))
    registry.register(ToolDefinition(name="check_system_health", description="Get system-wide health.", parameters=EMPTY_SCHEMA, handler=mock_check_system_health, requires_db=False))
    return registry


async def main():
    print("=" * 60)
    print("  Aegis — Diagnosis Agent E2E Test (Ollama llama3.2)")
    print("=" * 60)
    print()

    agent = DiagnosisAgent(
        llm=OllamaClient(model="llama3.2"),
        tools=build_mock_registry(),
        max_steps=10,
        token_budget=16000,
        task="diagnosis",
    )

    print("Incident: payment-service is DOWN")
    print("Running diagnosis agent...\n")

    output = await agent.diagnose(
        service_name="payment-service",
        incident_id="test-incident-001",
        db_session=None,
    )

    print()
    print("=" * 60)
    print("  DIAGNOSIS RESULT")
    print("=" * 60)
    print(f"  Root Cause    : {output.root_cause}")
    print(f"  Failure Type  : {output.failure_type}")
    print(f"  Confidence    : {output.confidence:.0%}")
    print(f"  Affected      : {', '.join(output.affected_services) or 'None listed'}")
    print()
    print("  Evidence:")
    for e in output.evidence:
        print(f"    - {e}")
    print()
    print("  Recommended Actions:")
    for a in output.recommended_actions:
        print(f"    -> {a}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
