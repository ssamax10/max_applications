# Stage 1 Deployment Guide

## Quick Start

### 1. Deploy the Dev Environment

```bash
cd MaxOpenBalloon

# Start all services
make docker-dev

# Wait 60-90 seconds for services to initialize
# Watch the logs to see when pdf-worker-service is ready
make docker-logs
```

Press `Ctrl+C` to stop watching logs once you see pdf-worker-service start successfully.

### 2. Verify Service is Running

```bash
# Check service status
docker compose ps pdf-worker-service

# You should see something like:
# Name                              State          Ports
# docker-pdf-worker-service         Up             0.0.0.0:18008->8000/tcp
```

### 3. Create a Test PDF

**Option A: Use an existing engineering drawing**
```bash
# Copy your PDF
cp /path/to/your/engineering-drawing.pdf /tmp/test.pdf
```

**Option B: Create a simple test PDF**
```bash
python3 -c "
from reportlab.pdfgen import canvas
c = canvas.Canvas('/tmp/test.pdf')
c.drawString(100, 700, 'PART NUMBER: ABC-123')
c.drawString(100, 650, 'Ø12 ±0.05')
c.drawString(100, 600, 'M8x1.25')
c.drawString(100, 550, '20 ±0.1')
c.save()
"
```

### 4. Run the Test Script

```bash
# From MaxOpenBalloon directory
./test_stage1.sh
```

## Expected Output

```
==========================================
Stage 1: PDF Vector Extraction Test
==========================================

✓ Test PDF found: /tmp/test.pdf

==========================================
Test 1: Health Check
==========================================
✓ Service is healthy
  Response: {"status":"ok","service":"pdf-worker-service"}

==========================================
Test 2: New /vector/extract Endpoint
==========================================
Extracting complete vector geometry...
✓ Vector extraction successful

Document Info:
  Document ID: 550e8400-e29b-41d4-a716-446655440000
  Page Count: 1
  Page Size: 612.0 x 792.0

Statistics:
  Text Blocks: 4
  Lines: 0
  Polylines: 0
  Bezier Curves: 0
  Annotations: 0
  Total Primitives: 4

Sample Text Blocks (first 3):
  1. 'PART NUMBER: ABC-123' at [100.0, 92.0, 250.0, 110.0]
  2. 'Ø12 ±0.05' at [100.0, 142.0, 180.0, 160.0]
  3. 'M8x1.25' at [100.0, 192.0, 170.0, 210.0]

  Full response saved to: /tmp/vector_extract_response.json

==========================================
Test 3: Old /extract Endpoint (Backward Compat)
==========================================
Extracting dimension text suggestions...
✓ Dimension extraction successful

Mode: vector

Suggestions (first 5):
  1. 'Ø12' (confidence: 0.98)
     Position: (140.0, 151.0)
  2. 'M8x1.25' (confidence: 0.98)
     Position: (135.0, 201.0)
  3. '20' (confidence: 0.98)
     Position: (115.0, 251.0)

  Full response saved to: /tmp/extract_response.json

==========================================
Summary
==========================================
✓ All tests passed!

Stage 1 is working correctly:
  • New /vector/extract endpoint returns complete vector geometry
  • Old /extract endpoint still works (backward compatible)
```

## Manual Testing with curl

### Test New Vector Extract Endpoint

```bash
curl -X POST http://localhost:18008/vector/extract \
  -H "Content-Type: application/pdf" \
  --data-binary @/tmp/test.pdf \
  | python3 -m json.tool
```

### Test Old Extract Endpoint

```bash
curl -X POST "http://localhost:18008/extract?max_suggestions=10" \
  -H "Content-Type: application/pdf" \
  --data-binary @/tmp/test.pdf \
  | python3 -m json.tool
```

### Test Health Endpoint

```bash
curl http://localhost:18008/health
```

## Understanding the Output

### New `/vector/extract` Response

This returns **complete vector geometry**:

```json
{
  "document_id": "uuid",
  "page_count": 1,
  "page_width": 612.0,
  "page_height": 792.0,
  "statistics": {
    "text_blocks": 4,
    "lines": 0,
    "polylines": 0,
    "bezier_curves": 0,
    "annotations": 0,
    "total_primitives": 4
  },
  "text_blocks": [
    {
      "text": "Ø12 ±0.05",
      "bbox": [100.0, 142.0, 180.0, 160.0],
      "font_name": "Helvetica",
      "font_size": 12.0,
      "color": [0, 0, 0],
      "rotation": 0.0,
      "confidence": 1.0,
      "source": "pymupdf"
    }
  ],
  "lines": [],
  "polylines": [],
  "bezier_curves": [],
  "annotations": []
}
```

**Key Points:**
- Returns ALL text, not just dimensions
- Includes formatting info (font, size, color)
- Captures all geometric primitives
- Foundation for Stages 2-5

### Old `/extract` Response

This returns **dimension text suggestions only**:

```json
{
  "mode": "vector",
  "profile": {
    "page_count": 1,
    "vector_word_count": 4,
    "used_dpi": 200,
    "tile_size": 512
  },
  "diagnostics": {
    "phase1": "Vector text objects detected; OCR skipped",
    "phase2": "Viewport segmentation bypassed in vector fast path",
    "phase3": "Regex-filtered vector words used",
    "phase4": "Balloon points offset from text boxes"
  },
  "suggestions": [
    {
      "text": "Ø12",
      "confidence": 0.98,
      "x": 140.0,
      "y": 151.0,
      "bbox": [100.0, 142.0, 180.0, 160.0],
      "stage": "vector_fast_path"
    }
  ]
}
```

**Key Points:**
- Only dimension-like text (filtered by regex)
- Ready for balloon placement
- Backward compatible with existing code

## Troubleshooting

### Service won't start

```bash
# Check logs
docker compose logs pdf-worker-service

# Common issues:
# 1. Port 18008 already in use
#    Solution: Change PDF_WORKER_SERVICE_PORT in .env or use different port
#
# 2. Dependencies not installed
#    Solution: Rebuild the service
#    docker compose build pdf-worker-service
#
# 3. Permission issues
#    Solution: Check file permissions in services/pdf-worker-service/
```

### Connection refused

```bash
# Verify service is running
docker compose ps pdf-worker-service

# If not running, start it
make docker-dev

# Wait 60-90 seconds for full startup
```

### Empty results

```bash
# Check if PDF has content
pdfinfo /tmp/test.pdf

# Try with a different PDF
# Engineering drawings with dimensions work best
```

### Port already in use

```bash
# Find what's using port 18008
lsof -i :18008

# Use a different port
PDF_WORKER_URL=http://localhost:18009 ./test_stage1.sh
```

## Key Differences: Stage 1 vs Old Approach

| Feature | Old `/extract` | New `/vector/extract` |
|---------|---------------|----------------------|
| **Output** | Dimension text only | All vector primitives |
| **Text Count** | Filtered (dimensions only) | Complete (all text) |
| **Geometry** | None | Lines, polylines, curves |
| **Annotations** | None | All 26 annotation types |
| **Formatting** | Basic | Fonts, colors, sizes |
| **Use Case** | Quick balloon placement | Full drawing analysis |
| **Stage Support** | Standalone | Foundation for Stages 2-5 |

## Next Steps

Once you've verified Stage 1 works:

1. **Review the JSON output** - Check `/tmp/vector_extract_response.json`
2. **Examine statistics** - See how many primitives your drawings have
3. **Validate accuracy** - Ensure lines, text, and annotations are captured
4. **Ready for Stage 2** - We can implement dimension reconstruction

## Quick Commands Reference

```bash
# Start services
make docker-dev

# Check service status
docker compose ps pdf-worker-service

# View logs
docker compose logs -f pdf-worker-service

# Stop services
make docker-down

# Run tests
./test_stage1.sh

# Manual test
curl -X POST http://localhost:18008/vector/extract \
  -H "Content-Type: application/pdf" \
  --data-binary @/tmp/test.pdf
```

## Service Information

- **Service Name**: `docker-pdf-worker-service` (Docker) / `pdf-worker-service` (internal)
- **Port**: 18008 (external) → 8000 (internal)
- **Health Endpoint**: http://localhost:18008/health
- **New Endpoint**: http://localhost:18008/vector/extract
- **Old Endpoint**: http://localhost:18008/extract

## Status: ✅ READY FOR TESTING

Stage 1 is fully implemented and ready for deployment. The test script will verify both the new vector extraction and backward compatibility with the old endpoint.