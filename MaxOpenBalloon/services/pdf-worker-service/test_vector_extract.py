#!/usr/bin/env python3
"""Test script for Stage 1: PDF Vector Extraction."""

import sys
from pathlib import Path

# Add service to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.vector_extractor import VectorExtractor
from app.domain.vector_models import DrawingDocument


def test_with_sample_pdf():
    """Test vector extraction with a sample PDF if available."""
    extractor = VectorExtractor(use_pdfminer=False)

    # Look for test PDFs in common locations
    test_pdf_locations = [
        Path("/tmp/test.pdf"),
        Path("./test.pdf"),
        Path("../test.pdf"),
    ]

    pdf_path = None
    for location in test_pdf_locations:
        if location.exists():
            pdf_path = location
            break

    if pdf_path is None:
        print("⚠️  No test PDF found. Creating a minimal test...")
        return test_minimal()

    print(f"📄 Testing with PDF: {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()

    try:
        drawing = extractor.extract(pdf_bytes)
        print(f"✅ Extraction successful!")
        print(f"   Document ID: {drawing.document_id}")
        print(f"   Page size: {drawing.page_width} x {drawing.page_height}")
        print(f"   Statistics: {extractor.get_statistics(drawing)}")

        # Show sample data
        if drawing.text_blocks:
            print(f"\n📝 Sample text blocks (first 3):")
            for tb in drawing.text_blocks[:3]:
                print(f"   - '{tb.text}' at {tb.bbox}")

        if drawing.lines:
            print(f"\n📏 Sample lines (first 3):")
            for line in drawing.lines[:3]:
                print(f"   - {line.start.to_dict()} → {line.end.to_dict()}")

        if drawing.annotations:
            print(f"\n🏷️  Annotations found: {len(drawing.annotations)}")
            for annot in drawing.annotations[:3]:
                print(f"   - {annot.annotation_type}: {annot.content}")

        return True

    except Exception as exc:
        print(f"❌ Extraction failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


def test_minimal():
    """Test with minimal validation."""
    extractor = VectorExtractor(use_pdfminer=False)

    # Test that the class can be instantiated
    print("✅ VectorExtractor instantiated successfully")

    # Test that models can be created
    from app.domain.vector_models import Point, Line, TextBlock

    point = Point(x=10.0, y=20.0)
    print(f"✅ Point created: {point.to_dict()}")

    line = Line(start=point, end=Point(x=30.0, y=40.0))
    print(f"✅ Line created: {line.to_dict()}")

    text = TextBlock(text="Ø12", bbox=(0, 0, 50, 10))
    print(f"✅ TextBlock created: {text.to_dict()}")

    print("\n✅ All basic tests passed!")
    return True


def test_models_serialization():
    """Test that all models can be serialized to dict."""
    from app.domain.vector_models import (
        Annotation,
        BezierCurve,
        DrawingDocument,
        Line,
        Point,
        Polyline,
        TextBlock,
    )

    print("\n🧪 Testing model serialization...")

    # Test Point
    p = Point(x=1.0, y=2.0)
    assert p.to_dict() == {"x": 1.0, "y": 2.0}
    print("✅ Point serialization OK")

    # Test TextBlock
    tb = TextBlock(text="Test", bbox=(0, 0, 10, 20))
    d = tb.to_dict()
    assert d["text"] == "Test"
    print("✅ TextBlock serialization OK")

    # Test Line
    line = Line(start=p, end=Point(x=3.0, y=4.0))
    d = line.to_dict()
    assert "start" in d and "end" in d
    print("✅ Line serialization OK")

    # Test Polyline
    pl = Polyline(points=[p, Point(x=3.0, y=4.0)])
    d = pl.to_dict()
    assert len(d["points"]) == 2
    print("✅ Polyline serialization OK")

    # Test BezierCurve
    bc = BezierCurve(
        start=p,
        control1=Point(x=2.0, y=2.0),
        control2=Point(x=3.0, y=3.0),
        end=Point(x=4.0, y=4.0),
    )
    d = bc.to_dict()
    assert "control1" in d and "control2" in d
    print("✅ BezierCurve serialization OK")

    # Test Annotation
    annot = Annotation(annotation_type="arrow", bbox=(0, 0, 10, 10))
    d = annot.to_dict()
    assert d["annotation_type"] == "arrow"
    print("✅ Annotation serialization OK")

    # Test DrawingDocument
    doc = DrawingDocument(page_count=1, page_width=100.0, page_height=200.0)
    d = doc.to_dict()
    assert d["page_count"] == 1
    assert d["page_width"] == 100.0
    print("✅ DrawingDocument serialization OK")

    # Test statistics
    stats = doc.get_statistics()
    assert stats["total_primitives"] == 0
    print("✅ DrawingDocument statistics OK")

    print("\n✅ All serialization tests passed!")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Stage 1: PDF Vector Extraction - Test Suite")
    print("=" * 60)

    success = True

    # Run model tests
    if not test_models_serialization():
        success = False

    # Run minimal tests
    if not test_minimal():
        success = False

    # Run PDF extraction test if available
    if not test_with_sample_pdf():
        success = False

    print("\n" + "=" * 60)
    if success:
        print("✅ All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        sys.exit(1)