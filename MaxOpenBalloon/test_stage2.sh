#!/bin/bash
# Test script for Stage 2: Dimension Reconstruction

set -e

echo "=========================================="
echo "Stage 2: Dimension Reconstruction Test"
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
    echo "  ./test_stage2.sh"
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

# Test 2: Dimension Reconstruction
echo "=========================================="
echo "Test 2: Dimension Reconstruction"
echo "=========================================="
echo "Reconstructing dimensions from vector geometry..."
echo "This will:"
echo "  1. Extract vector data (Stage 1)"
echo "  2. Detect arrowheads"
echo "  3. Detect dimension lines"
echo "  4. Detect extension lines"
echo "  5. Associate text with dimensions"
echo "  6. Build graph representation"
echo ""

DIM_RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/pdf" \
    --data-binary @"$TEST_PDF" \
    "$PDF_WORKER_URL/dimensions/reconstruct" 2>&1) || true

HTTP_CODE=$(echo "$DIM_RESPONSE" | tail -n1)
BODY=$(echo "$DIM_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ Dimension reconstruction successful${NC}"
    
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
    print(f\"  Total Dimensions: {stats.get('total_dimensions', 0)}\")
    print(f\"  Total Arrowheads: {stats.get('total_arrowheads', 0)}\")
    print(f\"  Total Extension Lines: {stats.get('total_extension_lines', 0)}\")
    print(f\"  Graph Nodes: {stats.get('graph_nodes', 0)}\")
    print(f\"  Graph Edges: {stats.get('graph_edges', 0)}\")
    print()
    
    # Show dimension groups
    groups = data.get('dimension_groups', [])
    if groups:
        print(f'Dimension Groups ({len(groups)} total):')
        for i, group in enumerate(groups[:5]):  # Show first 5
            print(f\"  {i+1}. Type: {group.get('dimension_type', 'unknown')}\")
            print(f\"     Text: '{group.get('text', '')}')\")
            print(f\"     Confidence: {group.get('confidence', 0):.2f}\")
            if group.get('dimension_line'):
                dl = group['dimension_line']
                print(f\"     Length: {dl.get('length', 0):.1f} pts\")
                print(f\"     Angle: {dl.get('angle', 0):.1f}°\")
            ext_count = len(group.get('extension_lines', []))
            print(f\"     Extension Lines: {ext_count}\")
            print()
        
        if len(groups) > 5:
            print(f\"  ... and {len(groups) - 5} more\")
    else:
        print('  No dimensions detected')
        print('  (This is normal for simple test PDFs without dimension annotations)')
    
    # Show graph structure
    graph = data.get('graph', {})
    nodes = graph.get('nodes', {})
    edges = graph.get('edges', [])
    if nodes:
        print()
        print('Graph Structure:')
        print(f\"  Nodes ({len(nodes)}):\")
        for node_id, node_data in list(nodes.items())[:3]:
            print(f\"    - {node_id}: {node_data.get('type', 'unknown')}\")
        if len(nodes) > 3:
            print(f\"    ... and {len(nodes) - 3} more\")
        print(f\"  Edges: {len(edges)}\")
        
except Exception as e:
    print(f\"  Error parsing response: {e}\")
    print(f\"  Raw body: {sys.stdin.read()[:500]}\")
" || echo -e "${YELLOW}  (Install python3 to see formatted output)${NC}"
    
    # Save full response
    echo "$BODY" > /tmp/dimension_reconstruct_response.json
    echo ""
    echo "  Full response saved to: /tmp/dimension_reconstruct_response.json"
else
    echo -e "${RED}✗ Dimension reconstruction failed (HTTP $HTTP_CODE)${NC}"
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
    print(f\"  PDF Type: {stats.get('pdf_type', 'unknown')}\")
    print(f\"  Text Blocks: {stats.get('text_blocks', 0)}\")
    print(f\"  Lines: {stats.get('lines', 0)}\")
    print(f\"  Polylines: {stats.get('polylines', 0)}\")
    print()
    print('  Stage 1 provides raw primitives')
    print('  Stage 2 groups them into meaningful dimensions')
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
echo -e "${GREEN}✓ Stage 2 testing complete!${NC}"
echo ""
echo "Stage 2 Capabilities:"
echo "  • Arrowhead detection using OpenCV"
echo "  • Extension line identification"
echo "  • Dimension line grouping"
echo "  • Text association with dimensions"
echo "  • Graph representation (Arrow → Text → Geometry)"
echo ""
echo "Output includes:"
echo "  • Dimension groups with all components"
echo "  • Graph nodes and edges"
echo "  • Statistics on detected elements"
echo ""
echo "Next steps:"
echo "  1. Review /tmp/dimension_reconstruct_response.json"
echo "  2. Check graph structure for dimension relationships"
echo "  3. Ready for Stage 3: GD&T Recognition"
echo ""
echo "To test with a different PDF:"
echo "  export TEST_PDF=/path/to/another.pdf"
echo "  ./test_stage2.sh"