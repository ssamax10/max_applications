"""Stage 5: Inspection Characteristic Extraction - Final stage producing structured output."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from app.domain.vector_models import DrawingDocument, TextBlock
from app.domain.dimension_models import DimensionGroup
from app.domain.gdt_models import GDTSet
from app.domain.feature_models import (
    FeatureAssociationResult,
    Hole,
    Slot,
    Chamfer,
    Radius,
    Thread,
)
from app.domain.inspection_models import InspectionCharacteristic, InspectionResult

logger = logging.getLogger(__name__)


class InspectionExtractor:
    """Extract complete inspection characteristics by merging all stage outputs."""

    def __init__(self):
        """Initialize inspection extractor."""
        # Tolerance value patterns
        self.tolerance_pattern = re.compile(r'[±]?\s*[\d.]+\s*(?:%|mm|in|°)?')
        self.dimension_pattern = re.compile(
            r'(?:Ø|⌀)?\s*[\d.]+\s*(?:x\d+(?:\.\d+)?)?\s*(?:mm|cm|m|in)?'
        )

    def extract(
        self,
        drawing: DrawingDocument,
        dimensions: list[DimensionGroup],
        gdt_sets: list[GDTSet],
        features: FeatureAssociationResult,
    ) -> InspectionResult:
        """
        Extract inspection characteristics from all stages.
        
        Args:
            drawing: Stage 1 vector data
            dimensions: Stage 2 dimension groups
            gdt_sets: Stage 3 GD&T sets
            features: Stage 4 feature associations
            
        Returns:
            InspectionResult with all characteristics
        """
        characteristics = []
        char_number = 1
        
        # Process features with their associations
        for feature_type, feature_list, feature_name in [
            (features.holes, features.holes, "hole"),
            (features.slots, features.slots, "slot"),
            (features.chamfers, features.chamfers, "chamfer"),
            (features.radii, features.radii, "radius"),
            (features.threads, features.threads, "thread"),
        ]:
            for feature in feature_list:
                # Find associated dimension
                assoc = self._find_feature_association(feature.id, features.associations)
                
                # Find associated GD&T
                gdt = self._find_associated_gdt(feature, gdt_sets)
                
                # Extract dimension and tolerance
                dimension, tolerance = self._parse_dimension_and_tolerance(
                    assoc.dimension_text if assoc else ""
                )
                
                # Create characteristic
                char = InspectionCharacteristic(
                    characteristic_number=char_number,
                    feature_type=feature_name,
                    feature_id=feature.id,
                    dimension=dimension,
                    tolerance=tolerance,
                    datums=gdt.datums if gdt else [],
                    gdt_type=gdt.tolerance.gdt_type if gdt and gdt.tolerance else "",
                    gdt_value=gdt.tolerance.value if gdt and gdt.tolerance else "",
                    bbox=feature.bbox if hasattr(feature, 'bbox') else (0, 0, 0, 0),
                    confidence=assoc.confidence if assoc else 0.5,
                    metadata={
                        "feature_data": feature.to_dict(),
                        "gdt_data": gdt.to_dict() if gdt else None,
                        "association_data": assoc.to_dict() if assoc else None,
                    }
                )
                characteristics.append(char)
                char_number += 1
        
        # Process standalone dimensions (dimensions without features)
        for dim_group in dimensions:
            # Check if this dimension is already associated with a feature
            is_associated = any(
                assoc.dimension_id == dim_group.id
                for assoc in features.associations
            )
            
            if not is_associated and dim_group.text:
                # This is a standalone dimension
                dimension, tolerance = self._parse_dimension_and_tolerance(dim_group.text)
                
                char = InspectionCharacteristic(
                    characteristic_number=char_number,
                    feature_type="linear_dimension" if dim_group.dimension_type == "linear" else "angular_dimension",
                    dimension=dimension,
                    tolerance=tolerance,
                    bbox=dim_group.text_bbox,
                    confidence=dim_group.confidence,
                    metadata={
                        "dimension_data": dim_group.to_dict(),
                    }
                )
                characteristics.append(char)
                char_number += 1
        
        # Process standalone GD&T (GD&T without features)
        for gdt_set in gdt_sets:
            # Check if this GD&T is already associated with a feature
            is_associated = any(
                char.gdt_type == gdt_set.tolerance.gdt_type and char.gdt_value == gdt_set.tolerance.value
                for char in characteristics
            )
            
            if not is_associated and gdt_set.tolerance:
                char = InspectionCharacteristic(
                    characteristic_number=char_number,
                    feature_type="gdt_tolerance",
                    dimension=gdt_set.tolerance.value,
                    tolerance=gdt_set.tolerance.value,
                    datums=gdt_set.tolerance.datums,
                    gdt_type=gdt_set.tolerance.gdt_type,
                    gdt_value=gdt_set.tolerance.value,
                    bbox=gdt_set.tolerance.bbox,
                    confidence=gdt_set.confidence,
                    metadata={
                        "gdt_data": gdt_set.to_dict(),
                    }
                )
                characteristics.append(char)
                char_number += 1
        
        # Build result
        result = InspectionResult(
            document_id=drawing.document_id,
            characteristics=characteristics,
            statistics={
                "total_characteristics": len(characteristics),
                "features": len(characteristics) - sum(1 for c in characteristics if c.feature_type in ["linear_dimension", "angular_dimension", "gdt_tolerance"]),
                "dimensions": sum(1 for c in characteristics if c.feature_type in ["linear_dimension", "angular_dimension"]),
                "gdt_tolerances": sum(1 for c in characteristics if c.feature_type == "gdt_tolerance"),
            }
        )
        
        return result

    def _find_feature_association(self, feature_id: str, 
                                  associations: list[Any]) -> Any | None:
        """Find association for a feature."""
        for assoc in associations:
            if assoc.feature_id == feature_id:
                return assoc
        return None

    def _find_associated_gdt(self, feature: Any, gdt_sets: list[GDTSet]) -> GDTSet | None:
        """Find GD&T set associated with a feature."""
        # Get feature center
        if not hasattr(feature, 'center'):
            return None
        
        feature_center = feature.center
        
        # Find nearest GD&T set
        nearest_gdt = None
        min_distance = float('inf')
        
        for gdt_set in gdt_sets:
            if not gdt_set.tolerance:
                continue
            
            # Calculate distance
            gdt_center_x = gdt_set.tolerance.center.x
            gdt_center_y = gdt_set.tolerance.center.y
            
            distance = (
                (feature_center.x - gdt_center_x)**2 +
                (feature_center.y - gdt_center_y)**2
            ) ** 0.5
            
            if distance < min_distance and distance < 150:
                min_distance = distance
                nearest_gdt = gdt_set
        
        return nearest_gdt

    def _parse_dimension_and_tolerance(self, text: str) -> tuple[str, str]:
        """
        Parse dimension and tolerance from text.
        
        Args:
            text: Dimension text (e.g., "Ø12 ±0.05", "20 +0.0/-0.1")
            
        Returns:
            Tuple of (dimension, tolerance)
        """
        if not text:
            return "", ""
        
        text = text.strip()
        
        # Try to extract tolerance
        tolerance_match = self.tolerance_pattern.search(text)
        tolerance = tolerance_match.group(0).strip() if tolerance_match else ""
        
        # Remove tolerance from dimension
        dimension = text
        if tolerance:
            dimension = text.replace(tolerance, "").strip()
        
        # Clean up dimension
        dimension = self.dimension_pattern.search(dimension)
        if dimension:
            dimension = dimension.group(0).strip()
        else:
            dimension = text
        
        return dimension, tolerance

    def extract_simple(self, drawing: DrawingDocument) -> InspectionResult:
        """
        Simple extraction without full pipeline (for basic documents).
        
        Args:
            drawing: DrawingDocument from Stage 1
            
        Returns:
            InspectionResult with basic characteristics
        """
        characteristics = []
        
        # Extract from text blocks
        for i, text_block in enumerate(drawing.text_blocks, 1):
            text = text_block.text.strip()
            
            # Check if it looks like a dimension
            if self._is_dimension_text(text):
                dimension, tolerance = self._parse_dimension_and_tolerance(text)
                
                char = InspectionCharacteristic(
                    characteristic_number=i,
                    feature_type="text_dimension",
                    dimension=dimension,
                    tolerance=tolerance,
                    bbox=text_block.bbox,
                    confidence=0.7,
                    metadata={
                        "source_text": text,
                        "font_name": text_block.font_name,
                    }
                )
                characteristics.append(char)
        
        # Build result
        result = InspectionResult(
            document_id=drawing.document_id,
            characteristics=characteristics,
            statistics={
                "total_characteristics": len(characteristics),
                "features": 0,
                "dimensions": len(characteristics),
                "gdt_tolerances": 0,
            }
        )
        
        return result

    def _is_dimension_text(self, text: str) -> bool:
        """Check if text looks like a dimension."""
        # Check for dimension patterns
        if re.search(r'\d+\.?\d*', text):
            return True
        return False