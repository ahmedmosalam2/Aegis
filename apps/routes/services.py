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


@router.get("/{service_id}")
async def get_service(
    service_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).where(Service.id == service_id)
    )

    service = result.scalar_one_or_none()

    if service is None:
        return {"detail": "Service not found"}

    return {
        "id": str(service.id),
        "name": service.name,
        "description": service.description,
        "environment": service.environment,
        "health_check_url": service.health_check_url,
        "status": service.status,
    }

@router.patch("/{service_id}")
async def update_service(
    service_id: UUID,
    service_data: ServiceUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).where(Service.id == service_id)
    )

    service = result.scalar_one_or_none()

    if service is None:
        return {"detail": "Service not found"}

    update_data = service_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(service, field, value)

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

@router.delete("/{service_id}")
async def delete_service(
    service_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).where(Service.id == service_id)
    )

    service = result.scalar_one_or_none()

    if service is None:
        return {"detail": "Service not found"}

    await db.delete(service)
    await db.commit()

    return {"detail": "Service deleted successfully"}