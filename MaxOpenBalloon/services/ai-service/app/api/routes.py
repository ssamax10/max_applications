import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel, Field
from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()

_SCHEMA_READY = False
LOGGER = logging.getLogger(__name__)
_PADDLE_OCR = None

DetectorMode = str
SUPPORTED_DETECTORS = {'heuristic', 'paddleocr_opencv', 'florence2', 'hybrid', 'dxf_vector', 'pdf_worker'}
BALLOON_TEXT_PATTERN = re.compile(r'^[A-Za-z]{0,3}[- ]?\d{1,4}[A-Za-z]?$')
PDF_RENDER_SCALE = 1.5


class BalloonSuggestionRequest(BaseModel):
    drawing_id: str
    max_suggestions: int | None = Field(default=None, ge=1, le=60)
    detector_mode: DetectorMode | None = None


class BalloonGeometry(BaseModel):
    x: float
    y: float
    size: float
    fill_color: str
    outline_color: str
    text_color: str
    font_family: str


class BalloonSuggestion(BaseModel):
    suggestion_id: str
    label: str
    confidence: float
    geometry: BalloonGeometry


class BalloonSuggestionResponse(BaseModel):
    tenant_id: str
    drawing_id: str
    generated_at: str
    suggestions: list[BalloonSuggestion]
    detector_used: str
    attempted_detectors: list[str]
    detector_diagnostics: dict[str, str]


class DrawingSource(BaseModel):
    source_uri: str
    source_format: str


class DetectorUnavailableError(RuntimeError):
    pass


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
                CREATE TABLE IF NOT EXISTS ai_balloon_suggestions (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES tenants(id),
                    drawing_id UUID NOT NULL REFERENCES drawings(id),
                    max_suggestions INTEGER NOT NULL,
                    suggestions JSONB NOT NULL,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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


def _create_minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _parse_minio_uri(source_uri: str) -> tuple[str, str]:
    if not source_uri.startswith('minio://'):
        raise DetectorUnavailableError(f'Unsupported source URI: {source_uri}')

    path = source_uri[len('minio://') :]
    bucket, separator, object_name = path.partition('/')
    if not separator or not bucket or not object_name:
        raise DetectorUnavailableError(f'Invalid minio URI: {source_uri}')

    return bucket, unquote(object_name)


def _read_source_bytes(source_uri: str) -> bytes:
    bucket, object_name = _parse_minio_uri(source_uri)
    client = _create_minio_client()

    try:
        response = client.get_object(bucket, object_name)
        payload = response.read()
        response.close()
        response.release_conn()
        return payload
    except S3Error as exc:
        raise DetectorUnavailableError('Failed to read source drawing from object storage') from exc


def _load_drawing_source(drawing_uuid: UUID, tenant_uuid: UUID) -> DrawingSource:
    with connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_uri, source_format
                FROM drawings
                WHERE id = %s AND tenant_id = %s
                """,
                (drawing_uuid, tenant_uuid),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail='Drawing not found')

    return DrawingSource(source_uri=row['source_uri'], source_format=row['source_format'])


def _normalize_label(raw: str, index: int) -> str:
    candidate = (raw or '').strip().upper().replace(' ', '')
    if BALLOON_TEXT_PATTERN.match(candidate):
        return candidate
    return str(index)


def _style_tokens(index: int) -> tuple[str, str, str, str, float]:
    fill_palette = ['#ffeddc', '#ffe0ea', '#fff0c2', '#dcf4eb', '#ddeeff']
    outline_palette = ['#d7651f', '#bf2d55', '#d3aa2f', '#049169', '#0f6a8a']
    text_palette = ['#fff4d8', '#fff1f5', '#2f2417', '#ffffff', '#eaf8ff']
    font_palette = ['Space Grotesk', 'IBM Plex Sans', 'Georgia', 'Arial', 'Verdana']
    size = float(18 + ((index - 1) % 2) * 2)
    return (
        fill_palette[(index - 1) % len(fill_palette)],
        outline_palette[(index - 1) % len(outline_palette)],
        text_palette[(index - 1) % len(text_palette)],
        font_palette[(index - 1) % len(font_palette)],
        size,
    )


def _build_suggestion(index: int, label: str, confidence: float, x: float, y: float) -> BalloonSuggestion:
    fill_color, outline_color, text_color, font_family, size = _style_tokens(index)
    return BalloonSuggestion(
        suggestion_id=str(uuid4()),
        label=_normalize_label(label, index),
        confidence=max(0.01, min(0.99, confidence)),
        geometry=BalloonGeometry(
            x=float(x),
            y=float(y),
            size=size,
            fill_color=fill_color,
            outline_color=outline_color,
            text_color=text_color,
            font_family=font_family,
        ),
    )


def _heuristic_suggestions(max_suggestions: int) -> list[BalloonSuggestion]:
    return [
        _build_suggestion(
            index=index,
            label=str(index),
            confidence=max(0.5, 1 - (index * 0.05)),
            x=float(140 + ((index - 1) % 6) * 130),
            y=float(110 + ((index - 1) // 6) * 80),
        )
        for index in range(1, max_suggestions + 1)
    ]


def _svg_bytes_to_cv2_image(svg_bytes: bytes):
    try:
        import cairosvg
        import cv2
        import numpy as np
    except Exception as exc:
        raise DetectorUnavailableError('cairosvg/opencv/numpy are unavailable for SVG rasterization') from exc

    png_bytes = cairosvg.svg2png(bytestring=svg_bytes)
    array = np.frombuffer(png_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise DetectorUnavailableError('Unable to rasterize SVG for OCR')
    return image


def _forward_headers(tenant_id: str, authorization: str | None) -> dict[str, str]:
    headers = {'Content-Type': 'application/json', 'X-Tenant-ID': tenant_id}
    if authorization:
        headers['Authorization'] = authorization
    return headers


def _translate_to_svg_uri(source: DrawingSource, tenant_id: str, authorization: str | None) -> str:
    endpoint = settings.dwg_translation_internal_url.rstrip('/') + '/translate/dwg'
    req = Request(
        endpoint,
        data=json.dumps({'source_uri': source.source_uri, 'target_format': 'SVG'}).encode('utf-8'),
        headers=_forward_headers(tenant_id, authorization),
        method='POST',
    )

    try:
        with urlopen(req, timeout=40) as response:
            body = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DetectorUnavailableError('DWG translation service failed to generate SVG preview') from exc

    output_uri = body.get('output_uri') if isinstance(body, dict) else None
    if not isinstance(output_uri, str) or not output_uri:
        raise DetectorUnavailableError('DWG translation did not return output_uri')
    return output_uri


def _translate_to_dxf_uri(source: DrawingSource, tenant_id: str, authorization: str | None) -> str:
    endpoint = settings.dwg_translation_internal_url.rstrip('/') + '/translate/dwg'
    req = Request(
        endpoint,
        data=json.dumps({'source_uri': source.source_uri, 'target_format': 'DXF'}).encode('utf-8'),
        headers=_forward_headers(tenant_id, authorization),
        method='POST',
    )

    try:
        with urlopen(req, timeout=40) as response:
            body = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DetectorUnavailableError('DWG translation service failed to generate DXF output') from exc

    output_uri = body.get('output_uri') if isinstance(body, dict) else None
    if not isinstance(output_uri, str) or not output_uri:
        raise DetectorUnavailableError('DWG translation did not return DXF output_uri')
    return output_uri


def _translate_to_pdf_uri(source: DrawingSource, tenant_id: str, authorization: str | None) -> str:
    endpoint = settings.dwg_translation_internal_url.rstrip('/') + '/translate/dwg'
    req = Request(
        endpoint,
        data=json.dumps({'source_uri': source.source_uri, 'target_format': 'PDF'}).encode('utf-8'),
        headers=_forward_headers(tenant_id, authorization),
        method='POST',
    )

    try:
        with urlopen(req, timeout=40) as response:
            body = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DetectorUnavailableError('DWG translation service failed to generate PDF output') from exc

    output_uri = body.get('output_uri') if isinstance(body, dict) else None
    if not isinstance(output_uri, str) or not output_uri:
        raise DetectorUnavailableError('DWG translation did not return PDF output_uri')
    return output_uri


def _dxf_vector_suggestions(
    source: DrawingSource,
    max_suggestions: int,
    tenant_id: str,
    authorization: str | None,
) -> list[BalloonSuggestion]:
    try:
        import ezdxf
    except Exception as exc:
        raise DetectorUnavailableError('ezdxf is unavailable') from exc

    suffix = Path(source.source_uri).suffix.lower()
    if suffix == '.dxf':
        dxf_bytes = _read_source_bytes(source.source_uri)
    elif suffix == '.dwg':
        dxf_uri = _translate_to_dxf_uri(source, tenant_id, authorization)
        req = Request(dxf_uri, headers={'X-Tenant-ID': tenant_id, **({'Authorization': authorization} if authorization else {})}, method='GET')
        try:
            with urlopen(req, timeout=40) as response:
                dxf_bytes = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise DetectorUnavailableError('Failed reading translated DXF output for vector detection') from exc
    else:
        raise DetectorUnavailableError('DXF vector detector only supports DWG/DXF sources')

    with TemporaryDirectory() as tmp_dir:
        dxf_path = Path(tmp_dir) / 'source.dxf'
        dxf_path.write_bytes(dxf_bytes)
        try:
            doc = ezdxf.readfile(dxf_path)
        except Exception as exc:
            raise DetectorUnavailableError('ezdxf failed to parse DXF') from exc

        msp = doc.modelspace()
        circles: list[tuple[float, float, float]] = []
        texts: list[tuple[str, float, float]] = []

        for entity in msp:
            kind = entity.dxftype()
            if kind == 'CIRCLE':
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                if radius >= 2.0:
                    circles.append((float(center.x), float(center.y), radius))
            elif kind in {'TEXT', 'MTEXT'}:
                raw_text = entity.plain_text() if hasattr(entity, 'plain_text') else str(getattr(entity.dxf, 'text', '') or '')
                label = raw_text.strip()
                if not label:
                    continue
                insert = entity.dxf.insert
                texts.append((label, float(insert.x), float(insert.y)))

        if not circles and not texts:
            return []

        candidates: list[tuple[str, float, float, float]] = []
        for cx, cy, radius in circles:
            matched_label = ''
            matched_score = 0.65
            for text, tx, ty in texts:
                if not BALLOON_TEXT_PATTERN.match(text.replace(' ', '').upper()):
                    continue
                distance = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
                if distance <= max(24.0, radius * 2.2):
                    matched_label = text
                    matched_score = 0.9
                    break
            if not matched_label:
                matched_label = str(len(candidates) + 1)
            candidates.append((matched_label, matched_score, cx, cy))

        if not candidates:
            for idx, (text, tx, ty) in enumerate(texts[:max_suggestions], start=1):
                candidates.append((text, 0.72, tx, ty))

        candidates.sort(key=lambda item: item[1], reverse=True)
        selected = candidates[:max_suggestions]
        return [
            _build_suggestion(index=i + 1, label=item[0], confidence=item[1], x=item[2], y=item[3])
            for i, item in enumerate(selected)
        ]


def _load_numpy_image(source: DrawingSource, tenant_id: str, authorization: str | None):
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        raise DetectorUnavailableError('opencv/numpy are unavailable') from exc

    source_bytes = _read_source_bytes(source.source_uri)
    suffix = Path(source.source_uri).suffix.lower()

    if suffix in {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}:
        array = np.frombuffer(source_bytes, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if image is None:
            raise DetectorUnavailableError('OpenCV could not decode the source image')
        return image

    if suffix == '.pdf':
        try:
            import pypdfium2 as pdfium
        except Exception as exc:
            raise DetectorUnavailableError('pypdfium2 is unavailable for PDF rasterization') from exc

        with TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / 'source.pdf'
            pdf_path.write_bytes(source_bytes)
            pdf = pdfium.PdfDocument(str(pdf_path))
            page = pdf[0]
            bitmap = page.render(scale=PDF_RENDER_SCALE)
            rendered = bitmap.to_numpy()
            if rendered is None:
                raise DetectorUnavailableError('Unable to rasterize PDF for OCR')
            return rendered[:, :, ::-1].copy()

    if suffix == '.svg':
        return _svg_bytes_to_cv2_image(source_bytes)

    if suffix in {'.dwg', '.dxf'}:
        svg_uri = _translate_to_svg_uri(source, tenant_id, authorization)
        headers = {'X-Tenant-ID': tenant_id}
        if authorization:
            headers['Authorization'] = authorization
        req = Request(svg_uri, headers=headers, method='GET')
        try:
            with urlopen(req, timeout=40) as response:
                svg_bytes = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise DetectorUnavailableError('Failed reading translated SVG output for OCR') from exc

        return _svg_bytes_to_cv2_image(svg_bytes)

    raise DetectorUnavailableError(f'Paddle first-pass does not support source format {suffix or source.source_format}')


def _estimate_budget_from_svg(source_uri: str) -> int:
    source_bytes = _read_source_bytes(source_uri)
    text = source_bytes.decode('utf-8', errors='ignore').lower()
    path_count = text.count('<path')
    circle_count = text.count('<circle') + text.count('<ellipse')
    text_count = text.count('<text')
    line_count = text.count('<line') + text.count('<polyline') + text.count('<polygon')

    weighted = (path_count // 30) + (line_count // 20) + (circle_count * 2) + text_count + 6
    return max(6, min(36, weighted))


def _estimate_suggestion_budget(
    source: DrawingSource,
    requested_max: int | None,
    tenant_id: str,
    authorization: str | None,
) -> int:
    if isinstance(requested_max, int):
        return max(1, min(60, requested_max))

    source_format = source.source_format.upper()

    if source_format == 'SVG':
        try:
            return _estimate_budget_from_svg(source.source_uri)
        except DetectorUnavailableError:
            return 10

    try:
        import cv2
    except Exception:
        cv2 = None

    if cv2 is not None:
        try:
            image = _load_numpy_image(source, tenant_id, authorization)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 80, 180)

            contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            significant_contours = sum(1 for contour in contours if cv2.contourArea(contour) >= 80)

            circles = cv2.HoughCircles(
                gray,
                cv2.HOUGH_GRADIENT,
                dp=1.2,
                minDist=20,
                param1=110,
                param2=24,
                minRadius=8,
                maxRadius=70,
            )
            circle_count = 0 if circles is None else len(circles[0])

            complexity = 6 + (significant_contours // 12) + (circle_count * 2)
            if source_format in {'DWG', 'DXF'}:
                complexity = max(14, complexity)
            if source_format == 'PDF':
                complexity += 2

            return max(6, min(40, complexity))
        except DetectorUnavailableError:
            pass
        except Exception:
            LOGGER.warning('Failed complexity estimation from rasterized source', exc_info=True)

    defaults = {
        'DWG': 16,
        'DXF': 14,
        'PDF': 12,
        'SVG': 10,
    }
    return defaults.get(source_format, 10)


def _paddle_opencv_suggestions(
    source: DrawingSource,
    max_suggestions: int,
    tenant_id: str,
    authorization: str | None,
) -> list[BalloonSuggestion]:
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        raise DetectorUnavailableError('paddleocr is unavailable') from exc

    try:
        import cv2
    except Exception as exc:
        raise DetectorUnavailableError('opencv-python-headless is unavailable') from exc

    image = _load_numpy_image(source, tenant_id, authorization)

    global _PADDLE_OCR
    if _PADDLE_OCR is None:
        _PADDLE_OCR = PaddleOCR(use_angle_cls=True, lang='en')

    result = _PADDLE_OCR.ocr(image, cls=True)
    lines = result[0] if result and result[0] else []

    circles: list[tuple[float, float, float]] = []
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 1.2)
        raw_circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=20,
            param1=110,
            param2=24,
            minRadius=8,
            maxRadius=70,
        )
        if raw_circles is not None:
            circles = [(float(c[0]), float(c[1]), float(c[2])) for c in raw_circles[0]]
    except Exception:
        circles = []

    candidates: list[tuple[str, float, float, float]] = []
    for entry in lines:
        if len(entry) < 2:
            continue
        box = entry[0]
        text_info = entry[1]
        text = str(text_info[0]).strip()
        confidence = float(text_info[1]) if len(text_info) > 1 else 0.0
        if confidence < 0.35:
            continue
        if not BALLOON_TEXT_PATTERN.match(text.replace(' ', '').upper()):
            continue

        points = [(float(point[0]), float(point[1])) for point in box]
        center_x = sum(point[0] for point in points) / len(points)
        center_y = sum(point[1] for point in points) / len(points)

        if circles:
            nearest = min(circles, key=lambda item: ((item[0] - center_x) ** 2 + (item[1] - center_y) ** 2))
            distance = ((nearest[0] - center_x) ** 2 + (nearest[1] - center_y) ** 2) ** 0.5
            tolerance = max(16.0, nearest[2] * 1.8)
            if distance <= tolerance:
                center_x, center_y = nearest[0], nearest[1]
                confidence = min(0.99, confidence + 0.1)
            else:
                continue

        candidates.append((text, confidence, center_x, center_y))

    if not candidates:
        return []

    candidates.sort(key=lambda item: item[1], reverse=True)
    selected = candidates[:max_suggestions]
    return [
        _build_suggestion(index=i + 1, label=item[0], confidence=item[1], x=item[2], y=item[3])
        for i, item in enumerate(selected)
    ]


def _refine_suggestions(detector: str, suggestions: list[BalloonSuggestion], max_suggestions: int) -> list[BalloonSuggestion]:
    if not suggestions:
        return []

    effective_limit = max_suggestions
    if detector == 'heuristic':
        effective_limit = min(max_suggestions, 24)

    min_confidence = 0.55 if detector in {'paddleocr_opencv', 'florence2', 'hybrid'} else 0.5

    ordered = sorted(suggestions, key=lambda entry: entry.confidence, reverse=True)
    refined: list[BalloonSuggestion] = []
    seen_labels: set[str] = set()

    for suggestion in ordered:
        if suggestion.confidence < min_confidence:
            continue

        normalized_label = suggestion.label.strip().upper().replace(' ', '')
        if normalized_label in seen_labels:
            continue

        duplicate_by_position = any(
            abs(existing.geometry.x - suggestion.geometry.x) <= 12
            and abs(existing.geometry.y - suggestion.geometry.y) <= 12
            for existing in refined
        )
        if duplicate_by_position:
            continue

        refined.append(suggestion)
        seen_labels.add(normalized_label)
        if len(refined) >= effective_limit:
            break

    return refined


def _renumber_suggestions(suggestions: list[BalloonSuggestion]) -> list[BalloonSuggestion]:
    return [
        suggestion.model_copy(update={'label': str(index)})
        for index, suggestion in enumerate(suggestions, start=1)
    ]


def _florence2_suggestions(source: DrawingSource, max_suggestions: int) -> list[BalloonSuggestion]:
    endpoint = settings.florence2_endpoint.strip()
    if not endpoint:
        raise DetectorUnavailableError('florence2 endpoint is not configured')

    payload = {
        'source_uri': source.source_uri,
        'source_format': source.source_format,
        'max_suggestions': max_suggestions,
    }

    req = Request(
        endpoint,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    try:
        with urlopen(req, timeout=settings.florence2_timeout_seconds) as response:
            body = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        raise DetectorUnavailableError('florence2 endpoint call failed') from exc

    raw_suggestions = body.get('suggestions') if isinstance(body, dict) else None
    if not isinstance(raw_suggestions, list):
        return []

    suggestions: list[BalloonSuggestion] = []
    for index, item in enumerate(raw_suggestions[:max_suggestions], start=1):
        if not isinstance(item, dict):
            continue
        geometry = item.get('geometry') if isinstance(item.get('geometry'), dict) else {}
        x = geometry.get('x', item.get('x', 120 + index * 20))
        y = geometry.get('y', item.get('y', 120 + index * 18))
        confidence = float(item.get('confidence', 0.75))
        suggestions.append(
            _build_suggestion(
                index=index,
                label=str(item.get('label', str(index))),
                confidence=confidence,
                x=float(x),
                y=float(y),
            )
        )
    return suggestions


def _pdf_worker_suggestions(
    source: DrawingSource,
    max_suggestions: int,
    tenant_id: str,
    authorization: str | None,
) -> list[BalloonSuggestion]:
    suffix = Path(source.source_uri).suffix.lower()

    endpoint = settings.pdf_worker_internal_url.rstrip('/')
    if not endpoint:
        raise DetectorUnavailableError('pdf_worker_internal_url is not configured')

    if suffix == '.pdf':
        payload = _read_source_bytes(source.source_uri)
    elif suffix in {'.dwg', '.dxf'}:
        pdf_uri = _translate_to_pdf_uri(source, tenant_id, authorization)
        headers = {'X-Tenant-ID': tenant_id}
        if authorization:
            headers['Authorization'] = authorization
        req = Request(pdf_uri, headers=headers, method='GET')
        try:
            with urlopen(req, timeout=40) as response:
                payload = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise DetectorUnavailableError('Failed reading translated PDF output for pdf_worker') from exc
    else:
        raise DetectorUnavailableError(f'pdf_worker does not support source format {suffix or source.source_format}')

    request_url = f"{endpoint}/extract?max_suggestions={max_suggestions}"
    headers = {'Content-Type': 'application/pdf', 'X-Tenant-ID': tenant_id}
    if authorization:
        headers['Authorization'] = authorization

    req = Request(
        request_url,
        data=payload,
        headers=headers,
        method='POST',
    )

    try:
        with urlopen(req, timeout=settings.pdf_worker_timeout_seconds) as response:
            body = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        raise DetectorUnavailableError(f'pdf_worker extraction call failed: {exc.__class__.__name__}') from exc

    raw_suggestions = body.get('suggestions') if isinstance(body, dict) else None
    if not isinstance(raw_suggestions, list):
        raise DetectorUnavailableError('pdf_worker response missing suggestions array')

    suggestions: list[BalloonSuggestion] = []
    for index, item in enumerate(raw_suggestions[:max_suggestions], start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get('text', '')).strip() or str(index)
        confidence = float(item.get('confidence', 0.75))
        x = float(item.get('x', 120 + index * 18))
        y = float(item.get('y', 120 + index * 16))

        suggestions.append(
            _build_suggestion(
                index=index,
                label=text,
                confidence=confidence,
                x=x,
                y=y,
            )
        )

    return suggestions


def _hybrid_suggestions(
    source: DrawingSource,
    max_suggestions: int,
    tenant_id: str,
    authorization: str | None,
) -> list[BalloonSuggestion]:
    pooled: list[BalloonSuggestion] = []
    detectors = ['paddleocr_opencv', 'heuristic']
    if _is_florence_configured():
        detectors.append('florence2')

    for detector in detectors:
        try:
            pooled.extend(_run_detector(detector, source, max_suggestions, tenant_id, authorization))
        except DetectorUnavailableError:
            continue

    if not pooled:
        return []

    pooled.sort(key=lambda suggestion: suggestion.confidence, reverse=True)
    deduped: list[BalloonSuggestion] = []
    for suggestion in pooled:
        duplicate = any(
            abs(existing.geometry.x - suggestion.geometry.x) <= 12
            and abs(existing.geometry.y - suggestion.geometry.y) <= 12
            for existing in deduped
        )
        if not duplicate:
            deduped.append(suggestion)
        if len(deduped) >= max_suggestions:
            break
    return deduped


def _run_detector(
    detector: str,
    source: DrawingSource,
    max_suggestions: int,
    tenant_id: str,
    authorization: str | None,
) -> list[BalloonSuggestion]:
    if detector == 'dxf_vector':
        return _dxf_vector_suggestions(source, max_suggestions, tenant_id, authorization)
    if detector == 'heuristic':
        return _heuristic_suggestions(max_suggestions)
    if detector == 'paddleocr_opencv':
        return _paddle_opencv_suggestions(source, max_suggestions, tenant_id, authorization)
    if detector == 'florence2':
        return _florence2_suggestions(source, max_suggestions)
    if detector == 'pdf_worker':
        return _pdf_worker_suggestions(source, max_suggestions, tenant_id, authorization)
    if detector == 'hybrid':
        return _hybrid_suggestions(source, max_suggestions, tenant_id, authorization)
    raise DetectorUnavailableError(f'Unknown detector mode: {detector}')


def _is_florence_configured() -> bool:
    return bool(settings.florence2_endpoint.strip())


def _detector_plan(requested_mode: str | None, source_format: str) -> list[str]:
    if requested_mode:
        if requested_mode not in SUPPORTED_DETECTORS:
            raise HTTPException(status_code=400, detail=f'Unsupported detector_mode: {requested_mode}')
        if requested_mode == 'paddleocr_opencv':
            plan = ['paddleocr_opencv', 'pdf_worker', 'dxf_vector', 'florence2']
            return [detector for detector in plan if detector != 'florence2' or _is_florence_configured()]
        if requested_mode == 'florence2':
            if not _is_florence_configured():
                raise HTTPException(status_code=400, detail='florence2 detector requested but FLORENCE2_ENDPOINT is not configured')
            return ['florence2', 'pdf_worker', 'dxf_vector', 'paddleocr_opencv']
        if requested_mode == 'pdf_worker':
            plan = ['pdf_worker', 'paddleocr_opencv', 'florence2', 'heuristic']
            return [detector for detector in plan if detector != 'florence2' or _is_florence_configured()]
        if requested_mode == 'heuristic':
            return ['heuristic']
        if requested_mode == 'dxf_vector':
            plan = ['dxf_vector', 'pdf_worker', 'florence2', 'paddleocr_opencv']
            return [detector for detector in plan if detector != 'florence2' or _is_florence_configured()]
        if requested_mode == 'hybrid':
            return ['hybrid']
        return [requested_mode]

    configured = [token.strip() for token in settings.detector_order.split(',') if token.strip()]
    ordered = [token for token in configured if token in SUPPORTED_DETECTORS]
    if not _is_florence_configured():
        ordered = [token for token in ordered if token != 'florence2']
    if ordered:
        return ordered

    format_upper = source_format.upper()
    if format_upper in {'DWG', 'DXF'}:
        return ['dxf_vector', 'hybrid', 'heuristic'] if not _is_florence_configured() else ['dxf_vector', 'florence2', 'hybrid', 'heuristic']
    if format_upper == 'PDF':
        return ['pdf_worker', 'hybrid', 'heuristic'] if not _is_florence_configured() else ['pdf_worker', 'florence2', 'hybrid', 'heuristic']
    if format_upper == 'SVG':
        return ['hybrid', 'heuristic'] if not _is_florence_configured() else ['florence2', 'hybrid', 'heuristic']

    return ['hybrid', 'heuristic', 'paddleocr_opencv'] if not _is_florence_configured() else ['florence2', 'hybrid', 'heuristic', 'paddleocr_opencv']


def _empty_detector_reason(detector: str) -> str:
    reasons = {
        'paddleocr_opencv': 'OCR produced no confident balloon labels near detected circles',
        'dxf_vector': 'DXF geometry/text scan found no balloon-like circle and label pairs',
        'florence2': 'Florence2 returned no suggestions for this drawing',
        'pdf_worker': 'PDF worker returned no valid dimension candidates',
        'hybrid': 'Hybrid detector merged output had no surviving unique suggestions',
        'heuristic': 'Heuristic detector produced no valid suggestions after filtering',
    }
    return reasons.get(detector, 'Detector returned no usable suggestions')


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


@router.post('/ai/suggest-balloons', response_model=BalloonSuggestionResponse)
def suggest_balloons(
    request: BalloonSuggestionRequest,
    authorization: str | None = Header(default=None, alias='Authorization'),
    context: TenantContext = Depends(tenant_context_dependency),
) -> BalloonSuggestionResponse:
    _ensure_schema()

    try:
        drawing_uuid = UUID(request.drawing_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid drawing id') from exc

    tenant_uuid = _ensure_tenant(context.tenant_id)

    drawing_source = _load_drawing_source(drawing_uuid, tenant_uuid)
    suggestion_budget = _estimate_suggestion_budget(drawing_source, request.max_suggestions, context.tenant_id, authorization)
    detector_plan = _detector_plan(request.detector_mode, drawing_source.source_format)

    suggestions: list[BalloonSuggestion] = []
    attempted_detectors: list[str] = []
    detector_diagnostics: dict[str, str] = {}
    detector_used = 'none'

    for detector in detector_plan:
        attempted_detectors.append(detector)
        try:
            suggestions = _run_detector(detector, drawing_source, suggestion_budget, context.tenant_id, authorization)
        except DetectorUnavailableError as exc:
            reason = str(exc) or 'Detector unavailable'
            detector_diagnostics[detector] = reason
            LOGGER.warning('Detector unavailable', extra={'detector': detector, 'reason': reason})
            continue

        suggestions = _refine_suggestions(detector, suggestions, suggestion_budget)

        if suggestions:
            suggestions = _renumber_suggestions(suggestions)
            detector_used = detector
            detector_diagnostics[detector] = f'Success: {len(suggestions)} suggestions'
            break

        detector_diagnostics[detector] = _empty_detector_reason(detector)

    if not suggestions:
        fallback_budget = max(1, min(suggestion_budget, 12))
        suggestions = _refine_suggestions('heuristic', _heuristic_suggestions(fallback_budget), fallback_budget)
        if suggestions:
            suggestions = _renumber_suggestions(suggestions)
            detector_used = 'heuristic_fallback'
            attempted_detectors.append('heuristic_fallback')
            detector_diagnostics['heuristic_fallback'] = f'Success: {len(suggestions)} fallback suggestions'
            LOGGER.warning(
                'All configured detectors produced no results; using heuristic fallback suggestions',
                extra={'drawing_id': request.drawing_id, 'tenant_id': context.tenant_id, 'budget': fallback_budget},
            )
        else:
            raise HTTPException(status_code=422, detail='No balloon suggestions generated by any configured detector')

    generated_at = datetime.now(timezone.utc)

    with connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_balloon_suggestions (id, tenant_id, drawing_id, max_suggestions, suggestions, generated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    tenant_uuid,
                    drawing_uuid,
                    suggestion_budget,
                    Json([suggestion.model_dump() for suggestion in suggestions]),
                    generated_at,
                ),
            )
        conn.commit()

    return BalloonSuggestionResponse(
        tenant_id=context.tenant_id,
        drawing_id=request.drawing_id,
        generated_at=generated_at.isoformat(),
        suggestions=suggestions,
        detector_used=detector_used,
        attempted_detectors=attempted_detectors,
        detector_diagnostics=detector_diagnostics,
    )


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
