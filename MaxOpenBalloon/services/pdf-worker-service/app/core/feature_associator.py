"""Stage 4: Feature Association - Detect and associate manufacturing features."""

from __future__ import annotations

import logging
import math
import re
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from app.domain.vector_models import DrawingDocument, Line, Point, Polyline, TextBlock
from app.domain.dimension_models import DimensionGroup
from app.domain.feature_models import (
    Chamfer,
    FeatureAssociation,
    FeatureAssociationResult,
    Hole,
    Radius,
    Slot,
    Thread,
)

logger = logging.getLogger(__name__)


class HoleDetector:
    """Detect circular holes in drawings."""

    def __init__(self, min_radius: float = 5.0, max_radius: float = 200.0):
        """
        Initialize hole detector.
        
        Args:
            min_radius: Minimum hole radius
            max_radius: Maximum hole radius
        """
        self.min_radius = min_radius
        self.max_radius = max_radius

    def detect(self, polylines: list[Polyline], circles: list, 
               text_blocks: list[TextBlock]) -> list[Hole]:
        """
        Detect holes from closed circular polylines.
        
        Args:
            polylines: List of polylines from Stage 1
            circles: List of detected circles
            text_blocks: List of text blocks for dimension association
            
        Returns:
            List of detected holes
        """
        holes = []
        
        # Detect from closed polylines (circles)
        for polyline in polylines:
            if not polyline.closed:
                continue
            
            hole = self._analyze_closed_polyline(polyline, text_blocks)
            if hole:
                holes.append(hole)
        
        # Detect from explicit circles
        for circle in circles:
            hole = self._analyze_circle(circle, text_blocks)
            if hole:
                holes.append(hole)
        
        return holes

    def _analyze_closed_polyline(self, polyline: Polyline, 
                                 text_blocks: list[TextBlock]) -> Hole | None:
        """Analyze if a closed polyline is a hole."""
        try:
            points = polyline.points
            if len(points) < 8:  # Need enough points for a circle
                return None
            
            # Get bounding box
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            
            # Calculate center
            center_x = sum(xs) / len(xs)
            center_y = sum(ys) / len(ys)
            
            # Calculate average radius
            distances = [math.sqrt((p.x - center_x)**2 + (p.y - center_y)**2) for p in points]
            avg_radius = sum(distances) / len(distances)
            radius_std = math.sqrt(sum((d - avg_radius)**2 for d in distances) / len(distances))
            
            # Check if roughly circular (low standard deviation)
            if radius_std > avg_radius * 0.2:  # 20% tolerance
                return None
            
            # Size check
            if not (self.min_radius <= avg_radius <= self.max_radius):
                return None
            
            # Calculate diameter
            diameter = avg_radius * 2
            
            # Find associated dimension text
            dimension_text = self._find_dimension_text(
                Point(x=center_x, y=center_y),
                text_blocks
            )
            
            bbox = (min(xs), min(ys), max(xs), max(ys))
            
            return Hole(
                center=Point(x=center_x, y=center_y),
                diameter=diameter,
                bbox=bbox,
                confidence=0.8 if radius_std < avg_radius * 0.1 else 0.6,
                metadata={
                    "radius_std": radius_std,
                    "point_count": len(points),
                    "dimension_text": dimension_text,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze closed polyline: {exc}")
            return None

    def _analyze_circle(self, circle: dict, text_blocks: list[TextBlock]) -> Hole | None:
        """Analyze explicit circle object."""
        try:
            # Extract circle parameters
            center_x = circle.get('center', (0, 0))[0]
            center_y = circle.get('center', (0, 0))[1]
            radius = circle.get('radius', 0)
            
            if not (self.min_radius <= radius <= self.max_radius):
                return None
            
            diameter = radius * 2
            
            # Find associated dimension text
            dimension_text = self._find_dimension_text(
                Point(x=center_x, y=center_y),
                text_blocks
            )
            
            bbox = (
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius
            )
            
            return Hole(
                center=Point(x=center_x, y=center_y),
                diameter=diameter,
                bbox=bbox,
                confidence=0.9,
                metadata={
                    "dimension_text": dimension_text,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze circle: {exc}")
            return None

    def _find_dimension_text(self, center: Point, 
                            text_blocks: list[TextBlock], 
                            max_distance: float = 100.0) -> str:
        """Find dimension text near a hole center."""
        best_text = ""
        best_distance = float('inf')
        
        for text_block in text_blocks:
            text_center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
            text_center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
            
            distance = math.sqrt(
                (text_center_x - center.x)**2 +
                (text_center_y - center.y)**2
            )
            
            # Look for diameter symbols (Ø) or dimension patterns
            if distance < max_distance and distance < best_distance:
                text = text_block.text
                # Prefer text with diameter symbol or dimension pattern
                if 'Ø' in text or '⌀' in text or any(c.isdigit() for c in text):
                    best_distance = distance
                    best_text = text
        
        return best_text


class SlotDetector:
    """Detect slot features (elongated holes)."""

    def __init__(self, min_width: float = 3.0, max_width: float = 50.0,
                 min_length: float = 10.0, max_length: float = 500.0):
        """
        Initialize slot detector.
        
        Args:
            min_width: Minimum slot width
            max_width: Maximum slot width
            min_length: Minimum slot length
            max_length: Maximum slot length
        """
        self.min_width = min_width
        self.max_width = max_width
        self.min_length = min_length
        self.max_length = max_length

    def detect(self, polylines: list[Polyline], 
               text_blocks: list[TextBlock]) -> list[Slot]:
        """
        Detect slots from elongated closed shapes.
        
        Args:
            polylines: List of polylines
            text_blocks: List of text blocks
            
        Returns:
            List of detected slots
        """
        slots = []
        
        for polyline in polylines:
            if not polyline.closed:
                continue
            
            slot = self._analyze_slot(polyline, text_blocks)
            if slot:
                slots.append(slot)
        
        return slots

    def _analyze_slot(self, polyline: Polyline, 
                      text_blocks: list[TextBlock]) -> Slot | None:
        """Analyze if a closed polyline is a slot."""
        try:
            points = polyline.points
            if len(points) < 8:
                return None
            
            # Get bounding box
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)
            
            # Slots are elongated (length >> width)
            aspect_ratio = max(width, height) / (min(width, height) + 1e-6)
            
            if aspect_ratio < 2.0:  # Not elongated enough
                return None
            
            # Size checks
            slot_length = max(width, height)
            slot_width = min(width, height)
            
            if not (self.min_width <= slot_width <= self.max_width):
                return None
            
            if not (self.min_length <= slot_length <= self.max_length):
                return None
            
            # Calculate center
            center_x = (min(xs) + max(xs)) / 2
            center_y = (min(ys) + max(ys)) / 2
            
            # Calculate angle (orientation)
            angle = math.degrees(math.atan2(height, width)) if width > height else math.degrees(math.atan2(width, height))
            
            # Find associated dimension text
            dimension_text = self._find_dimension_text(
                Point(x=center_x, y=center_y),
                text_blocks
            )
            
            bbox = (min(xs), min(ys), max(xs), max(ys))
            
            return Slot(
                center=Point(x=center_x, y=center_y),
                width=slot_width,
                length=slot_length,
                angle=angle,
                bbox=bbox,
                confidence=0.7,
                metadata={
                    "aspect_ratio": aspect_ratio,
                    "dimension_text": dimension_text,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze slot: {exc}")
            return None

    def _find_dimension_text(self, center: Point, 
                            text_blocks: list[TextBlock], 
                            max_distance: float = 100.0) -> str:
        """Find dimension text near a slot center."""
        best_text = ""
        best_distance = float('inf')
        
        for text_block in text_blocks:
            text_center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
            text_center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
            
            distance = math.sqrt(
                (text_center_x - center.x)**2 +
                (text_center_y - center.y)**2
            )
            
            if distance < max_distance and distance < best_distance:
                best_distance = distance
                best_text = text_block.text
        
        return best_text


class ChamferDetector:
    """Detect chamfer (beveled edge) features."""

    def __init__(self, min_length: float = 2.0, max_length: float = 100.0):
        """
        Initialize chamfer detector.
        
        Args:
            min_length: Minimum chamfer length
            max_length: Maximum chamfer length
        """
        self.min_length = min_length
        self.max_length = max_length

    def detect(self, lines: list[Line], corners: list[dict]) -> list[Chamfer]:
        """
        Detect chamfers from lines at corners.
        
        Args:
            lines: List of line segments
            corners: List of detected corners
            
        Returns:
            List of detected chamfers
        """
        chamfers = []
        
        # Look for short lines at corners (chamfers)
        for corner in corners:
            chamfer = self._analyze_corner(corner, lines)
            if chamfer:
                chamfers.append(chamfer)
        
        return chamfers

    def _analyze_corner(self, corner: dict, lines: list[Line]) -> Chamfer | None:
        """Analyze if a corner has a chamfer."""
        try:
            # Chamfer is typically a short line at 45 degrees
            corner_x = corner.get('x', 0)
            corner_y = corner.get('y', 0)
            
            # Find lines near this corner
            nearby_lines = []
            for line in lines:
                dist_to_corner = self._point_to_line_distance(
                    Point(x=corner_x, y=corner_y),
                    line.start,
                    line.end
                )
                if dist_to_corner < 10:
                    nearby_lines.append(line)
            
            if len(nearby_lines) < 2:
                return None
            
            # Look for a short connecting line (the chamfer)
            for line in nearby_lines:
                length = math.sqrt(
                    (line.end.x - line.start.x)**2 +
                    (line.end.y - line.start.y)**2
                )
                
                if self.min_length <= length <= self.max_length:
                    # Check if angle is approximately 45 degrees
                    angle = math.degrees(math.atan2(
                        line.end.y - line.start.y,
                        line.end.x - line.start.x
                    ))
                    
                    # Chamfer typically at 45 degrees (±10 degrees)
                    if 35 <= abs(angle) <= 55 or 125 <= abs(angle) <= 145:
                        return Chamfer(
                            start=line.start,
                            end=line.end,
                            length=length,
                            angle=45.0,
                            bbox=self._calculate_bbox(line),
                            confidence=0.7,
                            metadata={
                                "corner_x": corner_x,
                                "corner_y": corner_y,
                            }
                        )
            
            return None
            
        except Exception as exc:
            logger.debug(f"Failed to analyze corner: {exc}")
            return None

    def _point_to_line_distance(self, point: Point, line_start: Point, 
                               line_end: Point) -> float:
        """Calculate distance from point to line segment."""
        x0, y0 = point.x, point.y
        x1, y1 = line_start.x, line_start.y
        x2, y2 = line_end.x, line_end.y
        
        line_length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        if line_length == 0:
            return math.sqrt((x0 - x1)**2 + (y0 - y1)**2)
        
        t = max(0, min(1, ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / (line_length**2)))
        proj_x = x1 + t * (x2 - x1)
        proj_y = y1 + t * (y2 - y1)
        
        return math.sqrt((x0 - proj_x)**2 + (y0 - proj_y)**2)

    def _calculate_bbox(self, line: Line) -> tuple[float, float, float, float]:
        """Calculate bounding box for a line."""
        x0 = min(line.start.x, line.end.x)
        y0 = min(line.start.y, line.end.y)
        x1 = max(line.start.x, line.end.x)
        y1 = max(line.start.y, line.end.y)
        return (x0, y0, x1, y1)


class RadiusDetector:
    """Detect radius (rounded corner) features."""

    def __init__(self, min_radius: float = 2.0, max_radius: float = 100.0):
        """
        Initialize radius detector.
        
        Args:
            min_radius: Minimum radius value
            max_radius: Maximum radius value
        """
        self.min_radius = min_radius
        self.max_radius = max_radius

    def detect(self, polylines: list[Polyline], 
               arcs: list[dict]) -> list[Radius]:
        """
        Detect radii from arc segments.
        
        Args:
            polylines: List of polylines
            arcs: List of detected arcs
            
        Returns:
            List of detected radii
        """
        radii = []
        
        # Detect from arc objects
        for arc in arcs:
            radius = self._analyze_arc(arc)
            if radius:
                radii.append(radius)
        
        # Detect from polylines (arc segments)
        for polyline in polylines:
            if polyline.closed:
                continue  # Skip closed shapes (handled by hole/slot detector)
            
            radius = self._analyze_polyline_arc(polyline)
            if radius:
                radii.append(radius)
        
        return radii

    def _analyze_arc(self, arc: dict) -> Radius | None:
        """Analyze explicit arc object."""
        try:
            center_x = arc.get('center', (0, 0))[0]
            center_y = arc.get('center', (0, 0))[1]
            radius = arc.get('radius', 0)
            start_angle = arc.get('start_angle', 0)
            end_angle = arc.get('end_angle', 90)
            
            if not (self.min_radius <= radius <= self.max_radius):
                return None
            
            bbox = (
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius
            )
            
            return Radius(
                center=Point(x=center_x, y=center_y),
                radius=radius,
                start_angle=start_angle,
                end_angle=end_angle,
                bbox=bbox,
                confidence=0.9,
                metadata={}
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze arc: {exc}")
            return None

    def _analyze_polyline_arc(self, polyline: Polyline) -> Radius | None:
        """Analyze polyline to detect arc segments."""
        try:
            points = polyline.points
            if len(points) < 5:
                return None
            
            # Check if points form an arc (not a straight line)
            # Calculate curvature
            curvatures = []
            for i in range(1, len(points) - 1):
                p0 = points[i - 1]
                p1 = points[i]
                p2 = points[i + 1]
                
                # Calculate curvature using cross product
                v1 = (p1.x - p0.x, p1.y - p0.y)
                v2 = (p2.x - p1.x, p2.y - p1.y)
                
                cross = v1[0] * v2[1] - v1[1] * v2[0]
                len1 = math.sqrt(v1[0]**2 + v1[1]**2)
                len2 = math.sqrt(v2[0]**2 + v2[1]**2)
                
                if len1 > 0 and len2 > 0:
                    curvature = abs(cross) / (len1 * len2)
                    curvatures.append(curvature)
            
            if not curvatures:
                return None
            
            avg_curvature = sum(curvatures) / len(curvatures)
            
            # If curvature is significant, it's likely an arc
            if avg_curvature < 0.1:
                return None
            
            # Estimate radius from curvature
            estimated_radius = 1.0 / (avg_curvature + 1e-6)
            
            if not (self.min_radius <= estimated_radius <= self.max_radius):
                return None
            
            # Calculate center (simplified - use centroid)
            xs = [p.x for p in points]
            ys = [p.y for p in points]
            center_x = sum(xs) / len(xs)
            center_y = sum(ys) / len(ys)
            
            bbox = (min(xs), min(ys), max(xs), max(ys))
            
            return Radius(
                center=Point(x=center_x, y=center_y),
                radius=estimated_radius,
                bbox=bbox,
                confidence=0.6,
                metadata={
                    "curvature": avg_curvature,
                    "point_count": len(points),
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze polyline arc: {exc}")
            return None


class ThreadDetector:
    """Detect thread features."""

    def __init__(self):
        """Initialize thread detector."""
        # Thread patterns in text
        self.thread_patterns = [
            re.compile(r'M\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?'),  # Metric: M8x1.25
            re.compile(r'\d+(?:\.\d+)?-\d+(?:\.\d+)?'),  # Imperial: 1/4-20
            re.compile(r'UNC', re.IGNORECASE),  # Unified National Coarse
            re.compile(r'UNF', re.IGNORECASE),  # Unified National Fine
            re.compile(r'ACME', re.IGNORECASE),  # Acme thread
        ]

    def detect(self, text_blocks: list[TextBlock], 
               cylinders: list[dict]) -> list[Thread]:
        """
        Detect threads from text and cylindrical features.
        
        Args:
            text_blocks: List of text blocks
            cylinders: List of detected cylinders
            
        Returns:
            List of detected threads
        """
        threads = []
        
        # Detect from text patterns
        for text_block in text_blocks:
            thread = self._analyze_thread_text(text_block)
            if thread:
                threads.append(thread)
        
        return threads

    def _analyze_thread_text(self, text_block: TextBlock) -> Thread | None:
        """Analyze text for thread specification."""
        try:
            text = text_block.text.strip()
            
            # Check if text matches thread pattern
            thread_type = self._identify_thread_type(text)
            if not thread_type:
                return None
            
            # Extract diameter and pitch
            diameter, pitch = self._extract_thread_params(text)
            
            # Calculate center
            center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
            center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
            
            return Thread(
                center=Point(x=center_x, y=center_y),
                diameter=diameter,
                pitch=pitch,
                thread_type=thread_type,
                bbox=text_block.bbox,
                confidence=0.8,
                metadata={
                    "source_text": text,
                    "font_name": text_block.font_name,
                }
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze thread text: {exc}")
            return None

    def _identify_thread_type(self, text: str) -> str:
        """Identify thread type from text."""
        text_upper = text.upper()
        
        if 'M' in text_upper and any(c.isdigit() for c in text):
            return 'metric'
        elif 'UNC' in text_upper:
            return 'unc'
        elif 'UNF' in text_upper:
            return 'unf'
        elif 'ACME' in text_upper:
            return 'acme'
        elif re.search(r'\d+-\d+', text):
            return 'imperial'
        
        return ''

    def _extract_thread_params(self, text: str) -> tuple[float, float | None]:
        """Extract diameter and pitch from thread text."""
        diameter = 0.0
        pitch = None
        
        # Metric: M8x1.25 or M8
        metric_match = re.search(r'M(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?', text, re.IGNORECASE)
        if metric_match:
            diameter = float(metric_match.group(1))
            if metric_match.group(2):
                pitch = float(metric_match.group(2))
            return diameter, pitch
        
        # Imperial: 1/4-20 or 0.25-20
        imperial_match = re.search(r'(\d+(?:\.\d+)?)\s*-\s*(\d+)', text)
        if imperial_match:
            diameter = float(imperial_match.group(1))
            pitch = float(imperial_match.group(2))
            return diameter, pitch
        
        return diameter, pitch


class FeatureAssociator:
    """Main class for feature detection and association."""

    def __init__(self):
        """Initialize feature associator."""
        self.hole_detector = HoleDetector()
        self.slot_detector = SlotDetector()
        self.chamfer_detector = ChamferDetector()
        self.radius_detector = RadiusDetector()
        self.thread_detector = ThreadDetector()

    def associate(self, drawing: DrawingDocument, 
                  dimensions: list[DimensionGroup] | None = None) -> FeatureAssociationResult:
        """
        Detect features and associate with dimensions.
        
        Args:
            drawing: DrawingDocument from Stage 1
            dimensions: Optional dimension groups from Stage 2
            
        Returns:
            FeatureAssociationResult with all detected features
        """
        # Step 1: Detect holes
        holes = self.hole_detector.detect(
            drawing.polylines,
            [],  # Circles would be detected separately
            drawing.text_blocks
        )
        logger.info(f"Detected {len(holes)} holes")
        
        # Step 2: Detect slots
        slots = self.slot_detector.detect(
            drawing.polylines,
            drawing.text_blocks
        )
        logger.info(f"Detected {len(slots)} slots")
        
        # Step 3: Detect chamfers
        # Note: Need corner detection - simplified for now
        chamfers = self.chamfer_detector.detect(drawing.lines, [])
        logger.info(f"Detected {len(chamfers)} chamfers")
        
        # Step 4: Detect radii
        # Note: Need arc detection - simplified for now
        radii = self.radius_detector.detect(drawing.polylines, [])
        logger.info(f"Detected {len(radii)} radii")
        
        # Step 5: Detect threads
        threads = self.thread_detector.detect(drawing.text_blocks, [])
        logger.info(f"Detected {len(threads)} threads")
        
        # Step 6: Associate features with dimensions
        associations = self._associate_features_with_dimensions(
            holes, slots, chamfers, radii, threads,
            dimensions or [],
            drawing.text_blocks
        )
        logger.info(f"Created {len(associations)} feature-dimension associations")
        
        # Build result
        result = FeatureAssociationResult(
            document_id=drawing.document_id,
            holes=holes,
            slots=slots,
            chamfers=chamfers,
            radii=radii,
            threads=threads,
            associations=associations,
            statistics={
                "holes": len(holes),
                "slots": len(slots),
                "chamfers": len(chamfers),
                "radii": len(radii),
                "threads": len(threads),
                "associations": len(associations),
            }
        )
        
        return result

    def _associate_features_with_dimensions(
        self,
        holes: list[Hole],
        slots: list[Slot],
        chamfers: list[Chamfer],
        radii: list[Radius],
        threads: list[Thread],
        dimensions: list[DimensionGroup],
        text_blocks: list[TextBlock],
    ) -> list[FeatureAssociation]:
        """Associate features with their dimension text."""
        associations = []
        
        # Associate holes with dimensions
        for hole in holes:
            assoc = self._find_nearest_dimension(hole, dimensions, "hole")
            if assoc:
                associations.append(assoc)
        
        # Associate slots with dimensions
        for slot in slots:
            assoc = self._find_nearest_dimension(slot, dimensions, "slot")
            if assoc:
                associations.append(assoc)
        
        # Associate chamfers with dimensions
        for chamfer in chamfers:
            assoc = self._find_nearest_dimension(chamfer, dimensions, "chamfer")
            if assoc:
                associations.append(assoc)
        
        # Associate radii with dimensions
        for radius in radii:
            assoc = self._find_nearest_dimension(radius, dimensions, "radius")
            if assoc:
                associations.append(assoc)
        
        # Associate threads with dimensions
        for thread in threads:
            assoc = self._find_nearest_dimension(thread, dimensions, "thread")
            if assoc:
                associations.append(assoc)
        
        return associations

    def _find_nearest_dimension(self, feature: Any, 
                               dimensions: list[DimensionGroup],
                               feature_type: str) -> FeatureAssociation | None:
        """Find the nearest dimension to a feature."""
        if not dimensions:
            return None
        
        # Get feature center
        if hasattr(feature, 'center'):
            feature_center = feature.center
        else:
            return None
        
        nearest_dim = None
        min_distance = float('inf')
        
        for dim_group in dimensions:
            if not dim_group.dimension_line:
                continue
            
            # Calculate distance from feature to dimension line center
            dim_center_x = (dim_group.dimension_line.start.x + dim_group.dimension_line.end.x) / 2
            dim_center_y = (dim_group.dimension_line.start.y + dim_group.dimension_line.end.y) / 2
            
            distance = math.sqrt(
                (feature_center.x - dim_center_x)**2 +
                (feature_center.y - dim_center_y)**2
            )
            
            if distance < min_distance and distance < 200:  # Max 200 pts association distance
                min_distance = distance
                nearest_dim = dim_group
        
        if nearest_dim:
            return FeatureAssociation(
                feature_id=feature.id,
                feature_type=feature_type,
                dimension_text=nearest_dim.text,
                dimension_value=nearest_dim.text,
                dimension_id=nearest_dim.id,
                bbox=feature.bbox if hasattr(feature, 'bbox') else (0, 0, 0, 0),
                confidence=0.7,
                metadata={
                    "distance": min_distance,
                }
            )
        
        return None