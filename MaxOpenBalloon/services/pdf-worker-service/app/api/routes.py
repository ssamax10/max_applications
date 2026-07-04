import math
import re
from typing import Any

import fitz
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.settings import settings
from app.core.vector_extractor import VectorExtractor
from app.core.optimized_vector_extractor import OptimizedVectorExtractor
from app.core.dimension_reconstructor import DimensionReconstructor
from app.core.gdt_recognizer import GDTRecognizer
from app.core.feature_associator import FeatureAssociator
from app.core.inspection_extractor import InspectionExtractor
from app.domain.vector_models import DrawingDocument
from app.domain.dimension_models import DimensionGroup, DimensionGraph
from app.domain.gdt_models import GDTRecognitionResult
from app.domain.feature_models import FeatureAssociationResult
from app.domain.inspection_models import InspectionResult

router = APIRouter()

# Unified balloon text pattern: accepts dimension text AND balloon labels
# Dimension examples: 10, 10.5, 10mm, M12, M12x1.5, Ø12, Ø12H7, 10±0.05, 10+0.1/-0.05
# Balloon label examples: B-001, ITEM-1, PartA, 1, 01, A-1
BALLOON_TEXT_PATTERN = re.compile(
    r'^(?:'
    # Dimension numbers with optional unit
    r'(?:\d+(?:\.\d+)?\s*(?:mm|cm|m|in|")?(?:\s*[±+\-]\s*\d+(?:\.\d+)?)?)|'
    # Thread specs: M12, M12x1.5, M12x1.5-6H
    r'(?:M\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?(?:-\d+[A-Z])?)|'
    # Diameter symbols: Ø12, Ø12H7, Ø12h6, D12, R5
    r'(?:[ODR]?\s*\d+(?:\.\d+)?(?:\s*[xX]\s*\d+(?:\.\d+)?)?(?:[A-Z]{1,2}\d+)?)|'
    # Tolerance: ±0.05, +0.1/-0.05
    r'(?:[±+\-]\s*\d+(?:\.\d+)?(?:\s*/\s*[±+\-]\s*\d+(?:\.\d+)?)?)|'
    # Balloon labels: B-001, ITEM-1, A1, 01, etc.
    r'(?:[A-Za-z]{0,3}[- ]?\d{1,4}[A-Za-z]?)|'
    # Pure numbers (1-9999)
    r'(?:\d{1,4})'
    r')$'
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


class VectorExtractResponse(BaseModel):
    document_id: str
    page_count: int
    page_width: float
    page_height: float
    statistics: dict[str, Any]  # Allow int, str, bool values
    text_blocks: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    polylines: list[dict[str, Any]]
    bezier_curves: list[dict[str, Any]]
    annotations: list[dict[str, Any]]


class DimensionReconstructResponse(BaseModel):
    document_id: str
    dimension_groups: list[dict[str, Any]]
    graph: dict[str, Any]
    statistics: dict[str, int]


class GDTRecognitionResponse(BaseModel):
    document_id: str
    datum_symbols: list[dict[str, Any]]
    gdt_tolerances: list[dict[str, Any]]
    gdt_symbols: list[dict[str, Any]]
    gdt_sets: list[dict[str, Any]]
    statistics: dict[str, int]


class FeatureAssociationResponse(BaseModel):
    document_id: str
    holes: list[dict[str, Any]]
    slots: list[dict[str, Any]]
    chamfers: list[dict[str, Any]]
    radii: list[dict[str, Any]]
    threads: list[dict[str, Any]]
    associations: list[dict[str, Any]]
    statistics: dict[str, int]


class InspectionExtractResponse(BaseModel):
    document_id: str
    characteristics: list[dict[str, Any]]
    statistics: dict[str, int]


def _is_title_or_margin_zone(x0: float, y0: float, x1: float, y1: float, width: float, height: float) -> bool:
    if x0 < width * 0.02 or y0 < height * 0.02 or x1 > width * 0.98 or y1 > height * 0.98:
        return True

    # Typical engineering title block zone.
    if x0 > width * 0.55 and y0 > height * 0.78:
        return True

    return False


def _vector_fast_path(page: fitz.Page, max_suggestions: int, allow_sparse: bool = False) -> list[ExtractSuggestion]:
    words = page.get_text("words")
    width = float(page.rect.width)
    height = float(page.rect.height)

    if not allow_sparse and len(words) < settings.vector_word_threshold:
        return []

    suggestions: list[ExtractSuggestion] = []
    for item in words:
        if len(item) < 5:
            continue

        x0, y0, x1, y1, text = float(item[0]), float(item[1]), float(item[2]), float(item[3]), str(item[4]).strip()
        if not text:
            continue
        if _is_title_or_margin_zone(x0, y0, x1, y1, width, height):
            continue
        if not BALLOON_TEXT_PATTERN.match(text.replace(" ", "")):
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


def _vector_all_text(page: fitz.Page, max_suggestions: int) -> list[ExtractSuggestion]:
    """Extract ALL text words from the PDF page, filtering only title/margin zones.
    
    Unlike _vector_fast_path, this does NOT require BALLOON_TEXT_PATTERN matching.
    This ensures we always get some text positions to work with for balloon placement.
    """
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

        center_x = (x0 + x1) / 2.0
        center_y = (y0 + y1) / 2.0
        suggestions.append(
            ExtractSuggestion(
                text=text,
                confidence=0.85,
                x=center_x,
                y=center_y,
                bbox=[x0, y0, x1, y1],
                stage="vector_all_text",
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


# Use optimized extractor with caching and smart detection
_optimized_extractor = OptimizedVectorExtractor(use_pdfminer=False, cache_size=100)

# Stage 2: Dimension reconstructor
_dimension_reconstructor = DimensionReconstructor()

# Stage 3: GD&T recognizer
_gdt_recognizer = GDTRecognizer()

# Stage 4: Feature associator
_feature_associator = FeatureAssociator()

# Stage 5: Inspection extractor
_inspection_extractor = InspectionExtractor()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@router.post("/vector/extract", response_model=VectorExtractResponse)
async def extract_vector(
    request: Request,
    use_cache: bool = Query(default=True, description="Enable caching for repeated PDFs"),
    extract_text: bool = Query(default=True, description="Extract text blocks"),
    extract_lines: bool = Query(default=True, description="Extract lines"),
    extract_polylines: bool = Query(default=True, description="Extract polylines"),
    extract_curves: bool = Query(default=False, description="Extract Bezier curves (expensive)"),
    extract_annotations: bool = Query(default=False, description="Extract annotations"),
    max_primitives: int | None = Query(default=None, ge=100, le=50000, description="Limit total primitives"),
) -> VectorExtractResponse:
    """Stage 1: Extract complete vector geometry from PDF.
    
    Returns all primitives: text, lines, polylines, Bezier curves, annotations.
    
    Features:
    - Smart PDF type detection (vector vs scanned)
    - Caching for repeated access
    - Selective extraction to reduce response size
    - Automatic complexity limiting
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty PDF payload")

    if len(payload) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF payload exceeds configured size limit")

    try:
        # Build selective extraction config
        selective = None
        if not all([extract_text, extract_lines, extract_polylines, extract_curves, extract_annotations]):
            selective = {
                "extract_text": extract_text,
                "extract_lines": extract_lines,
                "extract_polylines": extract_polylines,
                "extract_curves": extract_curves,
                "extract_annotations": extract_annotations,
            }
        
        # Extract with optimizations
        drawing = _optimized_extractor.extract(
            payload,
            use_cache=use_cache,
            selective=selective,
            max_primitives=max_primitives,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vector extraction failed: {exc}") from exc

    stats = _optimized_extractor.get_statistics(drawing)

    return VectorExtractResponse(
        document_id=drawing.document_id,
        page_count=drawing.page_count,
        page_width=drawing.page_width,
        page_height=drawing.page_height,
        statistics=stats,
        text_blocks=[tb.to_dict() for tb in drawing.text_blocks],
        lines=[line.to_dict() for line in drawing.lines],
        polylines=[pl.to_dict() for pl in drawing.polylines],
        bezier_curves=[bc.to_dict() for bc in drawing.bezier_curves],
        annotations=[ann.to_dict() for ann in drawing.annotations],
    )


@router.post("/dimensions/reconstruct", response_model=DimensionReconstructResponse)
async def reconstruct_dimensions(
    request: Request,
) -> DimensionReconstructResponse:
    """Stage 2: Reconstruct dimensions from vector geometry.
    
    Takes vector data and identifies:
    - Arrowheads
    - Extension lines
    - Dimension lines
    - Associated text
    
    Returns grouped dimensions with graph representation.
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty PDF payload")

    if len(payload) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF payload exceeds configured size limit")

    try:
        # Extract vector data first
        drawing = _optimized_extractor.extract(payload, use_cache=True)
        
        # Reconstruct dimensions
        dimension_groups, graph = _dimension_reconstructor.reconstruct(drawing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dimension reconstruction failed: {exc}") from exc

    # Build statistics
    stats = {
        "total_dimensions": len(dimension_groups),
        "total_arrowheads": len([
            a for g in dimension_groups 
            if g.dimension_line and g.dimension_line.start_arrow
        ]),
        "total_extension_lines": sum(len(g.extension_lines) for g in dimension_groups),
        "graph_nodes": len(graph.nodes),
        "graph_edges": len(graph.edges),
    }

    return DimensionReconstructResponse(
        document_id=drawing.document_id,
        dimension_groups=[dg.to_dict() for dg in dimension_groups],
        graph=graph.to_dict(),
        statistics=stats,
    )


@router.post("/gdt/recognize", response_model=GDTRecognitionResponse)
async def recognize_gdt(
    request: Request,
) -> GDTRecognitionResponse:
    """Stage 3: Recognize GD&T features from vector geometry.
    
    Identifies:
    - Datum symbols (A, B, C, etc.)
    - GD&T tolerances (position, profile, runout, flatness, etc.)
    - GD&T symbol frames
    - Complete GD&T sets with associations
    
    Returns recognized GD&T features with datum references.
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty PDF payload")

    if len(payload) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF payload exceeds configured size limit")

    try:
        # Extract vector data first
        drawing = _optimized_extractor.extract(payload, use_cache=True)
        
        # Recognize GD&T features
        gdt_result = _gdt_recognizer.recognize(drawing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GD&T recognition failed: {exc}") from exc

    return GDTRecognitionResponse(
        document_id=drawing.document_id,
        datum_symbols=[ds.to_dict() for ds in gdt_result.datum_symbols],
        gdt_tolerances=[gt.to_dict() for gt in gdt_result.gdt_tolerances],
        gdt_symbols=[gs.to_dict() for gs in gdt_result.gdt_symbols],
        gdt_sets=[gs.to_dict() for gs in gdt_result.gdt_sets],
        statistics=gdt_result.statistics,
    )


@router.post("/features/associate", response_model=FeatureAssociationResponse)
async def associate_features(
    request: Request,
) -> FeatureAssociationResponse:
    """Stage 4: Detect and associate manufacturing features.
    
    Identifies:
    - Holes (circular features)
    - Slots (elongated holes)
    - Chamfers (beveled edges)
    - Radii (rounded corners)
    - Threads (screw threads)
    
    Associates features with their dimensions.
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty PDF payload")

    if len(payload) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF payload exceeds configured size limit")

    try:
        # Extract vector data first
        drawing = _optimized_extractor.extract(payload, use_cache=True)
        
        # Optionally get dimensions for association
        dimension_groups, _ = _dimension_reconstructor.reconstruct(drawing)
        
        # Associate features
        result = _feature_associator.associate(drawing, dimension_groups)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Feature association failed: {exc}") from exc

    return FeatureAssociationResponse(
        document_id=drawing.document_id,
        holes=[h.to_dict() for h in result.holes],
        slots=[s.to_dict() for s in result.slots],
        chamfers=[c.to_dict() for c in result.chamfers],
        radii=[r.to_dict() for r in result.radii],
        threads=[t.to_dict() for t in result.threads],
        associations=[a.to_dict() for a in result.associations],
        statistics=result.statistics,
    )


@router.post("/inspection/extract", response_model=InspectionExtractResponse)
async def extract_inspection(
    request: Request,
) -> InspectionExtractResponse:
    """Stage 5: Extract complete inspection characteristics.
    
    Merges data from all previous stages to produce:
    - Feature type (hole, slot, chamfer, radius, thread)
    - Dimension (Ø12, 20, M8x1.25)
    - Tolerance (±0.05, +0.0/-0.1)
    - Datum references (A, B, C)
    - GD&T type and value (position, profile, etc.)
    
    Returns structured inspection characteristics ready for quality control.
    """
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty PDF payload")

    if len(payload) > settings.max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF payload exceeds configured size limit")

    try:
        # Extract vector data first
        drawing = _optimized_extractor.extract(payload, use_cache=True)
        
        # Get dimensions from Stage 2
        dimension_groups, _ = _dimension_reconstructor.reconstruct(drawing)
        
        # Get GD&T from Stage 3
        gdt_result = _gdt_recognizer.recognize(drawing)
        
        # Get features from Stage 4
        feature_result = _feature_associator.associate(drawing, dimension_groups)
        
        # Extract inspection characteristics
        inspection_result = _inspection_extractor.extract(
            drawing,
            dimension_groups,
            gdt_result.gdt_sets,
            feature_result
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inspection extraction failed: {exc}") from exc

    return InspectionExtractResponse(
        document_id=drawing.document_id,
        characteristics=[c.to_dict() for c in inspection_result.characteristics],
        statistics=inspection_result.statistics,
    )


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    request: Request,
    max_suggestions: int = Query(default=40, ge=1, le=200),
    dpi: int = Query(default=settings.default_dpi, ge=200, le=600),
    text_only: bool = Query(default=False, description="Extract ALL text without dimension regex filtering"),
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
    pdf_type = _optimized_extractor.pdf_type_detector.detect(payload)
    profile = {
        "page_count": doc.page_count,
        "vector_word_count": len(profile_words),
        "used_dpi": dpi,
        "tile_size": settings.tile_size,
        "pdf_type": pdf_type,
    }

    # If text_only mode is requested, skip dimension regex filtering
    if text_only:
        suggestions = _vector_all_text(page, max_suggestions)
        if suggestions:
            return ExtractResponse(
                mode="vector_text_only",
                profile=profile,
                diagnostics={
                    "phase1": "Text-only mode: extracted all text words without dimension regex filtering",
                    "phase2": f"Found {len(suggestions)} text words outside title/margin zones",
                    "phase3": "No dimension regex applied — all text positions returned",
                    "phase4": "Balloon points anchored to text word centers",
                },
                suggestions=suggestions,
            )
        # Fall through to OCR if no vector text found
        profile["vector_word_count"] = 0

    if pdf_type in {"vector", "hybrid"}:
        vector_suggestions = _vector_fast_path(page, max_suggestions, allow_sparse=True)
        if not vector_suggestions:
            vector_suggestions = _vector_all_text(page, max_suggestions)

        if vector_suggestions:
            return ExtractResponse(
                mode="vector",
                profile=profile,
                diagnostics={
                    "phase1": f"PyMuPDF classified the PDF as {pdf_type}; vector text extraction used before OCR",
                    "phase2": f"Found {len(vector_suggestions)} vector-anchored text candidates",
                    "phase3": "Regex-filtered vector words preferred; sparse vector PDFs fall back to all text words",
                    "phase4": "Balloon points anchored to PyMuPDF text boxes",
                },
                suggestions=vector_suggestions,
            )

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
            if not BALLOON_TEXT_PATTERN.match(text.replace(" ", "")):
                continue

            points = [(float(p[0]) + tx, float(p[1]) + ty) for p in box]
            gx = sum(p[0] for p in points) / len(points)
            gy = sum(p[1] for p in points) / len(points)

            x0 = min(p[0] for p in points)
            y0 = min(p[1] for p in points)
            x1 = max(p[0] for p in points)
            y1 = max(p[1] for p in points)

            # Use a larger dedup radius (20px) to catch cross-tile duplicates
            key = (text.upper().replace(" ", ""), int(gx // 20), int(gy // 20))
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

    # Apply clearance point optimization to find best balloon anchor positions
    # that avoid crossing drawing lines
    if candidates:
        # Create a binary mask from the rendered page for clearance computation
        _, binary_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        
        for candidate in candidates:
            # Convert candidate position back to pixel coordinates for clearance computation
            px = int(candidate.x * scale)
            py = int(candidate.y * scale)
            
            # Find a clearance point that avoids drawing lines
            clearance_x, clearance_y = _clearance_point(binary_mask, px, py, pix.width, pix.height)
            
            # Update the suggestion with the clearance-optimized position
            candidate.x = clearance_x / scale
            candidate.y = clearance_y / scale
            candidate.stage = "raster_ocr_clearance"

    return ExtractResponse(
        mode="raster",
        profile=profile,
        diagnostics={
            "phase1": "No reliable vector fast path hit; rendered PDF at high DPI",
            "phase2": f"Detected {len(viewports)} viewport regions and generated {len(tiles)} OCR tiles",
            "phase3": f"OCR candidate count after regex filtering: {len(candidates)}",
            "phase4": "Anchored balloon coordinates to detected dimension text centers with clearance optimization",
        },
        suggestions=candidates,
    )