from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from apps.core.enums import FailureType, FailureStatus, IncidentSeverity


# ─── Failure Injection ─────────────────────────────────────────────


class FailureInjectRequest(BaseModel):
    service_id: str
    failure_type: FailureType
    severity: IncidentSeverity = IncidentSeverity.HIGH
    config: Optional[dict] = None
    description: Optional[str] = None
    auto_resolve_after: Optional[int] = Field(
        default=None,
        description="Auto-resolve after N seconds",
        ge=1,
    )


class FailureInjectResponse(BaseModel):
    id: str
    service_id: str
    failure_type: str
    status: str
    severity: str
    config: Optional[dict]
    description: Optional[str]
    injected_at: datetime
    resolved_at: Optional[datetime]
    auto_resolve_after: Optional[int]
    created_by: str

    class Config:
        from_attributes = True


class FailureResolveRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class FailureListResponse(BaseModel):
    id: str
    service_id: str
    failure_type: str
    status: str
    severity: str
    injected_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


# ─── Health ────────────────────────────────────────────────────────


class ServiceHealthResponse(BaseModel):
    service_name: str
    status: str
    active_failures: list[dict] = []
    degraded_dependencies: list[str] = []
    metrics: dict = {}


class SystemHealthResponse(BaseModel):
    overall_status: str
    total_services: int
    healthy: int
    degraded: int
    unhealthy: int
    down: int
    services: list[ServiceHealthResponse]
