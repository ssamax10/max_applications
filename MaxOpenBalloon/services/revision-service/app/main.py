import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.logging import configure_logging
from app.core.settings import settings
from app.telemetry.metrics import RequestTimer, request_latency_seconds, requests_total
from app.telemetry.tracing import configure_tracing

configure_logging()
configure_tracing(settings.service_name, settings.otel_exporter_otlp_endpoint if hasattr(settings, 'otel_exporter_otlp_endpoint') else None)
app = FastAPI(title=settings.service_name, version='1.1.0')


def _cors_origins() -> list[str]:
    raw = os.getenv('CORS_ALLOW_ORIGINS', 'http://localhost:5173')
    return [origin.strip() for origin in raw.split(',') if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def instrument_requests(request: Request, call_next):
    timer = RequestTimer()
    response = await call_next(request)

    route = request.url.path
    method = request.method
    status_code = str(response.status_code)

    requests_total.labels(
        service=settings.service_name,
        route=route,
        method=method,
        status_code=status_code,
    ).inc()
    request_latency_seconds.labels(
        service=settings.service_name,
        route=route,
        method=method,
    ).observe(timer.elapsed_seconds())

    return response


app.include_router(router)
