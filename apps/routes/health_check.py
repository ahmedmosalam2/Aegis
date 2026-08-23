from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from apps.models import Service
from apps.models.failure_injection import FailureInjection
from apps.core.enums import FailureStatus, ServiceStatus
from apps.core.service_graph import SERVICE_DEPENDENCIES
from apps.core.health_engine import compute_system_health, ServiceHealthReport
from apps.schemas.failures import ServiceHealthResponse, SystemHealthResponse


router = APIRouter(
    prefix="/health",
    tags=["Target System Health"],
)


async def _get_active_failures(db: AsyncSession) -> dict[str, list[dict]]:
    """Helper to fetch all active failures grouped by service name."""
    
    result = await db.execute(
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
        
    return failures_by_service


@router.get(
    "/system",
    response_model=SystemHealthResponse,
)
async def get_system_health(
    db: AsyncSession = Depends(get_db),
):
    """Get the overall health of the entire distributed system."""
    
    # 1. Fetch active failures
    active_failures = await _get_active_failures(db)
    
    # 2. Compute health using the simulation engine
    health_reports = compute_system_health(active_failures)
    
    # 3. Aggregate results
    counts = {
        ServiceStatus.HEALTHY: 0,
        ServiceStatus.DEGRADED: 0,
        ServiceStatus.UNHEALTHY: 0,
        ServiceStatus.DOWN: 0,
    }
    
    for report in health_reports.values():
        if report.status in counts:
            counts[report.status] += 1
            
    # Determine overall status
    overall = ServiceStatus.HEALTHY
    if counts[ServiceStatus.DOWN] > 0:
        overall = ServiceStatus.DOWN
    elif counts[ServiceStatus.UNHEALTHY] > 0:
        overall = ServiceStatus.UNHEALTHY
    elif counts[ServiceStatus.DEGRADED] > 0:
        overall = ServiceStatus.DEGRADED
        
    services_list = [
        ServiceHealthResponse(
            service_name=r.service_name,
            status=r.status.value,
            active_failures=r.active_failures,
            degraded_dependencies=r.degraded_dependencies,
            metrics=r.metrics,
        )
        for r in health_reports.values()
    ]
    
    return SystemHealthResponse(
        overall_status=overall.value,
        total_services=len(health_reports),
        healthy=counts[ServiceStatus.HEALTHY],
        degraded=counts[ServiceStatus.DEGRADED],
        unhealthy=counts[ServiceStatus.UNHEALTHY],
        down=counts[ServiceStatus.DOWN],
        services=services_list,
    )


@router.get(
    "/services/{service_name}",
    response_model=ServiceHealthResponse,
)
async def get_service_health(
    service_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the computed health for a specific service."""
    
    if service_name not in SERVICE_DEPENDENCIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_name}' not found in topology",
        )
        
    active_failures = await _get_active_failures(db)
    health_reports = compute_system_health(active_failures)
    
    report = health_reports[service_name]
    
    return ServiceHealthResponse(
        service_name=report.service_name,
        status=report.status.value,
        active_failures=report.active_failures,
        degraded_dependencies=report.degraded_dependencies,
        metrics=report.metrics,
    )


@router.get("/dependencies")
async def get_topology():
    """Returns the service dependency graph."""
    return SERVICE_DEPENDENCIES
