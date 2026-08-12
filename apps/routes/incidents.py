from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.database import AsyncSessionLocal
from apps.models import Incident
from sqlalchemy import select


router = APIRouter(
    prefix="/incidents",
    tags=["Incidents"],
)


class IncidentCreate(BaseModel):
    title: str
    description: str | None = None
    severity: str = "medium"


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