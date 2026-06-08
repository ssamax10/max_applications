import { getStoredOidcSession, refreshOidcSession } from "./oidcAuth";

export type SessionContext = {
  tenantId: string;
  accessToken: string | null;
};

export type ServiceResult = {
  drawingId: string;
  balloonId: string;
  revisionNumber: number;
  geometryFeatures: number;
  aiSuggestions: number;
  dwgJobId: string;
  mcpTool: string;
};

export type DrawingRecord = {
  id: string;
  tenant_id: string;
  source_uri: string;
  source_format: string;
  created_at: string;
};

export type BalloonRecord = {
  id: string;
  tenant_id: string;
  drawing_id: string;
  label: string;
  geometry: Record<string, unknown>;
  created_at: string;
};

export type TranslationJob = {
  job_id: string;
  tenant_id: string;
  source_uri: string;
  target_format: "SVG" | "PDF";
  status: "queued" | "completed";
  output_uri: string;
  submitted_at: string;
};

function resolveUploadPayload(input: unknown): { blob: Blob; filename: string } {
  if (input instanceof File) {
    return { blob: input, filename: input.name || "drawing-upload.bin" };
  }

  if (input instanceof Blob) {
    return { blob: input, filename: "drawing-upload.bin" };
  }

  if (typeof input === "object" && input !== null) {
    const possibleFile = (input as { file?: unknown }).file;
    if (possibleFile instanceof File) {
      return { blob: possibleFile, filename: possibleFile.name || "drawing-upload.bin" };
    }
    if (possibleFile instanceof Blob) {
      return { blob: possibleFile, filename: "drawing-upload.bin" };
    }

    const possibleTarget = (input as { target?: { files?: FileList | null } }).target;
    const targetFile = possibleTarget?.files?.[0];
    if (targetFile instanceof File) {
      return { blob: targetFile, filename: targetFile.name || "drawing-upload.bin" };
    }
  }

  throw new Error("Selected drawing is not a valid file blob. Please re-select the file and try again.");
}

const servicePorts = {
  drawing: 18001,
  geometry: 18002,
  balloon: 18003,
  ai: 18004,
  mcp: 18005,
  revision: 18006,
  dwg: 18007,
};

function serviceBase(port: number): string {
  const protocol = window.location.protocol;
  const hostname = window.location.hostname || "localhost";
  return `${protocol}//${hostname}:${port}`;
}

async function postJson<T>(url: string, tenantId: string, body: unknown): Promise<T> {
  return requestJson<T>(url, { tenantId, accessToken: null }, "POST", body);
}

async function requestJson<T>(
  url: string,
  session: SessionContext,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const baseHeaders: Record<string, string> = {
    "X-Tenant-ID": session.tenantId,
  };

  if (body !== undefined) {
    baseHeaders["Content-Type"] = "application/json";
  }

  const preflightSession = await refreshOidcSession();
  const initialToken = session.accessToken ?? preflightSession?.accessToken ?? getStoredOidcSession()?.accessToken ?? null;
  const headersWithToken = initialToken
    ? { ...baseHeaders, Authorization: `Bearer ${initialToken}` }
    : { ...baseHeaders };

  let response = await fetch(url, {
    method,
    headers: headersWithToken,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    let detail = await response.text();
    const tokenRejected = /missing bearer token|invalid bearer token|unable to validate bearer token|token/i.test(detail);

    if (response.status === 401 && tokenRejected) {
      try {
        const refreshed = await refreshOidcSession(true);
        if (refreshed?.accessToken && refreshed.accessToken !== initialToken) {
          response = await fetch(url, {
            method,
            headers: { ...baseHeaders, Authorization: `Bearer ${refreshed.accessToken}` },
            body: body !== undefined ? JSON.stringify(body) : undefined,
          });

          if (response.ok) {
            return (await response.json()) as T;
          }

          detail = await response.text();
        }
      } catch {
        // fall through to error handling below
      }
    }

    // Dev-friendly fallback: some services reject malformed/stale bearer tokens
    // but still allow tenant-header access when AUTH_REQUIRED is false.
    if (response.status === 401 && /missing bearer token|invalid bearer token/i.test(detail)) {
      response = await fetch(url, {
        method,
        headers: baseHeaders,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });

      if (!response.ok) {
        detail = await response.text();
        throw new Error(`Request failed: ${response.status} ${response.statusText} ${detail}`.trim());
      }
    } else {
      throw new Error(`Request failed: ${response.status} ${response.statusText} ${detail}`.trim());
    }
  }

  return (await response.json()) as T;
}

export async function uploadDrawingFile(
  session: SessionContext,
  file: unknown,
): Promise<DrawingRecord> {
  const { blob, filename } = resolveUploadPayload(file);
  const formData = new FormData();
  try {
    formData.append("file", blob, filename);
  } catch (error) {
    const payloadTag = Object.prototype.toString.call(blob);
    const message = `Upload payload could not be appended as file blob (${payloadTag}). Please re-select the DWG and retry.`;
    throw new Error(message);
  }

  const baseHeaders: Record<string, string> = {
    "X-Tenant-ID": session.tenantId,
  };

  const preflightSession = await refreshOidcSession();
  const initialToken = session.accessToken ?? preflightSession?.accessToken ?? getStoredOidcSession()?.accessToken ?? null;
  const headers = initialToken
    ? { ...baseHeaders, Authorization: `Bearer ${initialToken}` }
    : { ...baseHeaders };

  const response = await fetch(`${serviceBase(servicePorts.drawing)}/drawings/upload`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!response.ok) {
    let detail = await response.text();
    const tokenRejected = response.status === 401
      && /missing bearer token|invalid bearer token|unable to validate bearer token|token/i.test(detail);

    if (tokenRejected) {
      try {
        const refreshed = await refreshOidcSession(true);
        if (refreshed?.accessToken && refreshed.accessToken !== initialToken) {
          const retry = await fetch(`${serviceBase(servicePorts.drawing)}/drawings/upload`, {
            method: "POST",
            headers: {
              ...baseHeaders,
              Authorization: `Bearer ${refreshed.accessToken}`,
            },
            body: formData,
          });

          if (retry.ok) {
            return (await retry.json()) as DrawingRecord;
          }

          detail = await retry.text();
          throw new Error(`Request failed: ${retry.status} ${retry.statusText} ${detail}`.trim());
        }
      } catch {
        // continue to legacy fallback below
      }
    }

    if (response.status === 401 && /missing bearer token|invalid bearer token/i.test(detail)) {
      const fallback = await fetch(`${serviceBase(servicePorts.drawing)}/drawings/upload`, {
        method: "POST",
        headers: baseHeaders,
        body: formData,
      });

      if (fallback.ok) {
        return (await fallback.json()) as DrawingRecord;
      }

      detail = await fallback.text();
      throw new Error(`Request failed: ${fallback.status} ${fallback.statusText} ${detail}`.trim());
    }

    throw new Error(`Request failed: ${response.status} ${response.statusText} ${detail}`.trim());
  }

  return (await response.json()) as DrawingRecord;
}

export async function createDrawing(
  session: SessionContext,
  sourceUri: string,
  sourceFormat: "DWG" | "DXF" | "PDF" | "SVG",
): Promise<DrawingRecord> {
  return requestJson<DrawingRecord>(
    `${serviceBase(servicePorts.drawing)}/drawings`,
    session,
    "POST",
    {
      source_uri: sourceUri,
      source_format: sourceFormat,
    },
  );
}

export async function autoBalloon(
  session: SessionContext,
  drawingId: string,
  maxSuggestions?: number,
  detectorMode?: "paddleocr_opencv" | "heuristic" | "florence2" | "hybrid" | "pdf_worker",
): Promise<{
  balloons: BalloonRecord[];
  suggestions: number;
  detectorUsed: string;
  attemptedDetectors: string[];
  detectorDiagnostics: Record<string, string>;
}> {
  const ai = await requestJson<{
    suggestions: Array<{ label: string; geometry: Record<string, unknown> }>;
    detector_used?: string;
    attempted_detectors?: string[];
    detector_diagnostics?: Record<string, string>;
  }>(
    `${serviceBase(servicePorts.ai)}/ai/suggest-balloons`,
    session,
    "POST",
    {
      drawing_id: drawingId,
      ...(typeof maxSuggestions === "number" ? { max_suggestions: maxSuggestions } : {}),
      ...(detectorMode ? { detector_mode: detectorMode } : {}),
    },
  );

  if (ai.suggestions.length === 0) {
    throw new Error("No AI suggestions generated");
  }

  const palette = ["#ff8f3f", "#ef476f", "#ffd166", "#06d6a0", "#118ab2"];
  const outlinePalette = ["#d7651f", "#bf2d55", "#d3aa2f", "#049169", "#0f6a8a"];
  const balloons: BalloonRecord[] = [];

  for (let index = 0; index < ai.suggestions.length; index += 1) {
    const suggestion = ai.suggestions[index];
    const geometry = {
      ...suggestion.geometry,
      size: typeof suggestion.geometry.size === "number" && suggestion.geometry.size > 0
        ? suggestion.geometry.size
        : 18 + (index % 2) * 2,
      fill_color: typeof suggestion.geometry.fill_color === "string" && suggestion.geometry.fill_color
        ? suggestion.geometry.fill_color
        : (
          typeof suggestion.geometry.color === "string" && suggestion.geometry.color
            ? suggestion.geometry.color
            : "transparent"
        ),
      outline_color: typeof suggestion.geometry.outline_color === "string" && suggestion.geometry.outline_color
        ? suggestion.geometry.outline_color
        : outlinePalette[index % outlinePalette.length],
      text_color: typeof suggestion.geometry.text_color === "string" && suggestion.geometry.text_color
        ? suggestion.geometry.text_color
        : "#fff4d8",
      font_family: typeof suggestion.geometry.font_family === "string" && suggestion.geometry.font_family
        ? suggestion.geometry.font_family
        : "Space Grotesk",
    };

    const created = await requestJson<BalloonRecord>(
      `${serviceBase(servicePorts.balloon)}/balloons`,
      session,
      "POST",
      {
        drawing_id: drawingId,
        label: suggestion.label || `AI-B-${String(index + 1).padStart(3, "0")}`,
        geometry,
      },
    );
    balloons.push(created);
  }

  return {
    balloons,
    suggestions: ai.suggestions.length,
    detectorUsed: ai.detector_used ?? "unknown",
    attemptedDetectors: ai.attempted_detectors ?? [],
    detectorDiagnostics: ai.detector_diagnostics ?? {},
  };
}

export async function createBalloon(
  session: SessionContext,
  drawingId: string,
  label: string,
  geometry: Record<string, unknown>,
): Promise<BalloonRecord> {
  return requestJson<BalloonRecord>(
    `${serviceBase(servicePorts.balloon)}/balloons`,
    session,
    "POST",
    {
      drawing_id: drawingId,
      label,
      geometry,
    },
  );
}

export async function listBalloons(session: SessionContext, drawingId: string): Promise<BalloonRecord[]> {
  return requestJson<BalloonRecord[]>(
    `${serviceBase(servicePorts.balloon)}/drawings/${drawingId}/balloons`,
    session,
    "GET",
  );
}

export async function updateBalloon(
  session: SessionContext,
  balloonId: string,
  patch: { label?: string; geometry?: Record<string, unknown> },
): Promise<BalloonRecord> {
  return requestJson<BalloonRecord>(
    `${serviceBase(servicePorts.balloon)}/balloons/${balloonId}`,
    session,
    "PATCH",
    patch,
  );
}

export async function deleteBalloon(session: SessionContext, balloonId: string): Promise<void> {
  const baseHeaders: Record<string, string> = {
    "X-Tenant-ID": session.tenantId,
  };

  const preflightSession = await refreshOidcSession();
  const initialToken = session.accessToken ?? preflightSession?.accessToken ?? getStoredOidcSession()?.accessToken ?? null;
  const headers = initialToken
    ? { ...baseHeaders, Authorization: `Bearer ${initialToken}` }
    : { ...baseHeaders };

  let response = await fetch(`${serviceBase(servicePorts.balloon)}/balloons/${balloonId}`, {
    method: "DELETE",
    headers,
  });

  if (!response.ok) {
    let detail = await response.text();
    const tokenRejected = response.status === 401
      && /missing bearer token|invalid bearer token|unable to validate bearer token|token/i.test(detail);

    if (tokenRejected) {
      try {
        const refreshed = await refreshOidcSession(true);
        if (refreshed?.accessToken && refreshed.accessToken !== initialToken) {
          response = await fetch(`${serviceBase(servicePorts.balloon)}/balloons/${balloonId}`, {
            method: "DELETE",
            headers: {
              ...baseHeaders,
              Authorization: `Bearer ${refreshed.accessToken}`,
            },
          });

          if (response.ok) {
            return;
          }

          detail = await response.text();
        }
      } catch {
        // fall through to legacy fallback below
      }
    }

    if (response.status === 401 && /missing bearer token|invalid bearer token/i.test(detail)) {
      response = await fetch(`${serviceBase(servicePorts.balloon)}/balloons/${balloonId}`, {
        method: "DELETE",
        headers: baseHeaders,
      });

      if (response.ok) {
        return;
      }

      detail = await response.text();
    }

    throw new Error(`Request failed: ${response.status} ${response.statusText} ${detail}`.trim());
  }
}

export async function convertDrawing(
  session: SessionContext,
  sourceUri: string,
  targetFormat: "SVG" | "PDF",
): Promise<TranslationJob> {
  return requestJson<TranslationJob>(
    `${serviceBase(servicePorts.dwg)}/translate/dwg`,
    session,
    "POST",
    {
      source_uri: sourceUri,
      target_format: targetFormat,
    },
  );
}

export async function runFeatureFlow(tenantId: string): Promise<ServiceResult> {
  const drawing = await postJson<{ id: string }>(
    `${serviceBase(servicePorts.drawing)}/drawings`,
    tenantId,
    {
      source_uri: "minio://drawings/ui-sample-1.dwg",
      source_format: "DWG",
    },
  );

  const balloon = await postJson<{ id: string }>(
    `${serviceBase(servicePorts.balloon)}/balloons`,
    tenantId,
    {
      drawing_id: drawing.id,
      label: "UI-B-100",
      geometry: { x: 24, y: 18 },
    },
  );

  const revision = await postJson<{ revision_number: number }>(
    `${serviceBase(servicePorts.revision)}/revisions`,
    tenantId,
    {
      drawing_id: drawing.id,
      change_summary: "UI workflow revision",
    },
  );

  const geometry = await postJson<{ features: unknown[] }>(
    `${serviceBase(servicePorts.geometry)}/geometry/extract`,
    tenantId,
    {
      drawing_id: drawing.id,
      entity_types: ["line", "dimension"],
    },
  );

  const ai = await postJson<{ suggestions: unknown[] }>(
    `${serviceBase(servicePorts.ai)}/ai/suggest-balloons`,
    tenantId,
    {
      drawing_id: drawing.id,
      max_suggestions: 3,
    },
  );

  const dwg = await postJson<{ job_id: string }>(
    `${serviceBase(servicePorts.dwg)}/translate/dwg`,
    tenantId,
    {
      source_uri: "minio://drawings/ui-sample-1.dwg",
      target_format: "SVG",
    },
  );

  const mcp = await postJson<{ tool: string }>(
    `${serviceBase(servicePorts.mcp)}/mcp/invoke`,
    tenantId,
    {
      tool: "balloon.create",
      arguments: { drawing_id: drawing.id, label: "MCP-B-901" },
    },
  );

  return {
    drawingId: drawing.id,
    balloonId: balloon.id,
    revisionNumber: revision.revision_number,
    geometryFeatures: geometry.features.length,
    aiSuggestions: ai.suggestions.length,
    dwgJobId: dwg.job_id,
    mcpTool: mcp.tool,
  };
}

export function parseJwtRoles(token: string): string[] {
  const parts = token.split(".");
  if (parts.length < 2) {
    return [];
  }

  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))) as {
      roles?: unknown;
      realm_access?: { roles?: unknown };
      resource_access?: Record<string, { roles?: unknown }>;
    };

    const collected = new Set<string>();

    if (Array.isArray(payload.roles)) {
      payload.roles.forEach((role) => {
        if (typeof role === "string") {
          collected.add(role);
        }
      });
    }

    const realmRoles = payload.realm_access?.roles;
    if (Array.isArray(realmRoles)) {
      realmRoles.forEach((role) => {
        if (typeof role === "string") {
          collected.add(role);
        }
      });
    }

    if (payload.resource_access && typeof payload.resource_access === "object") {
      Object.values(payload.resource_access).forEach((entry) => {
        if (Array.isArray(entry.roles)) {
          entry.roles.forEach((role) => {
            if (typeof role === "string") {
              collected.add(role);
            }
          });
        }
      });
    }

    return [...collected];
  } catch {
    return [];
  }
}
