from fastapi import APIRouter, Header, HTTPException

from app.core.settings import settings
from app.telemetry.metrics import requests_total

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    requests_total.labels(service=settings.service_name, route="health").inc()
    return {"status": "ok", "service": settings.service_name}


@router.get("/tenant-context")
def tenant_context(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID")) -> dict[str, str]:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="Missing tenant header")
    requests_total.labels(service=settings.service_name, route="tenant-context").inc()
    return {"tenant_id": x_tenant_id, "service": settings.service_name}
