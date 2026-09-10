from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from apps.core.enums import IncidentStatus, IncidentSeverity


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    service_id: Optional[str] = None


class IncidentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = None
    severity: IncidentSeverity | None = None


class IncidentStatusUpdate(BaseModel):
    """Used for the dedicated status transition endpoint."""
    status: IncidentStatus
    reason: str = Field(..., min_length=1, max_length=500)


class IncidentResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    severity: str
    status: str
    service_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class IncidentEventCreate(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=1)
    metadata_: Optional[dict] = Field(default=None, alias="metadata")

    class Config:
        populate_by_name = True


class IncidentEventResponse(BaseModel):
    id: str
    incident_id: str
    event_type: str
    description: str
    old_status: Optional[str]
    new_status: Optional[str]
    metadata_: Optional[dict] = Field(default=None, alias="metadata")
    created_by: str
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True