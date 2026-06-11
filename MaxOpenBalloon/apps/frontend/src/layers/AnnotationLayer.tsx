import { type RefObject, useEffect, useRef, useState } from "react";

import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import pdfWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from "react-konva";

GlobalWorkerOptions.workerSrc = pdfWorkerSrc;

type LayerDimensions = {
  width: number;
  height: number;
};

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
    leaderX?: number | null;
    leaderY?: number | null;
    fillColor: string;
    outlineColor: string;
    size: number;
    textColor: string;
    fontFamily: string;
  }>;
  selectedBalloonId: string | null;
  onCanvasClick?: (point: { x: number; y: number }) => void;
  onSelectBalloon?: (balloonId: string) => void;
  onDeselectBalloon?: () => void;
  onMoveBalloon?: (payload: { id: string; x: number; y: number }) => void;
  drawingLayerUrl?: string | null;
  drawingLayerFormat?: "SVG" | "PDF" | "DWG" | "DXF" | null;
  drawingLayerLabel?: string;
  stageRef?: RefObject<any>;
  exportPreviewOnly?: boolean;
};

export function AnnotationLayer({
  featureCount,
  balloons,
  selectedBalloonId,
  onCanvasClick,
  onSelectBalloon,
  onDeselectBalloon,
  onMoveBalloon,
  drawingLayerUrl,
  drawingLayerFormat,
  drawingLayerLabel,
  stageRef,
  exportPreviewOnly = false,
}: AnnotationLayerProps) {
  const internalStageRef = useRef<any>(null);
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
  const [zoomScale, setZoomScale] = useState(1);
  const [stagePosition, setStagePosition] = useState({ x: 0, y: 0 });
  const [stageRotation, setStageRotation] = useState(0);
  const minZoom = 0.2;
  const maxZoom = 8;

  useEffect(() => {
    if (!stageRef) {
      return;
    }
    (stageRef as any).current = internalStageRef.current;
  });

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
          // nativeViewport at scale=1 gives dimensions in PDF points — same coordinate space as the detector.
          const nativeViewport = page.getViewport({ scale: 1 });
          // Render at higher scale for visual quality but map coordinates using PDF-point dimensions.
          const renderScale = 1.5;
          const viewport = page.getViewport({ scale: renderScale });

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
              // Store the PDF point dimensions (scale=1), NOT the rendered pixel size.
              // This ensures toCanvasPoint maps detector coordinates (in PDF points) correctly.
              setDrawingLayerDimensions({ width: nativeViewport.width, height: nativeViewport.height });
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
          }
          return;
        }
      }

      if (drawingLayerFormat === "SVG") {
        try {
          const svgResponse = await fetch(layerUrl as string);
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
  }, [drawingLayerFormat, drawingLayerUrl]);

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

  function toCanvasPoint(point: { x: number; y: number }) {
    if (!drawingSource) {
      return point;
    }

    return {
      x: drawingOffsetX + point.x * drawingScale,
      y: drawingOffsetY + point.y * drawingScale,
    };
  }

  function toSourcePoint(point: { x: number; y: number }) {
    if (!drawingSource || drawingScale <= 0) {
      return point;
    }

    return {
      x: (point.x - drawingOffsetX) / drawingScale,
      y: (point.y - drawingOffsetY) / drawingScale,
    };
  }

  function handleCanvasClick(x: number, y: number) {
    if (!onCanvasClick) {
      return;
    }

    const sourcePoint = toSourcePoint({ x, y });
    const clampedX = drawingSource
      ? Math.max(0, Math.min(drawingSource.width, sourcePoint.x))
      : Math.max(16, Math.min(canvasWidth - 16, sourcePoint.x));
    const clampedY = drawingSource
      ? Math.max(0, Math.min(drawingSource.height, sourcePoint.y))
      : Math.max(16, Math.min(canvasHeight - 16, sourcePoint.y));
    onCanvasClick({ x: clampedX, y: clampedY });
  }

  function applyZoom(nextScale: number, anchor: { x: number; y: number }) {
    const clampedScale = Math.max(minZoom, Math.min(maxZoom, nextScale));
    const stage = internalStageRef.current;
    if (!stage) {
      return;
    }

    const worldPoint = stage.getAbsoluteTransform().copy().invert().point(anchor);
    const radians = (stageRotation * Math.PI) / 180;
    const cos = Math.cos(radians);
    const sin = Math.sin(radians);
    const offsetX = canvasWidth / 2;
    const offsetY = canvasHeight / 2;
    const rotatedX = (worldPoint.x - offsetX) * clampedScale;
    const rotatedY = (worldPoint.y - offsetY) * clampedScale;

    setZoomScale(clampedScale);
    setStagePosition({
      x: anchor.x - offsetX - (rotatedX * cos - rotatedY * sin),
      y: anchor.y - offsetY - (rotatedX * sin + rotatedY * cos),
    });
  }

  function zoomFromCenter(multiplier: number) {
    applyZoom(zoomScale * multiplier, { x: canvasWidth / 2, y: canvasHeight / 2 });
  }

  function resetViewport() {
    setZoomScale(1);
    setStagePosition({ x: 0, y: 0 });
  }

  function applyRotation(nextRotation: number, anchor: { x: number; y: number }) {
    const stage = internalStageRef.current;
    if (!stage) {
      return;
    }

    const normalizedRotation = ((nextRotation % 360) + 360) % 360;
    const worldPoint = stage.getAbsoluteTransform().copy().invert().point(anchor);
    const radians = (normalizedRotation * Math.PI) / 180;
    const cos = Math.cos(radians);
    const sin = Math.sin(radians);
    const offsetX = canvasWidth / 2;
    const offsetY = canvasHeight / 2;
    const rotatedX = (worldPoint.x - offsetX) * zoomScale;
    const rotatedY = (worldPoint.y - offsetY) * zoomScale;

    setStageRotation(normalizedRotation);
    setStagePosition({
      x: anchor.x - offsetX - (rotatedX * cos - rotatedY * sin),
      y: anchor.y - offsetY - (rotatedX * sin + rotatedY * cos),
    });
  }

  function rotateFromCenter(deltaDegrees: number) {
    applyRotation(stageRotation + deltaDegrees, { x: canvasWidth / 2, y: canvasHeight / 2 });
  }

  function resetRotation() {
    applyRotation(0, { x: canvasWidth / 2, y: canvasHeight / 2 });
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
        <button type="button" className="secondary" onClick={() => rotateFromCenter(-90)}>Rotate Left</button>
        <button type="button" className="secondary" onClick={() => rotateFromCenter(90)}>Rotate Right</button>
        <button type="button" className="secondary" onClick={resetRotation}>Reset Rotation</button>
        <span style={{ color: "#8c6b47", fontSize: 13 }}>Zoom: {Math.round(zoomScale * 100)}%</span>
        <span style={{ color: "#8c6b47", fontSize: 13 }}>Rotation: {stageRotation} deg</span>
      </div>

      <Stage
        width={canvasWidth}
        height={canvasHeight}
        ref={internalStageRef}
        draggable
        x={stagePosition.x + canvasWidth / 2}
        y={stagePosition.y + canvasHeight / 2}
        offsetX={canvasWidth / 2}
        offsetY={canvasHeight / 2}
        scaleX={zoomScale}
        scaleY={zoomScale}
        rotation={stageRotation}
        onDragEnd={(event) => {
          setStagePosition({
            x: event.target.x() - canvasWidth / 2,
            y: event.target.y() - canvasHeight / 2,
          });
        }}
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
        <Layer
          onMouseDown={(event) => {
            const target = event.target;
            const balloonNode = target.findAncestor(".balloon-layer", true);
            if (!balloonNode) {
              onDeselectBalloon?.();
            }
          }}
        >
          <Rect
            name="background-canvas"
            x={0}
            y={0}
            width={canvasWidth}
            height={canvasHeight}
            fill={exportPreviewOnly ? "rgba(0,0,0,0)" : "#fff9ee"}
            onMouseDown={(event) => {
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
            const hasLeader = typeof balloon.leaderX === "number" && Number.isFinite(balloon.leaderX)
              && typeof balloon.leaderY === "number" && Number.isFinite(balloon.leaderY);
            const leaderCanvasPoint = hasLeader ? toCanvasPoint({ x: balloon.leaderX as number, y: balloon.leaderY as number }) : null;
            const leaderDx = leaderCanvasPoint ? leaderCanvasPoint.x - canvasPoint.x : 0;
            const leaderDy = leaderCanvasPoint ? leaderCanvasPoint.y - canvasPoint.y : 0;
            const leaderDistance = Math.hypot(leaderDx, leaderDy);
            const radius = Math.max(8, balloon.size / 2);
            const leaderStartX = hasLeader && leaderDistance > 0 ? (leaderDx / leaderDistance) * radius : 0;
            const leaderStartY = hasLeader && leaderDistance > 0 ? (leaderDy / leaderDistance) * radius : 0;
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
                  radius={radius}
                  fill={balloon.fillColor}
                  stroke={selected ? "#ffffff" : balloon.outlineColor}
                  strokeWidth={3}
                  opacity={0.95}
                />
                {hasLeader && leaderDistance > radius
                  ? (
                    <Line
                      points={[leaderStartX, leaderStartY, leaderDx, leaderDy]}
                      stroke={balloon.outlineColor}
                      strokeWidth={1.5}
                      opacity={0.9}
                    />
                  )
                  : null}
                <Text
                  x={0}
                  y={0}
                  text={balloon.label}
                  fill="#000000"
                  fontSize={Math.max(12, Math.round(balloon.size / 2.2))}
                  fontStyle="bold"
                  fontFamily={balloon.fontFamily}
                  align="center"
                  verticalAlign="middle"
                  offsetX={Math.max(6, Math.round((balloon.size / 2.2) * 0.55))}
                  offsetY={Math.max(6, Math.round((balloon.size / 2.2) * 0.55))}
                />
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
