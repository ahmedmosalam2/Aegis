from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.database import AsyncSessionLocal
from apps.models import Service
from apps.schemas.services import ServiceCreate, ServiceUpdate


router = APIRouter(
    prefix="/services",
    tags=["Services"],
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/")
async def create_service(
    service_data: ServiceCreate,
    db: AsyncSession = Depends(get_db),
):
    service = Service(
        name=service_data.name,
        description=service_data.description,
        environment=service_data.environment,
        health_check_url=service_data.health_check_url,
        status=service_data.status,
    )

    db.add(service)
    await db.commit()
    await db.refresh(service)

    return {
        "id": str(service.id),
        "name": service.name,
        "description": service.description,
        "environment": service.environment,
        "health_check_url": service.health_check_url,
        "status": service.status,
    }


@router.get("/")
async def list_services(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).order_by(Service.created_at.desc())
    )

    services = result.scalars().all()

    return services