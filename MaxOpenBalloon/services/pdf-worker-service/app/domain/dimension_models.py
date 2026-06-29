"""Stage 2: Dimension Reconstruction - Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.domain.vector_models import Point


@dataclass
class Arrowhead:
    """Detected arrowhead with position and orientation."""
    id: str = field(default_factory=lambda: str(uuid4()))
    tip: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    base: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    angle: float = 0.0  # Direction in degrees
    size: float = 0.0
    confidence: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tip": self.tip.to_dict(),
            "base": self.base.to_dict(),
            "angle": self.angle,
            "size": self.size,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
        }


@dataclass
class ExtensionLine:
    """Extension line connecting feature to dimension line."""
    id: str = field(default_factory=lambda: str(uuid4()))
    start: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    end: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    length: float = 0.0
    confidence: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "length": self.length,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
        }


@dataclass
class DimensionLine:
    """Dimension line with arrows at both ends."""
    id: str = field(default_factory=lambda: str(uuid4()))
    start: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    end: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    start_arrow: Arrowhead | None = None
    end_arrow: Arrowhead | None = None
    length: float = 0.0
    angle: float = 0.0
    confidence: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "start_arrow": self.start_arrow.to_dict() if self.start_arrow else None,
            "end_arrow": self.end_arrow.to_dict() if self.end_arrow else None,
            "length": self.length,
            "angle": self.angle,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
        }


@dataclass
class DimensionGroup:
    """Complete dimension with all components grouped together."""
    id: str = field(default_factory=lambda: str(uuid4()))
    dimension_line: DimensionLine | None = None
    extension_lines: list[ExtensionLine] = field(default_factory=list)
    text: str = ""
    text_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    text_center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    dimension_type: str = "linear"  # "linear", "angular", "radial", "diameter"
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dimension_line": self.dimension_line.to_dict() if self.dimension_line else None,
            "extension_lines": [el.to_dict() for el in self.extension_lines],
            "text": self.text,
            "text_bbox": list(self.text_bbox),
            "text_center": self.text_center.to_dict(),
            "dimension_type": self.dimension_type,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class DimensionGraph:
    """Graph representation of dimension relationships."""
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, type)

    def add_node(self, node_id: str, node_type: str, data: dict[str, Any]) -> None:
        """Add a node to the graph."""
        self.nodes[node_id] = {
            "type": node_type,
            **data
        }

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """Add an edge to the graph."""
        self.edges.append((from_id, to_id, edge_type))

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
        }