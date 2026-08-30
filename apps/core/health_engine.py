from dataclasses import dataclass, field
from datetime import datetime, timezone

from apps.core.enums import ServiceStatus, FailureType
from apps.core.service_graph import get_dependencies, SERVICE_DEPENDENCIES


@dataclass
class ServiceHealthReport:
    service_name: str
    status: ServiceStatus = ServiceStatus.HEALTHY
    active_failures: list[dict] = field(default_factory=list)
    degraded_dependencies: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# ───────────────────────────────────────────────

def _baseline_metrics() -> dict:
    return {
        "error_rate": 0.0,
        "latency_ms": 45,
        "cpu_percent": 12.0,
        "memory_percent": 35.0,
        "connection_pool_usage": 0.15,
    }


# ─── Per-Failure Metric Effects ────────────────────────────────────

def _apply_failure_effects(metrics: dict, failure_type: str, config: dict | None) -> tuple[dict, ServiceStatus]:
    """Apply a single failure's effects to the metrics. Returns updated metrics and worst status."""

    config = config or {}

    if failure_type == FailureType.SERVICE_CRASH.value:
        metrics["error_rate"] = 100.0
        metrics["latency_ms"] = 0
        return metrics, ServiceStatus.DOWN

    if failure_type == FailureType.HIGH_LATENCY.value:
        latency = config.get("latency_ms", 5000)
        metrics["latency_ms"] = max(metrics["latency_ms"], latency)
        metrics["error_rate"] = min(metrics["error_rate"] + 15.0, 100.0)
        return metrics, ServiceStatus.DEGRADED

    if failure_type == FailureType.MEMORY_LEAK.value:
        leak_percent = config.get("memory_percent", 92.0)
        metrics["memory_percent"] = max(metrics["memory_percent"], leak_percent)
        metrics["latency_ms"] = max(metrics["latency_ms"], 800)
        if metrics["memory_percent"] >= 95.0:
            return metrics, ServiceStatus.UNHEALTHY
        return metrics, ServiceStatus.DEGRADED

    if failure_type == FailureType.CPU_SATURATION.value:
        metrics["cpu_percent"] = max(metrics["cpu_percent"], 96.0)
        metrics["latency_ms"] = max(metrics["latency_ms"], 2000)
        metrics["error_rate"] = min(metrics["error_rate"] + 25.0, 100.0)
        return metrics, ServiceStatus.UNHEALTHY

    if failure_type == FailureType.DEPENDENCY_FAILURE.value:
        metrics["error_rate"] = min(metrics["error_rate"] + 40.0, 100.0)
        metrics["latency_ms"] = max(metrics["latency_ms"], 3000)
        return metrics, ServiceStatus.DEGRADED

    if failure_type == FailureType.CONNECTION_EXHAUSTION.value:
        metrics["connection_pool_usage"] = 1.0
        metrics["error_rate"] = min(metrics["error_rate"] + 60.0, 100.0)
        metrics["latency_ms"] = max(metrics["latency_ms"], 10000)
        return metrics, ServiceStatus.UNHEALTHY

    return metrics, ServiceStatus.HEALTHY


# ─── Status Priority ──────────────────────────────────────────────

_STATUS_PRIORITY = {
    ServiceStatus.HEALTHY: 0,
    ServiceStatus.DEGRADED: 1,
    ServiceStatus.UNHEALTHY: 2,
    ServiceStatus.DOWN: 3,
}


def _worst_status(a: ServiceStatus, b: ServiceStatus) -> ServiceStatus:
    if _STATUS_PRIORITY.get(a, 0) >= _STATUS_PRIORITY.get(b, 0):
        return a
    return b


# ─── Main Engine ───────────────────────────────────────────────────


def compute_service_health(
    service_name: str,
    active_failures: list[dict],
    all_service_statuses: dict[str, ServiceStatus] | None = None,
) -> ServiceHealthReport:
    """Compute the health of a single service.

    Args:
        service_name: Name of the service to evaluate.
        active_failures: List of active failure dicts for THIS service.
            Each dict should have: failure_type, config, severity.
        all_service_statuses: Optional map of other services' statuses,
            used to compute cascading dependency degradation.
    """

    metrics = _baseline_metrics()
    worst = ServiceStatus.HEALTHY
    failure_summaries = []

    # 1. Apply direct failure effects
    for failure in active_failures:
        ftype = failure.get("failure_type", "")
        config = failure.get("config")
        metrics, status = _apply_failure_effects(metrics, ftype, config)
        worst = _worst_status(worst, status)
        failure_summaries.append({
            "failure_type": ftype,
            "severity": failure.get("severity", "high"),
        })

    # 2. Check dependency health (cascading)
    degraded_deps = []
    if all_service_statuses:
        for dep in get_dependencies(service_name):
            dep_status = all_service_statuses.get(dep, ServiceStatus.UNKNOWN)
            if dep_status in (ServiceStatus.DOWN, ServiceStatus.UNHEALTHY):
                degraded_deps.append(dep)
                worst = _worst_status(worst, ServiceStatus.DEGRADED)
                metrics["error_rate"] = min(metrics["error_rate"] + 20.0, 100.0)
                metrics["latency_ms"] = max(metrics["latency_ms"], 1500)
            elif dep_status == ServiceStatus.DEGRADED:
                degraded_deps.append(dep)
                metrics["latency_ms"] = max(metrics["latency_ms"], 500)

    return ServiceHealthReport(
        service_name=service_name,
        status=worst,
        active_failures=failure_summaries,
        degraded_dependencies=degraded_deps,
        metrics=metrics,
    )


def compute_system_health(
    service_failures: dict[str, list[dict]],
) -> dict[str, ServiceHealthReport]:
    """Compute health for all services with cascading dependency effects.

    Args:
        service_failures: Map of service_name → list of active failure dicts.

    Returns:
        Map of service_name → ServiceHealthReport.
    """

    all_services = list(SERVICE_DEPENDENCIES.keys())

    # Pass 1: compute direct failures only
    direct_statuses: dict[str, ServiceStatus] = {}
    for svc in all_services:
        failures = service_failures.get(svc, [])
        report = compute_service_health(svc, failures)
        direct_statuses[svc] = report.status

    # Pass 2: recompute with dependency awareness
    results: dict[str, ServiceHealthReport] = {}
    for svc in all_services:
        failures = service_failures.get(svc, [])
        report = compute_service_health(svc, failures, direct_statuses)
        results[svc] = report

    return results
