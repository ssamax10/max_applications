import shlex
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Literal
from urllib.parse import unquote
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Response
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()


class DwgTranslateRequest(BaseModel):
    source_uri: str
    target_format: Literal['SVG', 'PDF', 'DXF']


class TranslationJob(BaseModel):
    job_id: str
    tenant_id: str
    source_uri: str
    target_format: str
    status: Literal['queued', 'completed']
    output_uri: str
    submitted_at: str


_SCHEMA_READY = False


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
                CREATE TABLE IF NOT EXISTS translation_jobs (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES tenants(id),
                    source_uri TEXT NOT NULL,
                    target_format TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_uri TEXT NOT NULL,
                    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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


def _to_job(row: dict, tenant_id: str) -> TranslationJob:
    return TranslationJob(
        job_id=str(row['id']),
        tenant_id=tenant_id,
        source_uri=row['source_uri'],
        target_format=row['target_format'],
        status=row['status'],
        output_uri=row['output_uri'],
        submitted_at=row['submitted_at'].isoformat(),
    )


def _create_minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _parse_minio_uri(source_uri: str) -> tuple[str, str]:
    if not source_uri.startswith('minio://'):
        raise HTTPException(status_code=400, detail=f'Unsupported source URI: {source_uri}')

    path = source_uri[len('minio://') :]
    bucket, separator, object_name = path.partition('/')
    if not separator or not bucket or not object_name:
        raise HTTPException(status_code=400, detail=f'Invalid minio URI: {source_uri}')

    return bucket, unquote(object_name)


def _download_source_to_temp(source_uri: str, target_path: Path) -> None:
    bucket, object_name = _parse_minio_uri(source_uri)
    client = _create_minio_client()

    try:
        response = client.get_object(bucket, object_name)
        with target_path.open('wb') as handle:
            for chunk in response.stream(32 * 1024):
                handle.write(chunk)
    except S3Error as exc:
        raise HTTPException(status_code=500, detail='Failed to read DWG source from object storage') from exc


def _qcad_converter_available() -> bool:
    command = settings.qcad_cmd.strip()
    if not command:
        return False

    parts = shlex.split(command)
    if not parts:
        return False

    executable = parts[0]
    if '/' in executable:
        executable_exists = Path(executable).exists()
    else:
        executable_exists = shutil.which(executable) is not None

    script_exists = Path(settings.qcad_script_path).exists()
    return executable_exists and script_exists


def _render_dxf_to_svg_bytes(dxf_path: Path) -> bytes:
    try:
        import ezdxf
        from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail='ezdxf is not available in the dwg-translation-service container',
        ) from exc

    try:
        document = ezdxf.readfile(dxf_path)
        context = RenderContext(document)
        backend = svg.SVGBackend()
        frontend = Frontend(context, backend)
        frontend.draw_layout(document.modelspace(), finalize=True)
        return backend.get_string(layout.Page(0, 0)).encode('utf-8')
    except Exception as exc:  # pragma: no cover - renderer exceptions depend on source content
        raise HTTPException(status_code=500, detail=f'ezdxf failed to render DXF as SVG: {exc}') from exc


def _run_qcad_to_pdf(source_path: Path, output_pdf_path: Path) -> bytes:
    command_parts = shlex.split(settings.qcad_cmd)
    if not command_parts:
        raise HTTPException(status_code=500, detail='QCAD command is not configured')

    script_path = Path(settings.qcad_script_path)
    if not script_path.exists():
        raise HTTPException(status_code=500, detail='QCAD script is missing in dwg-translation-service image')

    command = [
        *command_parts,
        '-no-gui',
        '-autostart',
        str(script_path),
        '--',
        str(source_path),
        str(output_pdf_path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.conversion_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail='QCAD executable was not found') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='QCAD DWG conversion timed out') from exc

    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        raise HTTPException(
            status_code=500,
            detail=(
                'QCAD DWG conversion failed. '
                f'rc={result.returncode} stderr={stderr[:300]} stdout={stdout[:300]}'
            ),
        )

    if not output_pdf_path.exists() or output_pdf_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail='QCAD finished without producing PDF output')

    return output_pdf_path.read_bytes()


def _extract_svg_bytes(text: str) -> bytes | None:
    data = (text or '').strip()
    if not data:
        return None

    # Some LibreDWG builds print SVG to stdout instead of writing output files.
    svg_start = data.find('<svg')
    if svg_start == -1:
        return None

    xml_start = data.find('<?xml')
    payload = data[xml_start:] if xml_start != -1 and xml_start < svg_start else data[svg_start:]
    if '<svg' not in payload:
        return None
    return payload.encode('utf-8')


def _run_libredwg_to_svg(source_path: Path, output_svg_path: Path) -> bytes:
    attempts = [
        ['dwg2SVG', '-o', str(output_svg_path), str(source_path)],
        ['dwg2SVG', str(source_path), '-o', str(output_svg_path)],
        ['dwg2SVG', str(source_path), str(output_svg_path)],
    ]

    errors: list[str] = []
    for command in attempts:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.conversion_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail='LibreDWG dwg2SVG command is not available in dwg-translation-service container',
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail='DWG conversion timed out') from exc

        if result.returncode == 0 and output_svg_path.exists() and output_svg_path.stat().st_size > 0:
            return output_svg_path.read_bytes()

        if result.returncode == 0:
            stdout_svg = _extract_svg_bytes(result.stdout or '')
            if stdout_svg is not None:
                return stdout_svg

        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        errors.append(
            f"cmd={' '.join(command)} rc={result.returncode} stderr={stderr[:200]} stdout={stdout[:200]}"
        )

    detail = 'LibreDWG failed to produce SVG output'
    if errors:
        detail = f"{detail}. Last attempt: {errors[-1]}"
    raise HTTPException(status_code=500, detail=detail)


def _svg_bytes_to_pdf(svg_bytes: bytes) -> bytes:
    try:
        import cairosvg as cairo_svg
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail='CairoSVG is not available in the dwg-translation-service container',
        ) from exc
    return cairo_svg.svg2pdf(bytestring=svg_bytes)


def _convert_dwg_to_pdf(source_path: Path, output_svg_path: Path, output_pdf_path: Path) -> bytes:
    engine = settings.dwg_conversion_engine.strip().lower()

    if engine == 'libredwg':
        svg_bytes = _run_libredwg_to_svg(source_path, output_svg_path)
        return _svg_bytes_to_pdf(svg_bytes)

    if engine == 'qcad-only':
        return _run_qcad_to_pdf(source_path, output_pdf_path)

    if engine in {'qcad', 'auto'}:
        if _qcad_converter_available():
            try:
                return _run_qcad_to_pdf(source_path, output_pdf_path)
            except HTTPException:
                # QCAD trial can fail for license/runtime reasons; fallback keeps workflow usable.
                pass

        svg_bytes = _run_libredwg_to_svg(source_path, output_svg_path)
        return _svg_bytes_to_pdf(svg_bytes)

    raise HTTPException(status_code=500, detail=f'Unsupported DWG conversion engine: {engine}')


def _convert_source_to_output(source_uri: str, target_format: str) -> tuple[bytes, str]:
    suffix = (Path(source_uri).suffix or '.dwg').lower()

    with tempfile.TemporaryDirectory(prefix='dwg-convert-') as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / f'source{suffix}'
        svg_output_path = temp_path / 'output.svg'
        pdf_output_path = temp_path / 'output.pdf'

        _download_source_to_temp(source_uri, source_path)
        source_bytes = source_path.read_bytes()

        if target_format == 'PDF':
            if suffix == '.pdf':
                return source_bytes, 'application/pdf'
            if suffix == '.svg':
                return _svg_bytes_to_pdf(source_bytes), 'application/pdf'
            if suffix == '.dxf':
                svg_bytes = _render_dxf_to_svg_bytes(source_path)
                return _svg_bytes_to_pdf(svg_bytes), 'application/pdf'
            if suffix == '.dwg':
                pdf_bytes = _convert_dwg_to_pdf(source_path, svg_output_path, pdf_output_path)
                return pdf_bytes, 'application/pdf'
            raise HTTPException(status_code=415, detail=f'Unsupported source format for PDF export: {suffix}')

        if target_format == 'DXF':
            if suffix == '.dxf':
                return source_bytes, 'application/dxf'
            if suffix == '.dwg':
                raise HTTPException(status_code=415, detail='DWG to DXF is disabled in QCAD/LibreDWG mode')
            raise HTTPException(status_code=415, detail=f'Unsupported source format for DXF export: {suffix}')

        if target_format == 'SVG':
            if suffix == '.svg':
                return source_bytes, 'image/svg+xml'
            if suffix == '.dxf':
                return _render_dxf_to_svg_bytes(source_path), 'image/svg+xml'
            if suffix == '.dwg':
                return _run_libredwg_to_svg(source_path, svg_output_path), 'image/svg+xml'
            raise HTTPException(status_code=415, detail=f'Unsupported source format for SVG export: {suffix}')

    raise HTTPException(status_code=415, detail='Unsupported translation output format')


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


@router.post('/translate/dwg', response_model=TranslationJob)
def submit_translation(
    request: DwgTranslateRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> TranslationJob:
    _ensure_schema()

    tenant_uuid = _ensure_tenant(context.tenant_id)
    job_id = uuid4()
    output_uri = f'{settings.public_base_url}/translate/dwg/{job_id}/output'

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO translation_jobs (id, tenant_id, source_uri, target_format, status, output_uri)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, source_uri, target_format, status, output_uri, submitted_at
                """,
                (job_id, tenant_uuid, request.source_uri, request.target_format, 'completed', output_uri),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(status_code=500, detail='Failed to create translation job')

    return _to_job(row, context.tenant_id)


@router.get('/translate/dwg/{job_id}/output')
def get_translation_output(job_id: str) -> Response:
    _ensure_schema()

    try:
        job_uuid = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid job id') from exc

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_uri, target_format
                FROM translation_jobs
                WHERE id = %s
                """,
                (job_uuid,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail='Translation job output not found')

    payload, media_type = _convert_source_to_output(row['source_uri'], row['target_format'])
    return Response(content=payload, media_type=media_type)


@router.get('/translate/dwg/{job_id}', response_model=TranslationJob)
def get_translation_job(
    job_id: str,
    context: TenantContext = Depends(tenant_context_dependency),
) -> TranslationJob:
    _ensure_schema()

    try:
        job_uuid = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid job id') from exc

    tenant_uuid = _tenant_uuid(context.tenant_id)

    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_uri, target_format, status, output_uri, submitted_at
                FROM translation_jobs
                WHERE id = %s AND tenant_id = %s
                """,
                (job_uuid, tenant_uuid),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail='Translation job not found')

    return _to_job(row, context.tenant_id)


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
