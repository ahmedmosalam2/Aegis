from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.models.service import Service
from apps.core.service_graph import DEFAULT_SERVICES


async def seed_default_services(db: AsyncSession) -> None:
    """Create the target system services if they don't exist yet."""

    result = await db.execute(select(Service.name))
    existing_names = set(result.scalars().all())

    created = []
    for svc_data in DEFAULT_SERVICES:
        if svc_data["name"] not in existing_names:
            service = Service(
                name=svc_data["name"],
                description=svc_data["description"],
                environment=svc_data["environment"],
                health_check_url=svc_data.get("health_check_url"),
                status="healthy",
            )
            db.add(service)
            created.append(svc_data["name"])

    if created:
        await db.commit()
        print(f"[Seed] Created {len(created)} services: {created}")
    else:
        print("[Seed] All default services already exist")
