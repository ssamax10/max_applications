#!/bin/bash
# Test script for Stage 4: Feature Association

set -e

echo "=========================================="
echo "Stage 4: Feature Association Test"
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
    echo "  ./test_stage4.sh"
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

# Test 2: Feature Association
echo "=========================================="
echo "Test 2: Feature Association"
echo "=========================================="
echo "Detecting and associating manufacturing features..."
echo "This will:"
echo "  1. Extract vector data (Stage 1)"
echo "  2. Detect holes (circular features)"
echo "  3. Detect slots (elongated holes)"
echo "  4. Detect chamfers (beveled edges)"
echo "  5. Detect radii (rounded corners)"
echo "  6. Detect threads (screw threads)"
echo "  7. Associate features with dimensions"
echo ""

FEATURE_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/features/associate" 2>&1) || true

HTTP_CODE=$(echo "$FEATURE_RESPONSE" | tail -n1)
BODY=$(echo "$FEATURE_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Feature association successful${NC}"
    
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
    print(f\"  Holes: {stats.get('holes', 0)}\")
    print(f\"  Slots: {stats.get('slots', 0)}\")
    print(f\"  Chamfers: {stats.get('chamfers', 0)}\")
    print(f\"  Radii: {stats.get('radii', 0)}\")
    print(f\"  Threads: {stats.get('threads', 0)}\")
    print(f\"  Associations: {stats.get('associations', 0)}\")
    print()
    
    # Show holes
    holes = data.get('holes', [])
    if holes:
        print(f'Holes ({len(holes)} total):')
        for i, hole in enumerate(holes[:3]):
            print(f\"  {i+1}. Diameter: {hole.get('diameter', 0):.1f} pts\")
            print(f\"     Center: ({hole.get('center', {}).get('x', 0):.1f}, {hole.get('center', {}).get('y', 0):.1f})\")
            print(f\"     Confidence: {hole.get('confidence', 0):.2f}\")
        if len(holes) > 3:
            print(f\"  ... and {len(holes) - 3} more\")
        print()
    
    # Show threads
    threads = data.get('threads', [])
    if threads:
        print(f'Threads ({len(threads)} total):')
        for i, thread in enumerate(threads[:3]):
            print(f\"  {i+1}. Type: {thread.get('thread_type', 'unknown')}\")
            print(f\"     Diameter: {thread.get('diameter', 0):.1f}\")
            if thread.get('pitch'):
                print(f\"     Pitch: {thread.get('pitch', 0)}\")
            print(f\"     Text: {thread.get('metadata', {}).get('source_text', '')}\")
        if len(threads) > 3:
            print(f\"  ... and {len(threads) - 3} more\")
        print()
    
    # Show associations
    associations = data.get('associations', [])
    if associations:
        print(f'Feature-Dimension Associations ({len(associations)} total):')
        for i, assoc in enumerate(associations[:5]):
            print(f\"  {i+1}. {assoc.get('feature_type', 'unknown')} → '{assoc.get('dimension_text', '')}'\")
            print(f\"     Confidence: {assoc.get('confidence', 0):.2f}\")
        if len(associations) > 5:
            print(f\"  ... and {len(associations) - 5} more\")
    else:
        print('  No feature-dimension associations found')
        print('  (Features may be present but not associated with dimensions)')
    
except Exception as e:
    print(f\"  Error parsing response: {e}\")
    print(f\"  Raw body: {sys.stdin.read()[:500]}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
    
    # Save full response
    echo "$BODY" > /tmp/feature_association_response.json
    echo ""
    echo "  Full response saved to: /tmp/feature_association_response.json"
else
    echo -e "${RED}✗ Feature association failed (HTTP $HTTP_CODE)${NC}"
    echo "  Response: $BODY"
    echo ""
    echo "Check logs:"
    echo "  docker compose -f deploy/docker/docker-compose.yml logs --tail=50 pdf-worker-service"
    exit 1
fi
echo ""

# Test 3: Compare with Stage 1
echo "=========================================="
echo "Test 3: Compare with Stage 1 (Vector Extract)"
echo "=========================================="
echo "Extracting raw vector data for comparison..."

VECTOR_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/vector/extract" 2>&1) || true

HTTP_CODE=$(echo "$VECTOR_RESPONSE" | tail -n1)
BODY=$(echo "$VECTOR_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Vector extraction successful${NC}"
    echo "$BODY" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    stats = data.get('statistics', {})
    print(f\"  Text Blocks: {stats.get('text_blocks', 0)}\")
    print(f\"  Lines: {stats.get('lines', 0)}\")
    print(f\"  Polylines: {stats.get('polylines', 0)}\")
    print()
    print('  Stage 1 provides raw primitives')
    print('  Stage 4 identifies features (holes, slots, threads, etc.)')
except Exception as e:
    print(f\"  Error: {e}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
else
    echo -e "${RED}✗ Vector extraction failed (HTTP $HTTP_CODE)${NC}"
fi
echo ""

# Summary
echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "${GREEN}✓ Stage 4 testing complete!${NC}"
echo ""
echo "Stage 4 Capabilities:"
echo "  • Hole detection (circular features)"
echo "  • Slot detection (elongated holes)"
echo "  • Chamfer detection (beveled edges)"
echo "  • Radius detection (rounded corners)"
echo "  • Thread detection (screw threads)"
echo "  • Feature-dimension association"
echo ""
echo "Output includes:"
echo "  • Detected features with geometry"
echo "  • Feature properties (diameter, width, angle, etc.)"
echo "  • Associations between features and dimensions"
echo "  • Statistics on detected features"
echo ""
echo "Next steps:"
echo "  1. Review /tmp/feature_association_response.json"
echo "  2. Check feature-dimension associations"
echo "  3. Ready for Stage 5: Inspection Characteristic Extraction"
echo ""
echo "To test with a different PDF:"
echo "  export TEST_PDF=/path/to/another.pdf"
echo "  ./test_stage4.sh"