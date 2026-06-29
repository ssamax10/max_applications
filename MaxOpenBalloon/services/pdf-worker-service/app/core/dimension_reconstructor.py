"""Stage 2: Dimension Reconstruction - Identify and group dimension components."""

from __future__ import annotations

import logging
import math
from typing import Any
from uuid import uuid4

import numpy as np
import cv2

from app.domain.vector_models import DrawingDocument, Line, Point, TextBlock
from app.domain.dimension_models import (
    Arrowhead,
    DimensionGraph,
    DimensionGroup,
    DimensionLine,
    ExtensionLine,
)

logger = logging.getLogger(__name__)


class ArrowheadDetector:
    """Detect arrowheads in vector drawings using OpenCV."""

    def __init__(self, min_size: float = 5.0, max_size: float = 50.0):
        """
        Initialize arrowhead detector.
        
        Args:
            min_size: Minimum arrowhead size in points
            max_size: Maximum arrowhead size in points
        """
        self.min_size = min_size
        self.max_size = max_size

    def detect(self, lines: list[Line], image_size: tuple[int, int]) -> list[Arrowhead]:
        """
        Detect arrowheads from line endpoints.
        
        Args:
            lines: List of line segments
            image_size: (width, height) of the drawing
            
        Returns:
            List of detected arrowheads
        """
        arrowheads = []
        
        # Create binary image from lines
        img = np.zeros((image_size[1], image_size[0]), dtype=np.uint8)
        
        for line in lines:
            cv2.line(
                img,
                (int(line.start.x), int(line.start.y)),
                (int(line.end.x), int(line.end.y)),
                255,
                max(1, int(line.line_width))
            )
        
        # Find contours
        contours, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for contour in contours:
            # Approximate polygon
            epsilon = 0.05 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            
            # Arrowheads typically have 3-7 vertices
            if 3 <= len(approx) <= 7:
                arrowhead = self._analyze_contour(approx, lines)
                if arrowhead and self.min_size <= arrowhead.size <= self.max_size:
                    arrowheads.append(arrowhead)
        
        return arrowheads

    def _analyze_contour(self, contour: np.ndarray, lines: list[Line]) -> Arrowhead | None:
        """Analyze a contour to determine if it's an arrowhead."""
        try:
            # Get bounding box
            x, y, w, h = cv2.boundingRect(contour)
            bbox = (float(x), float(y), float(x + w), float(y + h))
            
            # Calculate size (diagonal of bounding box)
            size = math.sqrt(w**2 + h**2)
            
            # Get centroid
            M = cv2.moments(contour)
            if M["m00"] == 0:
                return None
            
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            
            # Find the tip (pointiest vertex)
            tip_idx = self._find_tip(contour)
            if tip_idx is None:
                return None
            
            tip_point = contour[tip_idx][0]
            
            # Find base (opposite side)
            base_point = self._find_base(contour, tip_point)
            
            # Calculate angle
            dx = tip_point[0] - base_point[0]
            dy = tip_point[1] - base_point[1]
            angle = math.degrees(math.atan2(dy, dx))
            
            # Calculate confidence based on shape regularity
            confidence = self._calculate_confidence(contour, tip_point, base_point)
            
            return Arrowhead(
                tip=Point(x=float(tip_point[0]), y=float(tip_point[1])),
                base=Point(x=float(base_point[0]), y=float(base_point[1])),
                angle=angle,
                size=size,
                confidence=confidence,
                bbox=bbox,
            )
            
        except Exception as exc:
            logger.debug(f"Failed to analyze contour: {exc}")
            return None

    def _find_tip(self, contour: np.ndarray) -> int | None:
        """Find the tip of the arrowhead (most acute angle)."""
        if len(contour) < 3:
            return None
        
        min_angle = float('inf')
        tip_idx = 0
        
        for i in range(len(contour)):
            # Get three consecutive points
            p0 = contour[i - 1][0]
            p1 = contour[i][0]
            p2 = contour[(i + 1) % len(contour)][0]
            
            # Calculate angle
            v1 = np.array([p0[0] - p1[0], p0[1] - p1[1]])
            v2 = np.array([p2[0] - p1[0], p2[1] - p1[1]])
            
            angle = np.arccos(
                np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)
            )
            
            if angle < min_angle:
                min_angle = angle
                tip_idx = i
        
        return tip_idx if min_angle < math.pi / 2 else None

    def _find_base(self, contour: np.ndarray, tip: np.ndarray) -> np.ndarray:
        """Find the base of the arrowhead (opposite the tip)."""
        # Find the point farthest from the tip
        max_dist = 0
        base_point = contour[0][0]
        
        for point in contour:
            dist = np.linalg.norm(point[0] - tip)
            if dist > max_dist:
                max_dist = dist
                base_point = point[0]
        
        return base_point

    def _calculate_confidence(self, contour: np.ndarray, tip: np.ndarray, base: np.ndarray) -> float:
        """Calculate confidence score for arrowhead detection."""
        try:
            # Factor 1: Aspect ratio (arrowheads are typically wider than tall or vice versa)
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = max(w, h) / (min(w, h) + 1e-6)
            aspect_score = min(1.0, aspect_ratio / 3.0)
            
            # Factor 2: Tip sharpness (already checked in _find_tip)
            tip_score = 0.8  # Good since we found a valid tip
            
            # Factor 3: Size reasonableness
            size = math.sqrt(w**2 + h**2)
            size_score = 1.0 if 5 <= size <= 50 else 0.5
            
            # Combined confidence
            confidence = (aspect_score + tip_score + size_score) / 3.0
            return min(1.0, max(0.0, confidence))
            
        except Exception:
            return 0.5


class ExtensionLineDetector:
    """Detect extension lines in dimension annotations."""

    def __init__(self, min_length: float = 10.0, max_length: float = 500.0):
        """
        Initialize extension line detector.
        
        Args:
            min_length: Minimum extension line length
            max_length: Maximum extension line length
        """
        self.min_length = min_length
        self.max_length = max_length

    def detect(
        self,
        lines: list[Line],
        dimension_lines: list[DimensionLine],
        tolerance: float = 5.0,
    ) -> list[ExtensionLine]:
        """
        Detect extension lines.
        
        Args:
            lines: All line segments in the drawing
            dimension_lines: Detected dimension lines
            tolerance: Pixel tolerance for parallel/perpendicular checks
            
        Returns:
            List of extension lines
        """
        extension_lines = []
        
        for dim_line in dimension_lines:
            # Extension lines are typically perpendicular to dimension line
            dim_angle = dim_line.angle
            
            # Find lines that are perpendicular to dimension line
            for line in lines:
                line_angle = self._calculate_line_angle(line)
                angle_diff = abs(self._normalize_angle(line_angle - dim_angle))
                
                # Perpendicular (90 degrees ± tolerance)
                if 90 - tolerance <= angle_diff <= 90 + tolerance:
                    ext_line = self._create_extension_line(line, dim_line)
                    if ext_line and self.min_length <= ext_line.length <= self.max_length:
                        extension_lines.append(ext_line)
        
        return extension_lines

    def _calculate_line_angle(self, line: Line) -> float:
        """Calculate angle of a line in degrees."""
        dx = line.end.x - line.start.x
        dy = line.end.y - line.start.y
        return math.degrees(math.atan2(dy, dx))

    def _normalize_angle(self, angle: float) -> float:
        """Normalize angle to 0-180 range."""
        while angle < 0:
            angle += 180
        while angle >= 180:
            angle -= 180
        return angle

    def _create_extension_line(self, line: Line, dim_line: DimensionLine) -> ExtensionLine | None:
        """Create an ExtensionLine object if it's associated with a dimension line."""
        try:
            # Check if line is near dimension line endpoints
            dist_to_start = self._point_to_line_distance(
                line.start, dim_line.start, dim_line.end
            )
            dist_to_end = self._point_to_line_distance(
                line.end, dim_line.start, dim_line.end
            )
            
            # Should be near one of the dimension line endpoints
            if dist_to_start < 20 or dist_to_end < 20:
                length = math.sqrt(
                    (line.end.x - line.start.x)**2 +
                    (line.end.y - line.start.y)**2
                )
                
                return ExtensionLine(
                    start=line.start,
                    end=line.end,
                    length=length,
                    confidence=0.8,
                    bbox=self._calculate_bbox(line),
                )
            
            return None
            
        except Exception as exc:
            logger.debug(f"Failed to create extension line: {exc}")
            return None

    def _point_to_line_distance(self, point: Point, line_start: Point, line_end: Point) -> float:
        """Calculate distance from point to line segment."""
        x0, y0 = point.x, point.y
        x1, y1 = line_start.x, line_start.y
        x2, y2 = line_end.x, line_end.y
        
        # Line segment length
        line_length = math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        if line_length == 0:
            return math.sqrt((x0 - x1)**2 + (y0 - y1)**2)
        
        # Calculate projection
        t = max(0, min(1, ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / (line_length**2)))
        
        # Closest point on line
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


class DimensionLineDetector:
    """Detect dimension lines with arrowheads."""

    def __init__(self, min_length: float = 20.0):
        """
        Initialize dimension line detector.
        
        Args:
            min_length: Minimum dimension line length
        """
        self.min_length = min_length

    def detect(
        self,
        lines: list[Line],
        arrowheads: list[Arrowhead],
        tolerance: float = 10.0,
    ) -> list[DimensionLine]:
        """
        Detect dimension lines.
        
        Args:
            lines: All line segments
            arrowheads: Detected arrowheads
            tolerance: Distance tolerance for arrowhead association
            
        Returns:
            List of dimension lines
        """
        dimension_lines = []
        
        # Group lines by orientation
        horizontal_lines = []
        vertical_lines = []
        diagonal_lines = []
        
        for line in lines:
            angle = self._calculate_angle(line)
            if abs(angle) < 5 or abs(angle - 180) < 5:
                horizontal_lines.append(line)
            elif abs(angle - 90) < 5 or abs(angle - 270) < 5:
                vertical_lines.append(line)
            else:
                diagonal_lines.append(line)
        
        # Process each group
        for line_group in [horizontal_lines, vertical_lines, diagonal_lines]:
            dimension_lines.extend(self._process_line_group(line_group, arrowheads, tolerance))
        
        return dimension_lines

    def _calculate_angle(self, line: Line) -> float:
        """Calculate line angle in degrees."""
        dx = line.end.x - line.start.x
        dy = line.end.y - line.start.y
        return math.degrees(math.atan2(dy, dx))

    def _process_line_group(
        self,
        lines: list[Line],
        arrowheads: list[Arrowhead],
        tolerance: float,
    ) -> list[DimensionLine]:
        """Process a group of similarly-oriented lines."""
        dimension_lines = []
        
        for line in lines:
            length = math.sqrt(
                (line.end.x - line.start.x)**2 +
                (line.end.y - line.start.y)**2
            )
            
            if length < self.min_length:
                continue
            
            # Find arrowheads near line endpoints
            start_arrow = self._find_nearest_arrowhead(line.start, arrowheads, tolerance)
            end_arrow = self._find_nearest_arrowhead(line.end, arrowheads, tolerance)
            
            # Create dimension line if at least one arrow found
            if start_arrow or end_arrow:
                angle = self._calculate_angle(line)
                
                dim_line = DimensionLine(
                    start=line.start,
                    end=line.end,
                    start_arrow=start_arrow,
                    end_arrow=end_arrow,
                    length=length,
                    angle=angle,
                    confidence=0.8 if (start_arrow and end_arrow) else 0.6,
                    bbox=self._calculate_bbox(line),
                )
                dimension_lines.append(dim_line)
        
        return dimension_lines

    def _find_nearest_arrowhead(
        self,
        point: Point,
        arrowheads: list[Arrowhead],
        tolerance: float,
    ) -> Arrowhead | None:
        """Find arrowhead nearest to a point."""
        nearest = None
        min_dist = float('inf')
        
        for arrowhead in arrowheads:
            dist = math.sqrt(
                (arrowhead.tip.x - point.x)**2 +
                (arrowhead.tip.y - point.y)**2
            )
            
            if dist < min_dist and dist <= tolerance:
                min_dist = dist
                nearest = arrowhead
        
        return nearest

    def _calculate_bbox(self, line: Line) -> tuple[float, float, float, float]:
        """Calculate bounding box for a line."""
        x0 = min(line.start.x, line.end.x)
        y0 = min(line.start.y, line.end.y)
        x1 = max(line.start.x, line.end.x)
        y1 = max(line.start.y, line.end.y)
        return (x0, y0, x1, y1)


class DimensionReconstructor:
    """Main class for reconstructing dimensions from vector data."""

    def __init__(self):
        """Initialize dimension reconstructor."""
        self.arrowhead_detector = ArrowheadDetector()
        self.extension_line_detector = ExtensionLineDetector()
        self.dimension_line_detector = DimensionLineDetector()

    def reconstruct(self, drawing: DrawingDocument) -> tuple[list[DimensionGroup], DimensionGraph]:
        """
        Reconstruct dimensions from drawing.
        
        Args:
            drawing: DrawingDocument from Stage 1
            
        Returns:
            Tuple of (dimension groups, dimension graph)
        """
        # Step 1: Detect arrowheads
        arrowheads = self.arrowhead_detector.detect(
            drawing.lines,
            (int(drawing.page_width), int(drawing.page_height))
        )
        logger.info(f"Detected {len(arrowheads)} arrowheads")
        
        # Step 2: Detect dimension lines
        dimension_lines = self.dimension_line_detector.detect(
            drawing.lines,
            arrowheads
        )
        logger.info(f"Detected {len(dimension_lines)} dimension lines")
        
        # Step 3: Detect extension lines
        extension_lines = self.extension_line_detector.detect(
            drawing.lines,
            dimension_lines
        )
        logger.info(f"Detected {len(extension_lines)} extension lines")
        
        # Step 4: Associate text with dimensions
        dimension_groups = self._associate_text(
            dimension_lines,
            extension_lines,
            drawing.text_blocks
        )
        logger.info(f"Created {len(dimension_groups)} dimension groups")
        
        # Step 5: Build graph
        graph = self._build_graph(dimension_groups, arrowheads, extension_lines)
        
        return dimension_groups, graph

    def _associate_text(
        self,
        dimension_lines: list[DimensionLine],
        extension_lines: list[ExtensionLine],
        text_blocks: list[TextBlock],
    ) -> list[DimensionGroup]:
        """Associate text blocks with dimension lines."""
        groups = []
        
        for dim_line in dimension_lines:
            # Find text near the dimension line center
            center_x = (dim_line.start.x + dim_line.end.x) / 2
            center_y = (dim_line.start.y + dim_line.end.y) / 2
            
            best_text = ""
            best_text_bbox = (0.0, 0.0, 0.0, 0.0)
            best_distance = float('inf')
            
            for text_block in text_blocks:
                text_center_x = (text_block.bbox[0] + text_block.bbox[2]) / 2
                text_center_y = (text_block.bbox[1] + text_block.bbox[3]) / 2
                
                distance = math.sqrt(
                    (text_center_x - center_x)**2 +
                    (text_center_y - center_y)**2
                )
                
                # Text should be within reasonable distance
                if distance < 100 and distance < best_distance:
                    best_distance = distance
                    best_text = text_block.text
                    best_text_bbox = text_block.bbox
            
            # Find associated extension lines
            associated_ext = self._find_associated_extension_lines(
                dim_line,
                extension_lines
            )
            
            # Determine dimension type
            dim_type = self._determine_dimension_type(dim_line, associated_ext)
            
            group = DimensionGroup(
                dimension_line=dim_line,
                extension_lines=associated_ext,
                text=best_text,
                text_bbox=best_text_bbox,
                text_center=Point(x=center_x, y=center_y),
                dimension_type=dim_type,
                confidence=dim_line.confidence,
            )
            groups.append(group)
        
        return groups

    def _find_associated_extension_lines(
        self,
        dim_line: DimensionLine,
        extension_lines: list[ExtensionLine],
        tolerance: float = 30.0,
    ) -> list[ExtensionLine]:
        """Find extension lines associated with a dimension line."""
        associated = []
        
        for ext_line in extension_lines:
            # Check if extension line is near dimension line endpoints
            dist_to_start = self._point_to_line_distance(
                ext_line.start, dim_line.start, dim_line.end
            )
            dist_to_end = self._point_to_line_distance(
                ext_line.end, dim_line.start, dim_line.end
            )
            
            if dist_to_start < tolerance or dist_to_end < tolerance:
                associated.append(ext_line)
        
        return associated

    def _point_to_line_distance(self, point: Point, line_start: Point, line_end: Point) -> float:
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

    def _determine_dimension_type(
        self,
        dim_line: DimensionLine,
        extension_lines: list[ExtensionLine],
    ) -> str:
        """Determine the type of dimension."""
        if not extension_lines:
            return "linear"
        
        # Check if extension lines are parallel (linear dimension)
        if len(extension_lines) >= 2:
            angle1 = self._calculate_line_angle_from_points(
                extension_lines[0].start,
                extension_lines[0].end
            )
            angle2 = self._calculate_line_angle_from_points(
                extension_lines[1].start,
                extension_lines[1].end
            )
            
            angle_diff = abs(angle1 - angle2)
            if angle_diff < 10 or angle_diff > 170:
                return "linear"
        
        # Check for angular dimension
        if len(extension_lines) == 2:
            return "angular"
        
        return "linear"

    def _calculate_line_angle_from_points(self, start: Point, end: Point) -> float:
        """Calculate angle from two points."""
        dx = end.x - start.x
        dy = end.y - start.y
        return math.degrees(math.atan2(dy, dx))

    def _build_graph(
        self,
        dimension_groups: list[DimensionGroup],
        arrowheads: list[Arrowhead],
        extension_lines: list[ExtensionLine],
    ) -> DimensionGraph:
        """Build graph representation of dimension relationships."""
        graph = DimensionGraph()
        
        # Add dimension groups as nodes
        for group in dimension_groups:
            graph.add_node(group.id, "dimension", {
                "text": group.text,
                "type": group.dimension_type,
                "confidence": group.confidence,
            })
            
            # Add dimension line as node
            if group.dimension_line:
                dl_id = f"{group.id}_line"
                graph.add_node(dl_id, "dimension_line", {
                    "length": group.dimension_line.length,
                    "angle": group.dimension_line.angle,
                })
                graph.add_edge(group.id, dl_id, "has_line")
            
            # Add extension lines as nodes
            for ext_line in group.extension_lines:
                ext_id = f"{group.id}_ext_{ext_line.id}"
                graph.add_node(ext_id, "extension_line", {
                    "length": ext_line.length,
                })
                graph.add_edge(group.id, ext_id, "has_extension")
            
            # Add text as node
            if group.text:
                text_id = f"{group.id}_text"
                graph.add_node(text_id, "text", {
                    "content": group.text,
                    "bbox": list(group.text_bbox),
                })
                graph.add_edge(group.id, text_id, "has_text")
        
        # Add arrowheads as nodes
        for arrowhead in arrowheads:
            graph.add_node(arrowhead.id, "arrowhead", {
                "angle": arrowhead.angle,
                "size": arrowhead.size,
                "confidence": arrowhead.confidence,
            })
        
        # Add extension lines as nodes (if not already added)
        for ext_line in extension_lines:
            if not any(group.id in ext_line.id for group in dimension_groups):
                graph.add_node(ext_line.id, "extension_line", {
                    "length": ext_line.length,
                })
        
        return graph