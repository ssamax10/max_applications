import math
import re
from typing import Any

import fitz
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.settings import settings

router = APIRouter()

DIMENSION_REGEX = re.compile(
    r"^(?:"
    r"(?:M\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?)|"
    r"(?:[ODR]?\s*\d+(?:\.\d+)?(?:\s*(?:x|X)\s*\d+(?:\.\d+)?)?)|"
    r"(?:\d+(?:\.\d+)?\s*(?:mm|cm|m|in|\")?)"
    r")(?:\s*(?:\+/?-?|\u00b1)\s*\d+(?:\.\d+)?)?$",
    re.IGNORECASE,
)

_PADDLE_OCR = None


class ExtractSuggestion(BaseModel):
    text: str
    confidence: float
    x: float
    y: float
    bbox: list[float]
    stage: str


class ExtractResponse(BaseModel):
    mode: str
    profile: dict[str, Any]
    diagnostics: dict[str, str]
    suggestions: list[ExtractSuggestion]


def _is_title_or_margin_zone(x0: float, y0: float, x1: float, y1: float, width: float, height: float) -> bool:
    if x0 < width * 0.02 or y0 < height * 0.02 or x1 > width * 0.98 or y1 > height * 0.98:
        return True

    # Typical engineering title block zone.
    if x0 > width * 0.55 and y0 > height * 0.78:
        return True

    return False


def _vector_fast_path(page: fitz.Page, max_suggestions: int) -> list[ExtractSuggestion]:
    words = page.get_text("words")
    width = float(page.rect.width)
    height = float(page.rect.height)

    suggestions: list[ExtractSuggestion] = []
    for item in words:
        if len(item) < 5:
            continue

        x0, y0, x1, y1, text = float(item[0]), float(item[1]), float(item[2]), float(item[3]), str(item[4]).strip()
        if not text:
            continue
        if _is_title_or_margin_zone(x0, y0, x1, y1, width, height):
            continue
        if not DIMENSION_REGEX.match(text.replace(" ", "")):
            continue

        center_x = (x0 + x1) / 2.0
        center_y = (y0 + y1) / 2.0
        suggestions.append(
            ExtractSuggestion(
                text=text,
                confidence=0.98,
                x=center_x,
                y=center_y,
                bbox=[x0, y0, x1, y1],
                stage="vector_fast_path",
            )
        )
        if len(suggestions) >= max_suggestions:
            break

    return suggestions


def _segment_action_regions(gray_image, width: int, height: int) -> list[tuple[int, int, int, int]]:
    import cv2

    x0 = int(width * 0.03)
    y0 = int(height * 0.03)
    x1 = int(width * 0.97)
    y1 = int(height * 0.97)

    # Mask out a probable title block area.
    tx0 = int(width * 0.55)
    ty0 = int(height * 0.78)

    mask = cv2.threshold(gray_image, 210, 255, cv2.THRESH_BINARY_INV)[1]
    mask[ty0:y1, tx0:x1] = 0

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    viewports: list[tuple[int, int, int, int]] = []
    min_area = int(width * height * 0.02)

    for contour in contours:
        area = int(cv2.contourArea(contour))
        if area < min_area:
            continue
        rx, ry, rw, rh = cv2.boundingRect(contour)
        if rw < width * 0.12 or rh < height * 0.12:
            continue
        if rx > width * 0.55 and ry > height * 0.78:
            continue
        viewports.append((rx, ry, rw, rh))

    if not viewports:
        return [(x0, y0, x1 - x0, y1 - y0)]

    return sorted(viewports, key=lambda box: box[2] * box[3], reverse=True)[:4]


def _tile_regions(viewports: list[tuple[int, int, int, int]], tile_size: int, overlap_ratio: float) -> list[tuple[int, int, int, int]]:
    step = max(64, int(tile_size * (1.0 - overlap_ratio)))
    tiles: list[tuple[int, int, int, int]] = []

    for vx, vy, vw, vh in viewports:
        max_x = vx + vw
        max_y = vy + vh

        y = vy
        while y < max_y:
            x = vx
            while x < max_x:
                x2 = min(x + tile_size, max_x)
                y2 = min(y + tile_size, max_y)
                tiles.append((x, y, x2 - x, y2 - y))
                if x + tile_size >= max_x:
                    break
                x += step
            if y + tile_size >= max_y:
                break
            y += step

    return tiles


def _clearance_point(binary_mask, center_x: float, center_y: float, width: int, height: int) -> tuple[float, float]:
    import cv2
    import numpy as np

    angle_candidates = [0, 35, 70, 110, 145, 180, 215, 250, 290, 325]

    best_score = float("inf")
    best_point: tuple[float, float] | None = None

    for radius in range(56, 230, 16):
        for angle in angle_candidates:
            rad = math.radians(angle)
            cx = int(center_x + radius * math.cos(rad))
            cy = int(center_y + radius * math.sin(rad))

            if cx < 20 or cy < 20 or cx > width - 20 or cy > height - 20:
                continue

            roi_x0 = max(0, cx - 16)
            roi_y0 = max(0, cy - 16)
            roi_x1 = min(width, cx + 16)
            roi_y1 = min(height, cy + 16)
            roi = binary_mask[roi_y0:roi_y1, roi_x0:roi_x1]
            if roi.size == 0:
                continue

            occupancy = float(np.mean(roi > 0))
            if occupancy > 0.08:
                continue

            line_mask = cv2.line(
                np.zeros_like(binary_mask),
                (int(center_x), int(center_y)),
                (cx, cy),
                color=255,
                thickness=1,
            )
            crossings = float(np.mean((binary_mask > 0) & (line_mask > 0)))
            score = occupancy + crossings * 0.6 + radius * 0.0005
            if score < best_score:
                best_score = score
                best_point = (float(cx), float(cy))

    if best_point is None:
        return (
            float(min(width - 20, max(20, int(center_x + 64)))),
            float(min(height - 20, max(20, int(center_y - 52)))),
        )

    return best_point


def _get_ocr_engine():
    global _PADDLE_OCR
    if _PADDLE_OCR is None:
        from paddleocr import PaddleOCR

        _PADDLE_OCR = PaddleOCR(use_angle_cls=True, lang="en")
    return _PADDLE_OCR


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    request: Request,
    max_suggestions: int = Query(default=40, ge=1, le=200),
    dpi: int = Query(default=settings.default_dpi, ge=200, le=600),
) -> ExtractResponse:
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty PDF payload")

    if len(payload) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF payload exceeds configured size limit")

    try:
        doc = fitz.open(stream=payload, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid PDF payload") from exc

    if doc.page_count == 0:
        raise HTTPException(status_code=400, detail="PDF contains no pages")

    page = doc[0]
    profile_words = page.get_text("words")
    profile = {
        "page_count": doc.page_count,
        "vector_word_count": len(profile_words),
        "used_dpi": dpi,
        "tile_size": settings.tile_size,
    }

    vector_suggestions = []
    if len(profile_words) >= settings.vector_word_threshold:
        vector_suggestions = _vector_fast_path(page, max_suggestions)

    if vector_suggestions:
        return ExtractResponse(
            mode="vector",
            profile=profile,
            diagnostics={
                "phase1": "Vector text objects detected; OCR skipped",
                "phase2": "Viewport segmentation bypassed in vector fast path",
                "phase3": "Regex-filtered vector words used",
                "phase4": "Balloon points offset from text boxes",
            },
            suggestions=vector_suggestions,
        )

    scale = float(dpi) / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    import cv2
    import numpy as np

    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    viewports = _segment_action_regions(gray, pix.width, pix.height)
    tiles = _tile_regions(viewports, settings.tile_size, settings.tile_overlap)

    try:
        ocr = _get_ocr_engine()
    except Exception as exc:
        return ExtractResponse(
            mode="raster",
            profile=profile,
            diagnostics={
                "phase1": "No reliable vector fast path hit; rendered PDF at high DPI",
                "phase2": f"Detected {len(viewports)} viewport regions and generated {len(tiles)} OCR tiles",
                "phase3": f"OCR engine unavailable: {exc.__class__.__name__}",
                "phase4": "Skipped clearance placement because OCR engine could not initialize",
            },
            suggestions=[],
        )
    candidates: list[ExtractSuggestion] = []
    seen_keys: set[tuple[str, int, int]] = set()

    for tx, ty, tw, th in tiles:
        tile = img[ty:ty + th, tx:tx + tw]
        if tile.size == 0:
            continue

        result = ocr.ocr(tile, cls=True)
        lines = result[0] if result and result[0] else []

        for entry in lines:
            if len(entry) < 2:
                continue
            box = entry[0]
            text_info = entry[1]
            text = str(text_info[0]).strip()
            confidence = float(text_info[1]) if len(text_info) > 1 else 0.0

            if confidence < 0.35:
                continue
            if not DIMENSION_REGEX.match(text.replace(" ", "")):
                continue

            points = [(float(p[0]) + tx, float(p[1]) + ty) for p in box]
            gx = sum(p[0] for p in points) / len(points)
            gy = sum(p[1] for p in points) / len(points)

            x0 = min(p[0] for p in points)
            y0 = min(p[1] for p in points)
            x1 = max(p[0] for p in points)
            y1 = max(p[1] for p in points)

            key = (text.upper().replace(" ", ""), int(gx // 8), int(gy // 8))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            candidates.append(
                ExtractSuggestion(
                    text=text,
                    confidence=min(0.99, confidence),
                    x=gx / scale,
                    y=gy / scale,
                    bbox=[x0 / scale, y0 / scale, x1 / scale, y1 / scale],
                    stage="raster_ocr_clearance",
                )
            )

    candidates.sort(key=lambda item: item.confidence, reverse=True)
    candidates = candidates[:max_suggestions]

    return ExtractResponse(
        mode="raster",
        profile=profile,
        diagnostics={
            "phase1": "No reliable vector fast path hit; rendered PDF at high DPI",
            "phase2": f"Detected {len(viewports)} viewport regions and generated {len(tiles)} OCR tiles",
            "phase3": f"OCR candidate count after regex filtering: {len(candidates)}",
            "phase4": "Anchored balloon coordinates to detected dimension text centers",
        },
        suggestions=candidates,
    )
