"""
Tool Definitions — the actual tools available to Aegis agents.

Each tool is a window into the system. The agents use these tools
to investigate incidents, just like a real SRE engineer would use
Grafana, Datadog, kubectl, or the service dependency map.

Currently these tools query our internal simulation engine.
In production, they would query real observability systems:
    query_metrics      → Prometheus / Datadog
    search_logs        → Elasticsearch / Loki
    get_dependencies   → Service mesh / Kubernetes
    get_active_failures → Chaos engineering platform
    check_system_health → Unified health dashboard

The key design decision: tools are the ONLY way agents interact
with the system. They never touch SQLAlchemy or the DB directly.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents.tools.registry import ToolDefinition, ToolRegistry
from apps.core.health_engine import compute_service_health, compute_system_health
from apps.core.service_graph import (
    get_dependencies,
    get_dependents,
    get_dependency_chain,
    would_cascade,
    SERVICE_DEPENDENCIES,
)
from apps.core.enums import FailureStatus


# ═══════════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════


async def query_metrics(service_name: str, session: AsyncSession) -> dict:
    """Query current health metrics for a service.

    Returns simulated metrics based on active failure injections.
    In production → Prometheus API / Datadog API.

    Metrics returned:
        error_rate (%), latency_ms, cpu_percent, memory_percent,
        connection_pool_usage, overall status
    """
    from apps.models import Service
    from apps.models.failure_injection import FailureInjection

    # Find service
    result = await session.execute(
        select(Service).where(Service.name == service_name)
    )
    service = result.scalar_one_or_none()

    if not service:
        return {"error": f"Service '{service_name}' not found"}

    # Get active failures for this service
    result = await session.execute(
        select(FailureInjection).where(
            FailureInjection.service_id == service.id,
            FailureInjection.status == FailureStatus.ACTIVE.value,
        )
    )
    failures = result.scalars().all()

    failure_dicts = [
        {"failure_type": f.failure_type, "config": f.config, "severity": f.severity}
        for f in failures
    ]

    # Compute health using our simulation engine
    health = compute_service_health(service_name, failure_dicts)

    return {
        "service_name": service_name,
        "status": health.status.value,
        "metrics": health.metrics,
        "active_failure_count": len(failure_dicts),
    }


async def get_service_dependencies(service_name: str, **kwargs) -> dict:
    """Get the dependency graph for a service.

    Shows what this service depends on (upstream) and what depends
    on it (downstream), plus cascade risk assessment.

    In production → Kubernetes service mesh / Istio / Consul.
    """
    if service_name not in SERVICE_DEPENDENCIES:
        return {"error": f"Service '{service_name}' not found in topology"}

    deps = get_dependencies(service_name)
    dependents = get_dependents(service_name)
    cascade_chain = list(get_dependency_chain(service_name))

    return {
        "service_name": service_name,
        "depends_on": deps,
        "depended_by": dependents,
        "cascade_risk": would_cascade(service_name),
        "full_blast_radius": cascade_chain,
        "blast_radius_size": len(cascade_chain),
    }


async def get_active_failures(
    service_name: str | None = None,
    session: AsyncSession = None,
) -> list[dict]:
    """Get active failure injections, optionally filtered by service.

    Tells the agent what known failures are currently active.
    Essential for root cause analysis and confirmation.

    In production → Chaos engineering platform / PagerDuty.
    """
    from apps.models import Service
    from apps.models.failure_injection import FailureInjection

    query = (
        select(FailureInjection, Service)
        .join(Service)
        .where(FailureInjection.status == FailureStatus.ACTIVE.value)
    )

    if service_name:
        query = query.where(Service.name == service_name)

    result = await session.execute(query)
    rows = result.all()

    return [
        {
            "failure_id": str(injection.id),
            "service_name": service.name,
            "failure_type": injection.failure_type,
            "severity": injection.severity,
            "description": injection.description,
            "injected_at": (
                injection.injected_at.isoformat()
                if injection.injected_at else None
            ),
        }
        for injection, service in rows
    ]


async def search_logs(
    service_name: str,
    level: str = "error",
    minutes: int = 30,
    session: AsyncSession = None,
) -> list[dict]:
    """Search recent logs for a service.

    Currently returns simulated log entries based on active failures.
    In production → Elasticsearch / Loki / CloudWatch Logs.

    The simulated logs are realistic — they match what real services
    would produce for each failure type.
    """
    from apps.models import Service
    from apps.models.failure_injection import FailureInjection

    # Get service and its active failures
    svc_result = await session.execute(
        select(Service).where(Service.name == service_name)
    )
    service = svc_result.scalar_one_or_none()

    if not service:
        return [{"error": f"Service '{service_name}' not found"}]

    result = await session.execute(
        select(FailureInjection).where(
            FailureInjection.service_id == service.id,
            FailureInjection.status == FailureStatus.ACTIVE.value,
        )
    )
    failures = result.scalars().all()

    # ── Simulated log templates per failure type ──
    # These mimic real production log patterns
    LOG_TEMPLATES = {
        "service_crash": [
            {"level": "error", "message": "Process exited with code 137 (OOM Killed)"},
            {"level": "error", "message": "Service health check failed — no response"},
            {"level": "warning", "message": "Liveness probe failed 3 consecutive times"},
        ],
        "high_latency": [
            {"level": "warning", "message": "Request latency exceeded SLO threshold (p99 > 2000ms)"},
            {"level": "warning", "message": "Upstream timeout waiting for response"},
            {"level": "error", "message": "Request timeout after 30s"},
        ],
        "memory_leak": [
            {"level": "warning", "message": "Memory usage at 92% — approaching OOM threshold"},
            {"level": "warning", "message": "GC pause time increased to 450ms"},
            {"level": "error", "message": "Heap allocation failed — insufficient memory"},
        ],
        "cpu_saturation": [
            {"level": "warning", "message": "CPU usage at 96% — thread pool exhausted"},
            {"level": "error", "message": "Request queue backlog: 1,247 pending requests"},
            {"level": "warning", "message": "Worker thread starvation detected"},
        ],
        "dependency_failure": [
            {"level": "error", "message": "Connection to downstream service refused"},
            {"level": "error", "message": "Circuit breaker OPEN for dependency"},
            {"level": "warning", "message": "Retry budget exhausted for downstream calls"},
        ],
        "connection_exhaustion": [
            {"level": "error", "message": "Connection pool exhausted — all connections in use"},
            {"level": "error", "message": "Cannot acquire database connection: pool limit reached"},
            {"level": "warning", "message": "Connection wait time exceeded 10s"},
        ],
    }

    # Generate simulated log entries
    logs = []
    now = datetime.now(timezone.utc)

    for failure in failures:
        templates = LOG_TEMPLATES.get(failure.failure_type, [
            {"level": "error", "message": f"Unknown failure condition: {failure.failure_type}"},
        ])
        for i, template in enumerate(templates):
            # Filter by requested log level
            if (
                level == "all"
                or template["level"] == level
                or (level == "error" and template["level"] in ("error", "critical"))
            ):
                logs.append({
                    "timestamp": (now - timedelta(minutes=i * 2)).isoformat(),
                    "service": service_name,
                    "level": template["level"],
                    "message": template["message"],
                    "failure_type": failure.failure_type,
                })

    if not logs:
        logs.append({
            "timestamp": now.isoformat(),
            "service": service_name,
            "level": "info",
            "message": "No error logs found in the specified time range",
        })

    return logs


async def get_incident_history(
    service_name: str,
    limit: int = 5,
    session: AsyncSession = None,
) -> list[dict]:
    """Get recent incidents for a service.

    Helps the agent identify recurring patterns — if the same
    service keeps having the same failure type, that's a signal.

    In production → Incident management system / PagerDuty.
    """
    from apps.models import Incident, Service

    svc_result = await session.execute(
        select(Service).where(Service.name == service_name)
    )
    service = svc_result.scalar_one_or_none()

    if not service:
        return []

    result = await session.execute(
        select(Incident)
        .where(Incident.service_id == service.id)
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )
    incidents = result.scalars().all()

    return [
        {
            "incident_id": str(inc.id),
            "title": inc.title,
            "severity": inc.severity,
            "status": inc.status,
            "created_at": (
                inc.created_at.isoformat() if inc.created_at else None
            ),
        }
        for inc in incidents
    ]


async def check_system_health(session: AsyncSession = None) -> dict:
    """Get overall system health across all services.

    Gives the agent a bird's-eye view of the entire distributed
    system. Useful for understanding if an issue is isolated
    or part of a wider outage.

    In production → Unified health dashboard / status page.
    """
    from apps.models import Service
    from apps.models.failure_injection import FailureInjection

    # Fetch all active failures grouped by service
    result = await session.execute(
        select(FailureInjection, Service)
        .join(Service)
        .where(FailureInjection.status == FailureStatus.ACTIVE.value)
    )
    rows = result.all()

    failures_by_service: dict[str, list[dict]] = {}
    for injection, service in rows:
        if service.name not in failures_by_service:
            failures_by_service[service.name] = []
        failures_by_service[service.name].append({
            "failure_type": injection.failure_type,
            "severity": injection.severity,
            "config": injection.config,
        })

    # Compute health using the simulation engine
    health_reports = compute_system_health(failures_by_service)

    services = []
    for name, report in health_reports.items():
        services.append({
            "service_name": name,
            "status": report.status.value,
            "active_failures": len(report.active_failures),
            "degraded_dependencies": report.degraded_dependencies,
        })

    # Determine overall status
    statuses = [s["status"] for s in services]
    if "down" in statuses:
        overall = "down"
    elif "unhealthy" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "overall_status": overall,
        "total_services": len(services),
        "services": services,
    }


# ═══════════════════════════════════════════════════════════════════
# JSON SCHEMAS (for OpenAI function calling)
# ═══════════════════════════════════════════════════════════════════


QUERY_METRICS_SCHEMA = {
    "type": "object",
    "properties": {
        "service_name": {
            "type": "string",
            "description": (
                "Name of the service to query metrics for "
                "(e.g., 'payment-service', 'order-service', 'api-gateway')"
            ),
        },
    },
    "required": ["service_name"],
}

GET_SERVICE_DEPENDENCIES_SCHEMA = {
    "type": "object",
    "properties": {
        "service_name": {
            "type": "string",
            "description": "Name of the service to get dependencies for",
        },
    },
    "required": ["service_name"],
}

GET_ACTIVE_FAILURES_SCHEMA = {
    "type": "object",
    "properties": {
        "service_name": {
            "type": "string",
            "description": (
                "Filter failures by service name. "
                "Omit to get all active failures across the system."
            ),
        },
    },
    "required": [],
}

SEARCH_LOGS_SCHEMA = {
    "type": "object",
    "properties": {
        "service_name": {
            "type": "string",
            "description": "Name of the service to search logs for",
        },
        "level": {
            "type": "string",
            "enum": ["error", "warning", "info", "all"],
            "description": "Log level filter. Default: 'error'",
        },
        "minutes": {
            "type": "integer",
            "description": "How many minutes back to search. Default: 30",
        },
    },
    "required": ["service_name"],
}

GET_INCIDENT_HISTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "service_name": {
            "type": "string",
            "description": "Name of the service to get incident history for",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of incidents to return. Default: 5",
        },
    },
    "required": ["service_name"],
}

CHECK_SYSTEM_HEALTH_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
}


# ═══════════════════════════════════════════════════════════════════
# FACTORY FUNCTIONS
# Each agent type gets only the tools it needs — principle of least
# privilege applied to AI agents.
# ═══════════════════════════════════════════════════════════════════


def create_diagnosis_tools() -> ToolRegistry:
    """Create and register all tools for the Diagnosis Agent.

    The Diagnosis Agent gets the full toolkit — it needs to
    investigate from every angle.
    """
    registry = ToolRegistry()

    registry.register(ToolDefinition(
        name="query_metrics",
        description=(
            "Query current health metrics for a specific service. "
            "Returns: error_rate (%), latency_ms, cpu_percent, memory_percent, "
            "connection_pool_usage (0.0-1.0), and overall status. "
            "Use this FIRST to understand the current state of a service."
        ),
        parameters=QUERY_METRICS_SCHEMA,
        handler=query_metrics,
    ))

    registry.register(ToolDefinition(
        name="get_service_dependencies",
        description=(
            "Get the dependency graph for a service. Shows what this service "
            "depends on (upstream) and what depends on it (downstream). "
            "Also shows blast radius — how many services would be affected. "
            "Use this to understand cascading failure risk."
        ),
        parameters=GET_SERVICE_DEPENDENCIES_SCHEMA,
        handler=get_service_dependencies,
        requires_db=False,
    ))

    registry.register(ToolDefinition(
        name="get_active_failures",
        description=(
            "Get currently active failure injections in the system. "
            "Can filter by service name or get all active failures. "
            "Returns failure type, severity, and injection time. "
            "Use this to confirm suspected failures."
        ),
        parameters=GET_ACTIVE_FAILURES_SCHEMA,
        handler=get_active_failures,
    ))

    registry.register(ToolDefinition(
        name="search_logs",
        description=(
            "Search recent log entries for a service. Filter by log level "
            "(error, warning, info, all). Returns timestamped log messages. "
            "Use this to find error patterns and failure evidence."
        ),
        parameters=SEARCH_LOGS_SCHEMA,
        handler=search_logs,
    ))

    registry.register(ToolDefinition(
        name="get_incident_history",
        description=(
            "Get recent past incidents for a service. "
            "Shows title, severity, status, and creation time. "
            "Use this to identify recurring patterns or related issues."
        ),
        parameters=GET_INCIDENT_HISTORY_SCHEMA,
        handler=get_incident_history,
    ))

    registry.register(ToolDefinition(
        name="check_system_health",
        description=(
            "Get a bird's-eye view of the entire system health. "
            "Shows status of ALL services and their active failure counts. "
            "Use this to understand if the issue is isolated or widespread."
        ),
        parameters=CHECK_SYSTEM_HEALTH_SCHEMA,
        handler=check_system_health,
    ))

    return registry


def create_triage_tools() -> ToolRegistry:
    """Create tools for the Triage Agent (subset).

    Triage only needs to assess impact — not deep investigation.
    """
    registry = ToolRegistry()

    registry.register(ToolDefinition(
        name="query_metrics",
        description="Query current health metrics for a specific service.",
        parameters=QUERY_METRICS_SCHEMA,
        handler=query_metrics,
    ))

    registry.register(ToolDefinition(
        name="get_service_dependencies",
        description="Get the dependency graph and blast radius for a service.",
        parameters=GET_SERVICE_DEPENDENCIES_SCHEMA,
        handler=get_service_dependencies,
        requires_db=False,
    ))

    registry.register(ToolDefinition(
        name="check_system_health",
        description="Get overall system health across all services.",
        parameters=CHECK_SYSTEM_HEALTH_SCHEMA,
        handler=check_system_health,
    ))

    return registry


def create_verification_tools() -> ToolRegistry:
    """Create tools for the Verification Agent (subset).

    Verification only needs to check if things are healthy now.
    """
    registry = ToolRegistry()

    registry.register(ToolDefinition(
        name="query_metrics",
        description="Query current health metrics for a specific service.",
        parameters=QUERY_METRICS_SCHEMA,
        handler=query_metrics,
    ))

    registry.register(ToolDefinition(
        name="check_system_health",
        description="Get overall system health across all services.",
        parameters=CHECK_SYSTEM_HEALTH_SCHEMA,
        handler=check_system_health,
    ))

    registry.register(ToolDefinition(
        name="get_active_failures",
        description="Get currently active failure injections.",
        parameters=GET_ACTIVE_FAILURES_SCHEMA,
        handler=get_active_failures,
    ))

    return registry
