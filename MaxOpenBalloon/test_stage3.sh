#!/bin/bash
# Test script for Stage 3: GD&T Recognition

set -e

echo "=========================================="
echo "Stage 3: GD&T Recognition Test"
echo "=========================================="
echo ""

# Configuration
PDF_WORKER_URL="${PDF_WORKER_URL:-http://localhost:18008}"
TEST_PDF="${TEST_PDF:-/tmp/vector_test.pdf}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check if test PDF exists
if [ ! -f "$TEST_PDF" ]; then
    echo -e "${YELLOW}⚠️  No test PDF found at $TEST_PDF${NC}"
    echo ""
    echo "Please provide a test PDF:"
    echo "  export TEST_PDF=/path/to/your/drawing.pdf"
    echo "  ./test_stage3.sh"
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

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Service is healthy${NC}"
else
    echo -e "${RED}✗ Service health check failed (HTTP $HTTP_CODE)${NC}"
    echo "  Start: make docker-dev"
    exit 1
fi
echo ""

# Test 2: GD&T Recognition
echo "=========================================="
echo "Test 2: GD&T Recognition"
echo "=========================================="
echo "Recognizing GD&T features from vector geometry..."
echo "This will:"
echo "  1. Extract vector data (Stage 1)"
echo "  2. Detect datum symbols (A, B, C, etc.)"
echo "  3. Detect GD&T symbol frames"
echo "  4. Recognize tolerance values"
echo "  5. Build complete GD&T sets"
echo ""

GDT_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/gdt/recognize" 2>&1) || true

HTTP_CODE=$(echo "$GDT_RESPONSE" | tail -n1)
BODY=$(echo "$GDT_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ GD&T recognition successful${NC}"
    
    echo ""
    echo "Document Info:"
    echo "$BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f\"  Document ID: {data.get('document_id', 'N/A')}\")
    print()
    print('Statistics:')
    stats = data.get('statistics', {})
    print(f\"  Datum Symbols: {stats.get('datum_symbols', 0)}\")
    print(f\"  GD&T Symbols: {stats.get('gdt_symbols', 0)}\")
    print(f\"  GD&T Tolerances: {stats.get('gdt_tolerances', 0)}\")
    print(f\"  GD&T Sets: {stats.get('gdt_sets', 0)}\")
    print()
    
    # Show datum symbols
    datums = data.get('datum_symbols', [])
    if datums:
        print(f'Datum Symbols ({len(datums)} total):')
        for i, datum in enumerate(datums[:5]):
            print(f\"  {i+1}. Label: '{datum.get('label', '')}' \")
            print(f\"     Confidence: {datum.get('confidence', 0):.2f}\")
        if len(datums) > 5:
            print(f\"  ... and {len(datums) - 5} more\")
        print()
    
    # Show GD&T sets
    gdt_sets = data.get('gdt_sets', [])
    if gdt_sets:
        print(f'GD&T Sets ({len(gdt_sets)} total):')
        for i, gdt_set in enumerate(gdt_sets[:5]):
            print(f\"  {i+1}. Type: {gdt_set.get('tolerance', {}).get('gdt_type', 'unknown')}\")
            print(f\"     Value: {gdt_set.get('tolerance', {}).get('value', '')}\")
            print(f\"     Datums: {gdt_set.get('tolerance', {}).get('datums', [])}\")
            print(f\"     Confidence: {gdt_set.get('confidence', 0):.2f}\")
        if len(gdt_sets) > 5:
            print(f\"  ... and {len(gdt_sets) - 5} more\")
    else:
        print('  No GD&T sets detected')
        print('  (This is normal for simple test PDFs without GD&T annotations)')
    
except Exception as e:
    print(f\"  Error parsing response: {e}\")
    print(f\"  Raw body: {sys.stdin.read()[:500]}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
    
    # Save full response
    echo "$BODY" > /tmp/gdt_recognition_response.json
    echo ""
    echo "  Full response saved to: /tmp/gdt_recognition_response.json"
else
    echo -e "${RED}✗ GD&T recognition failed (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
    echo ""
    echo "Check logs:"
    echo "  docker compose -f deploy/docker/docker-compose.yml logs --tail=50 pdf-worker-service"
    exit 1
fi
echo ""

# Test 3: Compare with Stage 2
echo "=========================================="
echo "Test 3: Compare with Stage 2 (Dimensions)"
echo "=========================================="
echo "Extracting dimensions for comparison..."

DIM_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/dimensions/reconstruct" 2>&1) || true

HTTP_CODE=$(echo "$DIM_RESPONSE" | tail -n1)
BODY=$(echo "$DIM_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Dimension reconstruction successful${NC}"
    echo "$BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    stats = data.get('statistics', {})
    print(f\"  Total Dimensions: {stats.get('total_dimensions', 0)}\")
    print(f\"  Total Arrowheads: {stats.get('total_arrowheads', 0)}\")
    print()
    print('  Stage 2 provides dimensions')
    print('  Stage 3 adds GD&T (tolerances, datums, symbols)')
except Exception as e:
    print(f\"  Error: {e}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
else
    echo -e "${RED}✗ Dimension reconstruction failed (HTTP $HTTP_CODE)${NC}"
fi
echo ""

# Summary
echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "${GREEN}✓ Stage 3 testing complete!${NC}"
echo ""
echo "Stage 3 Capabilities:"
echo "  • Datum symbol detection (A, B, C, etc.)"
echo "  • GD&T tolerance recognition (position, profile, runout, flatness)"
echo "  • Symbol frame detection (rectangles, circles)"
echo "  • Complete GD&T set building"
echo ""
echo "Output includes:"
echo "  • Datum symbols with labels"
echo "  • GD&T tolerances with values and datum references"
echo "  • GD&T symbol frames"
echo "  • Complete GD&T sets (symbol + tolerance + datums)"
echo ""
echo "Next steps:"
echo "  1. Review /tmp/gdt_recognition_response.json"
echo "  2. Check datum symbols and GD&T sets"
echo "  3. Ready for Stage 4: Feature Association"
echo ""
echo "To test with a different PDF:"
echo "  export TEST_PDF=/path/to/another.pdf"
echo "  ./test_stage3.sh"