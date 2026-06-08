import { useEffect, useMemo, useState } from "react";

type LibraCadViewerProps = {
  sourceUrl: string | null;
  sourceFormat: "DWG" | "DXF" | "PDF" | "SVG";
  sourceLabel: string;
};

export function LibraCadViewer({ sourceUrl, sourceFormat, sourceLabel }: LibraCadViewerProps) {
  const [zoomScale, setZoomScale] = useState(1);
  const [embedFailed, setEmbedFailed] = useState(false);

  const canRender = Boolean(sourceUrl);
  const viewerSrc = sourceUrl ?? "";

  const embedTitle = useMemo(() => {
    if (!canRender) {
      return "No drawing source loaded";
    }
    return `CAD base viewer (${sourceFormat})`;
  }, [canRender, sourceFormat]);

  const mediaType = useMemo(() => {
    if (sourceFormat === "SVG") {
      return "image/svg+xml";
    }
    if (sourceFormat === "PDF") {
      return "application/pdf";
    }
    return "image/svg+xml";
  }, [sourceFormat]);

  useEffect(() => {
    setEmbedFailed(false);
  }, [sourceUrl, sourceFormat]);

  function zoomIn() {
    setZoomScale((current) => Math.min(4, current * 1.15));
  }

  function zoomOut() {
    setZoomScale((current) => Math.max(0.35, current / 1.15));
  }

  function resetZoom() {
    setZoomScale(1);
  }

  return (
    <div className="cad-base-panel">
      <div className="cad-toolbar">
        <button type="button" onClick={zoomOut}>Zoom Out</button>
        <button type="button" onClick={zoomIn}>Zoom In</button>
        <button type="button" className="secondary" onClick={resetZoom}>Reset</button>
        <span className="timestamp">{Math.round(zoomScale * 100)}%</span>
        {sourceUrl ? (
          <a href={sourceUrl} target="_blank" rel="noreferrer" className="cad-link">
            Open Dynamic Source
          </a>
        ) : null}
      </div>

      {canRender ? (
        <div className="cad-frame-shell">
          <div
            className="cad-frame-scale"
            style={{ transform: `scale(${zoomScale})`, transformOrigin: "top left" }}
          >
            {!embedFailed ? (
              <object
                aria-label={embedTitle}
                data={viewerSrc}
                type={mediaType}
                className="cad-frame"
                onError={() => setEmbedFailed(true)}
              >
                <p className="empty-state">
                  Embedded preview is unavailable in this browser.
                  <a href={viewerSrc} target="_blank" rel="noreferrer" className="cad-link"> Open Dynamic Source</a>
                </p>
              </object>
            ) : (
              <div className="cad-fallback">
                <p className="empty-state">Embedded preview was blocked by browser policy.</p>
                <a href={viewerSrc} target="_blank" rel="noreferrer" className="cad-link">
                  Open Dynamic Source
                </a>
              </div>
            )}
          </div>
        </div>
      ) : (
        <p className="empty-state">
          {sourceLabel}
        </p>
      )}
    </div>
  );
}
