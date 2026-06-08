from datetime import datetime, timezone
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from psycopg import connect
from psycopg.types.json import Json

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()

_SCHEMA_READY = False


class GeometryExtractRequest(BaseModel):
    drawing_id: str
    revision_id: str | None = None
    entity_types: list[str] = Field(default_factory=list)


class GeometryFeature(BaseModel):
    id: str
    entity_type: str
    bounds: list[float]


class GeometryExtractResponse(BaseModel):
    tenant_id: str
    drawing_id: str
    extracted_at: str
    features: list[GeometryFeature]


def _tenant_uuid(tenant_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f'maxopenballoon:tenant:{tenant_id}')


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS geometry_extractions (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES tenants(id),
                    drawing_id UUID NOT NULL REFERENCES drawings(id),
                    revision_id UUID NULL REFERENCES revisions(id),
                    features JSONB NOT NULL,
                    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()

    _SCHEMA_READY = True


def _ensure_tenant(tenant_id: str) -> UUID:
    tenant_uuid = _tenant_uuid(tenant_id)
    with connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenants (id, name)
                VALUES (%s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (tenant_uuid, tenant_id),
            )
        conn.commit()
    return tenant_uuid


@router.get('/health/live')
def health_live() -> dict[str, str]:
    return {'status': 'alive', 'service': settings.service_name}


@router.get('/health/ready')
def health_ready() -> dict[str, str]:
    return {'status': 'ready', 'service': settings.service_name}


@router.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok', 'service': settings.service_name}


@router.get('/tenant-context')
def tenant_context(context: TenantContext = Depends(tenant_context_dependency)) -> dict[str, str | None]:
    return {'tenant_id': context.tenant_id, 'subject': context.subject, 'service': settings.service_name}


@router.post('/geometry/extract', response_model=GeometryExtractResponse)
def extract_geometry(
    request: GeometryExtractRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> GeometryExtractResponse:
    _ensure_schema()

    try:
        drawing_uuid = UUID(request.drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid drawing id') from exc

    revision_uuid: UUID | None = None
    if request.revision_id is not None:
        try:
            revision_uuid = UUID(request.revision_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='Invalid revision id') from exc

    tenant_uuid = _ensure_tenant(context.tenant_id)

    with connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM drawings
                WHERE id = %s AND tenant_id = %s
                """,
                (drawing_uuid, tenant_uuid),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail='Drawing not found')

            if revision_uuid is not None:
                cur.execute(
                    """
                    SELECT 1 FROM revisions
                    WHERE id = %s AND drawing_id = %s AND tenant_id = %s
                    """,
                    (revision_uuid, drawing_uuid, tenant_uuid),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail='Revision not found')

    entity_types = request.entity_types or ['line', 'circle', 'dimension']
    features = [
        GeometryFeature(
            id=str(uuid4()),
            entity_type=entity_type,
            bounds=[float(index), float(index), float(index + 10), float(index + 5)],
        )
        for index, entity_type in enumerate(entity_types, start=1)
    ]

    extracted_at = datetime.now(timezone.utc)

    with connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geometry_extractions (id, tenant_id, drawing_id, revision_id, features, extracted_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    tenant_uuid,
                    drawing_uuid,
                    revision_uuid,
                    Json([feature.model_dump() for feature in features]),
                    extracted_at,
                ),
            )
        conn.commit()

    return GeometryExtractResponse(
        tenant_id=context.tenant_id,
        drawing_id=request.drawing_id,
        extracted_at=extracted_at.isoformat(),
        features=features,
    )


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
