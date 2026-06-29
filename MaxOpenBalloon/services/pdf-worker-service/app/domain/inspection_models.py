"""Stage 5: Inspection Characteristic Extraction - Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class InspectionCharacteristic:
    """Complete inspection characteristic with all required information."""
    id: str = field(default_factory=lambda: str(uuid4()))
    characteristic_number: int = 0
    feature_type: str = ""  # "hole", "slot", "chamfer", "radius", "thread", "linear_dimension", "angular_dimension"
    feature_id: str = ""
    dimension: str = ""  # e.g., "Ø12", "20", "M8x1.25"
    tolerance: str = ""  # e.g., "±0.05", "+0.0/-0.1", "0.1"
    datums: list[str] = field(default_factory=list)  # ["A", "B", "C"]
    gdt_type: str = ""  # "position", "profile", "flatness", etc.
    gdt_value: str = ""  # e.g., "0.1", "±0.05"
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "characteristic": self.characteristic_number,
            "feature": self.feature_type,
            "dimension": self.dimension,
            "tolerance": self.tolerance,
            "datum": self.datums,
            "gdt_type": self.gdt_type if self.gdt_type else None,
            "gdt_value": self.gdt_value if self.gdt_value else None,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class InspectionResult:
    """Complete inspection extraction result."""
    document_id: str = ""
    characteristics: list[InspectionCharacteristic] = field(default_factory=list)
    statistics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "characteristics": [c.to_dict() for c in self.characteristics],
            "statistics": self.statistics,
        }