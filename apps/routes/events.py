from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.database import AsyncSessionLocal
from apps.models import Event
from apps.schemas.events import EventCreate, EventUpdate

router = APIRouter(
    prefix="/events",
    tags=["Events"],
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/")
async def create_event(
    event_data: EventCreate,
    db: AsyncSession = Depends(get_db),
):
    event = Event(
        event_type=event_data.event_type,
        source=event_data.source,
        severity=event_data.severity,
        message=event_data.message,
    )

    db.add(event)
    await db.commit()
    await db.refresh(event)

    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "source": event.source,
        "severity": event.severity,
        "message": event.message,
        "created_at": event.created_at,
    }

@router.get("/")
async def list_events(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Event).order_by(Event.created_at.desc())
    )

    events = result.scalars().all()

    return events