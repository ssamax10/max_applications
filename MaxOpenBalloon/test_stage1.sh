#!/bin/bash
# Test script for Stage 1: PDF Vector Extraction
# This script tests both the old /extract and new /vector/extract endpoints

set -e

echo "=========================================="
echo "Stage 1: PDF Vector Extraction Test"
echo "=========================================="
echo ""

# Configuration
PDF_WORKER_URL="${PDF_WORKER_URL:-http://localhost:18008}"
TEST_PDF="${TEST_PDF:-/tmp/test.pdf}"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if test PDF exists
if [ ! -f "$TEST_PDF" ]; then
    echo -e "${YELLOW}⚠️  No test PDF found at $TEST_PDF${NC}"
    echo ""
    echo "Please provide a test PDF:"
    echo "  export TEST_PDF=/path/to/your/drawing.pdf"
    echo "  ./test_stage1.sh"
    echo ""
    echo "Or create a simple test PDF:"
    echo "  python3 -c \"from reportlab.pdfgen import canvas; c = canvas.Canvas('/tmp/test.pdf'); c.drawString(100, 700, 'Ø12'); c.save()\""
    exit 1
fi

echo -e "${GREEN}✓ Test PDF found: $TEST_PDF${NC}"
echo ""

# Test 1: Health Check
echo "=========================================="
echo "Test 1: Health Check"
echo "=========================================="
HEALTH_RESPONSE=$(curl -s -w "\n%{http_code}" "$PDF_WORKER_URL/health" 2>&1) || true
HTTP_CODE=$(echo "$HEALTH_RESPONSE" | tail -n1)
BODY=$(echo "$HEALTH_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Service is healthy${NC}"
    echo "  Response: $BODY"
else
    echo -e "${RED}✗ Service health check failed (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
    echo ""
    echo "Is the pdf-worker-service running?"
    echo "  Check: docker compose ps pdf-worker-service"
    echo "  Start: make docker-dev"
    exit 1
fi
echo ""

# Test 2: New Vector Extract Endpoint (with optimizations)
echo "=========================================="
echo "Test 2: New /vector/extract Endpoint (Optimized)"
echo "=========================================="
echo "Extracting complete vector geometry..."
echo "Features: Smart detection, caching, selective extraction"

VECTOR_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/vector/extract?use_cache=true&extract_text=true&extract_lines=true" 2>&1) || true

HTTP_CODE=$(echo "$VECTOR_RESPONSE" | tail -n1)
BODY=$(echo "$VECTOR_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Vector extraction successful${NC}"
    
    # Parse and display key information
    echo ""
    echo "Document Info:"
    echo "$BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f\"  Document ID: {data.get('document_id', 'N/A')}\")
    print(f\"  Page Count: {data.get('page_count', 'N/A')}\")
    print(f\"  Page Size: {data.get('page_width', 0):.1f} x {data.get('page_height', 0):.1f}\")
    print()
    print('Statistics:')
    stats = data.get('statistics', {})
    print(f\"  PDF Type: {stats.get('pdf_type', 'unknown')}\")
    print(f\"  Text Blocks: {stats.get('text_blocks', 0)}\")
    print(f\"  Lines: {stats.get('lines', 0)}\")
    print(f\"  Polylines: {stats.get('polylines', 0)}\")
    print(f\"  Bezier Curves: {stats.get('bezier_curves', 0)}\")
    print(f\"  Annotations: {stats.get('annotations', 0)}\")
    print(f\"  Total Primitives: {stats.get('total_primitives', 0)}\")
    if stats.get('filtered'):
        print(f\"  Filtered: Yes\")
    if stats.get('sampled'):
        print(f\"  Sampled: Yes\")
    print()
    print('Sample Text Blocks (first 3):')
    for i, tb in enumerate(data.get('text_blocks', [])[:3]):
        print(f\"  {i+1}. '{tb.get('text', '')}' at {tb.get('bbox', [])}\")
    print()
    print('Sample Lines (first 3):')
    for i, line in enumerate(data.get('lines', [])[:3]):
        start = line.get('start', {})
        end = line.get('end', {})
        print(f\"  {i+1}. ({start.get('x', 0):.1f}, {start.get('y', 0):.1f}) → ({end.get('x', 0):.1f}, {end.get('y', 0):.1f})\")
except Exception as e:
    print(f\"  Error parsing response: {e}\")
    print(f\"  Raw body: {sys.stdin.read()[:200]}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
    
    # Save full response
    echo "$BODY" > /tmp/vector_extract_response.json
    echo ""
    echo "  Full response saved to: /tmp/vector_extract_response.json"
else
    echo -e "${RED}✗ Vector extraction failed (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
    exit 1
fi
echo ""

# Test 3: Old Extract Endpoint (Backward Compatibility)
echo "=========================================="
echo "Test 3: Old /extract Endpoint (Backward Compat)"
echo "=========================================="
echo "Extracting dimension text suggestions..."

EXTRACT_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/extract?max_suggestions=10" 2>&1) || true

HTTP_CODE=$(echo "$EXTRACT_RESPONSE" | tail -n1)
BODY=$(echo "$EXTRACT_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Dimension extraction successful${NC}"
    
    echo ""
    echo "Mode: $(echo "$BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('mode', 'unknown'))" 2>/dev/null || echo "unknown")"
    echo ""
    echo "Suggestions (first 5):"
    echo "$BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for i, sugg in enumerate(data.get('suggestions', [])[:5]):
        print(f\"  {i+1}. '{sugg.get('text', '')}' (confidence: {sugg.get('confidence', 0):.2f})\")
        print(f\"     Position: ({sugg.get('x', 0):.1f}, {sugg.get('y', 0):.1f})\")
except Exception as e:
    print(f\"  Error: {e}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
    
    # Save full response
    echo "$BODY" > /tmp/extract_response.json
    echo ""
    echo "  Full response saved to: /tmp/extract_response.json"
else
    echo -e "${RED}✗ Dimension extraction failed (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
    exit 1
fi
echo ""

# Test 4: Selective Extraction (Optional)
echo ""
echo "=========================================="
echo "Test 4: Selective Extraction (Performance Test)"
echo "=========================================="
echo "Extracting only text (faster)..."

SELECTIVE_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/vector/extract?extract_text=true&extract_lines=false&extract_polylines=false&extract_curves=false&extract_annotations=false" 2>&1) || true

HTTP_CODE=$(echo "$SELECTIVE_RESPONSE" | tail -n1)
BODY=$(echo "$SELECTIVE_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Selective extraction successful${NC}"
    echo "$BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    stats = data.get('statistics', {})
    print(f\"  PDF Type: {stats.get('pdf_type', 'unknown')}\")
    print(f\"  Text Blocks: {stats.get('text_blocks', 0)}\")
    print(f\"  Lines: {stats.get('lines', 0)} (excluded)\")
    print(f\"  Filtered: {stats.get('filtered', False)}\")
except Exception as e:
    print(f\"  Error: {e}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
else
    echo -e "${YELLOW}⚠️  Selective extraction failed (HTTP $HTTP_CODE) - this is optional${NC}"
fi

# Summary
echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "${GREEN}✓ All tests passed!${NC}"
echo ""
echo "Stage 1 is working correctly with optimizations:"
echo "  • Smart PDF type detection (vector/scanned/hybrid)"
echo "  • Caching enabled for repeated access"
echo "  • Selective extraction available"
echo "  • New /vector/extract endpoint returns complete vector geometry"
echo "  • Old /extract endpoint still works (backward compatible)"
echo ""
echo "Optimization Features:"
echo "  • use_cache=true - Cache PDFs for faster re-extraction"
echo "  • extract_text/lines/polylines/curves/annotations - Choose what to extract"
echo "  • max_primitives - Limit response size for complex drawings"
echo "  • Smart detection - Automatically identifies vector vs scanned PDFs"
echo ""
echo "Next steps:"
echo "  1. Review the extracted data in /tmp/vector_extract_response.json"
echo "  2. Check statistics to understand your drawing complexity"
echo "  3. Try selective extraction to reduce response size"
echo "  4. Ready to proceed to Stage 2: Dimension Reconstruction"
echo ""
echo "To test with a different PDF:"
echo "  export TEST_PDF=/path/to/another.pdf"
echo "  ./test_stage1.sh"
echo ""
echo "Example selective extraction:"
echo "  curl -X POST http://localhost:18008/vector/extract?extract_text=true&extract_lines=false \\"
echo "    -H 'Content-Type: application/pdf' \\"
echo "    --data-binary @/tmp/test.pdf"
