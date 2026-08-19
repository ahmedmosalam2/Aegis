from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from apps.models import Incident
from apps.models.incident_event import IncidentEvent
from apps.core.enums import IncidentStatus, validate_status_transition
from apps.schemas.incidents import (
    IncidentCreate,
    IncidentUpdate,
    IncidentStatusUpdate,
    IncidentResponse,
    IncidentEventCreate,
    IncidentEventResponse,
)


router = APIRouter(
    prefix="/incidents",
    tags=["Incidents"],
)


# ─── CRUD ──────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_incident(
    incident_data: IncidentCreate,
    db: AsyncSession = Depends(get_db),
):
    incident = Incident(
        title=incident_data.title,
        description=incident_data.description,
        severity=incident_data.severity.value,
        service_id=incident_data.service_id,
    )

    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    # Log the creation event
    event = IncidentEvent(
        incident_id=incident.id,
        event_type="incident_created",
        description=f"Incident '{incident.title}' created with severity {incident.severity}",
        new_status=IncidentStatus.DETECTED.value,
        created_by="system",
    )
    db.add(event)
    await db.commit()

    return incident


@router.get("/", response_model=list[IncidentResponse])
async def list_incidents(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).order_by(Incident.created_at.desc())
    )

    return result.scalars().all()


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident_or_404(incident_id, db)
    return incident


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: UUID,
    incident_data: IncidentUpdate,
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident_or_404(incident_id, db)

    update_data = incident_data.model_dump(exclude_unset=True)

    # Convert enum to string value if severity is present
    if "severity" in update_data and update_data["severity"] is not None:
        update_data["severity"] = update_data["severity"].value

    for field, value in update_data.items():
        setattr(incident, field, value)

    await db.commit()
    await db.refresh(incident)

    return incident


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident_or_404(incident_id, db)

    await db.delete(incident)
    await db.commit()


# ─── STATUS TRANSITION ─────────────────────────────────────────────


@router.patch(
    "/{incident_id}/status",
    response_model=IncidentResponse,
)
async def transition_incident_status(
    incident_id: UUID,
    status_update: IncidentStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Transition an incident to a new status.

    Validates that the transition is allowed by the state machine.
    Automatically logs the transition as an IncidentEvent.
    """
    incident = await _get_incident_or_404(incident_id, db)

    current_status = IncidentStatus(incident.status)
    target_status = status_update.status

    if not validate_status_transition(current_status, target_status):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid status transition: "
                f"'{current_status.value}' → '{target_status.value}'. "
                f"Allowed transitions from '{current_status.value}': "
                f"{[s.value for s in __get_allowed_transitions(current_status)]}"
            ),
        )

    old_status = incident.status
    incident.status = target_status.value

    # Log the status change as an event
    event = IncidentEvent(
        incident_id=incident.id,
        event_type="status_changed",
        description=status_update.reason,
        old_status=old_status,
        new_status=target_status.value,
        created_by="system",
    )
    db.add(event)

    await db.commit()
    await db.refresh(incident)

    return incident


# ─── TIMELINE / EVENTS ─────────────────────────────────────────────


@router.get(
    "/{incident_id}/timeline",
    response_model=list[IncidentEventResponse],
)
async def get_incident_timeline(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the complete timeline of events for an incident."""
    # Verify incident exists
    await _get_incident_or_404(incident_id, db)

    result = await db.execute(
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.created_at.asc())
    )

    return result.scalars().all()


@router.post(
    "/{incident_id}/timeline",
    response_model=IncidentEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_incident_event(
    incident_id: UUID,
    event_data: IncidentEventCreate,
    db: AsyncSession = Depends(get_db),
):
    """Manually add an event to an incident's timeline."""
    await _get_incident_or_404(incident_id, db)

    event = IncidentEvent(
        incident_id=incident_id,
        event_type=event_data.event_type,
        description=event_data.description,
        metadata_=event_data.metadata_,
        created_by="system",
    )

    db.add(event)
    await db.commit()
    await db.refresh(event)

    return event


# ─── HELPERS ────────────────────────────────────────────────────────


async def _get_incident_or_404(
    incident_id: UUID,
    db: AsyncSession,
) -> Incident:
    """Fetch an incident by ID or raise 404."""
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = result.scalar_one_or_none()

    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    return incident


def __get_allowed_transitions(
    current: IncidentStatus,
) -> list[IncidentStatus]:
    """Get allowed target statuses from current status."""
    from apps.core.enums import VALID_STATUS_TRANSITIONS
    return VALID_STATUS_TRANSITIONS.get(current, [])
