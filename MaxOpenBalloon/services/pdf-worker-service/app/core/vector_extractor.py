"""Stage 1: PDF Structural Analysis - Vector Geometry Extraction.

Extracts complete vector representation from PDFs including:
- Text blocks with precise coordinates
- Lines, polylines, Bezier curves
- Annotations (arrows, symbols)
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import fitz
try:
    from pdfminer.high_level import extract_text
    from pdfminer.layout import LAParams, LTTextBox, LTTextLine, LTLine, LTRect, LTCurve
    PDFMINER_AVAILABLE = True
except ImportError:
    PDFMINER_AVAILABLE = False

from app.domain.vector_models import (
    Annotation,
    BezierCurve,
    DrawingDocument,
    Line,
    Point,
    Polyline,
    TextBlock,
)

logger = logging.getLogger(__name__)


class VectorExtractor:
    """Extracts complete vector geometry from PDF documents."""

    def __init__(self, use_pdfminer: bool = True):
        """Initialize extractor.

        Args:
            use_pdfminer: Whether to use pdfminer.six for complementary text extraction
        """
        self.use_pdfminer = use_pdfminer

    def extract(self, pdf_bytes: bytes) -> DrawingDocument:
        """Extract complete vector representation from PDF.

        Args:
            pdf_bytes: Raw PDF file bytes

        Returns:
            DrawingDocument with all extracted primitives
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise ValueError(f"Invalid PDF: {exc}") from exc

        if doc.page_count == 0:
            raise ValueError("PDF contains no pages")

        # Initialize document
        page = doc[0]
        drawing = DrawingDocument(
            page_count=doc.page_count,
            page_width=float(page.rect.width),
            page_height=float(page.rect.height),
        )

        # Extract from all pages (currently focusing on first page)
        for page_idx in range(min(doc.page_count, 1)):  # TODO: Support multi-page
            page = doc[page_idx]
            self._extract_page(page, drawing)

        doc.close()
        return drawing

    def _extract_page(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract all primitives from a single page."""
        # Extract text blocks
        self._extract_text(page, drawing)

        # Extract vector graphics
        self._extract_lines(page, drawing)
        self._extract_polylines(page, drawing)
        self._extract_curves(page, drawing)

        # Extract annotations
        self._extract_annotations(page, drawing)

        # Optionally extract with pdfminer for comparison
        if self.use_pdfminer:
            self._extract_text_pdfminer(page, drawing)

    def _extract_text(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract text blocks using PyMuPDF."""
        try:
            # Get text with detailed formatting information
            text_dict = page.get_text("dict")

            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:  # Skip non-text blocks
                    continue

                bbox = tuple(block.get("bbox", [0, 0, 0, 0]))
                text = " ".join(
                    span.get("text", "")
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()

                if not text:
                    continue

                # Extract formatting from first span
                spans = [
                    span
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ]
                first_span = spans[0] if spans else {}

                # Convert color from 0-1 range to 0-255 if needed
                color_raw = first_span.get("color")
                color = None
                if color_raw is not None:
                    if isinstance(color_raw, (int, float)):
                        # Grayscale
                        color = (color_raw, color_raw, color_raw)
                    elif isinstance(color_raw, (tuple, list)) and len(color_raw) >= 3:
                        # RGB - check if in 0-1 or 0-255 range
                        if all(c <= 1.0 for c in color_raw[:3]):
                            color = tuple(int(c * 255) for c in color_raw[:3])
                        else:
                            color = tuple(int(c) for c in color_raw[:3])

                text_block = TextBlock(
                    text=text,
                    bbox=bbox,
                    font_name=first_span.get("font", ""),
                    font_size=first_span.get("size", 0.0),
                    color=color,
                    rotation=first_span.get("rotation", 0.0),
                    confidence=1.0,
                    source="pymupdf",
                )
                drawing.text_blocks.append(text_block)

        except Exception as exc:
            logger.warning(f"Failed to extract text with PyMuPDF: {exc}")

    def _extract_text_pdfminer(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract text using pdfminer.six for comparison/complement."""
        if not PDFMINER_AVAILABLE:
            logger.debug("pdfminer.six not available, skipping")
            return
            
        try:
            # pdfminer requires the original PDF stream, not a page
            # This would need to be called with the full PDF bytes
            # For now, we'll skip this as PyMuPDF provides good text extraction
            logger.debug("pdfminer extraction skipped - requires full PDF stream")
        except Exception as exc:
            logger.debug(f"pdfminer extraction skipped: {exc}")

    def _extract_lines(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract line segments from page."""
        try:
            # Get drawing commands
            drawings = page.get_drawings()

            for drawing_cmd in drawings:
                # Extract color
                color = None
                if "color" in drawing_cmd:
                    color_raw = drawing_cmd["color"]
                    if isinstance(color_raw, (tuple, list)) and len(color_raw) >= 3:
                        if all(c <= 1.0 for c in color_raw[:3]):
                            color = tuple(int(c * 255) for c in color_raw[:3])
                        else:
                            color = tuple(int(c) for c in color_raw[:3])

                # Extract line width
                line_width = drawing_cmd.get("width", 1.0)

                # Extract items (lines, rects, etc.)
                for item in drawing_cmd.get("items", []):
                    item_type = item[0]

                    if item_type == "l":  # Line
                        # item format: ("l", x0, y0, x1, y1)
                        if len(item) >= 5:
                            start = Point(x=item[1], y=item[2])
                            end = Point(x=item[3], y=item[4])
                            line = Line(
                                start=start,
                                end=end,
                                color=color,
                                line_width=line_width,
                            )
                            drawing.lines.append(line)

                    elif item_type == "re":  # Rectangle
                        # item format: ("re", x0, y0, x1, y1)
                        if len(item) >= 5:
                            x0, y0, x1, y1 = item[1:5]
                            # Convert rect to 4 lines
                            rect_lines = [
                                (x0, y0, x1, y0),  # Top
                                (x1, y0, x1, y1),  # Right
                                (x1, y1, x0, y1),  # Bottom
                                (x0, y1, x0, y0),  # Left
                            ]
                            for lx0, ly0, lx1, ly1 in rect_lines:
                                line = Line(
                                    start=Point(x=lx0, y=ly0),
                                    end=Point(x=lx1, y=ly1),
                                    color=color,
                                    line_width=line_width,
                                )
                                drawing.lines.append(line)

        except Exception as exc:
            logger.warning(f"Failed to extract lines: {exc}")

    def _extract_polylines(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract polylines from page."""
        try:
            drawings = page.get_drawings()

            for drawing_cmd in drawings:
                # Extract color
                color = None
                if "color" in drawing_cmd:
                    color_raw = drawing_cmd["color"]
                    if isinstance(color_raw, (tuple, list)) and len(color_raw) >= 3:
                        if all(c <= 1.0 for c in color_raw[:3]):
                            color = tuple(int(c * 255) for c in color_raw[:3])
                        else:
                            color = tuple(int(c) for c in color_raw[:3])

                line_width = drawing_cmd.get("width", 1.0)
                close_path = drawing_cmd.get("closePath", False)

                # Check if this is a polyline (multiple connected lines)
                points = []
                for item in drawing_cmd.get("items", []):
                    if item[0] == "l" and len(item) >= 5:
                        points.append(Point(x=item[1], y=item[2]))

                # If we have multiple points, it's a polyline
                if len(points) >= 2:
                    polyline = Polyline(
                        points=points,
                        closed=close_path,
                        color=color,
                        line_width=line_width,
                    )
                    drawing.polylines.append(polyline)

        except Exception as exc:
            logger.warning(f"Failed to extract polylines: {exc}")

    def _extract_curves(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract Bezier curves from page."""
        try:
            drawings = page.get_drawings()

            for drawing_cmd in drawings:
                # Extract color
                color = None
                if "color" in drawing_cmd:
                    color_raw = drawing_cmd["color"]
                    if isinstance(color_raw, (tuple, list)) and len(color_raw) >= 3:
                        if all(c <= 1.0 for c in color_raw[:3]):
                            color = tuple(int(c * 255) for c in color_raw[:3])
                        else:
                            color = tuple(int(c) for c in color_raw[:3])

                line_width = drawing_cmd.get("width", 1.0)

                # Extract Bezier curves
                # item format: ("c", x1, y1, x2, y2, x3, y3) - cubic Bezier
                items = drawing_cmd.get("items", [])
                i = 0
                while i < len(items):
                    item = items[i]
                    if item[0] == "c" and len(item) >= 7:
                        # Cubic Bezier: start is previous point or first point
                        if i == 0:
                            # Start from first point in drawing rect
                            rect = drawing_cmd.get("rect", [0, 0, 0, 0])
                            start = Point(x=rect[0], y=rect[1])
                        else:
                            # Use end point of previous curve
                            prev_item = items[i - 1]
                            if prev_item[0] == "c" and len(prev_item) >= 7:
                                start = Point(x=prev_item[5], y=prev_item[6])
                            else:
                                start = Point(x=item[1], y=item[2])

                        control1 = Point(x=item[1], y=item[2])
                        control2 = Point(x=item[3], y=item[4])
                        end = Point(x=item[5], y=item[6])

                        curve = BezierCurve(
                            start=start,
                            control1=control1,
                            control2=control2,
                            end=end,
                            color=color,
                            line_width=line_width,
                        )
                        drawing.bezier_curves.append(curve)
                    i += 1

        except Exception as exc:
            logger.warning(f"Failed to extract curves: {exc}")

    def _extract_annotations(self, page: fitz.Page, drawing: DrawingDocument) -> None:
        """Extract PDF annotations."""
        try:
            annot = page.first_annot
            while annot:
                annot_type = annot.type[0] if isinstance(annot.type, tuple) else annot.type
                rect = annot.rect
                bbox = (rect.x0, rect.y0, rect.x1, rect.y1)

                # Determine annotation type
                annotation_type = "unknown"
                content = None

                if annot_type == 0:  # Text
                    annotation_type = "text"
                    content = annot.info.get("content", "")
                elif annot_type == 1:  # Link
                    annotation_type = "link"
                    content = annot.info.get("uri", "")
                elif annot_type in [2, 3]:  # FreeText, Line
                    annotation_type = "line"
                    content = annot.info.get("content", "")
                elif annot_type == 4:  # Square
                    annotation_type = "rectangle"
                elif annot_type == 5:  # Circle
                    annotation_type = "circle"
                elif annot_type == 6:  # Polygon
                    annotation_type = "polygon"
                elif annot_type == 7:  # PolyLine
                    annotation_type = "polyline"
                elif annot_type == 8:  # Highlight
                    annotation_type = "highlight"
                elif annot_type == 9:  # Underline
                    annotation_type = "underline"
                elif annot_type == 10:  # Squiggly
                    annotation_type = "squiggly"
                elif annot_type == 11:  # StrikeOut
                    annotation_type = "strikeout"
                elif annot_type == 12:  # Stamp
                    annotation_type = "stamp"
                elif annot_type == 13:  # Caret
                    annotation_type = "caret"
                elif annot_type == 14:  # Ink
                    annotation_type = "ink"
                elif annot_type == 15:  # Popup
                    annotation_type = "popup"
                elif annot_type == 16:  # FileAttachment
                    annotation_type = "file_attachment"
                elif annot_type == 17:  # Sound
                    annotation_type = "sound"
                elif annot_type == 18:  # Movie
                    annotation_type = "movie"
                elif annot_type == 19:  # Widget
                    annotation_type = "widget"
                elif annot_type == 20:  # Screen
                    annotation_type = "screen"
                elif annot_type == 21:  # PrinterMark
                    annotation_type = "printer_mark"
                elif annot_type == 22:  # TrapNet
                    annotation_type = "trap_net"
                elif annot_type == 23:  # Watermark
                    annotation_type = "watermark"
                elif annot_type == 24:  # 3D
                    annotation_type = "3d"
                elif annot_type == 25:  # Redact
                    annotation_type = "redact"

                # Extract color if available
                color = None
                try:
                    annot_color = annot.colors.get("stroke", None)
                    if annot_color and len(annot_color) >= 3:
                        if all(c <= 1.0 for c in annot_color[:3]):
                            color = tuple(int(c * 255) for c in annot_color[:3])
                        else:
                            color = tuple(int(c) for c in annot_color[:3])
                except Exception:
                    pass

                annotation = Annotation(
                    annotation_type=annotation_type,
                    bbox=bbox,
                    content=content,
                    color=color,
                    flags=[],
                    metadata={"annot_type_raw": annot_type},
                )
                drawing.annotations.append(annotation)

                annot = annot.next

        except Exception as exc:
            logger.warning(f"Failed to extract annotations: {exc}")

    def get_statistics(self, drawing: DrawingDocument) -> dict[str, int]:
        """Get detailed statistics about extracted document."""
        stats = drawing.get_statistics()
        stats["text_blocks_with_fonts"] = sum(
            1 for tb in drawing.text_blocks if tb.font_name
        )
        return stats
