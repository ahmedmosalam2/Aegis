from apps.api.database import get_session_factory


async def get_db():
    async with get_session_factory()() as session:
        yield session
