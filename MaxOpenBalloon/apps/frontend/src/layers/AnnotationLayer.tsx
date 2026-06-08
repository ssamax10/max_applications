import { type RefObject, useEffect, useState } from "react";

import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import pdfWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from "react-konva";

GlobalWorkerOptions.workerSrc = pdfWorkerSrc;

const PDF_RENDER_SCALE = 2;

type LayerDimensions = {
  width: number;
  height: number;
};

function normalizeQuarterTurns(rotation: number): 0 | 1 | 2 | 3 {
  const normalized = ((rotation % 360) + 360) % 360;
  if (normalized === 90) {
    return 1;
  }
  if (normalized === 180) {
    return 2;
  }
  if (normalized === 270) {
    return 3;
  }
  return 0;
}

function rotatePoint(point: { x: number; y: number }, dimensions: LayerDimensions, rotation: number): { x: number; y: number } {
  const turns = normalizeQuarterTurns(rotation);
  if (turns === 1) {
    return { x: dimensions.height - point.y, y: point.x };
  }
  if (turns === 2) {
    return { x: dimensions.width - point.x, y: dimensions.height - point.y };
  }
  if (turns === 3) {
    return { x: point.y, y: dimensions.width - point.x };
  }
  return point;
}

function unrotatePoint(point: { x: number; y: number }, dimensions: LayerDimensions, rotation: number): { x: number; y: number } {
  const turns = normalizeQuarterTurns(rotation);
  if (turns === 1) {
    return { x: point.y, y: dimensions.height - point.x };
  }
  if (turns === 2) {
    return { x: dimensions.width - point.x, y: dimensions.height - point.y };
  }
  if (turns === 3) {
    return { x: dimensions.width - point.y, y: point.x };
  }
  return point;
}

function parseSvgDimensions(svgText: string): LayerDimensions | null {
  const normalized = svgText.trim();
  if (!normalized) {
    return null;
  }

  const widthMatch = normalized.match(/\bwidth\s*=\s*["']([0-9.]+)(px)?["']/i);
  const heightMatch = normalized.match(/\bheight\s*=\s*["']([0-9.]+)(px)?["']/i);

  if (widthMatch && heightMatch) {
    const width = Number(widthMatch[1]);
    const height = Number(heightMatch[1]);
    if (Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) {
      return { width, height };
    }
  }

  const viewBoxMatch = normalized.match(/\bviewBox\s*=\s*["']([^"']+)["']/i);
  if (!viewBoxMatch) {
    return null;
  }

  const parts = viewBoxMatch[1].trim().split(/[\s,]+/).map(Number);
  if (parts.length !== 4) {
    return null;
  }

  const [, , width, height] = parts;
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
    return null;
  }

  return { width, height };
}

type AnnotationLayerProps = {
  featureCount: number;
  balloons: Array<{
    id: string;
    label: string;
    x: number;
    y: number;
    fillColor: string;
    outlineColor: string;
    size: number;
    textColor: string;
    fontFamily: string;
    textRotation: number;
    debugSourceX?: number;
    debugSourceY?: number;
  }>;
  selectedBalloonId: string | null;
  onCanvasClick?: (point: { x: number; y: number }) => void;
  onDeselectBalloon?: () => void;
  onSelectBalloon?: (balloonId: string) => void;
  onMoveBalloon?: (payload: { id: string; x: number; y: number }) => void;
  drawingLayerUrl?: string | null;
  drawingLayerFormat?: "SVG" | "PDF" | "DWG" | "DXF" | null;
  drawingLayerLabel?: string;
  stageRef?: RefObject<any>;
  exportPreviewOnly?: boolean;
  showDebugAnchors?: boolean;
};

export function AnnotationLayer({
  featureCount,
  balloons,
  selectedBalloonId,
  onCanvasClick,
  onDeselectBalloon,
  onSelectBalloon,
  onMoveBalloon,
  drawingLayerUrl,
  drawingLayerFormat,
  drawingLayerLabel,
  stageRef,
  exportPreviewOnly = false,
  showDebugAnchors = false,
}: AnnotationLayerProps) {
  const safeCount = Math.max(1, Math.min(featureCount || 3, 8));
  const markers = Array.from({ length: safeCount }, (_, index) => ({
    id: index + 1,
    x: 42 + index * 50,
    y: 78 + (index % 2) * 36,
  }));

  const [viewportSize, setViewportSize] = useState({ width: 1200, height: 720 });
  const canvasWidth = viewportSize.width;
  const canvasHeight = viewportSize.height;
  const gridStepX = 104;
  const gridStepY = 70;
  const [drawingLayerImage, setDrawingLayerImage] = useState<HTMLImageElement | null>(null);
  const [drawingLayerDimensions, setDrawingLayerDimensions] = useState<LayerDimensions | null>(null);
  const [pdfBaseDimensions, setPdfBaseDimensions] = useState<LayerDimensions | null>(null);
  const [pdfRotation, setPdfRotation] = useState(0);
  const [zoomScale, setZoomScale] = useState(1);
  const [stagePosition, setStagePosition] = useState({ x: 0, y: 0 });
  const minZoom = 0.2;
  const maxZoom = 8;

  useEffect(() => {
    function updateViewport() {
      const width = Math.max(760, window.innerWidth - 120);
      const height = Math.max(480, window.innerHeight - 220);
      setViewportSize({ width, height });
    }

    updateViewport();
    window.addEventListener("resize", updateViewport);
    return () => {
      window.removeEventListener("resize", updateViewport);
    };
  }, []);

  useEffect(() => {
    const layerUrl = drawingLayerUrl ?? undefined;

    if (!layerUrl) {
      setDrawingLayerImage(null);
      setDrawingLayerDimensions(null);
      setPdfBaseDimensions(null);
      return;
    }

    let cancelled = false;
    let parsedDimensions: LayerDimensions | null = null;

    async function loadDrawingLayer(): Promise<void> {
      if (drawingLayerFormat === "PDF") {
        try {
          const loadingTask = getDocument(layerUrl);
          const pdfDocument = await loadingTask.promise;
          const page = await pdfDocument.getPage(1);
          const baseRotation = ((page.rotate ?? 0) + 360) % 360;
          const baseViewport = page.getViewport({ scale: PDF_RENDER_SCALE, rotation: baseRotation });
          const baseUnitViewport = page.getViewport({ scale: 1, rotation: baseRotation });
          const viewport = page.getViewport({ scale: PDF_RENDER_SCALE, rotation: ((baseRotation + pdfRotation) % 360 + 360) % 360 });

          if (!cancelled) {
            setPdfBaseDimensions({ width: baseUnitViewport.width, height: baseUnitViewport.height });
          }

          const canvas = document.createElement("canvas");
          canvas.width = Math.ceil(viewport.width);
          canvas.height = Math.ceil(viewport.height);
          const context = canvas.getContext("2d");
          if (!context) {
            throw new Error("Unable to create canvas context for PDF rendering.");
          }

          await page.render({ canvasContext: context, viewport }).promise;

          const image = new window.Image();
          image.onload = () => {
            if (!cancelled) {
              setDrawingLayerImage(image);
              setDrawingLayerDimensions({ width: canvas.width, height: canvas.height });
            }
          };
          image.onerror = () => {
            if (!cancelled) {
              setDrawingLayerImage(null);
              setDrawingLayerDimensions(null);
            }
          };
          image.src = canvas.toDataURL("image/png");
          return;
        } catch {
          if (!cancelled) {
            setDrawingLayerImage(null);
            setDrawingLayerDimensions(null);
            setPdfBaseDimensions(null);
          }
          return;
        }
      }

      if (drawingLayerFormat === "SVG") {
        if (!cancelled) {
          setPdfBaseDimensions(null);
        }
        try {
          const svgResponse = await fetch(layerUrl);
          const svgText = await svgResponse.text();
          parsedDimensions = parseSvgDimensions(svgText);
          if (!cancelled) {
            setDrawingLayerDimensions(parsedDimensions);
          }
        } catch {
          if (!cancelled) {
            setDrawingLayerDimensions(null);
          }
        }
      } else if (!cancelled) {
        setDrawingLayerDimensions(null);
        setPdfBaseDimensions(null);
      }

      const image = new window.Image();
      image.onload = () => {
        if (!cancelled) {
          setDrawingLayerImage(image);
          if (!parsedDimensions) {
            const fallbackWidth = image.naturalWidth || image.width;
            const fallbackHeight = image.naturalHeight || image.height;
            if (fallbackWidth > 0 && fallbackHeight > 0) {
              setDrawingLayerDimensions({ width: fallbackWidth, height: fallbackHeight });
            }
          }
        }
      };
      image.onerror = () => {
        if (!cancelled) {
          setDrawingLayerImage(null);
          setDrawingLayerDimensions(null);
        }
      };
      if (layerUrl) {
        image.src = layerUrl;
      }
    }

    void loadDrawingLayer();

    return () => {
      cancelled = true;
    };
  }, [drawingLayerFormat, drawingLayerUrl, pdfRotation]);

  const hasDrawingLayer = Boolean(drawingLayerImage);
  const drawingSource = drawingLayerImage;
  const drawingSourceWidth = drawingLayerDimensions?.width
    ?? (drawingSource ? drawingSource.naturalWidth || drawingSource.width : 0);
  const drawingSourceHeight = drawingLayerDimensions?.height
    ?? (drawingSource ? drawingSource.naturalHeight || drawingSource.height : 0);
  const drawingScale = drawingSource && drawingSourceWidth > 0 && drawingSourceHeight > 0
    ? Math.min(canvasWidth / drawingSourceWidth, canvasHeight / drawingSourceHeight)
    : 1;
  const drawingRenderWidth = drawingSource ? drawingSourceWidth * drawingScale : 0;
  const drawingRenderHeight = drawingSource ? drawingSourceHeight * drawingScale : 0;
  const drawingOffsetX = hasDrawingLayer ? (canvasWidth - drawingRenderWidth) / 2 : 0;
  const drawingOffsetY = hasDrawingLayer ? (canvasHeight - drawingRenderHeight) / 2 : 0;
  const sourceDimensions = drawingLayerFormat === "PDF"
    ? pdfBaseDimensions ?? drawingLayerDimensions
    : drawingLayerDimensions;
  const sourceCoordinateWidth = sourceDimensions?.width ?? drawingSourceWidth;
  const sourceCoordinateHeight = sourceDimensions?.height ?? drawingSourceHeight;
  const sourceCoordinateScale = drawingSource && sourceCoordinateWidth > 0 && sourceCoordinateHeight > 0
    ? Math.min(canvasWidth / sourceCoordinateWidth, canvasHeight / sourceCoordinateHeight)
    : drawingScale;
  const sourceOffsetX = hasDrawingLayer ? (canvasWidth - sourceCoordinateWidth * sourceCoordinateScale) / 2 : drawingOffsetX;
  const sourceOffsetY = hasDrawingLayer ? (canvasHeight - sourceCoordinateHeight * sourceCoordinateScale) / 2 : drawingOffsetY;
  const sourceRotation = drawingLayerFormat === "PDF" ? pdfRotation : 0;

  function clampStagePosition(position: { x: number; y: number }, scale = zoomScale): { x: number; y: number } {
    if (scale <= 1) {
      return { x: 0, y: 0 };
    }

    const scaledWidth = canvasWidth * scale;
    const scaledHeight = canvasHeight * scale;
    const minX = canvasWidth - scaledWidth;
    const minY = canvasHeight - scaledHeight;

    return {
      x: Math.max(minX, Math.min(0, position.x)),
      y: Math.max(minY, Math.min(0, position.y)),
    };
  }

  function toCanvasPoint(point: { x: number; y: number }) {
    if (!drawingSource) {
      return point;
    }

    const transformedPoint = sourceDimensions
      ? rotatePoint(point, sourceDimensions, sourceRotation)
      : point;

    return {
      x: sourceOffsetX + transformedPoint.x * sourceCoordinateScale,
      y: sourceOffsetY + transformedPoint.y * sourceCoordinateScale,
    };
  }

  function toSourcePoint(point: { x: number; y: number }) {
    if (!drawingSource || sourceCoordinateScale <= 0) {
      return point;
    }

    const displayPoint = {
      x: (point.x - sourceOffsetX) / sourceCoordinateScale,
      y: (point.y - sourceOffsetY) / sourceCoordinateScale,
    };

    return sourceDimensions
      ? unrotatePoint(displayPoint, sourceDimensions, sourceRotation)
      : displayPoint;
  }

  function handleCanvasClick(x: number, y: number) {
    if (!onCanvasClick) {
      return;
    }

    const sourcePoint = toSourcePoint({ x, y });
    const sourceMaxX = sourceDimensions?.width ?? drawingSource.width;
    const sourceMaxY = sourceDimensions?.height ?? drawingSource.height;
    const clampedX = drawingSource
      ? Math.max(0, Math.min(sourceMaxX, sourcePoint.x))
      : Math.max(16, Math.min(canvasWidth - 16, sourcePoint.x));
    const clampedY = drawingSource
      ? Math.max(0, Math.min(sourceMaxY, sourcePoint.y))
      : Math.max(16, Math.min(canvasHeight - 16, sourcePoint.y));
    onCanvasClick({ x: clampedX, y: clampedY });
  }

  function applyZoom(nextScale: number, anchor: { x: number; y: number }) {
    const clampedScale = Math.max(minZoom, Math.min(maxZoom, nextScale));
    const worldPoint = {
      x: (anchor.x - stagePosition.x) / zoomScale,
      y: (anchor.y - stagePosition.y) / zoomScale,
    };

    const nextPosition = clampStagePosition({
      x: anchor.x - worldPoint.x * clampedScale,
      y: anchor.y - worldPoint.y * clampedScale,
    }, clampedScale);

    setZoomScale(clampedScale);
    setStagePosition(nextPosition);
  }

  function zoomFromCenter(multiplier: number) {
    applyZoom(zoomScale * multiplier, { x: canvasWidth / 2, y: canvasHeight / 2 });
  }

  function resetViewport() {
    setZoomScale(1);
    setStagePosition({ x: 0, y: 0 });
  }

  function rotateViewport(deltaDegrees: number) {
    setPdfRotation((current) => {
      const nextRotation = ((current + deltaDegrees) % 360 + 360) % 360;
      return nextRotation;
    });
  }

  function fitToDrawing() {
    setZoomScale(1);
    setStagePosition({ x: 0, y: 0 });
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
        <button type="button" onClick={() => zoomFromCenter(1 / 1.15)}>Zoom Out</button>
        <button type="button" onClick={() => zoomFromCenter(1.15)}>Zoom In</button>
        <button type="button" className="secondary" onClick={fitToDrawing}>Fit Drawing</button>
        <button type="button" className="secondary" onClick={resetViewport}>Reset View</button>
        {drawingLayerFormat === "PDF" ? (
          <>
            <button type="button" className="secondary" onClick={() => rotateViewport(-90)}>Rotate Left</button>
            <button type="button" className="secondary" onClick={() => rotateViewport(90)}>Rotate Right</button>
            <button type="button" className="secondary" onClick={() => setPdfRotation(0)}>Reset Rotation</button>
          </>
        ) : null}
        <span style={{ color: "#8c6b47", fontSize: 13 }}>Zoom: {Math.round(zoomScale * 100)}%</span>
        {drawingLayerFormat === "PDF" ? (
          <span style={{ color: "#8c6b47", fontSize: 13 }}>Rotation: {pdfRotation}°</span>
        ) : null}
      </div>

      <Stage
        width={canvasWidth}
        height={canvasHeight}
        ref={stageRef}
        draggable={false}
        x={stagePosition.x}
        y={stagePosition.y}
        scaleX={zoomScale}
        scaleY={zoomScale}
        onWheel={(event) => {
          event.evt.preventDefault();
          const stage = event.target.getStage();
          const pointer = stage?.getPointerPosition();
          if (!pointer) {
            return;
          }

          const zoomDirection = event.evt.deltaY > 0 ? 1 / 1.1 : 1.1;
          applyZoom(zoomScale * zoomDirection, pointer);
        }}
      >
        <Layer>
          <Rect
            name="background-canvas"
            x={0}
            y={0}
            width={canvasWidth}
            height={canvasHeight}
            fill={exportPreviewOnly ? "rgba(0,0,0,0)" : "#fff9ee"}
            onMouseDown={(event) => {
              onDeselectBalloon?.();
              const stage = event.target.getStage();
              const pointer = stage?.getPointerPosition();
              if (!stage || !pointer) {
                return;
              }

              const world = stage.getAbsoluteTransform().copy().invert().point(pointer);
              handleCanvasClick(world.x, world.y);
            }}
          />
          {Array.from({ length: Math.ceil(canvasWidth / gridStepX) + 1 }, (_, col) => (
            <Line
              name="grid-line"
              key={`grid-col-${col}`}
              points={[col * gridStepX, 0, col * gridStepX, canvasHeight]}
              stroke="#e8dcc3"
              strokeWidth={1}
              visible={!exportPreviewOnly}
            />
          ))}
          {Array.from({ length: Math.ceil(canvasHeight / gridStepY) + 1 }, (_, row) => (
            <Line
              name="grid-line"
              key={`grid-row-${row}`}
              points={[0, row * gridStepY, canvasWidth, row * gridStepY]}
              stroke="#e8dcc3"
              strokeWidth={1}
              visible={!exportPreviewOnly}
            />
          ))}

          {drawingSource ? (
            <KonvaImage
              name="drawing-layer"
              image={drawingSource}
              x={drawingOffsetX}
              y={drawingOffsetY}
              width={drawingRenderWidth}
              height={drawingRenderHeight}
              opacity={0.94}
            />
          ) : (
            <Text
              name="viewer-message"
              x={18}
              y={44}
              text={drawingLayerLabel || "No drawing layer loaded. For overlay editing, use an SVG drawing as base layer."}
              fill="#8c6b47"
              fontSize={14}
              fontFamily="'IBM Plex Sans', sans-serif"
              visible={!exportPreviewOnly}
            />
          )}

          {balloons.flatMap((balloon) => {
            const selected = balloon.id === selectedBalloonId;
            const canvasPoint = toCanvasPoint({ x: balloon.x, y: balloon.y });
            const radius = Math.max(8, balloon.size / 2);
            const textSize = Math.max(12, Math.round(radius * 0.95));
            return [
              <Group
                name="balloon-layer"
                key={`balloon-${balloon.id}`}
                x={canvasPoint.x}
                y={canvasPoint.y}
                draggable
                onMouseDown={(event) => {
                  event.cancelBubble = true;
                  onSelectBalloon?.(balloon.id);
                }}
                onDragEnd={(event) => {
                  const point = event.target.position();
                  const sourcePoint = toSourcePoint({ x: point.x, y: point.y });
                  onMoveBalloon?.({ id: balloon.id, x: sourcePoint.x, y: sourcePoint.y });
                }}
              >
                <Circle
                  x={0}
                  y={0}
                  radius={radius}
                  fill={balloon.fillColor}
                  stroke={selected ? "#ffffff" : balloon.outlineColor}
                  strokeWidth={3}
                  opacity={0.95}
                />
                <Text
                  x={-radius}
                  y={-textSize / 2}
                  width={radius * 2}
                  align="center"
                  text={balloon.label}
                  fill="#111111"
                  fontSize={textSize}
                  fontStyle="bold"
                  fontFamily={balloon.fontFamily}
                  rotation={balloon.textRotation}
                />
                {showDebugAnchors ? (
                  <Text
                    x={radius + 8}
                    y={-8}
                    text={`src ${Math.round(balloon.debugSourceX ?? balloon.x)}, ${Math.round(balloon.debugSourceY ?? balloon.y)}`}
                    fill="#0f172a"
                    fontSize={11}
                    fontFamily="IBM Plex Sans"
                  />
                ) : null}
              </Group>,
            ];
          })}

          {markers.map((marker) => (
            <Circle
              name="helper-marker"
              key={`marker-${marker.id}`}
              x={marker.x}
              y={marker.y}
              radius={8}
              fill="#ffe8b6"
              stroke="#d18a59"
              strokeWidth={2}
              visible={!exportPreviewOnly}
            />
          ))}

          <Text
            name="overlay-hud"
            x={12}
            y={12}
            text={`features detected: ${featureCount} | balloons: ${balloons.length}`}
            fill="#5d7691"
            fontSize={16}
            fontFamily="'Space Grotesk', sans-serif"
            visible={!exportPreviewOnly}
          />
        </Layer>
      </Stage>
    </div>
  );
}
