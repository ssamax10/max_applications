"""Stage 3: GD&T Recognition - Identify datum symbols and tolerances."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from app.domain.vector_models import DrawingDocument, Point, TextBlock
from app.domain.gdt_models import (
    DatumSymbol,
    GDTSet,
    GDTSymbol,
    GDTTolerance,
    GDTRecognitionResult,
)

logger = logging.getLogger(__name__)


class DatumSymbolDetector:
    """Detect datum reference symbols (A, B, C, etc.) in drawings."""

    # Datum symbol patterns
    DATUM_PATTERNS = [
        re.compile(r'\b([A-Z])\b'),  # Single capital letter
        re.compile(r'DATUM\s+([A-Z])', re.IGNORECASE),
        re.compile(r'⌀\s*([A-Z])'),  # Diameter symbol + letter
    ]

    def __init__(self):
        """Initialize datum symbol detector."""
        pass

    def detect(self, text_blocks: list[TextBlock]) -> list[DatumSymbol]:
        """
        Detect datum symbols from text blocks.
        
        Args:
            text_blocks: List of text blocks from Stage 1
            
        Returns:
            List of detected datum symbols
        """
        datum_symbols = []
        seen_labels = set()

        for text_block in text_blocks:
            text = text_block.text.strip()
            
            # Check if text matches datum pattern
            datum_label = self._extract_datum_label(text)
            
            if datum_label and datum_label not in seen_labels:
                seen_labels.add(datum_label)
                
                # Calculate center and bbox
                center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
                center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
                
                datum_symbol = DatumSymbol(
                    label=datum_label,
                    bbox=text_block.bbox,
                    center=Point(x=center_x, y=center_y),
                    confidence=0.9 if len(text) <= 2 else 0.7,
                    metadata={
                        "source_text": text,
                        "font_name": text_block.font_name,
                    }
                )
                datum_symbols.append(datum_symbol)

        return datum_symbols

    def _extract_datum_label(self, text: str) -> str | None:
        """Extract datum label from text."""
        # Clean text
        text = text.strip()
        
        # Check patterns
        for pattern in self.DATUM_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        
        # Single capital letter (A-Z)
        if len(text) == 1 and text.isupper() and text.isalpha():
            return text
        
        return None


class GDTSymbolDetector:
    """Detect GD&T symbol frames and boxes."""

    # GD&T symbol types and their characteristics
    SYMBOL_TYPES = {
        'position': {'shape': 'rectangle', 'features': ['⌀']},
        'profile': {'shape': 'rectangle', 'features': ['⌒', '⌓']},
        'runout': {'shape': 'rectangle', 'features': ['↗']},
        'flatness': {'shape': 'parallelogram', 'features': ['▱']},
        'straightness': {'shape': 'line', 'features': ['—']},
        'perpendicularity': {'shape': 'rectangle', 'features': ['⊥']},
        'parallelism': {'shape': 'rectangle', 'features': ['//']},
        'angularity': {'shape': 'rectangle', 'features': ['∠']},
        'cylindricity': {'shape': 'circle', 'features': ['○']},
        'circularity': {'shape': 'circle', 'features': ['◎']},
        'concentricity': {'shape': 'circle', 'features': ['◎']},
        'symmetry': {'shape': 'rectangle', 'features': ['≡']},
    }

    def __init__(self, min_size: float = 10.0, max_size: float = 100.0):
        """
        Initialize GD&T symbol detector.
        
        Args:
            min_size: Minimum symbol size
            max_size: Maximum symbol size
        """
        self.min_size = min_size
        self.max_size = max_size

    def detect(self, polylines: list, rectangles: list, text_blocks: list[TextBlock]) -> list[GDTSymbol]:
        """
        Detect GD&T symbols.
        
        Args:
            polylines: List of polylines from Stage 1
            rectangles: List of rectangles (from lines)
            text_blocks: List of text blocks
            
        Returns:
            List of detected GD&T symbols
        """
        gdt_symbols = []
        
        # Look for rectangular frames (common in GD&T)
        for rect in rectangles:
            symbol = self._analyze_rectangle(rect, text_blocks)
            if symbol:
                gdt_symbols.append(symbol)
        
        # Look for circular symbols
        for polyline in polylines:
            if polyline.closed and len(polyline.points) >= 8:
                symbol = self._analyze_circle(polyline, text_blocks)
                if symbol:
                    gdt_symbols.append(symbol)
        
        return gdt_symbols

    def _analyze_rectangle(self, rect, text_blocks: list[TextBlock]) -> GDTSymbol | None:
        """Analyze if a rectangle is a GD&T symbol frame."""
        try:
            # Calculate size
            width = abs(rect[2] - rect[0])
            height = abs(rect[3] - rect[1])
            
            # Size check
            if not (self.min_size <= width <= self.max_size and 
                    self.min_size <= height <= self.max_size):
                return None
            
            # Aspect ratio (GD&T frames are typically wider than tall)
            aspect_ratio = width / (height + 1e-6)
            if aspect_ratio < 0.5 or aspect_ratio > 5.0:
                return None
            
            # Check if there's text inside or near the rectangle
            center_x = (rect[0] + rect[2]) / 2
            center_y = (rect[1] + rect[3]) / 2
            
            # Look for nearby text
            nearby_text = self._find_nearby_text(center_x, center_y, text_blocks)
            
            # Determine symbol type based on nearby text
            symbol_type = self._determine_symbol_type(nearby_text)
            
            return GDTSymbol(
                symbol_type=symbol_type,
                bbox=(rect[0], rect[1], rect[2], rect[3]),
                center=Point(x=center_x, y=center_y),
                width=width,
                height=height,
                confidence=0.7 if nearby_text else 0.5,
                metadata={
                    "nearby_text": nearby_text,
                    "aspect_ratio": aspect_ratio,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze rectangle: {exc}")
            return None

    def _analyze_circle(self, polyline, text_blocks: list[TextBlock]) -> GDTSymbol | None:
        """Analyze if a closed polyline is a GD&T circular symbol."""
        try:
            # Calculate approximate center and radius
            points = polyline.points
            if len(points) < 8:
                return None
            
            # Get bounding box
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            
            # Size check
            if not (self.min_size <= width <= self.max_size and 
                    self.min_size <= height <= self.max_size):
                return None
            
            # Check if roughly circular
            aspect_ratio = width / (height + 1e-6)
            if aspect_ratio < 0.7 or aspect_ratio > 1.4:
                return None
            
            center_x = (min(xs) + max(xs)) / 2
            center_y = (min(ys) + max(ys)) / 2
            
            # Look for nearby text
            nearby_text = self._find_nearby_text(center_x, center_y, text_blocks)
            
            return GDTSymbol(
                symbol_type="circular",
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                center=Point(x=center_x, y=center_y),
                width=width,
                height=height,
                confidence=0.6,
                metadata={
                    "nearby_text": nearby_text,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze circle: {exc}")
            return None

    def _find_nearby_text(self, x: float, y: float, text_blocks: list[TextBlock], 
                          max_distance: float = 50.0) -> str:
        """Find text near a point."""
        nearby_texts = []
        
        for text_block in text_blocks:
            text_center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
            text_center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
            
            distance = ((text_center_x - x)**2 + (text_center_y - y)**2)**0.5
            
            if distance < max_distance:
                nearby_texts.append(text_block.text)
        
        return " ".join(nearby_texts) if nearby_texts else ""

    def _determine_symbol_type(self, nearby_text: str) -> str:
        """Determine GD&T symbol type from nearby text."""
        text_lower = nearby_text.lower()
        
        # Check for specific GD&T keywords
        if 'position' in text_lower or 'pos' in text_lower:
            return 'position'
        elif 'profile' in text_lower:
            return 'profile'
        elif 'runout' in text_lower or 'run-out' in text_lower:
            return 'runout'
        elif 'flatness' in text_lower:
            return 'flatness'
        elif 'straightness' in text_lower:
            return 'straightness'
        elif 'perpendicular' in text_lower or 'perp' in text_lower:
            return 'perpendicularity'
        elif 'parallel' in text_lower:
            return 'parallelism'
        elif 'angular' in text_lower:
            return 'angularity'
        elif 'cylindric' in text_lower:
            return 'cylindricity'
        elif 'circular' in text_lower or 'round' in text_lower:
            return 'circularity'
        elif 'concentric' in text_lower:
            return 'concentricity'
        elif 'symmetr' in text_lower:
            return 'symmetry'
        
        return 'unknown'


class GDTToleranceRecognizer:
    """Recognize GD&T tolerance values and datums."""

    # Tolerance value pattern (e.g., 0.1, ±0.05, .5)
    TOLERANCE_PATTERN = re.compile(r'[±]?\s*[\d.]+\s*(?:%|mm|in|°)?', re.IGNORECASE)
    
    # Datum reference pattern (e.g., A, B, C, A|B|C)
    DATUM_REF_PATTERN = re.compile(r'\b([A-Z](?:\s*[-|]\s*[A-Z])*)\b')

    def __init__(self):
        """Initialize GD&T tolerance recognizer."""
        pass

    def recognize(self, text_blocks: list[TextBlock], 
                  gdt_symbols: list[GDTSymbol]) -> list[GDTTolerance]:
        """
        Recognize GD&T tolerances from text.
        
        Args:
            text_blocks: List of text blocks
            gdt_symbols: Detected GD&T symbols
            
        Returns:
            List of recognized GD&T tolerances
        """
        tolerances = []
        
        for text_block in text_blocks:
            text = text_block.text.strip()
            
            # Try to extract tolerance information
            tolerance = self._parse_tolerance_text(text, text_block)
            if tolerance:
                tolerances.append(tolerance)
        
        return tolerances

    def _parse_tolerance_text(self, text: str, text_block: TextBlock) -> GDTTolerance | None:
        """Parse tolerance information from text."""
        try:
            # Extract tolerance value
            value_match = self.TOLERANCE_PATTERN.search(text)
            if not value_match:
                return None
            
            value = value_match.group(0).strip()
            
            # Extract datum references
            datums = self._extract_datums(text)
            
            # Determine GD&T type from context
            gdt_type = self._determine_gdt_type(text)
            
            # Calculate center
            center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
            center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
            
            return GDTTolerance(
                gdt_type=gdt_type,
                value=value,
                datums=datums,
                bbox=text_block.bbox,
                center=Point(x=center_x, y=center_y),
                confidence=0.8,
                metadata={
                    "source_text": text,
                    "font_name": text_block.font_name,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to parse tolerance text '{text}': {exc}")
            return None

    def _extract_datums(self, text: str) -> list[str]:
        """Extract datum references from text."""
        datums = []
        
        # Find datum references
        match = self.DATUM_REF_PATTERN.search(text)
        if match:
            datum_str = match.group(1)
            # Split on common separators
            for sep in ['-', '|', '/', ' ']:
                if sep in datum_str:
                    datums = [d.strip() for d in datum_str.split(sep) if d.strip()]
                    break
            
            if not datums and len(datum_str) <= 3:
                datums = list(datum_str.replace(' ', ''))
        
        return datums

    def _determine_gdt_type(self, text: str) -> str:
        """Determine GD&T type from text."""
        text_lower = text.lower()
        
        # Check for GD&T keywords
        if 'position' in text_lower or 'pos' in text_lower:
            return 'position'
        elif 'profile' in text_lower:
            return 'profile'
        elif 'runout' in text_lower or 'run-out' in text_lower:
            return 'runout'
        elif 'flatness' in text_lower:
            return 'flatness'
        elif 'straightness' in text_lower:
            return 'straightness'
        elif 'perpendicular' in text_lower:
            return 'perpendicularity'
        elif 'parallel' in text_lower:
            return 'parallelism'
        elif 'angular' in text_lower:
            return 'angularity'
        elif 'cylindric' in text_lower:
            return 'cylindricity'
        elif 'circular' in text_lower or 'round' in text_lower:
            return 'circularity'
        elif 'concentric' in text_lower:
            return 'concentricity'
        elif 'symmetr' in text_lower:
            return 'symmetry'
        
        return 'unknown'


class GDTSetBuilder:
    """Build complete GD&T sets by associating symbols, tolerances, and datums."""

    def __init__(self, association_distance: float = 100.0):
        """
        Initialize GD&T set builder.
        
        Args:
            association_distance: Maximum distance to associate components
        """
        self.association_distance = association_distance

    def build_sets(self, symbols: list[GDTSymbol], tolerances: list[GDTTolerance],
                   datums: list[DatumSymbol]) -> list[GDTSet]:
        """
        Build GD&T sets by associating components.
        
        Args:
            symbols: Detected GD&T symbols
            tolerances: Recognized tolerances
            datums: Detected datum symbols
            
        Returns:
            List of complete GD&T sets
        """
        gdt_sets = []
        
        # Associate tolerances with symbols
        for tolerance in tolerances:
            # Find nearest symbol
            nearest_symbol = self._find_nearest_symbol(tolerance, symbols)
            
            if nearest_symbol:
                # Find associated datums
                associated_datums = self._find_associated_datums(tolerance, datums)
                
                # Create GD&T set
                gdt_set = GDTSet(
                    symbol=nearest_symbol,
                    tolerance=tolerance,
                    datum_symbols=associated_datums,
                    associated_text=tolerance.value,
                    bbox=tolerance.bbox,
                    confidence=tolerance.confidence,
                    metadata={
                        "symbol_type": nearest_symbol.symbol_type,
                        "gdt_type": tolerance.gdt_type,
                    }
                )
                gdt_sets.append(gdt_set)
        
        return gdt_sets

    def _find_nearest_symbol(self, tolerance: GDTTolerance, 
                            symbols: list[GDTSymbol]) -> GDTSymbol | None:
        """Find the nearest GD&T symbol to a tolerance."""
        nearest = None
        min_distance = float('inf')
        
        for symbol in symbols:
            distance = self._calculate_distance(tolerance.center, symbol.center)
            
            if distance < min_distance and distance <= self.association_distance:
                min_distance = distance
                nearest = symbol
        
        return nearest

    def _find_associated_datums(self, tolerance: GDTTolerance,
                               datums: list[DatumSymbol]) -> list[DatumSymbol]:
        """Find datum symbols associated with a tolerance."""
        associated = []
        
        for datum_label in tolerance.datums:
            for datum in datums:
                if datum.label == datum_label:
                    associated.append(datum)
        
        return associated

    def _calculate_distance(self, p1: Any, p2: Any) -> float:
        """Calculate distance between two points."""
        return ((p1.x - p2.x)**2 + (p1.y - p2.y)**2)**0.5


class GDTRecognizer:
    """Main class for GD&T recognition."""

    def __init__(self):
        """Initialize GD&T recognizer."""
        self.datum_detector = DatumSymbolDetector()
        self.symbol_detector = GDTSymbolDetector()
        self.tolerance_recognizer = GDTToleranceRecognizer()
        self.set_builder = GDTSetBuilder()

    def recognize(self, drawing: DrawingDocument) -> GDTRecognitionResult:
        """
        Recognize GD&T features from drawing.
        
        Args:
            drawing: DrawingDocument from Stage 1
            
        Returns:
            GDTRecognitionResult with all detected GD&T features
        """
        # Step 1: Detect datum symbols
        datum_symbols = self.datum_detector.detect(drawing.text_blocks)
        logger.info(f"Detected {len(datum_symbols)} datum symbols")
        
        # Step 2: Detect GD&T symbols (frames, boxes, circles)
        # Note: We need rectangles from lines - for now, use empty list
        # In a full implementation, we'd detect rectangles from polylines
        gdt_symbols = self.symbol_detector.detect(
            drawing.polylines,
            [],  # Rectangles would be detected separately
            drawing.text_blocks
        )
        logger.info(f"Detected {len(gdt_symbols)} GD&T symbols")
        
        # Step 3: Recognize tolerances
        tolerances = self.tolerance_recognizer.recognize(
            drawing.text_blocks,
            gdt_symbols
        )
        logger.info(f"Recognized {len(tolerances)} GD&T tolerances")
        
        # Step 4: Build GD&T sets
        gdt_sets = self.set_builder.build_sets(
            gdt_symbols,
            tolerances,
            datum_symbols
        )
        logger.info(f"Built {len(gdt_sets)} GD&T sets")
        
        # Build result
        result = GDTRecognitionResult(
            document_id=drawing.document_id,
            datum_symbols=datum_symbols,
            gdt_tolerances=tolerances,
            gdt_symbols=gdt_symbols,
            gdt_sets=gdt_sets,
            statistics={
                "datum_symbols": len(datum_symbols),
                "gdt_symbols": len(gdt_symbols),
                "gdt_tolerances": len(tolerances),
                "gdt_sets": len(gdt_sets),
            }
        )
        
        return result