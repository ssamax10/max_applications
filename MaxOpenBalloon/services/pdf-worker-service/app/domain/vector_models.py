from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Point:
    """2D point with x, y coordinates."""
    x: float
    y: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass
class TextBlock:
    """Extracted text with precise location and formatting."""
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    font_name: str = ""
    font_size: float = 0.0
    color: tuple[float, float, float] | None = None  # RGB
    rotation: float = 0.0
    confidence: float = 1.0
    source: str = "pymupdf"  # "pymupdf" or "pdfminer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bbox": list(self.bbox),
            "font_name": self.font_name,
            "font_size": self.font_size,
            "color": list(self.color) if self.color else None,
            "rotation": self.rotation,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass
class Line:
    """Line segment with start and end points."""
    start: Point
    end: Point
    color: tuple[float, float, float] | None = None  # RGB
    line_width: float = 1.0
    line_style: str = "solid"  # "solid", "dashed", "dotted"
    dash_pattern: list[float] | None = None
    layer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "color": list(self.color) if self.color else None,
            "line_width": self.line_width,
            "line_style": self.line_style,
            "dash_pattern": self.dash_pattern,
            "layer": self.layer,
        }


@dataclass
class Polyline:
    """Polyline as ordered sequence of points."""
    points: list[Point]
    closed: bool = False
    color: tuple[float, float, float] | None = None
    line_width: float = 1.0
    line_style: str = "solid"
    dash_pattern: list[float] | None = None
    layer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": [p.to_dict() for p in self.points],
            "closed": self.closed,
            "color": list(self.color) if self.color else None,
            "line_width": self.line_width,
            "line_style": self.line_style,
            "dash_pattern": self.dash_pattern,
            "layer": self.layer,
        }


@dataclass
class BezierCurve:
    """Cubic Bezier curve defined by 4 control points."""
    start: Point
    control1: Point
    control2: Point
    end: Point
    color: tuple[float, float, float] | None = None
    line_width: float = 1.0
    layer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.to_dict(),
            "control1": self.control1.to_dict(),
            "control2": self.control2.to_dict(),
            "end": self.end.to_dict(),
            "color": list(self.color) if self.color else None,
            "line_width": self.line_width,
            "layer": self.layer,
        }


@dataclass
class Annotation:
    """PDF annotation (arrows, symbols, dimensions)."""
    annotation_type: str  # "arrow", "text", "dimension", "symbol"
    bbox: tuple[float, float, float, float]
    content: str | None = None
    color: tuple[float, float, float] | None = None
    flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_type": self.annotation_type,
            "bbox": list(self.bbox),
            "content": self.content,
            "color": list(self.color) if self.color else None,
            "flags": self.flags,
            "metadata": self.metadata,
        }


@dataclass
class DrawingDocument:
    """Complete vector representation of a PDF drawing."""
    document_id: str = field(default_factory=lambda: str(uuid4()))
    page_count: int = 0
    page_width: float = 0.0
    page_height: float = 0.0
    text_blocks: list[TextBlock] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    polylines: list[Polyline] = field(default_factory=list)
    bezier_curves: list[BezierCurve] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    layers: dict[str, list[str]] = field(default_factory=dict)  # layer_name -> entity_ids
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_count": self.page_count,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "text_blocks": [tb.to_dict() for tb in self.text_blocks],
            "lines": [line.to_dict() for line in self.lines],
            "polylines": [pl.to_dict() for pl in self.polylines],
            "bezier_curves": [bc.to_dict() for bc in self.bezier_curves],
            "annotations": [ann.to_dict() for ann in self.annotations],
            "layers": self.layers,
            "metadata": self.metadata,
        }

    def get_statistics(self) -> dict[str, int]:
        """Return counts of each entity type."""
        return {
            "text_blocks": len(self.text_blocks),
            "lines": len(self.lines),
            "polylines": len(self.polylines),
            "bezier_curves": len(self.bezier_curves),
            "annotations": len(self.annotations),
            "total_primitives": (
                len(self.text_blocks)
                + len(self.lines)
                + len(self.polylines)
                + len(self.bezier_curves)
                + len(self.annotations)
            ),
        }