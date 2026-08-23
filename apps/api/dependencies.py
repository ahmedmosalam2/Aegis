from apps.api.database import get_session_factory


async def get_db():
    """Shared database session dependency for all routes."""
    async with get_session_factory()() as session:
        yield session
