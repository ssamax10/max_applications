from datetime import datetime, timezone
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()


class BalloonCreateRequest(BaseModel):
    drawing_id: str
    label: str
    geometry: dict[str, object]


class BalloonRecord(BaseModel):
    id: str
    tenant_id: str
    drawing_id: str
    label: str
    geometry: dict[str, object]
    created_at: str


class BalloonUpdateRequest(BaseModel):
    label: str | None = None
    geometry: dict[str, object] | None = None


def _tenant_uuid(tenant_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f'maxopenballoon:tenant:{tenant_id}')


def _to_record(row: dict, tenant_id: str) -> BalloonRecord:
    return BalloonRecord(
        id=str(row['id']),
        tenant_id=tenant_id,
        drawing_id=str(row['drawing_id']),
        label=row['label'],
        geometry=row['geometry'],
        created_at=row['created_at'].isoformat(),
    )


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


@router.post('/balloons', response_model=BalloonRecord)
def create_balloon(
    request: BalloonCreateRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> BalloonRecord:
    try:
        drawing_uuid = UUID(request.drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid drawing id') from exc

    tenant_uuid = _ensure_tenant(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
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

            balloon_id = uuid4()
            cur.execute(
                """
                INSERT INTO balloons (id, tenant_id, drawing_id, label, geometry)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, drawing_id, label, geometry, created_at
                """,
                (balloon_id, tenant_uuid, drawing_uuid, request.label, Json(request.geometry)),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(status_code=500, detail='Failed to create balloon')

    return _to_record(row, context.tenant_id)


@router.get('/drawings/{drawing_id}/balloons', response_model=list[BalloonRecord])
def list_balloons_for_drawing(
    drawing_id: str,
    context: TenantContext = Depends(tenant_context_dependency),
) -> list[BalloonRecord]:
    try:
        drawing_uuid = UUID(drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid drawing id') from exc

    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, drawing_id, label, geometry, created_at
                FROM balloons
                WHERE tenant_id = %s AND drawing_id = %s
                ORDER BY created_at ASC
                """,
                (tenant_uuid, drawing_uuid),
            )
            rows = cur.fetchall()

    return [_to_record(row, context.tenant_id) for row in rows]


@router.patch('/balloons/{balloon_id}', response_model=BalloonRecord)
def update_balloon(
    balloon_id: str,
    request: BalloonUpdateRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> BalloonRecord:
    if request.label is None and request.geometry is None:
        raise HTTPException(status_code=400, detail='No update fields provided')

    try:
        balloon_uuid = UUID(balloon_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid balloon id') from exc

    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, drawing_id, label, geometry, created_at
                FROM balloons
                WHERE id = %s AND tenant_id = %s
                """,
                (balloon_uuid, tenant_uuid),
            )
            existing = cur.fetchone()

            if existing is None:
                raise HTTPException(status_code=404, detail='Balloon not found')

            next_label = request.label if request.label is not None else existing['label']
            next_geometry = request.geometry if request.geometry is not None else existing['geometry']

            cur.execute(
                """
                UPDATE balloons
                SET label = %s, geometry = %s
                WHERE id = %s AND tenant_id = %s
                RETURNING id, drawing_id, label, geometry, created_at
                """,
                (next_label, Json(next_geometry), balloon_uuid, tenant_uuid),
            )
            row = cur.fetchone()

        conn.commit()

    if row is None:
        raise HTTPException(status_code=500, detail='Failed to update balloon')

    return _to_record(row, context.tenant_id)


@router.delete('/balloons/{balloon_id}', status_code=204)
def delete_balloon(
    balloon_id: str,
    context: TenantContext = Depends(tenant_context_dependency),
) -> Response:
    try:
        balloon_uuid = UUID(balloon_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid balloon id') from exc

    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM balloons
                WHERE id = %s AND tenant_id = %s
                RETURNING id
                """,
                (balloon_uuid, tenant_uuid),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(status_code=404, detail='Balloon not found')

    return Response(status_code=204)


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
