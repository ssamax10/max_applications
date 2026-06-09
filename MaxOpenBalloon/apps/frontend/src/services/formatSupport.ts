export type SupportedFormat = "DWG" | "DXF" | "PDF" | "SVG";

export const supportedFormats: SupportedFormat[] = ["DWG", "DXF", "PDF", "SVG"];

export function requiresIsolatedTranslation(format: SupportedFormat): boolean {
  return format === "DWG";
}
