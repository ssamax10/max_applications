"""Stage 4: Feature Association - Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.domain.vector_models import Point


@dataclass
class Hole:
    """Circular hole feature."""
    id: str = field(default_factory=lambda: str(uuid4()))
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    diameter: float = 0.0
    depth: float | None = None
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_type": "hole",
            "center": self.center.to_dict(),
            "diameter": self.diameter,
            "depth": self.depth,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class Slot:
    """Slot feature (elongated hole)."""
    id: str = field(default_factory=lambda: str(uuid4()))
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    width: float = 0.0
    length: float = 0.0
    angle: float = 0.0  # Orientation in degrees
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_type": "slot",
            "center": self.center.to_dict(),
            "width": self.width,
            "length": self.length,
            "angle": self.angle,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class Chamfer:
    """Chamfer (beveled edge) feature."""
    id: str = field(default_factory=lambda: str(uuid4()))
    start: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    end: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    length: float = 0.0
    angle: float = 45.0  # Typically 45 degrees
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_type": "chamfer",
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "length": self.length,
            "angle": self.angle,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class Radius:
    """Radius (rounded corner) feature."""
    id: str = field(default_factory=lambda: str(uuid4()))
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 90.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_type": "radius",
            "center": self.center.to_dict(),
            "radius": self.radius,
            "start_angle": self.start_angle,
            "end_angle": self.end_angle,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class Thread:
    """Thread feature."""
    id: str = field(default_factory=lambda: str(uuid4()))
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    diameter: float = 0.0
    pitch: float | None = None  # Thread pitch
    thread_type: str = ""  # "metric", "imperial", "acme", etc.
    length: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_type": "thread",
            "center": self.center.to_dict(),
            "diameter": self.diameter,
            "pitch": self.pitch,
            "thread_type": self.thread_type,
            "length": self.length,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class FeatureAssociation:
    """Association between a feature and its dimension."""
    id: str = field(default_factory=lambda: str(uuid4()))
    feature_id: str = ""
    feature_type: str = ""  # "hole", "slot", "chamfer", "radius", "thread"
    dimension_text: str = ""
    dimension_value: str = ""
    dimension_id: str | None = None
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "dimension_text": self.dimension_text,
            "dimension_value": self.dimension_value,
            "dimension_id": self.dimension_id,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class FeatureAssociationResult:
    """Complete feature association result."""
    document_id: str = ""
    holes: list[Hole] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    chamfers: list[Chamfer] = field(default_factory=list)
    radii: list[Radius] = field(default_factory=list)
    threads: list[Thread] = field(default_factory=list)
    associations: list[FeatureAssociation] = field(default_factory=list)
    statistics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "holes": [h.to_dict() for h in self.holes],
            "slots": [s.to_dict() for s in self.slots],
            "chamfers": [c.to_dict() for c in self.chamfers],
            "radii": [r.to_dict() for r in self.radii],
            "threads": [t.to_dict() for t in self.threads],
            "associations": [a.to_dict() for a in self.associations],
            "statistics": self.statistics,
        }