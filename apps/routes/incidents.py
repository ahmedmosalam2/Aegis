from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.database import AsyncSessionLocal
from apps.models import Incident
from apps.schemas.incidents import IncidentCreate ,IncidentUpdate


router = APIRouter(
    prefix="/incidents",
    tags=["Incidents"],
)



async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/")
async def create_incident(
    incident_data: IncidentCreate,
    db: AsyncSession = Depends(get_db),
):
    incident = Incident(
        title=incident_data.title,
        description=incident_data.description,
        severity=incident_data.severity,
    )

    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    return {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "status": incident.status,
    }


@router.get("/")
async def list_incidents(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).order_by(Incident.created_at.desc())
    )

    incidents = result.scalars().all()

    return incidents

@router.get("/{incident_id}")
async def get_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = result.scalar_one_or_none()

    if incident is None:
        return {"detail": "Incident not found"}

    return {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "status": incident.status,
    }
@router.patch("/{incident_id}")
async def update_incident(
    incident_id: UUID,
    incident_data: IncidentUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = result.scalar_one_or_none()

    if incident is None:
        return {"detail": "Incident not found"}

    update_data = incident_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(incident, field, value)

    await db.commit()
    await db.refresh(incident)

    return {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "status": incident.status,
    }

@router.delete("/{incident_id}")
async def delete_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )

    incident = result.scalar_one_or_none()

    if incident is None:
        return {"detail": "Incident not found"}

    await db.delete(incident)
    await db.commit()

    return {"detail": "Incident deleted successfully"}
  
