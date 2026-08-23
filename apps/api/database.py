from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from apps.api.config import settings

# Lazy — created on first access, after telemetry is initialized
_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.ENVIRONMENT == "development",
        )
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


class Base(DeclarativeBase):
    pass


async def init_db():
    from apps.models import Incident, Service, Event, IncidentEvent, FailureInjection

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def check_db_connection():
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))