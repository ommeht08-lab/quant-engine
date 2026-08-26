/**
 * Mirrors the backend's `SectorMedianUnavailableCode`
 * (`src/api/sector_median_thresholds.py`) — a small, stable vocabulary
 * this copy switches on instead of the free-text
 * `sector_median_unavailable_reason` diagnostic, which is internal-only
 * (precise staleness windows, coverage percentages, raw validation
 * text) and must never be shown to a user verbatim.
 */
export type SectorMedianUnavailableCode = "incompatible_assumptions" | "insufficient_peers" | "snapshot_unavailable";

/**
 * Truthful, code-driven copy for why a sector-relative comparison isn't
 * shown. Each branch describes what's ACTUALLY true rather than a single
 * one-size-fits-all message:
 *   - incompatible_assumptions: a snapshot exists, but only compares
 *     against its own baseline model assumptions, not this request's.
 *   - insufficient_peers: a snapshot exists but too few peer companies
 *     in this sector were valued to trust a median.
 *   - snapshot_unavailable (also the fallback for an unrecognized/null
 *     code): no usable snapshot at all right now.
 * Deliberately never promises a specific fix or timeline ("the next
 * refresh will fix this") — a fresh refresh can still leave any of
 * these conditions unresolved (e.g. the user's own custom assumptions
 * will never match a baseline snapshot), and never repeats the backend's
 * raw diagnostic text.
 */
export function sectorMedianUnavailableCopy(code: SectorMedianUnavailableCode | null | undefined): string {
  switch (code) {
    case "incompatible_assumptions":
      return "This comparison is only available under the snapshot's baseline model assumptions.";
    case "insufficient_peers":
      return "Not enough comparable companies have been valued in this sector yet.";
    case "snapshot_unavailable":
    default:
      return "Peer data is temporarily unavailable.";
  }
}

interface SectorMedianSnapshotProvenance {
  generated_at: string;
  universe_size: number;
  tickers_used: number;
  sector_sample_count: number;
}

/**
 * A concise, research-grade provenance caption for a successful
 * sector-relative comparison -- shows where the comparison denominator
 * actually came from (when it was generated, how much of the reference
 * universe it covered, how many peers backed this specific sector)
 * rather than presenting the sector median as an unexplained number.
 */
export function sectorMedianProvenanceCaption(snapshot: SectorMedianSnapshotProvenance): string {
  const generatedAt = new Date(snapshot.generated_at);
  const formattedDate = Number.isNaN(generatedAt.getTime())
    ? "unknown date"
    : generatedAt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

  return (
    `As of ${formattedDate} · ${snapshot.tickers_used}/${snapshot.universe_size} tickers valued · ` +
    `${snapshot.sector_sample_count} sampled in this sector`
  );
}
