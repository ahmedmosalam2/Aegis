from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from apps.models import Service
from apps.schemas.services import (
    ServiceCreate,
    ServiceUpdate,
    ServiceResponse,
)


router = APIRouter(
    prefix="/services",
    tags=["Services"],
)


@router.post(
    "/",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
)
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

    return service


@router.get("/", response_model=list[ServiceResponse])
async def list_services(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).order_by(Service.created_at.desc())
    )

    return result.scalars().all()


@router.get("/{service_id}", response_model=ServiceResponse)
async def get_service(
    service_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).where(Service.id == service_id)
    )

    service = result.scalar_one_or_none()

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    return service


@router.patch("/{service_id}", response_model=ServiceResponse)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    update_data = service_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(service, field, value)

    await db.commit()
    await db.refresh(service)

    return service


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_service(
    service_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Service).where(Service.id == service_id)
    )

    service = result.scalar_one_or_none()

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    await db.delete(service)
    await db.commit()