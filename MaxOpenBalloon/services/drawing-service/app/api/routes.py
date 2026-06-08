import os
from datetime import datetime, timezone
from io import BytesIO
from typing import Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()


class DrawingCreateRequest(BaseModel):
    source_uri: str
    source_format: Literal['DWG', 'DXF', 'PDF', 'SVG']


class DrawingRecord(BaseModel):
    id: str
    tenant_id: str
    source_uri: str
    source_format: str
    created_at: str


def _infer_source_format(filename: str, content_type: str | None = None) -> Literal['DWG', 'DXF', 'PDF', 'SVG']:
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'dxf':
        return 'DXF'
    if ext == 'pdf':
        return 'PDF'
    if ext == 'svg':
        return 'SVG'

    normalized_content_type = (content_type or '').lower()
    if normalized_content_type in {'application/dxf', 'image/vnd.dxf'}:
        return 'DXF'
    if normalized_content_type == 'application/pdf':
        return 'PDF'
    if normalized_content_type in {'image/svg+xml', 'text/svg'}:
        return 'SVG'

    return 'DWG'


def _sanitize_filename(filename: str | None) -> str:
    if not filename:
        return 'drawing.dwg'

    normalized = os.path.basename(filename).replace(' ', '_')
    return normalized or 'drawing.dwg'


def _create_minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_drawings_bucket(client: Minio) -> None:
    if not client.bucket_exists(settings.drawings_bucket):
        client.make_bucket(settings.drawings_bucket)


def _tenant_uuid(tenant_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f'maxopenballoon:tenant:{tenant_id}')


def _to_record(row: dict, tenant_id: str) -> DrawingRecord:
    return DrawingRecord(
        id=str(row['id']),
        tenant_id=tenant_id,
        source_uri=row['source_uri'],
        source_format=row['source_format'],
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


def _insert_drawing_record(tenant_id: str, source_uri: str, source_format: str) -> DrawingRecord:
    tenant_uuid = _ensure_tenant(tenant_id)
    drawing_id = uuid4()

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO drawings (id, tenant_id, source_uri, source_format)
                VALUES (%s, %s, %s, %s)
                RETURNING id, source_uri, source_format, created_at
                """,
                (drawing_id, tenant_uuid, source_uri, source_format),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(status_code=500, detail='Failed to create drawing')

    return _to_record(row, tenant_id)


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


@router.post('/drawings', response_model=DrawingRecord)
def create_drawing(
    request: DrawingCreateRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> DrawingRecord:
    return _insert_drawing_record(context.tenant_id, request.source_uri, request.source_format)


@router.post('/drawings/upload', response_model=DrawingRecord)
async def upload_drawing(
    file: UploadFile = File(...),
    context: TenantContext = Depends(tenant_context_dependency),
) -> DrawingRecord:
    filename = _sanitize_filename(file.filename)
    source_format = _infer_source_format(filename, file.content_type)
    content = await file.read()

    if not content:
        raise HTTPException(status_code=400, detail='Uploaded file is empty')

    object_name = f"{context.tenant_id}/{uuid4()}-{filename}"
    client = _create_minio_client()

    try:
        _ensure_drawings_bucket(client)
        client.put_object(
            settings.drawings_bucket,
            object_name,
            BytesIO(content),
            length=len(content),
            content_type=file.content_type or 'application/octet-stream',
        )
    except S3Error as exc:
        raise HTTPException(status_code=500, detail='Failed to store drawing file') from exc

    source_uri = f"minio://{settings.drawings_bucket}/{object_name}"
    return _insert_drawing_record(context.tenant_id, source_uri, source_format)


@router.get('/drawings', response_model=list[DrawingRecord])
def list_drawings(context: TenantContext = Depends(tenant_context_dependency)) -> list[DrawingRecord]:
    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_uri, source_format, created_at
                FROM drawings
                WHERE tenant_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_uuid,),
            )
            rows = cur.fetchall()

    return [_to_record(row, context.tenant_id) for row in rows]


@router.get('/drawings/{drawing_id}', response_model=DrawingRecord)
def get_drawing(drawing_id: str, context: TenantContext = Depends(tenant_context_dependency)) -> DrawingRecord:
    try:
        drawing_uuid = UUID(drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid drawing id') from exc

    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_uri, source_format, created_at
                FROM drawings
                WHERE id = %s AND tenant_id = %s
                """,
                (drawing_uuid, tenant_uuid),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail='Drawing not found')

    return _to_record(row, context.tenant_id)


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
