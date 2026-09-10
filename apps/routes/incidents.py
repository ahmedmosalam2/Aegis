from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from apps.api.config import settings
from apps.api.dependencies import get_db
from apps.models import Incident, Service
from apps.models.incident_event import IncidentEvent
from apps.core.enums import IncidentStatus, validate_status_transition
from apps.core.logging import get_logger
from apps.core.telemetry import INCIDENTS_CREATED, INCIDENTS_RESOLVED
from apps.schemas.incidents import (
    IncidentCreate,
    IncidentUpdate,
    IncidentStatusUpdate,
    IncidentResponse,
    IncidentEventCreate,
    IncidentEventResponse,
)


# ─── Workflow Schemas ──────────────────────────────────────────────

class WorkflowTriggerRequest(BaseModel):
    """Request body to trigger a workflow for an incident."""
    service_name: str = Field(..., min_length=1, description="Name of the affected service")


class WorkflowTriggerResponse(BaseModel):
    """Response after triggering a workflow."""
    incident_id: str
    workflow_id: str
    status: str = "started"
    message: str


class ApprovalRequest(BaseModel):
    """Request body for approving/rejecting a remediation action."""
    reason: str = Field(default="Operator decision", max_length=500)

logger = get_logger("routes.incidents")

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
    
    logger.info(
        f"Incident created: {incident.title}",
        extra={"extra_data": {"incident_id": str(incident.id), "severity": incident.severity}}
    )
    INCIDENTS_CREATED.inc()

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
    
    logger.info(
        f"Incident {incident.id} status transition: {old_status} → {target_status.value}",
        extra={"extra_data": {"incident_id": str(incident.id), "new_status": target_status.value}}
    )
    if target_status == IncidentStatus.RESOLVED:
        INCIDENTS_RESOLVED.inc()

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


# ─── WORKFLOW CONTROL ──────────────────────────────────────────────


async def _get_temporal_client() -> Client:
    """Get a Temporal client connection."""
    return await Client.connect(settings.TEMPORAL_ADDRESS)


@router.post(
    "/{incident_id}/workflow",
    response_model=WorkflowTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_incident_workflow(
    incident_id: UUID,
    body: WorkflowTriggerRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger the Incident Resolution Workflow for an incident.

    Starts a durable Temporal workflow that orchestrates:
    Triage → Diagnosis → Remediation → Verification → Postmortem.
    """
    # Verify incident exists
    incident = await _get_incident_or_404(incident_id, db)

    # Verify service exists
    result = await db.execute(
        select(Service).where(Service.name == body.service_name)
    )
    service = result.scalar_one_or_none()
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{body.service_name}' not found",
        )

    # Connect to Temporal and start workflow
    from apps.workflows.incident_workflow import IncidentResolutionWorkflow
    from apps.workflows.dataclasses import IncidentWorkflowInput

    client = await _get_temporal_client()
    workflow_id = f"incident-{incident_id}"

    try:
        await client.start_workflow(
            IncidentResolutionWorkflow.run,
            IncidentWorkflowInput(
                incident_id=str(incident_id),
                service_name=body.service_name,
            ),
            id=workflow_id,
            task_queue="aegis-incidents",
        )
    except Exception as e:
        if "already started" in str(e).lower() or "already running" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Workflow already running for incident {incident_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to start workflow: {e}",
        )

    logger.info(
        f"Workflow started for incident {incident_id}",
        extra={"extra_data": {"workflow_id": workflow_id, "service": body.service_name}},
    )

    return WorkflowTriggerResponse(
        incident_id=str(incident_id),
        workflow_id=workflow_id,
        status="started",
        message=f"Incident resolution workflow started for service '{body.service_name}'",
    )


@router.post(
    "/{incident_id}/approve",
    status_code=status.HTTP_200_OK,
)
async def approve_remediation(
    incident_id: UUID,
    body: ApprovalRequest = ApprovalRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Approve a high-risk remediation action for an incident.

    Sends an 'approve' signal to the running Temporal workflow.
    The workflow must be in the 'awaiting_approval' phase.
    """
    await _get_incident_or_404(incident_id, db)

    client = await _get_temporal_client()
    workflow_id = f"incident-{incident_id}"

    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal("approve_remediation", body.reason)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to send approval signal: {e}",
        )

    logger.info(
        f"Remediation approved for incident {incident_id}",
        extra={"extra_data": {"reason": body.reason}},
    )

    return {"status": "approved", "incident_id": str(incident_id), "reason": body.reason}


@router.post(
    "/{incident_id}/reject",
    status_code=status.HTTP_200_OK,
)
async def reject_remediation(
    incident_id: UUID,
    body: ApprovalRequest = ApprovalRequest(),
    db: AsyncSession = Depends(get_db),
):
    """Reject a high-risk remediation action for an incident.

    Sends a 'reject' signal to the running Temporal workflow.
    The workflow will re-investigate the incident.
    """
    await _get_incident_or_404(incident_id, db)

    client = await _get_temporal_client()
    workflow_id = f"incident-{incident_id}"

    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal("reject_remediation", body.reason)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to send rejection signal: {e}",
        )

    logger.info(
        f"Remediation rejected for incident {incident_id}",
        extra={"extra_data": {"reason": body.reason}},
    )

    return {"status": "rejected", "incident_id": str(incident_id), "reason": body.reason}
