from apps.api.database import AsyncSessionLocal


async def get_db():
    """Shared database session dependency for all routes."""
    async with AsyncSessionLocal() as session:
        yield session
