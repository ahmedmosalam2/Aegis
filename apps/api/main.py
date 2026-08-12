from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.config import settings
from apps.api.database import init_db
from apps.routes.health import router as health_router
from apps.routes.incidents import router as incidents_router




@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)


app.include_router(health_router)
app.include_router(incidents_router)