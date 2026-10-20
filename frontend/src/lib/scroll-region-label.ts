const MAX_DERIVED_LABEL = 80;

/** Accessible name for a scrollable table region: an explicit label, else the caption's first sentence, shortened. */
export function scrollRegionLabel(caption: string | null | undefined, explicit?: string): string {
  const trimmed = (explicit ?? "").trim();
  if (trimmed) return trimmed;
  const text = (caption ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "Scrollable table";
  const first = text.split(/(?<=[.:;])\s|\s[—–]\s/)[0];
  const short = first.length > MAX_DERIVED_LABEL ? `${first.slice(0, MAX_DERIVED_LABEL - 1).trimEnd()}…` : first;
  return short.replace(/[.:;]$/, "");
}
