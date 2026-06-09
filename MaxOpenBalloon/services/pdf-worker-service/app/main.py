from fastapi import FastAPI

from app.api.routes import router
from app.core.settings import settings

app = FastAPI(title=settings.service_name, version='1.0.0')
app.include_router(router)
