from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from apps.models import Service, IncidentEvent
from apps.models.failure_injection import FailureInjection
from apps.core.enums import FailureStatus
from apps.core.logging import get_logger
from apps.core.telemetry import FAILURES_INJECTED, FAILURES_RESOLVED
from apps.schemas.failures import (
    FailureInjectRequest,
    FailureInjectResponse,
    FailureResolveRequest,
    FailureListResponse,
)

logger = get_logger("routes.failures")

router = APIRouter(
    prefix="/failures",
    tags=["Chaos Engineering & Failures"],
)


@router.post(
    "/inject",
    response_model=FailureInjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def inject_failure(
    request: FailureInjectRequest,
    db: AsyncSession = Depends(get_db),
):
    """Inject a new failure into a target service."""
    
    # 1. Verify service exists
    result = await db.execute(
        select(Service).where(Service.id == UUID(request.service_id))
    )
    service = result.scalar_one_or_none()
    
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target service not found",
        )

    # 2. Create the injection record
    injection = FailureInjection(
        service_id=service.id,
        failure_type=request.failure_type.value,
        status=FailureStatus.ACTIVE.value,
        severity=request.severity.value,
        config=request.config,
        description=request.description,
        auto_resolve_after=request.auto_resolve_after,
        created_by="api_user",
    )
    
    db.add(injection)
    await db.commit()
    await db.refresh(injection)
    
    logger.info(
        f"Injected {injection.failure_type} into {service.name}",
        extra={"extra_data": {"failure_id": str(injection.id)}}
    )
    FAILURES_INJECTED.labels(
        failure_type=injection.failure_type,
        service_name=service.name,
    ).inc()
    
    return injection


@router.post(
    "/{failure_id}/resolve",
    response_model=FailureInjectResponse,
)
async def resolve_failure(
    failure_id: UUID,
    request: FailureResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resolve an active failure injection."""
    
    result = await db.execute(
        select(FailureInjection).where(FailureInjection.id == failure_id)
    )
    injection = result.scalar_one_or_none()
    
    if not injection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Failure injection not found",
        )
        
    if injection.status != FailureStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failure is already {injection.status}",
        )
        
    injection.status = FailureStatus.RESOLVED.value
    injection.resolved_at = datetime.now(timezone.utc)
    
    # We could also log an event here for audit purposes
    
    await db.commit()
    await db.refresh(injection)
    
    logger.info(
        f"Resolved failure {injection.id}",
        extra={"extra_data": {"failure_id": str(injection.id)}}
    )
    FAILURES_RESOLVED.inc()
    
    return injection


@router.get(
    "/",
    response_model=list[FailureListResponse],
)
async def list_failures(
    status: str | None = None,
    service_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List historical and active failure injections."""
    
    query = select(FailureInjection)
    
    if status:
        query = query.where(FailureInjection.status == status)
    if service_id:
        query = query.where(FailureInjection.service_id == service_id)
        
    query = query.order_by(FailureInjection.injected_at.desc())
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/active",
    response_model=list[FailureListResponse],
)
async def list_active_failures(
    db: AsyncSession = Depends(get_db),
):
    """Quick helper to get all currently active failures across the system."""
    
    result = await db.execute(
        select(FailureInjection)
        .where(FailureInjection.status == FailureStatus.ACTIVE.value)
        .order_by(FailureInjection.injected_at.desc())
    )
    
    return result.scalars().all()
