from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.config import settings
from apps.api.database import init_db, get_session_factory
from apps.core.seed import seed_default_services
from apps.core.logging import setup_logging
from apps.core.telemetry import setup_telemetry, shutdown_telemetry
from apps.api.middleware import RequestContextMiddleware

# Routers
from apps.routes.health import router as ping_router
from apps.routes.incidents import router as incidents_router
from apps.routes.services import router as services_router
from apps.routes.events import router as events_router
from apps.routes.failures import router as failures_router
from apps.routes.health_check import router as system_health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Setup structured logging
    setup_logging(settings.LOG_LEVEL)
    
    # 1. Telemetry FIRST — before any engine creation
    setup_telemetry(app)

    # 2. Now safe to init DB (engine created here, already instrumented)
    await init_db()
    
    # 3. Seed default services for the Target System
    async with get_session_factory()() as session:
        await seed_default_services(session)
        
    yield
    
    # Cleanup
    shutdown_telemetry()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)

# Add Middleware
app.add_middleware(RequestContextMiddleware)

# Routers
app.include_router(ping_router)
app.include_router(incidents_router)
app.include_router(services_router)
app.include_router(events_router)
app.include_router(failures_router)
app.include_router(system_health_router)