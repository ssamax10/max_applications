from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import configure_logging
from app.core.settings import settings
from app.telemetry.tracing import configure_tracing

configure_logging()
configure_tracing(settings.service_name)
app = FastAPI(title=settings.service_name, version="1.0.0")
app.include_router(router)
