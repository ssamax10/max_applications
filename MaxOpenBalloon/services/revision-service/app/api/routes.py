from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()


class RevisionCreateRequest(BaseModel):
    drawing_id: str
    change_summary: str


class RevisionRecord(BaseModel):
    id: str
    tenant_id: str
    drawing_id: str
    revision_number: int
    change_summary: str
    created_at: str


class RevisionDiffResponse(BaseModel):
    tenant_id: str
    drawing_id: str
    left_revision: int
    right_revision: int
    changed_objects: int


_SCHEMA_READY = False


def _tenant_uuid(tenant_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f'maxopenballoon:tenant:{tenant_id}')


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE revisions ADD COLUMN IF NOT EXISTS change_summary TEXT NOT NULL DEFAULT ''")
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


def _to_record(row: dict, tenant_id: str) -> RevisionRecord:
    return RevisionRecord(
        id=str(row['id']),
        tenant_id=tenant_id,
        drawing_id=str(row['drawing_id']),
        revision_number=row['revision_number'],
        change_summary=row['change_summary'],
        created_at=row['created_at'].isoformat(),
    )


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


@router.post('/revisions', response_model=RevisionRecord)
def create_revision(
    request: RevisionCreateRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> RevisionRecord:
    _ensure_schema()

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

            cur.execute(
                """
                SELECT COALESCE(MAX(revision_number), 0) AS current_max
                FROM revisions
                WHERE tenant_id = %s AND drawing_id = %s
                """,
                (tenant_uuid, drawing_uuid),
            )
            current_max = int(cur.fetchone()['current_max'])
            next_revision = current_max + 1
            revision_id = uuid4()

            cur.execute(
                """
                INSERT INTO revisions (id, tenant_id, drawing_id, revision_number, change_summary)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, drawing_id, revision_number, change_summary, created_at
                """,
                (revision_id, tenant_uuid, drawing_uuid, next_revision, request.change_summary),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(status_code=500, detail='Failed to create revision')

    return _to_record(row, context.tenant_id)


@router.get('/drawings/{drawing_id}/revisions', response_model=list[RevisionRecord])
def list_revisions(
    drawing_id: str,
    context: TenantContext = Depends(tenant_context_dependency),
) -> list[RevisionRecord]:
    _ensure_schema()

    try:
        drawing_uuid = UUID(drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid drawing id') from exc

    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, drawing_id, revision_number, change_summary, created_at
                FROM revisions
                WHERE tenant_id = %s AND drawing_id = %s
                ORDER BY revision_number ASC
                """,
                (tenant_uuid, drawing_uuid),
            )
            rows = cur.fetchall()

    return [_to_record(row, context.tenant_id) for row in rows]


@router.get('/revisions/diff', response_model=RevisionDiffResponse)
def revision_diff(
    drawing_id: str = Query(...),
    left_revision: int = Query(..., ge=1),
    right_revision: int = Query(..., ge=1),
    context: TenantContext = Depends(tenant_context_dependency),
) -> RevisionDiffResponse:
    changed_objects = abs(right_revision - left_revision) * 7
    return RevisionDiffResponse(
        tenant_id=context.tenant_id,
        drawing_id=drawing_id,
        left_revision=left_revision,
        right_revision=right_revision,
        changed_objects=changed_objects,
    )


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
