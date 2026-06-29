#!/bin/bash
# Test script for Stage 5: Inspection Characteristic Extraction

set -e

echo "=========================================="
echo "Stage 5: Inspection Characteristic Extraction"
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
    echo "  ./test_stage5.sh"
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

# Test 2: Full Inspection Extraction (All Stages)
echo "=========================================="
echo "Test 2: Full Inspection Extraction (Stage 5)"
echo "=========================================="
echo "Running complete pipeline:"
echo "  Stage 1: Vector extraction"
echo "  Stage 2: Dimension reconstruction"
echo "  Stage 3: GD&T recognition"
echo "  Stage 4: Feature association"
echo "  Stage 5: Inspection characteristic extraction"
echo ""

INSPECTION_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/inspection/extract" 2>&1) || true

HTTP_CODE=$(echo "$INSPECTION_RESPONSE" | tail -n1)
BODY=$(echo "$INSPECTION_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Inspection extraction successful${NC}"
    
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
    print(f\"  Total Characteristics: {stats.get('total_characteristics', 0)}\")
    print(f\"  Features: {stats.get('features', 0)}\")
    print(f\"  Dimensions: {stats.get('dimensions', 0)}\")
    print(f\"  GD&T Tolerances: {stats.get('gdt_tolerances', 0)}\")
    print()
    
    # Show characteristics
    chars = data.get('characteristics', [])
    if chars:
        print(f'Inspection Characteristics ({len(chars)} total):')
        print()
        for i, char in enumerate(chars[:10], 1):
            print(f\"  {i}. Characteristic #{char.get('characteristic', i)}\")
            print(f\"     Feature: {char.get('feature', 'unknown')}\")
            print(f\"     Dimension: {char.get('dimension', 'N/A')}\")
            print(f\"     Tolerance: {char.get('tolerance', 'N/A')}\")
            if char.get('datum'):
                print(f\"     Datums: {char.get('datum', [])}\")
            if char.get('gdt_type'):
                print(f\"     GD&T: {char.get('gdt_type', '')} = {char.get('gdt_value', '')}\")
            print(f\"     Confidence: {char.get('confidence', 0):.2f}\")
            print()
        
        if len(chars) > 10:
            print(f\"  ... and {len(chars) - 10} more characteristics\")
    else:
        print('  No characteristics detected')
        print('  (This is normal for simple test PDFs)')
    
    # Show example output format
    print()
    print('Example Output Format:')
    if chars:
        example = chars[0]
        print(json.dumps(example, indent=2))
    
except Exception as e:
    print(f\"  Error parsing response: {e}\")
    print(f\"  Raw body: {sys.stdin.read()[:500]}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
    
    # Save full response
    echo "$BODY" > /tmp/inspection_extraction_response.json
    echo ""
    echo "  Full response saved to: /tmp/inspection_extraction_response.json"
else
    echo -e "${RED}✗ Inspection extraction failed (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
    echo ""
    echo "Check logs:"
    echo "  docker compose -f deploy/docker/docker-compose.yml logs --tail=50 pdf-worker-service"
    exit 1
fi
echo ""

# Test 3: Show pipeline summary
echo "=========================================="
echo "Test 3: Complete Pipeline Summary"
echo "=========================================="
echo "The multi-stage balloon detection pipeline:"
echo ""
echo "  Stage 1: PDF Structural Analysis"
echo "    → Extracts: text, lines, polylines, curves, annotations"
echo "    → Output: Raw vector geometry"
echo ""
echo "  Stage 2: Dimension Reconstruction"
echo "    → Identifies: arrowheads, extension lines, dimension lines"
echo "    → Output: Grouped dimensions with graph"
echo ""
echo "  Stage 3: GD&T Recognition"
echo "    → Detects: datum symbols, tolerances, symbol frames"
echo "    → Output: GD&T sets with datum references"
echo ""
echo "  Stage 4: Feature Association"
echo "    → Detects: holes, slots, chamfers, radii, threads"
echo "    → Output: Features linked to dimensions"
echo ""
echo "  Stage 5: Inspection Characteristic Extraction"
echo "    → Merges: All previous stages"
echo "    → Output: Structured inspection characteristics"
echo ""
echo "Final Output Format:"
echo "  {"
echo "    \"characteristic\": 1,"
echo "    \"feature\": \"hole\","
echo "    \"dimension\": \"Ø12\","
echo "    \"tolerance\": \"±0.05\","
echo "    \"datum\": [\"A\", \"B\", \"C\"]"
echo "  }"
echo ""

# Summary
echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "${GREEN}✓ Stage 5 testing complete!${NC}"
echo ""
echo "Stage 5 Capabilities:"
echo "  • Merges data from all previous stages"
echo "  • Extracts complete inspection characteristics"
echo "  • Produces structured output for quality control"
echo ""
echo "Output includes:"
echo "  • Feature type (hole, slot, chamfer, radius, thread)"
echo "  • Dimension (Ø12, 20, M8x1.25)"
echo "  • Tolerance (±0.05, +0.0/-0.1)"
echo "  • Datum references (A, B, C)"
echo "  • GD&T type and value"
echo "  • Confidence scores"
echo ""
echo "API Endpoints Available:"
echo "  • POST /vector/extract - Stage 1"
echo "  • POST /dimensions/reconstruct - Stage 2"
echo "  • POST /gdt/recognize - Stage 3"
echo "  • POST /features/associate - Stage 4"
echo "  • POST /inspection/extract - Stage 5 (FULL PIPELINE)"
echo ""
echo "Next steps:"
echo "  1. Review /tmp/inspection_extraction_response.json"
echo "  2. Test with real engineering drawings"
echo "  3. Integrate with quality control systems"
echo ""
echo "To test with a different PDF:"
echo "  export TEST_PDF=/path/to/another.pdf"
echo "  ./test_stage5.sh"