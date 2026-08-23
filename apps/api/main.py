from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.config import settings
from apps.api.database import init_db, AsyncSessionLocal
from apps.core.seed import seed_default_services

# Routers
from apps.routes.health import router as ping_router
from apps.routes.incidents import router as incidents_router
from apps.routes.services import router as services_router
from apps.routes.events import router as events_router
from apps.routes.failures import router as failures_router
from apps.routes.health_check import router as system_health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables
    await init_db()
    
    # Seed default services for the Target System
    async with AsyncSessionLocal() as session:
        await seed_default_services(session)
        
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)


app.include_router(ping_router)
app.include_router(incidents_router)
app.include_router(services_router)
app.include_router(events_router)
app.include_router(failures_router)
app.include_router(system_health_router)