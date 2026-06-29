import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.settings import settings

app = FastAPI(title=settings.service_name, version='1.0.0')


def _cors_origins() -> list[str]:
    raw = os.getenv('CORS_ALLOW_ORIGINS', 'http://localhost:5173')
    return [origin.strip() for origin in raw.split(',') if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=['*'],
    allow_headers=['*'],
)


app.include_router(router)
