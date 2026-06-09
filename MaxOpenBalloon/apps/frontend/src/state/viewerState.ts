import { SupportedFormat } from "../services/formatSupport";

export function useViewerState(): { activeFormat: SupportedFormat } {
  return { activeFormat: "SVG" };
}
