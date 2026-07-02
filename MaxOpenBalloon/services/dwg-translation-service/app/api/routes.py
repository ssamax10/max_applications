import os
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
from app.core.logging import logger
from app.core.settings import settings
from app.services.aps_service import convert_dwg_to_pdf_via_aps
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

    svg_script_exists = Path(settings.qcad_script_path).exists()
    dxf_script_exists = Path('/app/app/scripts/qcad_dwg_to_dxf.js').exists()
    return executable_exists and (svg_script_exists or dxf_script_exists)


def _get_page_from_dxf(document: "ezdxf.drawing.Drawing") -> "layout.Page":
    from ezdxf.addons.drawing import layout

    margin = 10.0
    try:
        extents = document.modelspace().pen_and_paper_extents()
        if extents is not None and len(extents) == 4:
            x1, y1, x2, y2 = extents
            width = abs(x2 - x1)
            height = abs(y2 - y1)
            if width > 0 and height > 0:
                pw = max(width + 2 * margin, 148.0)
                ph = max(height + 2 * margin, 210.0)
                return layout.Page(pw, ph)
    except Exception:
        pass

    return layout.Page(297.0, 210.0)


def _svg_has_content(svg_bytes: bytes) -> bool:
    text = svg_bytes.decode('utf-8', errors='replace')
    drawing_tags = (
        '<path', '<line', '<circle', '<rect', '<ellipse', '<polyline',
        '<polygon', '<text', '<image', '<use',
    )
    for tag in drawing_tags:
        if tag in text:
            return True
    return False


def _render_dxf_to_svg_bytes(dxf_path: Path) -> bytes:
    try:
        import ezdxf
        from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
    except ModuleNotFoundError as exc:
        logger.error('ezdxf not available for DXF->SVG rendering')
        raise HTTPException(status_code=500, detail='ezdxf is not available') from exc

    try:
        dxf_size = dxf_path.stat().st_size
        document = ezdxf.readfile(dxf_path)
        msp = document.modelspace()
        entity_count = len(list(msp))
        logger.info('dxf_loaded', dxf_path=str(dxf_path), dxf_size_bytes=dxf_size, entity_count=entity_count)
        if entity_count == 0:
            raise RuntimeError(f'DXF has zero entities in modelspace (size={dxf_size} bytes)')
        context = RenderContext(document)
        backend = svg.SVGBackend()
        frontend = Frontend(context, backend)
        frontend.draw_layout(document.modelspace(), finalize=True)
        page = _get_page_from_dxf(document)
        svg_content = backend.get_string(page).encode('utf-8')
        logger.info('dxf_rendered', dxf_path=str(dxf_path), dxf_size_bytes=dxf_size,
                    page_width=page.width, page_height=page.height, svg_size_bytes=len(svg_content))
        if not _svg_has_content(svg_content):
            raise RuntimeError(f'ezdxf produced an SVG with no drawing elements (page={page.width}x{page.height})')
        svg_text = svg_content.decode('utf-8', errors='replace')
        svg_text = _normalize_svg_for_render(svg_text)
        return svg_text.encode('utf-8')
    except Exception as exc:
        logger.error('dxf_to_svg_failed', dxf_path=str(dxf_path), error=str(exc))
        raise HTTPException(status_code=500, detail=f'ezdxf failed to render DXF as SVG: {exc}') from exc


def _run_qcad_to_dxf(source_path: Path, output_dxf_path: Path) -> bytes:
    command_parts = shlex.split(settings.qcad_cmd)
    if not command_parts:
        raise HTTPException(status_code=500, detail='QCAD command is not configured')

    dxf_script_path = Path('/app/app/scripts/qcad_dwg_to_dxf.js')
    if not dxf_script_path.exists():
        raise HTTPException(status_code=500, detail='QCAD DXF script is missing')

    command = [*command_parts, '-no-gui', '-autostart', str(dxf_script_path), '--', str(source_path), str(output_dxf_path)]
    logger.info('qcad_dxf_start', source=str(source_path), output=str(output_dxf_path))

    wrapper_cmd = settings.qcad_wrapper_cmd.strip()
    try:
        if wrapper_cmd:
            wrapper_parts = shlex.split(wrapper_cmd)
            full_command = [*wrapper_parts, *command]
            result = subprocess.run(full_command, capture_output=True, text=True,
                                    timeout=settings.conversion_timeout_seconds, check=False)
        else:
            qcad_env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=settings.conversion_timeout_seconds, check=False, env=qcad_env)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail='QCAD executable was not found') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='QCAD DXF conversion timed out') from exc

    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        raise HTTPException(status_code=500,
                            detail=f'QCAD DWG to DXF failed. rc={result.returncode} stderr={stderr[:300]} stdout={stdout[:300]}')
    if not output_dxf_path.exists() or output_dxf_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail='QCAD finished without producing DXF output')

    logger.info('qcad_dxf_success', source=str(source_path), dxf_size_bytes=output_dxf_path.stat().st_size)
    return output_dxf_path.read_bytes()


def _run_oda_dwg2svg(source_path: Path, output_svg_path: Path) -> bytes:
    dwg2svg_cmd = settings.qcad_dwg2svg_cmd.strip()
    if not dwg2svg_cmd:
        raise HTTPException(status_code=500, detail='ODA dwg2svg command is not configured')
    if not Path(dwg2svg_cmd).exists():
        raise HTTPException(status_code=500, detail=f'ODA dwg2svg not found at {dwg2svg_cmd}')

    wrapper_cmd = settings.qcad_wrapper_cmd.strip()
    command = [dwg2svg_cmd, '-f', '-o', str(output_svg_path), str(source_path)]
    full_command = [*shlex.split(wrapper_cmd), *command] if wrapper_cmd else command

    logger.info('oda_dwg2svg_start', source=str(source_path), output=str(output_svg_path),
                cmd=' '.join(full_command))

    try:
        result = subprocess.run(full_command, capture_output=True, text=True,
                                timeout=settings.conversion_timeout_seconds, check=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f'ODA dwg2svg executable not found: {dwg2svg_cmd}') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='ODA dwg2svg conversion timed out') from exc

    if result.returncode != 0 or not output_svg_path.exists() or output_svg_path.stat().st_size == 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        raise HTTPException(status_code=500,
                            detail=f'ODA dwg2svg failed. rc={result.returncode} stderr={stderr[:300]} stdout={stdout[:300]}')

    svg_size = output_svg_path.stat().st_size
    logger.info('oda_dwg2svg_success', source=str(source_path), svg_size_bytes=svg_size)
    svg_bytes = output_svg_path.read_bytes()
    svg_text = svg_bytes.decode('utf-8', errors='replace')
    svg_text = _normalize_svg_for_render(svg_text)
    return svg_text.encode('utf-8')


def _run_oda_dwg2pdf(source_path: Path, output_pdf_path: Path) -> bytes:
    dwg2pdf_cmd = '/opt/qcad/dwg2pdf'
    if not Path(dwg2pdf_cmd).exists():
        raise HTTPException(status_code=500, detail=f'ODA dwg2pdf not found at {dwg2pdf_cmd}')

    wrapper_cmd = settings.qcad_wrapper_cmd.strip()
    command = [
        dwg2pdf_cmd,
        '-f',
        '-a',
        '-paper=A3',
        '-landscape',
        '-colormode=truecolor',
        '-ltscale=0.5',
        '-min-lineweight=0.15',
        '-max-lineweight=1.0',
        '-fs', 'romans.shx=Arial',
        '-fs', 'simplex.shx=Arial',
        '-o', str(output_pdf_path),
        str(source_path),
    ]
    full_command = [*shlex.split(wrapper_cmd), *command] if wrapper_cmd else command

    logger.info('oda_dwg2pdf_start', source=str(source_path), output=str(output_pdf_path),
                cmd=' '.join(full_command))

    try:
        result = subprocess.run(full_command, capture_output=True, text=True,
                                timeout=settings.conversion_timeout_seconds, check=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f'ODA dwg2pdf executable not found: {dwg2pdf_cmd}') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='ODA dwg2pdf conversion timed out') from exc

    if result.returncode != 0 or not output_pdf_path.exists() or output_pdf_path.stat().st_size == 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        raise HTTPException(status_code=500,
                            detail=f'ODA dwg2pdf failed. rc={result.returncode} stderr={stderr[:300]} stdout={stdout[:300]}')

    logger.info('oda_dwg2pdf_success', source=str(source_path), pdf_size_bytes=output_pdf_path.stat().st_size)
    return output_pdf_path.read_bytes()


def _run_qcad_to_svg_bytes(source_path: Path, output_svg_path: Path) -> bytes:
    command_parts = shlex.split(settings.qcad_cmd)
    if not command_parts:
        raise HTTPException(status_code=500, detail='QCAD command is not configured')

    script_path = Path(settings.qcad_script_path)
    if not script_path.exists():
        raise HTTPException(status_code=500, detail='QCAD script is missing')

    command = [*command_parts, '-no-gui', '-autostart', str(script_path), '--', str(source_path), str(output_svg_path)]
    logger.info('qcad_start', source=str(source_path), output=str(output_svg_path), cmd=' '.join(command))

    wrapper_cmd = settings.qcad_wrapper_cmd.strip()
    try:
        if wrapper_cmd:
            wrapper_parts = shlex.split(wrapper_cmd)
            full_command = [*wrapper_parts, *command]
            result = subprocess.run(full_command, capture_output=True, text=True,
                                    timeout=settings.conversion_timeout_seconds, check=False)
        else:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=settings.conversion_timeout_seconds, check=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail='QCAD executable was not found') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='QCAD DWG conversion timed out') from exc

    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        raise HTTPException(status_code=500,
                            detail=f'QCAD DWG conversion failed. rc={result.returncode} stderr={stderr[:300]} stdout={stdout[:300]}')
    if not output_svg_path.exists() or output_svg_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail='QCAD finished without producing SVG output')

    logger.info('qcad_svg_success', source=str(source_path), svg_size_bytes=output_svg_path.stat().st_size)
    svg_bytes = output_svg_path.read_bytes()
    svg_text = svg_bytes.decode('utf-8', errors='replace')
    svg_text = _normalize_svg_for_render(svg_text)
    return svg_text.encode('utf-8')


def _run_qcad_to_pdf(source_path: Path, output_pdf_path: Path) -> bytes:
    temp_dir = source_path.parent
    svg_intermediate_path = temp_dir / 'qcad_intermediate.svg'
    svg_bytes = _run_qcad_to_svg_bytes(source_path, svg_intermediate_path)
    return _svg_bytes_to_pdf(svg_bytes)


def _extract_svg_bytes(text: str) -> bytes | None:
    data = (text or '').strip()
    if not data:
        return None
    svg_start = data.find('<svg')
    if svg_start == -1:
        return None
    xml_start = data.find('<?xml')
    payload = data[xml_start:] if xml_start != -1 and xml_start < svg_start else data[svg_start:]
    if '<svg' not in payload:
        return None
    import re as _re
    payload = _re.sub(r'\s+width\s*=\s*"[^"]*"', '', payload, count=1)
    payload = _re.sub(r'\s+height\s*=\s*"[^"]*"', '', payload, count=1)
    vb_match = _re.search(r'\bviewBox\s*=\s*"([^"]+)"', payload)
    if vb_match:
        parts = vb_match.group(1).strip().split()
        if len(parts) == 4:
            try:
                _, _, vb_w, vb_h = map(float, parts)
                max_dim = max(vb_w, vb_h)
                if max_dim > 0:
                    scale = min(2000.0 / max_dim, 1.0)
                    w = int(vb_w * scale)
                    h = int(vb_h * scale)
                    payload = payload.replace('<svg', f'<svg width="{w}" height="{h}"', 1)
            except ValueError:
                pass
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
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=settings.conversion_timeout_seconds, check=False)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500,
                                detail='LibreDWG dwg2SVG command is not available') from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail='DWG conversion timed out') from exc

        if result.returncode == 0 and output_svg_path.exists() and output_svg_path.stat().st_size > 0:
            raw_bytes = output_svg_path.read_bytes()
            normalized = _normalize_svg_for_render(raw_bytes.decode('utf-8', errors='replace'))
            return normalized.encode('utf-8')
        if result.returncode == 0:
            stdout_svg = _extract_svg_bytes(result.stdout or '')
            if stdout_svg is not None:
                normalized = _normalize_svg_for_render(stdout_svg.decode('utf-8', errors='replace'))
                return normalized.encode('utf-8')
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        logger.warning('dwg2svg_attempt_failed', cmd=' '.join(command), rc=result.returncode, stderr=stderr[:200])
        errors.append(f"cmd={' '.join(command)} rc={result.returncode} stderr={stderr[:200]} stdout={stdout[:200]}")
    raise HTTPException(status_code=500, detail=f'LibreDWG failed to produce SVG output. Last attempt: {errors[-1]}')


def _run_libredwg_to_dxf(source_path: Path, output_dxf_path: Path) -> bytes:
    try:
        result = subprocess.run(['dwgread', '-O', 'DXF', '-o', str(output_dxf_path), str(source_path)],
                                capture_output=True, text=True,
                                timeout=settings.conversion_timeout_seconds, check=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500,
                            detail='LibreDWG dwgread command is not available') from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='DWG to DXF conversion timed out') from exc

    if result.returncode == 0 and output_dxf_path.exists() and output_dxf_path.stat().st_size > 0:
        return output_dxf_path.read_bytes()
    stderr = (result.stderr or '').strip()
    stdout = (result.stdout or '').strip()
    raise HTTPException(status_code=500,
                        detail=f'LibreDWG dwgread -O DXF failed. rc={result.returncode} stderr={stderr[:300]} stdout={stdout[:300]}')


def _compute_content_bbox(svg_text: str) -> tuple[float, float, float, float] | None:
    """Scan SVG path 'd' attributes and compute the bounding box of all drawing content
    in SVG coordinate space (after Y-flip transform).
    
    Returns (min_x, min_y, max_x, max_y) or None if no paths found.
    """
    import re
    import math
    
    path_matches = list(re.finditer(r'\bd\s*=\s*"([^"]+)"', svg_text))
    if not path_matches:
        return None
    
    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')
    
    for match in path_matches:
        d_val = match.group(1)
        nums = re.findall(r'[-]?\d+\.?\d*', d_val)
        coords = [float(n) for n in nums]
        for i in range(0, len(coords), 2):
            if i + 1 < len(coords):
                x, y = coords[i], coords[i+1]
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    
    if math.isinf(min_x):
        return None
    
    return (min_x, min_y, max_x, max_y)


def _normalize_svg_for_render(svg_text: str) -> str:
    """Normalize SVG for browser/Konva rendering.

    1. Auto-crops viewBox to tightly fit drawing content
    2. Injects visible CSS defaults for stroke/fill
    3. Strips width/height with mm/inches units
    4. Injects pixel-based width/height from viewBox
    5. Fixes negative viewBox origins
    6. Increases minimum visible stroke width
    """
    import re

    if 'maxopenballoon-svg-style' in svg_text:
        return svg_text

    # Parse viewBox
    viewbox_match = re.search(r'\bviewBox\s*=\s*"([^"]+)"', svg_text)
    vb_x = vb_y = vb_w = vb_h = 0.0
    if viewbox_match:
        parts = viewbox_match.group(1).strip().split()
        if len(parts) == 4:
            try:
                vb_x, vb_y, vb_w, vb_h = map(float, parts)
            except ValueError:
                pass

    new_w = vb_w
    new_h = vb_h

    # Strip any fill:none from the root <svg> style
    svg_text = re.sub(r'\s+style\s*=\s*"[^"]*fill\s*:\s*none[^"]*"', '', svg_text, count=1)
    svg_text = re.sub(r'\s+style\s*=\s*"[^"]*"', '', svg_text, count=1)

    # Fix ODA's scale(1,-1) Y-flip transform.
    # SVG applies transforms right-to-left:
    #   translate(0,H) scale(1,-1)  =  flip Y, then shift down by H
    svg_text = re.sub(
        r'transform\s*=\s*"scale\(1,-1\)"',
        f'transform="translate(0,{int(vb_h)}) scale(1,-1)"',
        svg_text,
        count=1,
    )

    # CSS: force visible strokes and default fill for text paths.
    style_block = (
        '<style id="maxopenballoon-svg-style">'
        '*[stroke] {'
        'stroke-width: max(8, inherit) !important;'
        '}'
        'path:not([fill]),polygon:not([fill]) {'
        'fill: black;'
        '}'
        '</style>'
    )
    svg_open_match = re.search(r'(<svg[^>]*>)', svg_text)
    if svg_open_match:
        svg_text = svg_text.replace(svg_open_match.group(1), svg_open_match.group(1) + style_block, 1)
    elif '</defs>' in svg_text:
        svg_text = svg_text.replace('</defs>', f'</defs>{style_block}')
    else:
        svg_text = svg_text + style_block

    # Strip confusing width/height (e.g. "1189mm", "841mm")
    svg_text = re.sub(r'\s+width\s*=\s*"[^"]*"', '', svg_text, count=1)
    svg_text = re.sub(r'\s+height\s*=\s*"[^"]*"', '', svg_text, count=1)

    # Inject pixel-based width/height from viewBox
    if viewbox_match and vb_w > 0 and vb_h > 0:
        svg_text = svg_text.replace('<svg', f'<svg width="{new_w}" height="{new_h}"', 1)

    # Fix negative viewBox origin (ODA uses Y-down in viewBox but Y-up in content)
    need_vb_fix = viewbox_match and vb_y < 0.0
    if need_vb_fix:
        vb_y = 0.0
        svg_text = re.sub(
            r'\bviewBox\s*=\s*"[^"]+"',
            f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}"',
            svg_text,
            count=1,
        )

    return svg_text


def _svg_bytes_to_pdf(svg_bytes: bytes) -> bytes:
    if not _svg_has_content(svg_bytes):
        raise HTTPException(status_code=500,
                            detail='SVG content is empty — no drawing elements found. '
                                   'The DWG file may be in an unsupported format or corrupted.')
    svg_text = svg_bytes.decode('utf-8', errors='replace')
    svg_text = _normalize_svg_for_render(svg_text)
    try:
        import fitz
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False, mode='w') as tmp:
            tmp.write(svg_text)
            tmp_path = tmp.name
        try:
            svg_doc = fitz.open(tmp_path)
            pdf_bytes = svg_doc.convert_to_pdf()
            svg_doc.close()
            logger.info('fitz_svg_to_pdf', svg_size_bytes=len(svg_bytes), pdf_size_bytes=len(pdf_bytes))
            return pdf_bytes
        except Exception as fitz_exc:
            logger.warning('fitz_svg_open_failed', error=str(fitz_exc))
            try:
                import cairosvg as cairo_svg
                png_bytes = cairo_svg.svg2png(bytestring=svg_text.encode('utf-8'), dpi=300, scale=3.0)
                try:
                    from PIL import Image
                    import io
                    rgba = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
                    white_bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
                    composited = Image.alpha_composite(white_bg, rgba).convert('RGB')
                    buf = io.BytesIO()
                    composited.save(buf, format='PNG')
                    png_bytes = buf.getvalue()
                except ImportError:
                    pass
                import struct
                if png_bytes.startswith(b'\x89PNG'):
                    png_w = struct.unpack('>I', png_bytes[16:20])[0]
                    png_h = struct.unpack('>I', png_bytes[20:24])[0]
                else:
                    png_w, png_h = 0, 0
                page_w = max(100, min(2000, png_w / 4))
                page_h = max(100, min(2000, png_h / 4))
                pdf_doc = fitz.open()
                page = pdf_doc.new_page(width=page_w, height=page_h)
                page.insert_image(page.rect, stream=png_bytes, keep_proportion=True)
                pdf_bytes = pdf_doc.write()
                pdf_doc.close()
                logger.info('cairosvg_fallback_pdf', pdf_size_bytes=len(pdf_bytes))
                return pdf_bytes
            except Exception as cairo_exc:
                raise HTTPException(status_code=500,
                                    detail=f'All SVG-to-PDF methods failed. Last error: {cairo_exc}') from cairo_exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500,
                            detail='PyMuPDF (fitz) is not available. Cannot convert SVG to PDF.') from exc


def _convert_dwg_to_svg_via_dxf(source_path: Path) -> bytes:
    temp_dir = source_path.parent
    dxf_path = temp_dir / 'intermediate.dxf'
    if _qcad_converter_available():
        try:
            _run_qcad_to_dxf(source_path, dxf_path)
            return _render_dxf_to_svg_bytes(dxf_path)
        except HTTPException:
            logger.warning('qcad_to_dxf_failed, falling back to libredwg dwgread')
    _run_libredwg_to_dxf(source_path, dxf_path)
    return _render_dxf_to_svg_bytes(dxf_path)


def _convert_dwg_to_pdf(source_path: Path, output_svg_path: Path, output_pdf_path: Path) -> bytes:
    engine = settings.dwg_conversion_engine.strip().lower()
    if engine == 'libredwg':
        try:
            svg_bytes = _run_libredwg_to_svg(source_path, output_svg_path)
            return _svg_bytes_to_pdf(svg_bytes)
        except HTTPException:
            svg_bytes = _convert_dwg_to_svg_via_dxf(source_path)
            return _svg_bytes_to_pdf(svg_bytes)
    if engine == 'qcad-only':
        return _run_qcad_to_pdf(source_path, output_pdf_path)
    if engine in {'qcad', 'auto'}:
        # Primary: Autodesk Platform Services (APS) — production-quality PDF
        try:
            return convert_dwg_to_pdf_via_aps(source_path, output_pdf_path)
        except Exception as exc:
            logger.warning('aps_dwg2pdf_failed, falling back to ODA dwg2pdf', error=str(exc))
        # Fallback: ODA dwg2pdf with proper flags
        try:
            return _run_oda_dwg2pdf(source_path, output_pdf_path)
        except HTTPException:
            logger.warning('oda_dwg2pdf_failed, falling back to qcad dwg2svg->pymupdf')
        # Fallback: ODA dwg2svg → PyMuPDF
        if _qcad_converter_available():
            try:
                return _run_qcad_to_pdf(source_path, output_pdf_path)
            except HTTPException:
                pass
        try:
            svg_bytes = _convert_dwg_to_svg_via_dxf(source_path)
            return _svg_bytes_to_pdf(svg_bytes)
        except HTTPException:
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
                dxf_bytes = _run_libredwg_to_dxf(source_path, pdf_output_path)
                return dxf_bytes, 'application/dxf'
            raise HTTPException(status_code=415, detail=f'Unsupported source format for DXF export: {suffix}')

        if target_format == 'SVG':
            if suffix == '.svg':
                return source_bytes, 'image/svg+xml'
            if suffix == '.dxf':
                return _render_dxf_to_svg_bytes(source_path), 'image/svg+xml'
            if suffix == '.dwg':
                try:
                    svg_bytes = _run_oda_dwg2svg(source_path, svg_output_path)
                    return svg_bytes, 'image/svg+xml'
                except HTTPException:
                    logger.warning('oda_dwg2svg_failed, falling back to DXF intermediate path')
                try:
                    svg_bytes = _convert_dwg_to_svg_via_dxf(source_path)
                    return svg_bytes, 'image/svg+xml'
                except HTTPException:
                    svg_bytes = _run_libredwg_to_svg(source_path, svg_output_path)
                    return svg_bytes, 'image/svg+xml'
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
                """INSERT INTO translation_jobs (id, tenant_id, source_uri, target_format, status, output_uri)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, source_uri, target_format, status, output_uri, submitted_at""",
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
                """SELECT id, source_uri, target_format FROM translation_jobs WHERE id = %s""",
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
                """SELECT id, source_uri, target_format, status, output_uri, submitted_at
                   FROM translation_jobs WHERE id = %s AND tenant_id = %s""",
                (job_uuid, tenant_uuid),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='Translation job not found')
    return _to_job(row, context.tenant_id)


@router.get('/translate/debug/svg-head')
def debug_svg_head() -> Response:
    """Return the first 3KB of the most recently converted SVG for debugging."""
    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT source_uri, target_format
                   FROM translation_jobs
                   WHERE target_format = 'SVG'
                   ORDER BY submitted_at DESC LIMIT 1"""
            )
            row = cur.fetchone()
    if row is None:
        return Response(content='No SVG conversions found', media_type='text/plain')
    payload, _ = _convert_source_to_output(row['source_uri'], row['target_format'])
    head = payload[:3000]
    return Response(content=head, media_type='text/plain; charset=utf-8')


@router.get('/translate/debug/raw-pdf/{job_id}')
def debug_raw_pdf(job_id: str) -> Response:
    """Download the raw PDF file directly for inspection."""
    _ensure_schema()
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return Response(content='Invalid job id', media_type='text/plain', status_code=400)
    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT source_uri, target_format FROM translation_jobs WHERE id = %s""",
                (job_uuid,),
            )
            row = cur.fetchone()
    if row is None:
        return Response(content='Job not found', media_type='text/plain', status_code=404)
    
    suffix = (Path(row['source_uri']).suffix or '.dwg').lower()
    import tempfile as _tf
    with _tf.TemporaryDirectory(prefix='dwg-debug-') as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / f'source{suffix}'
        pdf_output_path = temp_path / 'output.pdf'
        _download_source_to_temp(row['source_uri'], source_path)
        
        dwg2pdf_cmd = settings.qcad_dwg2pdf_cmd.strip()
        wrapper_cmd = settings.qcad_wrapper_cmd.strip()
        command = [dwg2pdf_cmd, '-f', '-o', str(pdf_output_path), str(source_path)]
        full_command = [*shlex.split(wrapper_cmd), *command] if wrapper_cmd else command
        
        result = subprocess.run(full_command, capture_output=True, text=True, timeout=settings.conversion_timeout_seconds, check=False)
        
        if not pdf_output_path.exists():
            return Response(content=f'ODA dwg2pdf failed. rc={result.returncode} stderr={result.stderr[:500]}', media_type='text/plain')
        
        raw_pdf = pdf_output_path.read_bytes()
        return Response(content=raw_pdf, media_type='application/pdf')


@router.get('/translate/debug/raw-svg/{job_id}')
def debug_raw_svg(job_id: str) -> Response:
    """Return the raw (un-normalized) SVG first 3KB from the temp file for comparison."""
    _ensure_schema()
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return Response(content='Invalid job id', media_type='text/plain', status_code=400)
    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT source_uri, target_format FROM translation_jobs WHERE id = %s""",
                (job_uuid,),
            )
            row = cur.fetchone()
    if row is None:
        return Response(content='Job not found', media_type='text/plain', status_code=404)
    
    suffix = (Path(row['source_uri']).suffix or '.dwg').lower()
    import tempfile as _tf
    with _tf.TemporaryDirectory(prefix='dwg-debug-') as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / f'source{suffix}'
        svg_output_path = temp_path / 'output.svg'
        _download_source_to_temp(row['source_uri'], source_path)
        
        dwg2svg_cmd = settings.qcad_dwg2svg_cmd.strip()
        wrapper_cmd = settings.qcad_wrapper_cmd.strip()
        command = [dwg2svg_cmd, '-f', '-o', str(svg_output_path), str(source_path)]
        full_command = [*shlex.split(wrapper_cmd), *command] if wrapper_cmd else command
        
        result = subprocess.run(full_command, capture_output=True, text=True, timeout=settings.conversion_timeout_seconds, check=False)
        
        if not svg_output_path.exists():
            return Response(content=f'ODA dwg2svg failed. rc={result.returncode} stderr={result.stderr[:500]}', media_type='text/plain')
        
        raw_svg = svg_output_path.read_bytes()
        head = raw_svg[:3000]
        info = (
            f"Raw SVG size: {len(raw_svg)} bytes\n"
            f"ODA return code: {result.returncode}\n"
            f"ODA stderr: {result.stderr[:500]}\n"
            f"--- SVG HEAD (first 3000 bytes) ---\n"
            f"{head.decode('utf-8', errors='replace')}"
        )
        return Response(content=info, media_type='text/plain; charset=utf-8')


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())