"""Stage 3: GD&T Recognition - Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.domain.vector_models import Point


@dataclass
class DatumSymbol:
    """Datum reference symbol (A, B, C, etc.)."""
    id: str = field(default_factory=lambda: str(uuid4()))
    label: str = ""  # A, B, C, etc.
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "bbox": list(self.bbox),
            "center": self.center.to_dict(),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class GDTTolerance:
    """Geometric Dimensioning and Tolerancing feature."""
    id: str = field(default_factory=lambda: str(uuid4()))
    gdt_type: str = ""  # "position", "profile", "runout", "flatness", "straightness", "perpendicularity", "parallelism", "angularity", "cylindricity", "circularity", "concentricity", "symmetry"
    value: str = ""  # Tolerance value (e.g., "0.1", "±0.05")
    datums: list[str] = field(default_factory=list)  # ["A", "B", "C"]
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "gdt_type": self.gdt_type,
            "value": self.value,
            "datums": self.datums,
            "bbox": list(self.bbox),
            "center": self.center.to_dict(),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class GDTSymbol:
    """GD&T symbol frame/box."""
    id: str = field(default_factory=lambda: str(uuid4()))
    symbol_type: str = ""  # "diameter", "spherical_diameter", "square", "position", "profile", "runout", "flatness", etc.
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    center: Point = field(default_factory=lambda: Point(x=0.0, y=0.0))
    width: float = 0.0
    height: float = 0.0
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol_type": self.symbol_type,
            "bbox": list(self.bbox),
            "center": self.center.to_dict(),
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class GDTSet:
    """Complete GD&T annotation with symbol, tolerance, and datums."""
    id: str = field(default_factory=lambda: str(uuid4()))
    symbol: GDTSymbol | None = None
    tolerance: GDTTolerance | None = None
    datum_symbols: list[DatumSymbol] = field(default_factory=list)
    associated_text: str = ""
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol.to_dict() if self.symbol else None,
            "tolerance": self.tolerance.to_dict() if self.tolerance else None,
            "datum_symbols": [ds.to_dict() for ds in self.datum_symbols],
            "associated_text": self.associated_text,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class GDTRecognitionResult:
    """Complete GD&T recognition result."""
    document_id: str = ""
    datum_symbols: list[DatumSymbol] = field(default_factory=list)
    gdt_tolerances: list[GDTTolerance] = field(default_factory=list)
    gdt_symbols: list[GDTSymbol] = field(default_factory=list)
    gdt_sets: list[GDTSet] = field(default_factory=list)
    statistics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "datum_symbols": [ds.to_dict() for ds in self.datum_symbols],
            "gdt_tolerances": [gt.to_dict() for gt in self.gdt_tolerances],
            "gdt_symbols": [gs.to_dict() for gs in self.gdt_symbols],
            "gdt_sets": [gs.to_dict() for gs in self.gdt_sets],
            "statistics": self.statistics,
        }