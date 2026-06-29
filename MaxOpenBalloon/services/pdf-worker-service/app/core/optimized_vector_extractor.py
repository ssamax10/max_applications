"""Optimized Stage 1: PDF Structural Analysis with smart detection, caching, and selective extraction."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import uuid4

import fitz

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


class PDFTypeDetector:
    """Detect PDF type: vector, scanned, or hybrid."""

    @staticmethod
    def detect(pdf_bytes: bytes) -> str:
        """
        Detect PDF type based on content analysis.
        
        Returns:
            "vector" - CAD/vector drawing with geometric primitives
            "scanned" - Image-based PDF (scanned document)
            "hybrid" - Mix of vector and scanned content
            "unknown" - Cannot determine
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = doc[0]
            
            # Get vector drawings
            drawings = page.get_drawings()
            
            # Get text blocks
            text_dict = page.get_text("dict")
            text_blocks = [b for b in text_dict.get("blocks", []) if b.get("type") == 0]
            
            # Get image count
            image_list = page.get_images()
            
            # Heuristics for detection
            vector_score = 0
            scanned_score = 0
            
            # Lots of vector drawings = vector PDF
            if len(drawings) > 20:
                vector_score += 3
            elif len(drawings) > 5:
                vector_score += 2
            elif len(drawings) > 0:
                vector_score += 1
            
            # Many images = scanned PDF
            if len(image_list) > 5:
                scanned_score += 3
            elif len(image_list) > 0:
                scanned_score += 1
            
            # Text blocks present
            if len(text_blocks) > 0:
                # Check if text is from OCR (usually in scanned PDFs)
                # OCR text often has specific characteristics
                total_text_length = sum(
                    len(" ".join(span.get("text", "") 
                                for line in block.get("lines", []) 
                                for span in line.get("spans", [])))
                    for block in text_blocks
                )
                
                if total_text_length > 100:
                    vector_score += 1
            
            # Check for dimension-like patterns (common in CAD)
            drawing_commands = sum(1 for d in drawings if d.get("items"))
            if drawing_commands > 10:
                vector_score += 2
            
            doc.close()
            
            # Decision
            if vector_score >= 3 and scanned_score < 2:
                return "vector"
            elif scanned_score >= 2 and vector_score < 2:
                return "scanned"
            elif vector_score >= 2 and scanned_score >= 2:
                return "hybrid"
            else:
                return "unknown"
                
        except Exception as exc:
            logger.warning(f"PDF type detection failed: {exc}")
            return "unknown"


class CachedVectorExtractor:
    """Vector extractor with caching support."""

    def __init__(self, cache_size: int = 100):
        """Initialize with LRU cache.
        
        Args:
            cache_size: Maximum number of PDFs to cache
        """
        self.cache_size = cache_size
        self._cache: dict[str, DrawingDocument] = {}
        self._access_order: list[str] = []

    def _get_cache_key(self, pdf_bytes: bytes) -> str:
        """Generate cache key from PDF content."""
        return hashlib.sha256(pdf_bytes).hexdigest()

    def _get_from_cache(self, cache_key: str) -> DrawingDocument | None:
        """Get document from cache if available."""
        if cache_key in self._cache:
            # Move to end (most recently used)
            self._access_order.remove(cache_key)
            self._access_order.append(cache_key)
            return self._cache[cache_key]
        return None

    def _add_to_cache(self, cache_key: str, document: DrawingDocument) -> None:
        """Add document to cache with LRU eviction."""
        # Remove oldest if at capacity
        if len(self._cache) >= self.cache_size:
            oldest_key = self._access_order.pop(0)
            del self._cache[oldest_key]
        
        self._cache[cache_key] = document
        self._access_order.append(cache_key)

    def clear_cache(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        self._access_order.clear()

    def get_cache_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            "cached_documents": len(self._cache),
            "cache_size_limit": self.cache_size,
            "cache_hit_ratio": len(self._access_order) / max(1, self.cache_size),
        }


class SelectiveVectorExtractor:
    """Vector extractor with selective extraction capabilities."""

    @staticmethod
    def extract_selective(
        base_document: DrawingDocument,
        extract_text: bool = True,
        extract_lines: bool = True,
        extract_polylines: bool = True,
        extract_curves: bool = False,  # Expensive, off by default
        extract_annotations: bool = False,  # Usually not needed
    ) -> DrawingDocument:
        """
        Create a new document with only selected primitive types.
        
        Args:
            base_document: Source document
            extract_text: Include text blocks
            extract_lines: Include lines
            extract_polylines: Include polylines
            extract_curves: Include Bezier curves (expensive)
            extract_annotations: Include annotations
            
        Returns:
            New DrawingDocument with only selected primitives
        """
        filtered = DrawingDocument(
            document_id=str(uuid4()),
            page_count=base_document.page_count,
            page_width=base_document.page_width,
            page_height=base_document.page_height,
            metadata={
                **base_document.metadata,
                "filtered": True,
                "filter_criteria": {
                    "text": extract_text,
                    "lines": extract_lines,
                    "polylines": extract_polylines,
                    "curves": extract_curves,
                    "annotations": extract_annotations,
                }
            }
        )
        
        if extract_text:
            filtered.text_blocks = base_document.text_blocks.copy()
        if extract_lines:
            filtered.lines = base_document.lines.copy()
        if extract_polylines:
            filtered.polylines = base_document.polylines.copy()
        if extract_curves:
            filtered.bezier_curves = base_document.bezier_curves.copy()
        if extract_annotations:
            filtered.annotations = base_document.annotations.copy()
        
        return filtered

    @staticmethod
    def extract_by_complexity(
        base_document: DrawingDocument,
        max_primitives: int = 5000,
        prioritize_text: bool = True,
        prioritize_annotations: bool = True,
    ) -> DrawingDocument:
        """
        Limit primitives while preserving important ones.
        
        Args:
            base_document: Source document
            max_primitives: Maximum total primitives to keep
            prioritize_text: Always keep all text blocks
            prioritize_annotations: Always keep all annotations
        """
        total = (
            len(base_document.text_blocks) +
            len(base_document.lines) +
            len(base_document.polylines) +
            len(base_document.bezier_curves) +
            len(base_document.annotations)
        )
        
        if total <= max_primitives:
            return base_document
        
        # Calculate how many to keep
        essential = 0
        if prioritize_text:
            essential += len(base_document.text_blocks)
        if prioritize_annotations:
            essential += len(base_document.annotations)
        
        remaining_budget = max_primitives - essential
        
        if remaining_budget <= 0:
            # Only return essentials
            filtered = DrawingDocument(
                document_id=str(uuid4()),
                page_count=base_document.page_count,
                page_width=base_document.page_width,
                page_height=base_document.page_height,
                metadata={**base_document.metadata, "sampled": True}
            )
            if prioritize_text:
                filtered.text_blocks = base_document.text_blocks
            if prioritize_annotations:
                filtered.annotations = base_document.annotations
            return filtered
        
        # Sample non-essential primitives proportionally
        non_essential_counts = {
            "lines": len(base_document.lines),
            "polylines": len(base_document.polylines),
            "curves": len(base_document.bezier_curves),
        }
        total_non_essential = sum(non_essential_counts.values())
        
        filtered = DrawingDocument(
            document_id=str(uuid4()),
            page_count=base_document.page_count,
            page_width=base_document.page_width,
            page_height=base_document.page_height,
            metadata={**base_document.metadata, "sampled": True}
        )
        
        if prioritize_text:
            filtered.text_blocks = base_document.text_blocks
        if prioritize_annotations:
            filtered.annotations = base_document.annotations
        
        # Sample proportionally
        if total_non_essential > 0 and remaining_budget > 0:
            for key, count in non_essential_counts.items():
                if count == 0:
                    continue
                sample_size = max(1, int((count / total_non_essential) * remaining_budget))
                
                if key == "lines":
                    # Sample evenly
                    step = max(1, count // sample_size)
                    filtered.lines = base_document.lines[::step][:sample_size]
                elif key == "polylines":
                    step = max(1, count // sample_size)
                    filtered.polylines = base_document.polylines[::step][:sample_size]
                elif key == "curves":
                    step = max(1, count // sample_size)
                    filtered.bezier_curves = base_document.bezier_curves[::step][:sample_size]
        
        return filtered


class OptimizedVectorExtractor:
    """Complete optimized vector extractor with all enhancements."""

    def __init__(self, use_pdfminer: bool = False, cache_size: int = 100):
        """Initialize optimized extractor.
        
        Args:
            use_pdfminer: Whether to use pdfminer.six
            cache_size: Maximum cached documents
        """
        self.use_pdfminer = use_pdfminer
        self.pdf_type_detector = PDFTypeDetector()
        self.cache = CachedVectorExtractor(cache_size=cache_size)
        self.selective = SelectiveVectorExtractor()

    def extract(
        self,
        pdf_bytes: bytes,
        use_cache: bool = True,
        selective: dict[str, bool] | None = None,
        max_primitives: int | None = None,
    ) -> DrawingDocument:
        """
        Extract vector geometry with optimizations.
        
        Args:
            pdf_bytes: Raw PDF bytes
            use_cache: Whether to use cache
            selective: Dict of primitive types to extract
            max_primitives: Maximum primitives (enables sampling)
            
        Returns:
            DrawingDocument with extracted primitives
        """
        # Check cache
        cache_key = self.cache._get_cache_key(pdf_bytes)
        if use_cache:
            cached = self.cache._get_from_cache(cache_key)
            if cached:
                logger.debug(f"Cache hit: {cache_key[:16]}...")
                
                # Apply selective extraction if requested
                if selective:
                    return self.selective.extract_selective(cached, **selective)
                
                # Apply complexity limit if requested
                if max_primitives:
                    return self.selective.extract_by_complexity(cached, max_primitives)
                
                return cached
        
        # Detect PDF type
        pdf_type = self.pdf_type_detector.detect(pdf_bytes)
        logger.info(f"Detected PDF type: {pdf_type}")
        
        # Extract based on type
        if pdf_type == "scanned":
            # For scanned PDFs, return empty document (OCR will handle it)
            document = DrawingDocument(
                page_count=0,
                page_width=0,
                page_height=0,
                metadata={"pdf_type": "scanned", "note": "Use OCR endpoint instead"}
            )
        else:
            # Extract vector data
            from app.core.vector_extractor import VectorExtractor
            extractor = VectorExtractor(use_pdfminer=self.use_pdfminer)
            document = extractor.extract(pdf_bytes)
            
            # Validate extraction - if no primitives found, might be scanned
            total_primitives = (
                len(document.text_blocks) +
                len(document.lines) +
                len(document.polylines) +
                len(document.bezier_curves) +
                len(document.annotations)
            )
            
            if total_primitives == 0 and pdf_type == "vector":
                # Detected as vector but no content extracted
                # This is likely a scanned PDF with some vector borders/headers
                logger.warning(f"PDF detected as '{pdf_type}' but no primitives extracted. Reclassifying as 'scanned'.")
                pdf_type = "scanned"
                document.metadata["pdf_type"] = pdf_type
                document.metadata["reclassified"] = True
                document.metadata["reclassification_reason"] = "No primitives extracted"
            else:
                document.metadata["pdf_type"] = pdf_type
        
        # Cache the result
        if use_cache and pdf_type != "scanned":
            self.cache._add_to_cache(cache_key, document)
        
        # Apply selective extraction
        if selective and pdf_type != "scanned":
            document = self.selective.extract_selective(document, **selective)
        
        # Apply complexity limit
        if max_primitives and pdf_type != "scanned":
            document = self.selective.extract_by_complexity(document, max_primitives)
        
        return document

    def get_statistics(self, drawing: DrawingDocument) -> dict[str, Any]:
        """Get statistics including PDF type info."""
        stats = drawing.get_statistics()
        stats["pdf_type"] = drawing.metadata.get("pdf_type", "unknown")
        stats["text_blocks_with_fonts"] = sum(
            1 for tb in drawing.text_blocks if tb.font_name
        )
        stats["filtered"] = drawing.metadata.get("filtered", False)
        stats["sampled"] = drawing.metadata.get("sampled", False)
        return stats

    def clear_cache(self) -> None:
        """Clear the extraction cache."""
        self.cache.clear_cache()