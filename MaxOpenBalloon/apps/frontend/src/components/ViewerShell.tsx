import { type ChangeEvent, useEffect, useRef, useState } from "react";
import { jsPDF } from "jspdf";

import { AnnotationLayer } from "../layers/AnnotationLayer";
import { BalloonOverlayLayer } from "../layers/BalloonOverlayLayer";
import { LibraCadViewer } from "../layers/LibraCadViewer";
import {
  autoBalloon,
  createBalloon,
  deleteBalloon,
  convertDrawing,
  listBalloons,
  runFeatureFlow,
  uploadDrawingFile,
  updateBalloon,
  type BalloonRecord,
  type ServiceResult,
  type SessionContext,
  type TranslationJob,
} from "../services/serviceApi";
import { completeOidcLoginFromUrl, startOidcLogin } from "../services/oidcAuth";
import { useViewerState } from "../state/viewerState";

const serviceSequence = [
  "Drawing",
  "Balloon",
  "Revision",
  "Geometry",
  "AI",
  "DWG",
  "MCP",
] as const;

type ServiceLabel = (typeof serviceSequence)[number];

type LastCanvasAction =
  | {
    kind: "move";
    balloonId: string;
    previousGeometry: Record<string, unknown>;
  }
  | {
    kind: "place";
    balloon: BalloonRecord;
  };

type DrawingFormat = "DWG" | "DXF" | "PDF" | "SVG";

function inferSourceFormat(fileName: string): DrawingFormat {
  const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "dxf") {
    return "DXF";
  }

  if (ext === "pdf") {
    return "PDF";
  }

  if (ext === "svg") {
    return "SVG";
  }

  return "DWG";
}

function geometryNumber(geometry: Record<string, unknown>, key: "x" | "y", fallback: number): number {
  const value = geometry[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function geometryFillColor(geometry: Record<string, unknown>, fallback = "transparent"): string {
  const value = geometry.fill_color ?? geometry.color;
  return typeof value === "string" && value ? value : fallback;
}

function isTransparentFill(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized === "transparent" || normalized === "none" || normalized === "#00000000";
}

function geometryOutlineColor(geometry: Record<string, unknown>, fallback = "#d7651f"): string {
  const value = geometry.outline_color;
  return typeof value === "string" && value ? value : fallback;
}

function geometrySize(geometry: Record<string, unknown>, fallback = 18): number {
  const value = geometry.size;
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function geometryTextColor(geometry: Record<string, unknown>, fallback = "#fff4d8"): string {
  const value = geometry.text_color;
  return typeof value === "string" && value ? value : fallback;
}

function geometryFontFamily(geometry: Record<string, unknown>, fallback = "Space Grotesk"): string {
  const value = geometry.font_family;
  return typeof value === "string" && value ? value : fallback;
}

function clampCanvasCoordinate(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function normalizeGridSize(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return 10;
  }
  return Math.max(2, Math.min(80, Math.round(parsed)));
}

function snapCoordinate(value: number, gridSize: number, enabled: boolean): number {
  if (!enabled) {
    return value;
  }

  return Math.round(value / gridSize) * gridSize;
}

function applyBalloonMoveGeometry(
  geometry: Record<string, unknown>,
  x: number,
  y: number,
): Record<string, unknown> {
  return {
    ...geometry,
    x: Math.round(x),
    y: Math.round(y),
  };
}

export function ViewerShell() {
  const { activeFormat } = useViewerState();
  const [tenantId, setTenantId] = useState("tenant-ui-001");
  const [session, setSession] = useState<SessionContext | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const stageRef = useRef<any>(null);

  const [sourceUri, setSourceUri] = useState("minio://drawings/ui-sample-1.dwg");
  const [sourceFormat, setSourceFormat] = useState<DrawingFormat>("DWG");
  const [previewAssetFormat, setPreviewAssetFormat] = useState<"SVG" | "PDF" | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedFileUrl, setSelectedFileUrl] = useState<string | null>(null);

  const [drawingId, setDrawingId] = useState<string | null>(null);
  const [balloons, setBalloons] = useState<BalloonRecord[]>([]);
  const [selectedBalloonId, setSelectedBalloonId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState("");
  const [editX, setEditX] = useState("24");
  const [editY, setEditY] = useState("18");
  const [editSize, setEditSize] = useState("18");
  const [editFillColor, setEditFillColor] = useState("#ffd7c2");
  const [editNoFill, setEditNoFill] = useState(true);
  const [editOutlineColor, setEditOutlineColor] = useState("#d7651f");
  const [editTextColor, setEditTextColor] = useState("#fff4d8");
  const [editFontFamily, setEditFontFamily] = useState("Space Grotesk");
  const [viewerMode, setViewerMode] = useState<"libracad" | "annotation">("annotation");
  const [placeModeEnabled, setPlaceModeEnabled] = useState(false);
  const [snapToGridEnabled, setSnapToGridEnabled] = useState(true);
  const [gridSizeInput, setGridSizeInput] = useState("10");
  const [lastCanvasAction, setLastCanvasAction] = useState<LastCanvasAction | null>(null);
  const [svgJob, setSvgJob] = useState<TranslationJob | null>(null);
  const [pdfJob, setPdfJob] = useState<TranslationJob | null>(null);
  const [viewerAssetUrl, setViewerAssetUrl] = useState<string | null>(null);
  const [isConvertingPreview, setIsConvertingPreview] = useState(false);
  const [isLoadingDrawing, setIsLoadingDrawing] = useState(false);
  const [loadStatus, setLoadStatus] = useState("Idle");
  const [previewLoadError, setPreviewLoadError] = useState<string | null>(null);
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exportPreviewOnly, setExportPreviewOnly] = useState(false);
  const [toolsPanelOpen, setToolsPanelOpen] = useState(false);

  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<ServiceResult | null>(null);
  const [aiSuggestionCount, setAiSuggestionCount] = useState(0);
  const [detectorMode, setDetectorMode] = useState<"paddleocr_opencv" | "heuristic" | "florence2" | "hybrid">("paddleocr_opencv");
  const [lastDetectorUsed, setLastDetectorUsed] = useState<string | null>(null);
  const [lastAttemptedDetectors, setLastAttemptedDetectors] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastRunAt, setLastRunAt] = useState<string | null>(null);

  const selectedBalloon = balloons.find((item) => item.id === selectedBalloonId) ?? null;

  useEffect(() => {
    return () => {
      if (selectedFileUrl) {
        URL.revokeObjectURL(selectedFileUrl);
      }
    };
  }, [selectedFileUrl]);

  useEffect(() => {
    return () => {
      if (viewerAssetUrl) {
        URL.revokeObjectURL(viewerAssetUrl);
      }
    };
  }, [viewerAssetUrl]);

  useEffect(() => {
    async function completeAuth() {
      try {
        const oidcSession = await completeOidcLoginFromUrl();
        if (!oidcSession) {
          return;
        }

        const resolvedTenant = oidcSession.tenantIdFromToken ?? tenantId;
        setTenantId(resolvedTenant);
        setSession({
          tenantId: resolvedTenant,
          accessToken: oidcSession.accessToken,
        });
        setError(null);
      } catch (authError) {
        const message = authError instanceof Error ? authError.message : "OIDC callback failed";
        setError(message);
      }
    }

    void completeAuth();
  }, [tenantId]);

  async function signIn() {
    const normalizedTenant = tenantId.trim();
    if (!normalizedTenant) {
      setError("Tenant ID is required.");
      return;
    }

    setAuthBusy(true);
    setError(null);

    try {
      await startOidcLogin(normalizedTenant);
    } catch (authError) {
      const message = authError instanceof Error ? authError.message : "Unable to start OIDC login";
      setError(message);
      setAuthBusy(false);
    }
  }

  function signOut() {
    setSession(null);
    setDrawingId(null);
    setBalloons([]);
    setSelectedBalloonId(null);
    setSvgJob(null);
    setPdfJob(null);
    setResult(null);
    setAiSuggestionCount(0);
    setLastDetectorUsed(null);
    setLastAttemptedDetectors([]);
    setError(null);
    setAuthBusy(false);
    setLastCanvasAction(null);
    setPreviewAssetFormat(null);
    setLoadStatus("Signed out.");
  }

  async function runWorkflow() {
    if (!session) {
      setError("Sign in before running workflow.");
      return;
    }

    setIsRunning(true);
    setError(null);

    try {
      const flowResult = await runFeatureFlow(session.tenantId);
      setResult(flowResult);
      setDrawingId(flowResult.drawingId);
      setEditLabel(`B-${flowResult.revisionNumber.toString().padStart(3, "0")}`);
      setLastRunAt(new Date().toLocaleTimeString());
    } catch (runError) {
      const message = runError instanceof Error ? runError.message : "Unknown error";
      setError(message);
    } finally {
      setIsRunning(false);
    }
  }

  async function loadDrawing(fileOverride?: File) {
    if (!session) {
      setError("Sign in before loading drawing.");
      return;
    }

    if (isLoadingDrawing) {
      return;
    }

    const fileToLoad = fileOverride ?? selectedFile;

    if (!fileToLoad) {
      setError("Select a desktop drawing file first.");
      return;
    }

    setError(null);
    setLoadStatus("Uploading drawing file...");
    setIsLoadingDrawing(true);
    try {
      const drawing = await uploadDrawingFile(session, fileToLoad);
      const detectedFormat = drawing.source_format as DrawingFormat;
      let existingBalloons: BalloonRecord[] = [];
      setLoadStatus("Loading existing balloons...");
      try {
        existingBalloons = await listBalloons(session, drawing.id);
      } catch (balloonListError) {
        const detail = balloonListError instanceof Error
          ? balloonListError.message
          : "Unknown balloon-service error";
        // Drawing load should still succeed even if balloon APIs are temporarily unavailable.
        setError(`Drawing loaded, but balloons are unavailable: ${detail}`);
      }
      setDrawingId(drawing.id);
      setSourceUri(drawing.source_uri);
      setSourceFormat(detectedFormat);
      setBalloons(existingBalloons);
      setSelectedBalloonId(existingBalloons.length > 0 ? existingBalloons[0].id : null);
      setAiSuggestionCount(0);
      setLastDetectorUsed(null);
      setLastAttemptedDetectors([]);

      setPreviewLoadError(null);
      if (detectedFormat === "DWG") {
        setIsConvertingPreview(true);
        setLoadStatus("Converting DWG to PDF with QCAD...");
        let pdfStepError: string | null = null;
        try {
          const pdfJobResult = await convertDrawing(session, drawing.source_uri, "PDF");
          setPdfJob(pdfJobResult);
        } catch (pdfPreviewError) {
          pdfStepError = pdfPreviewError instanceof Error ? pdfPreviewError.message : "DWG PDF conversion failed";
          setPdfJob(null);
        }

        setLoadStatus("Preparing DWG base preview as SVG...");
        try {
          const svgJobResult = await convertDrawing(session, drawing.source_uri, "SVG");
          setSvgJob(svgJobResult);
          const blobUrl = await resolveRemotePreviewUrl(svgJobResult.output_uri);
          setViewerAssetUrl((current) => {
            if (current && current.startsWith("blob:")) {
              URL.revokeObjectURL(current);
            }
            return blobUrl;
          });
          setPreviewAssetFormat("SVG");
          if (pdfStepError) {
            setLoadStatus("DWG preview ready using LibreDWG SVG. QCAD PDF step failed.");
          } else {
            setLoadStatus("DWG preview ready with LibreDWG SVG base layer.");
          }
        } catch (svgPreviewError) {
          const svgError = svgPreviewError instanceof Error ? svgPreviewError.message : "DWG SVG preview failed";
          const detail = pdfStepError ? `${pdfStepError}. SVG preview failed: ${svgError}` : svgError;
          setPreviewLoadError(detail);
          setViewerAssetUrl(null);
          setPreviewAssetFormat(null);
          setLoadStatus("Failed to prepare DWG preview.");
        } finally {
          setIsConvertingPreview(false);
        }
      } else if (detectedFormat === "DXF") {
        setIsConvertingPreview(true);
        setLoadStatus("Converting DXF to SVG preview...");
        try {
          const previewJob = await convertDrawing(session, drawing.source_uri, "SVG");
          setSvgJob(previewJob);
          const blobUrl = await resolveRemotePreviewUrl(previewJob.output_uri);
          setViewerAssetUrl((current) => {
            if (current && current.startsWith("blob:")) {
              URL.revokeObjectURL(current);
            }
            return blobUrl;
          });
          setPreviewAssetFormat("SVG");
          setLoadStatus("DXF preview ready.");
        } catch (dxfError) {
          setPreviewLoadError(dxfError instanceof Error ? dxfError.message : "DXF preview conversion failed");
          setViewerAssetUrl(null);
          setPreviewAssetFormat(null);
          setLoadStatus("Failed to prepare DXF preview.");
        } finally {
          setIsConvertingPreview(false);
        }
      } else if (detectedFormat === "PDF") {
        setViewerAssetUrl(selectedFileUrl);
        setPreviewAssetFormat("PDF");
        setSvgJob(null);
        setPdfJob(null);
        setLoadStatus("PDF loaded directly into base preview.");
      } else {
        setViewerAssetUrl(selectedFileUrl);
        setPreviewAssetFormat("SVG");
        setSvgJob(null);
        setPdfJob(null);
        setLoadStatus("SVG loaded directly into base preview.");
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load drawing");
      setLoadStatus("Loading failed.");
    } finally {
      setIsLoadingDrawing(false);
    }
  }

  async function runAutoBalloon() {
    if (!session || !drawingId) {
      setError("Load a drawing first.");
      return;
    }

    setError(null);
    try {
      const response = await autoBalloon(session, drawingId, 60, detectorMode);
      const created = response.balloons;
      setAiSuggestionCount(response.suggestions);
      setLastDetectorUsed(response.detectorUsed);
      setLastAttemptedDetectors(response.attemptedDetectors);
      setBalloons((current) => {
        const existing = new Map(current.map((entry) => [entry.id, entry]));
        created.forEach((entry) => {
          existing.set(entry.id, entry);
        });
        return [...existing.values()];
      });

      const first = created[0] ?? null;
      if (first) {
        setSelectedBalloonId(first.id);
        setEditLabel(first.label);
        setEditX(String(geometryNumber(first.geometry, "x", 24)));
        setEditY(String(geometryNumber(first.geometry, "y", 18)));
        setEditSize(String(geometrySize(first.geometry)));
        const nextFill = geometryFillColor(first.geometry);
        setEditFillColor(isTransparentFill(nextFill) ? "#ffd7c2" : nextFill);
        setEditNoFill(isTransparentFill(nextFill));
        setEditOutlineColor(geometryOutlineColor(first.geometry));
        setEditTextColor(geometryTextColor(first.geometry));
        setEditFontFamily(geometryFontFamily(first.geometry));
      }
    } catch (balloonError) {
      setError(balloonError instanceof Error ? balloonError.message : "Failed to auto-balloon drawing");
    }
  }

  async function addBalloonFromEditor() {
    if (!session || !drawingId) {
      setError("Load a drawing before adding balloons.");
      return;
    }

    setError(null);
    try {
      const created = await createBalloon(
        session,
        drawingId,
        editLabel || `B-${String(balloons.length + 1).padStart(3, "0")}`,
        {
          x: Number(editX),
          y: Number(editY),
          size: Number(editSize),
          fill_color: editNoFill ? "transparent" : editFillColor,
          outline_color: editOutlineColor,
          text_color: editTextColor,
          font_family: editFontFamily,
        },
      );
      setBalloons((current) => [...current, created]);
      setSelectedBalloonId(created.id);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Failed to add balloon");
    }
  }

  function selectBalloon(balloonId: string) {
    const next = balloons.find((entry) => entry.id === balloonId);
    if (!next) {
      return;
    }

    setSelectedBalloonId(next.id);
    setEditLabel(next.label);
    setEditX(String(geometryNumber(next.geometry, "x", 24)));
    setEditY(String(geometryNumber(next.geometry, "y", 18)));
    setEditSize(String(geometrySize(next.geometry)));
    const nextFill = geometryFillColor(next.geometry);
    setEditFillColor(isTransparentFill(nextFill) ? "#ffd7c2" : nextFill);
    setEditNoFill(isTransparentFill(nextFill));
    setEditOutlineColor(geometryOutlineColor(next.geometry));
    setEditTextColor(geometryTextColor(next.geometry));
    setEditFontFamily(geometryFontFamily(next.geometry));
  }

  async function moveBalloonOnCanvas(balloonId: string, x: number, y: number) {
    const gridSize = normalizeGridSize(gridSizeInput);
    const nextX = Math.round(snapCoordinate(x, gridSize, snapToGridEnabled));
    const nextY = Math.round(snapCoordinate(y, gridSize, snapToGridEnabled));

    const target = balloons.find((entry) => entry.id === balloonId);
    if (!target) {
      return;
    }

    const movedGeometry = applyBalloonMoveGeometry(target.geometry, nextX, nextY);
    setLastCanvasAction({
      kind: "move",
      balloonId,
      previousGeometry: target.geometry,
    });

    setBalloons((current) => current.map((entry) => (
      entry.id === balloonId
        ? {
          ...entry,
          geometry: movedGeometry,
        }
        : entry
    )));

    if (selectedBalloonId === balloonId) {
      setEditX(String(nextX));
      setEditY(String(nextY));
    }

    if (!session) {
      return;
    }

    try {
      const updated = await updateBalloon(session, balloonId, {
        geometry: movedGeometry,
      });
      setBalloons((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (moveError) {
      setError(moveError instanceof Error ? moveError.message : "Failed to persist balloon move");
    }
  }

  async function saveBalloonChanges() {
    if (!session || !selectedBalloon) {
      setError("Create a balloon before editing.");
      return;
    }

    setError(null);
    try {
      const updated = await updateBalloon(session, selectedBalloon.id, {
        label: editLabel,
        geometry: {
          x: Number(editX),
          y: Number(editY),
          size: Number(editSize),
          fill_color: editNoFill ? "transparent" : editFillColor,
          outline_color: editOutlineColor,
          text_color: editTextColor,
          font_family: editFontFamily,
        },
      });
      setBalloons((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (editError) {
      setError(editError instanceof Error ? editError.message : "Failed to update balloon");
    }
  }

  function openDesktopBrowse() {
    fileInputRef.current?.click();
  }

  function onDesktopFilePicked(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    if (!file) {
      return;
    }

    if (selectedFileUrl) {
      URL.revokeObjectURL(selectedFileUrl);
    }

    const objectUrl = URL.createObjectURL(file);
    setSelectedFile(file);
    setSelectedFileUrl(objectUrl);
    setSourceFormat(inferSourceFormat(file.name));
    setPreviewAssetFormat(null);
    setSourceUri(`local://desktop/${encodeURIComponent(file.name)}`);
    setLoadStatus(`Ready to upload ${file.name}.`);
    event.target.value = "";
  }

  async function resolveRemotePreviewUrl(remoteUrl: string): Promise<string> {
    if (!session) {
      throw new Error("Not signed in");
    }

    const headers: Record<string, string> = {
      "X-Tenant-ID": session.tenantId,
    };

    if (session.accessToken) {
      headers.Authorization = `Bearer ${session.accessToken}`;
    }

    let response = await fetch(remoteUrl, {
      method: "GET",
      headers,
    });

    if (!response.ok && response.status === 401 && session.accessToken) {
      const detail = await response.text();
      if (/invalid bearer token/i.test(detail)) {
        response = await fetch(remoteUrl, {
          method: "GET",
          headers: { "X-Tenant-ID": session.tenantId },
        });
      }
    }

    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.json();
        detail = body?.detail ?? body?.message ?? JSON.stringify(body);
      } catch {
        detail = await response.text().catch(() => response.statusText);
      }
      throw new Error(`Translation service error (HTTP ${response.status}): ${detail || response.statusText}`);
    }

    const blob = await response.blob();
    return URL.createObjectURL(blob);
  }

  async function convertToSvg() {
    if (!session) {
      setError("Sign in before conversion.");
      return;
    }

    if (!drawingId || !sourceUri.startsWith("minio://")) {
      setError("Load the drawing first. Export uses the uploaded drawing source, not a local desktop file.");
      return;
    }

    setError(null);
    setIsConvertingPreview(true);
    setPreviewLoadError(null);
    setLoadStatus("Refreshing SVG preview...");
    try {
      const job = await convertDrawing(session, sourceUri, "SVG");
      setSvgJob(job);
      const previewUrl = await resolveRemotePreviewUrl(job.output_uri);
      setViewerAssetUrl((current) => {
        if (current) {
          URL.revokeObjectURL(current);
        }
        return previewUrl;
      });
      setPreviewAssetFormat("SVG");
      setLoadStatus("SVG preview refreshed.");
    } catch (conversionError) {
      const msg = conversionError instanceof Error ? conversionError.message : "SVG conversion failed";
      setError(msg);
      setPreviewLoadError(msg);
      setLoadStatus("Preview refresh failed.");
    } finally {
      setIsConvertingPreview(false);
    }
  }

  async function placeBalloonFromCanvas(x: number, y: number) {
    const gridSize = normalizeGridSize(gridSizeInput);
    const nextX = Math.round(snapCoordinate(x, gridSize, snapToGridEnabled));
    const nextY = Math.round(snapCoordinate(y, gridSize, snapToGridEnabled));

    if (!placeModeEnabled) {
      setEditX(String(nextX));
      setEditY(String(nextY));
      return;
    }

    if (!session || !drawingId) {
      setError("Load a drawing before placing balloons on canvas.");
      return;
    }

    setError(null);
    try {
      const created = await createBalloon(
        session,
        drawingId,
        editLabel || `B-${String(balloons.length + 1).padStart(3, "0")}`,
        {
          x: nextX,
          y: nextY,
          size: Number(editSize),
          fill_color: editNoFill ? "transparent" : editFillColor,
          outline_color: editOutlineColor,
          text_color: editTextColor,
          font_family: editFontFamily,
        },
      );
      setBalloons((current) => [...current, created]);
      setSelectedBalloonId(created.id);
      setEditX(String(nextX));
      setEditY(String(nextY));
      setLastCanvasAction({
        kind: "place",
        balloon: created,
      });
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Failed to place balloon from canvas");
    }
  }

  async function undoLastCanvasChange() {
    if (!lastCanvasAction || !session) {
      return;
    }

    setError(null);

    if (lastCanvasAction.kind === "move") {
      try {
        const updated = await updateBalloon(session, lastCanvasAction.balloonId, {
          geometry: lastCanvasAction.previousGeometry,
        });
        setBalloons((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
        if (selectedBalloonId === updated.id) {
          setEditX(String(geometryNumber(updated.geometry, "x", 24)));
          setEditY(String(geometryNumber(updated.geometry, "y", 18)));
          setEditSize(String(geometrySize(updated.geometry)));
          const nextFill = geometryFillColor(updated.geometry);
          setEditFillColor(isTransparentFill(nextFill) ? "#ffd7c2" : nextFill);
          setEditNoFill(isTransparentFill(nextFill));
          setEditOutlineColor(geometryOutlineColor(updated.geometry));
          setEditTextColor(geometryTextColor(updated.geometry));
          setEditFontFamily(geometryFontFamily(updated.geometry));
        }
        setLastCanvasAction(null);
      } catch (undoError) {
        setError(undoError instanceof Error ? undoError.message : "Failed to undo balloon move");
      }
      return;
    }

    try {
      await deleteBalloon(session, lastCanvasAction.balloon.id);
      setBalloons((current) => current.filter((entry) => entry.id !== lastCanvasAction.balloon.id));
      if (selectedBalloonId === lastCanvasAction.balloon.id) {
        setSelectedBalloonId(null);
      }
      setLastCanvasAction(null);
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : "Failed to undo placed balloon");
    }
  }

  async function deleteSelectedBalloon() {
    if (!session || !selectedBalloon) {
      setError("Select a balloon before deleting.");
      return;
    }

    setError(null);
    try {
      const deletingId = selectedBalloon.id;
      await deleteBalloon(session, deletingId);
      setBalloons((current) => {
        const next = current.filter((entry) => entry.id !== deletingId);
        const nextSelected = next[0] ?? null;
        setSelectedBalloonId(nextSelected?.id ?? null);
        if (nextSelected) {
          setEditLabel(nextSelected.label);
          setEditX(String(geometryNumber(nextSelected.geometry, "x", 24)));
          setEditY(String(geometryNumber(nextSelected.geometry, "y", 18)));
          setEditSize(String(geometrySize(nextSelected.geometry)));
          const nextFill = geometryFillColor(nextSelected.geometry);
          setEditFillColor(isTransparentFill(nextFill) ? "#ffd7c2" : nextFill);
          setEditNoFill(isTransparentFill(nextFill));
          setEditOutlineColor(geometryOutlineColor(nextSelected.geometry));
          setEditTextColor(geometryTextColor(nextSelected.geometry));
          setEditFontFamily(geometryFontFamily(nextSelected.geometry));
        }
        return next;
      });
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Failed to delete balloon");
    }
  }

  const canvasBalloons = balloons.map((item, index) => {
    const fallbackX = 80 + ((index % 10) * 92);
    const fallbackY = 90 + (Math.floor(index / 10) * 64);

    return {
      id: item.id,
      label: item.label,
      x: geometryNumber(item.geometry, "x", fallbackX),
      y: geometryNumber(item.geometry, "y", fallbackY),
      fillColor: geometryFillColor(item.geometry),
      outlineColor: geometryOutlineColor(item.geometry),
      size: geometrySize(item.geometry),
      textColor: geometryTextColor(item.geometry),
      fontFamily: geometryFontFamily(item.geometry),
    };
  });

  const drawingLayerLabel = !selectedFileUrl
    ? "No drawing selected yet. Load a drawing to start layered editing."
    : sourceFormat === "DWG" || sourceFormat === "DXF"
      ? isConvertingPreview
        ? "Preparing converted DWG/DXF preview — please wait..."
        : previewLoadError
          ? `Preview failed: ${previewLoadError}`
          : viewerAssetUrl
            ? "DWG/DXF preview loaded and ready."
            : svgJob
              ? "Loading SVG preview from translation service..."
              : "Load drawing to trigger automatic preview conversion."
      : sourceFormat === "PDF"
        ? "PDF base layer is rendered from page 1 with balloon overlay on top."
        : "SVG base layer is rendered with interactive balloon overlay.";

  function exportLayeredCanvasPdf() {
    const stage = stageRef.current;
    if (!stage) {
      setError("Layered canvas is not ready for export.");
      return;
    }

    const width = Number(stage.width());
    const height = Number(stage.height());
    const image = stage.toDataURL({ pixelRatio: 2 });

    const pdf = new jsPDF({
      orientation: width >= height ? "landscape" : "portrait",
      unit: "px",
      format: [width, height],
      compress: true,
    });

    pdf.addImage(image, "PNG", 0, 0, width, height, undefined, "FAST");
    const filenameBase = (selectedFile?.name?.replace(/\.[^.]+$/, "") ?? "layered-drawing").trim();
    pdf.save(`${filenameBase || "layered-drawing"}-layered.pdf`);
  }

  async function buildMergedLayeredPdfBlob(stage: any): Promise<{ blob: Blob; fileName: string }> {
    const hiddenLayerNames = ["background-canvas", "grid-line", "helper-marker", "overlay-hud", "viewer-message"];
    const hiddenNodes: Array<{ node: any; visible: boolean }> = [];

    try {
      hiddenLayerNames.forEach((name) => {
        const nodes = stage.find(`.${name}`) || [];
        nodes.forEach((node: any) => {
          hiddenNodes.push({ node, visible: node.visible() });
          node.visible(false);
        });
      });

      stage.batchDraw();

      const drawingNode = stage.findOne(".drawing-layer");
      if (!drawingNode) {
        throw new Error("Drawing layer is unavailable for merged PDF export.");
      }

      const bounds = drawingNode.getClientRect({ skipShadow: true });
      const width = Math.max(1, Math.ceil(bounds.width));
      const height = Math.max(1, Math.ceil(bounds.height));
      const image = stage.toDataURL({
        x: bounds.x,
        y: bounds.y,
        width,
        height,
        pixelRatio: 2,
      });

      const pdf = new jsPDF({
        orientation: width >= height ? "landscape" : "portrait",
        unit: "px",
        format: [width, height],
        compress: true,
      });

      pdf.addImage(image, "PNG", 0, 0, width, height, undefined, "FAST");
      const filenameBase = (selectedFile?.name?.replace(/\.[^.]+$/, "") ?? "merged-drawing").trim();
      const blob = pdf.output("blob");
      return {
        blob,
        fileName: `${filenameBase || "merged-drawing"}-merged.pdf`,
      };
    } finally {
      hiddenNodes.forEach(({ node, visible }) => {
        node.visible(visible);
      });
      stage.batchDraw();
    }
  }

  async function downloadGeneratedPdf(outputUri: string) {
    const filenameBase = (selectedFile?.name?.replace(/\.[^.]+$/, "") ?? "drawing-export").trim();
    const response = await fetch(outputUri, { method: "GET" });
    if (!response.ok) {
      throw new Error(`PDF output download failed: ${response.status} ${response.statusText}`);
    }

    const pdfBlob = await response.blob();
    const blobUrl = URL.createObjectURL(pdfBlob);
    const anchor = document.createElement("a");

    anchor.href = blobUrl;
    anchor.download = `${filenameBase || "drawing-export"}-translated.pdf`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(blobUrl);
  }

  async function exportPdf() {
    setExportStatus("Preparing merged PDF export...");

    if (!session) {
      setError("Sign in before export.");
      setExportStatus(null);
      return;
    }

    if (!drawingId) {
      setError("Load the drawing first before exporting.");
      setExportStatus(null);
      return;
    }

    const stage = stageRef.current;
    if (!stage) {
      setError("Viewer stage is not ready for merged PDF export.");
      setExportStatus(null);
      return;
    }

    setError(null);
    setIsExportingPdf(true);
    try {
      const merged = await buildMergedLayeredPdfBlob(stage);
      const mergedFile = new File([merged.blob], merged.fileName, { type: "application/pdf" });
      const mergedBlobUrl = URL.createObjectURL(merged.blob);

      const anchor = document.createElement("a");
      anchor.href = mergedBlobUrl;
      anchor.download = merged.fileName;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(mergedBlobUrl);

      const uploadedDrawing = await uploadDrawingFile(session, mergedFile);
      const existingBalloons = await listBalloons(session, uploadedDrawing.id);
      setDrawingId(uploadedDrawing.id);
      setSourceUri(uploadedDrawing.source_uri);
      setSourceFormat(uploadedDrawing.source_format as DrawingFormat);
      setBalloons(existingBalloons);
      setSelectedBalloonId(existingBalloons.length > 0 ? existingBalloons[0].id : null);
      setPdfJob(null);

      if (selectedFileUrl) {
        URL.revokeObjectURL(selectedFileUrl);
      }
      setSelectedFile(mergedFile);
      const mergedUrl = URL.createObjectURL(mergedFile);
      setSelectedFileUrl(mergedUrl);
      setViewerAssetUrl(mergedUrl);
      setPreviewAssetFormat("PDF");
      setLoadStatus("Merged PDF exported and loaded as base preview.");

      setExportStatus("Merged PDF exported, downloaded, and loaded as new drawing source.");
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "PDF export failed");
      setExportStatus(null);
    } finally {
      setIsExportingPdf(false);
    }
  }

  const serviceDetails: Array<{ name: ServiceLabel; value: string | number | null }> = [
    { name: "Drawing", value: result?.drawingId ?? null },
    { name: "Balloon", value: result?.balloonId ?? null },
    { name: "Revision", value: result?.revisionNumber ?? null },
    { name: "Geometry", value: result?.geometryFeatures ?? null },
    { name: "AI", value: result?.aiSuggestions ?? null },
    { name: "DWG", value: result?.dwgJobId ?? null },
    { name: "MCP", value: result?.mcpTool ?? null },
  ];

  return (
    <main className="viewer-page">
      <header className="hero">
        <p className="eyebrow">MaxOpenBalloon - Production Playground</p>
        <h1>Engineering Drawing Intelligence Hub</h1>
        <p className="hero-subtitle">
          Workflow cockpit for login, 2D drawing load, auto-balloon recommendations,
          layered balloon editing, DWG viewing conversion, and PDF export.
        </p>
      </header>

      <section className="workspace-shell" aria-label="editor-workspace">
        <article className={`panel left-pane floating-tools ${toolsPanelOpen ? "open" : "collapsed"}`}>
          <div className="button-row tools-panel-header">
            <h2>{toolsPanelOpen ? "Load and Balloon Tools" : "Tools"}</h2>
            <button type="button" className="secondary" onClick={() => setToolsPanelOpen((current) => !current)}>
              {toolsPanelOpen ? "Minimize" : "Expand"}
            </button>
          </div>

          {!toolsPanelOpen ? <p className="timestamp">Floating controls</p> : null}

          {toolsPanelOpen ? (
            <>
          <p className="panel-hint">Left pane controls loading and balloon operations. Right pane is the layered editor.</p>

          <div className="field-row">
            <label htmlFor="tenant-id">Tenant ID</label>
            <input
              id="tenant-id"
              value={tenantId}
              onChange={(event) => setTenantId(event.target.value)}
              placeholder="tenant-ui-001"
            />
          </div>

          <div className="button-row">
            {session ? (
              <button type="button" onClick={signOut} className="secondary">
                Sign Out
              </button>
            ) : (
              <button type="button" onClick={() => void signIn()} disabled={authBusy}>
                {authBusy ? "Redirecting..." : "Sign In with Authentik"}
              </button>
            )}
          </div>

          <div className="meta-row">
            <span>Session</span>
            <strong>{session ? "Authenticated" : "Signed Out"}</strong>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept=".dwg,.dxf,.pdf,.svg"
            onChange={onDesktopFilePicked}
            className="hidden-file-input"
          />

          <div className="field-row">
            <label>Desktop Drawing</label>
            <div className="button-row">
              <button type="button" onClick={openDesktopBrowse}>Browse Desktop File</button>
              <p className="timestamp">{selectedFile?.name ?? "No file selected"}</p>
            </div>
          </div>

          <div className="field-row">
            <label htmlFor="source-format">Source Format</label>
            <select
              id="source-format"
              value={sourceFormat}
              onChange={(event) => setSourceFormat(event.target.value as DrawingFormat)}
            >
              <option value="DWG">DWG</option>
              <option value="DXF">DXF</option>
              <option value="PDF">PDF</option>
              <option value="SVG">SVG</option>
            </select>
          </div>

          <div className="meta-row">
            <span>Source URI</span>
            <strong>{sourceUri}</strong>
          </div>

          <div className="meta-row">
            <span>Loaded Drawing</span>
            <strong>{drawingId ?? "Not loaded"}</strong>
          </div>

          <button type="button" onClick={() => { void loadDrawing(); }} disabled={!session || !selectedFile || isLoadingDrawing}>
            {isLoadingDrawing ? "Loading Drawing..." : "Load 2D Drawing"}
          </button>

          <button type="button" onClick={runAutoBalloon} disabled={!drawingId || !session}>
            Auto Balloon
          </button>

          <div className="field-row">
            <label htmlFor="detector-mode">Detection Mode</label>
            <select
              id="detector-mode"
              value={detectorMode}
              onChange={(event) => setDetectorMode(event.target.value as "paddleocr_opencv" | "heuristic" | "florence2" | "hybrid")}
            >
              <option value="florence2">3 - Florence-2</option>
              <option value="paddleocr_opencv">2 - PaddleOCR + OpenCV</option>
              <option value="heuristic">1 - Heuristic</option>
              <option value="hybrid">4 - Hybrid</option>
            </select>
          </div>

          <div className="meta-row">
            <span>Detector Used</span>
            <strong>{lastDetectorUsed ?? "Not run yet"}</strong>
          </div>
          <div className="meta-row">
            <span>Attempted</span>
            <strong>{lastAttemptedDetectors.length > 0 ? lastAttemptedDetectors.join(", ") : "Not run yet"}</strong>
          </div>

          <button type="button" onClick={convertToSvg} disabled={!session || !drawingId || !sourceUri.startsWith("minio://")}>
            Refresh SVG Preview (Fallback)
          </button>

          <button type="button" onClick={() => void exportPdf()} disabled={!session || isExportingPdf}>
            {isExportingPdf ? "Exporting PDF..." : "Export as New PDF"}
          </button>

          <button
            type="button"
            className="secondary"
            onClick={() => {
              if (!pdfJob?.output_uri) {
                setError("No exported PDF is available yet. Run export first.");
                return;
              }
              void downloadGeneratedPdf(pdfJob.output_uri);
            }}
            disabled={!pdfJob?.output_uri}
          >
            Download Exported PDF
          </button>

          <button type="button" className="secondary" onClick={exportLayeredCanvasPdf} disabled={!session || !drawingId}>
            Export Layered Canvas PDF
          </button>

          <button
            type="button"
            className="secondary"
            onClick={() => setExportPreviewOnly((current) => !current)}
            disabled={viewerMode !== "annotation"}
          >
            {exportPreviewOnly ? "Exit Export Preview" : "Preview Export Content"}
          </button>

          {exportStatus ? <p className="timestamp">{exportStatus}</p> : null}
          {exportPreviewOnly ? <p className="timestamp">Preview shows only drawing + balloons that will be exported.</p> : null}

          <h2>Balloon Editing</h2>
          <p className="panel-hint">Default layout: small circle with transparent center and colored outline.</p>

          <div className="balloon-selector-list">
            {balloons.length === 0 ? (
              <p className="empty-state">No balloons available yet.</p>
            ) : (
              balloons.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`secondary ${selectedBalloonId === item.id ? "selected-balloon" : ""}`}
                  onClick={() => selectBalloon(item.id)}
                >
                  {item.label} ({item.id.slice(0, 8)})
                </button>
              ))
            )}
          </div>

          <div className="field-row">
            <label htmlFor="balloon-label">Balloon Label</label>
            <input
              id="balloon-label"
              value={editLabel}
              onChange={(event) => setEditLabel(event.target.value)}
              placeholder="B-001"
            />
          </div>
          <div className="inline-fields">
            <div className="field-row">
              <label htmlFor="balloon-x">X</label>
              <input
                id="balloon-x"
                value={editX}
                onChange={(event) => setEditX(event.target.value)}
              />
            </div>
            <div className="field-row">
              <label htmlFor="balloon-y">Y</label>
              <input
                id="balloon-y"
                value={editY}
                onChange={(event) => setEditY(event.target.value)}
              />
            </div>
            <div className="field-row">
              <label htmlFor="balloon-size">Size</label>
              <input
                id="balloon-size"
                type="number"
                min={12}
                max={120}
                value={editSize}
                onChange={(event) => setEditSize(event.target.value)}
              />
            </div>
            <div className="field-row">
              <label htmlFor="balloon-fill-color">Fill</label>
              <input
                id="balloon-fill-color"
                type="color"
                value={editFillColor}
                onChange={(event) => setEditFillColor(event.target.value)}
                disabled={editNoFill}
              />
            </div>
            <label className="place-mode-toggle">
              <input
                type="checkbox"
                checked={editNoFill}
                onChange={(event) => setEditNoFill(event.target.checked)}
              />
              No Fill (Transparent Center)
            </label>
            <div className="field-row">
              <label htmlFor="balloon-outline-color">Outline</label>
              <input
                id="balloon-outline-color"
                type="color"
                value={editOutlineColor}
                onChange={(event) => setEditOutlineColor(event.target.value)}
              />
            </div>
            <div className="field-row">
              <label htmlFor="balloon-text-color">Text Color</label>
              <input
                id="balloon-text-color"
                type="color"
                value={editTextColor}
                onChange={(event) => setEditTextColor(event.target.value)}
              />
            </div>
            <div className="field-row">
              <label htmlFor="balloon-font-family">Font</label>
              <select
                id="balloon-font-family"
                value={editFontFamily}
                onChange={(event) => setEditFontFamily(event.target.value)}
              >
                <option value="Space Grotesk">Space Grotesk</option>
                <option value="IBM Plex Sans">IBM Plex Sans</option>
                <option value="Georgia">Georgia</option>
                <option value="Arial">Arial</option>
              </select>
            </div>
          </div>
          <label className="place-mode-toggle">
            <input
              type="checkbox"
              checked={placeModeEnabled}
              onChange={(event) => setPlaceModeEnabled(event.target.checked)}
            />
            Click canvas to place balloon directly
          </label>
          <div className="snap-row">
            <label className="place-mode-toggle">
              <input
                type="checkbox"
                checked={snapToGridEnabled}
                onChange={(event) => setSnapToGridEnabled(event.target.checked)}
              />
              Snap moves/placement to grid
            </label>
            <div className="field-row compact-field">
              <label htmlFor="grid-size">Grid</label>
              <input
                id="grid-size"
                type="number"
                min={2}
                max={80}
                value={gridSizeInput}
                onChange={(event) => setGridSizeInput(event.target.value)}
              />
            </div>
          </div>
          <button type="button" onClick={addBalloonFromEditor} disabled={!drawingId || !session}>
            Add Balloon
          </button>
          <button type="button" onClick={saveBalloonChanges} disabled={!selectedBalloon || !session}>
            Save Balloon Changes
          </button>
          <button type="button" className="secondary" onClick={() => void deleteSelectedBalloon()} disabled={!selectedBalloon || !session}>
            Delete Selected Balloon
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => void undoLastCanvasChange()}
            disabled={!lastCanvasAction || !session}
          >
            Undo Last Move/Place
          </button>
          <button type="button" onClick={runWorkflow} disabled={isRunning || !session}>
            {isRunning ? "Running Workflow..." : "Run Full Feature Flow"}
          </button>

          {lastRunAt ? <p className="timestamp">Last successful run: {lastRunAt}</p> : null}
          {error ? <p role="alert" className="error">Error: {error}</p> : null}
          <p className="timestamp">Balloon ID: {selectedBalloon?.id ?? "No balloon selected"}</p>
            </>
          ) : null}
        </article>

        <article className="panel right-pane viewer-stage-panel">
          <div className="button-row">
            <h2>CAD + Balloon Workspace</h2>
            <button type="button" className="secondary" onClick={() => setToolsPanelOpen((current) => !current)}>
              {toolsPanelOpen ? "Hide Tools" : "Show Tools"}
            </button>
            <select
              aria-label="viewer-mode"
              value={viewerMode}
              onChange={(event) => setViewerMode(event.target.value as "libracad" | "annotation")}
            >
              <option value="annotation">Synced Overlay Editor (Recommended)</option>
              <option value="libracad">LibreCAD-style Base Viewer (No Overlay)</option>
            </select>
          </div>

          <div className={`status-bar ${isLoadingDrawing || isConvertingPreview ? "busy" : "ready"}`}>
            <span>Status</span>
            <strong>{loadStatus}</strong>
          </div>

          {viewerMode === "libracad" ? (
            <>
              <p className="panel-hint">Use this mode for large drawing navigation and source inspection. Balloons are not rendered in this base-only mode.</p>
              {isConvertingPreview ? (
                <p className="timestamp" style={{ padding: "12px 0", fontStyle: "italic" }}>Preparing converted preview, this may take a few seconds...</p>
              ) : previewLoadError ? (
                <p role="alert" className="error">Preview error: {previewLoadError}</p>
              ) : null}
              <LibraCadViewer
                sourceUrl={viewerAssetUrl}
                sourceFormat={previewAssetFormat ?? sourceFormat}
                sourceLabel={drawingLayerLabel}
              />
            </>
          ) : (
            <>
              <p className="panel-hint">Drawing is layer 1 and balloons are overlaid in the same synced coordinate space.</p>
              <p className="timestamp">Tip: enable "Click canvas to place balloon directly" to add new balloons by clicking the viewer.</p>
              {isConvertingPreview ? (
                <p className="timestamp" style={{ padding: "12px 0", fontStyle: "italic" }}>Preparing converted preview, this may take a few seconds...</p>
              ) : previewLoadError ? (
                <p role="alert" className="error">Preview error: {previewLoadError}</p>
              ) : null}
              <AnnotationLayer
                featureCount={result?.geometryFeatures ?? 0}
                balloons={canvasBalloons}
                selectedBalloonId={selectedBalloonId}
                drawingLayerUrl={viewerAssetUrl}
                drawingLayerFormat={previewAssetFormat ?? sourceFormat}
                drawingLayerLabel={drawingLayerLabel}
                stageRef={stageRef}
                exportPreviewOnly={exportPreviewOnly}
                onSelectBalloon={(balloonId) => {
                  selectBalloon(balloonId);
                }}
                onMoveBalloon={(payload) => {
                  void moveBalloonOnCanvas(payload.id, payload.x, payload.y);
                }}
                onCanvasClick={(point) => {
                  void placeBalloonFromCanvas(point.x, point.y);
                }}
              />
            </>
          )}
        </article>
      </section>

      <section className="grid-shell info-panels" aria-label="information-panels">
        <article className="panel status-panel" aria-live="polite">
          <h2>Drawing Workbench</h2>
          <div className="service-grid">
            {serviceDetails.map((service, index) => {
              const hasValue = service.value !== null;
              const runningCurrent = isRunning && !result && index === 0;
              const isComplete = hasValue && !isRunning;

              return (
                <div
                  key={service.name}
                  className={`service-card ${isComplete ? "complete" : "idle"} ${runningCurrent ? "running" : ""}`}
                >
                  <p className="service-name">{service.name}</p>
                  <p className="service-value">{hasValue ? String(service.value) : "Pending"}</p>
                </div>
              );
            })}
          </div>

          <div className="workbench-details">
            <div className="meta-row">
              <span>Viewer Format</span>
              <strong>{activeFormat}</strong>
            </div>
            <div className="meta-row">
              <span>SVG Conversion</span>
              <strong>{svgJob?.output_uri ?? "Not converted"}</strong>
            </div>
            <div className="meta-row">
              <span>PDF Export</span>
              <strong>{pdfJob?.output_uri ?? "Not exported"}</strong>
            </div>
          </div>
        </article>

        <article className="panel visual-panel">
          <h2>Balloon Overlay</h2>
          <p className="panel-hint">Generated overlay metadata from AI and balloon services.</p>
          <BalloonOverlayLayer
            balloons={canvasBalloons}
            selectedBalloonId={selectedBalloonId}
            aiSuggestions={aiSuggestionCount}
          />
        </article>
      </section>

      <section className="panel" aria-label="service-workflow-result">
        <h2>Payload Snapshot</h2>
        <p className="panel-hint">Exact response object returned by the orchestrated service flow.</p>
        {result ? (
          <pre>{JSON.stringify(result, null, 2)}</pre>
        ) : (
          <p className="empty-state">Run the workflow to generate a real payload snapshot.</p>
        )}
      </section>
    </main>
  );
}
