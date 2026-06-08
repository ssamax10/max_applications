type BalloonOverlayLayerProps = {
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
  }>;
  selectedBalloonId: string | null;
  aiSuggestions: number;
};

export function BalloonOverlayLayer({ balloons, selectedBalloonId, aiSuggestions }: BalloonOverlayLayerProps) {
  const latestBalloonId = balloons.length > 0 ? balloons[balloons.length - 1].id : null;

  return (
    <div className="balloon-panel" data-layer="balloon-overlay">
      <div className="balloon-chip-row">
        <span className="chip">Overlay Active</span>
        <span className="chip">AI Suggestions: {aiSuggestions}</span>
        <span className="chip">Balloons: {balloons.length}</span>
      </div>
      <p className="balloon-label">Latest Balloon ID</p>
      <p className="balloon-value">{latestBalloonId ?? "No balloon generated yet"}</p>

      <div className="balloon-list" aria-label="Balloon inventory list">
        {balloons.length === 0 ? (
          <p className="balloon-empty">No balloons created yet.</p>
        ) : (
          balloons.map((balloon) => (
            <div
              key={balloon.id}
              className={`balloon-item ${balloon.id === selectedBalloonId ? "selected" : ""}`}
            >
              <p>
                <span
                  className="balloon-color-dot"
                  style={{ backgroundColor: balloon.fillColor, borderColor: balloon.outlineColor }}
                />
                {balloon.label}
              </p>
              <span>
                x:{balloon.x} y:{balloon.y} size:{balloon.size}
              </span>
            </div>
          ))
        )}
      </div>

      <div className="balloon-track">
        <span style={{ width: `${Math.min(100, aiSuggestions * 20)}%` }} />
      </div>
    </div>
  );
}
