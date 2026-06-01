import { AnnotationLayer } from "../layers/AnnotationLayer";
import { BalloonOverlayLayer } from "../layers/BalloonOverlayLayer";
import { useViewerState } from "../state/viewerState";

export function ViewerShell() {
  const { activeFormat } = useViewerState();

  return (
    <main>
      <h1>MaxOpenBalloon Viewer</h1>
      <p>Active format: {activeFormat}</p>
      <section aria-label="rendering-layers">
        <AnnotationLayer />
        <BalloonOverlayLayer />
      </section>
    </main>
  );
}
