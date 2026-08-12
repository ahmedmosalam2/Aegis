from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

DATABASE_URL = (
    "postgresql+asyncpg://"
    "aegis:aegis_dev_password@localhost:5432/aegis"
)

engine= create_async_engine(
    DATABASE_URL,
    echo=True
)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass


async def init_db():
    from apps.models import Incident

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

async def check_db_connection():
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))