/**
 * Pure geometry for the valuation-range rail: where an arbitrary set of
 * labeled points (Bear / Base / Bull / market price) sit on one shared
 * scale. Generalizes `valuation-spread.ts`'s two-point domain-padding
 * approach (always includes zero, pads by a fraction of the range, never
 * collapses to a zero-width domain) to any number of points, and adds
 * clustering so points that land very close together on the scale can be
 * rendered with a visual offset instead of fully overlapping.
 */

export interface ValuationRangePoint {
  key: string;
  label: string;
  /** `null` when this point has no value to plot (e.g. an invalid scenario, or no market price). */
  value: number | null;
}

export interface ValuationRangeMarker {
  key: string;
  label: string;
  value: number;
  /** 0–100 position on the rail. */
  pct: number;
  /** A signed offset multiplier for a cluster of near-identical markers: 0 for a
   * marker with no close neighbors, otherwise a value centered on 0 within its
   * cluster (e.g. a 4-marker cluster gets -1.5, -0.5, 0.5, 1.5) so the cluster
   * fans out symmetrically in both directions instead of drifting one way.
   * Multiply by a pixel step to get an actual render offset. */
  stackLevel: number;
}

export interface ValuationRangeResult {
  domainMin: number;
  domainMax: number;
  /** Only the points with a non-null value, in the same relative order as the input. */
  markers: ValuationRangeMarker[];
}

const PAD_FRACTION = 0.12;
// Markers within this many percentage points of each other are
// considered clustered and get a stacking offset so they stay visually
// distinguishable instead of fully overlapping.
const CLUSTER_THRESHOLD_PCT = 4;

/**
 * Compute rail geometry for a set of labeled points. Returns `null` only
 * when NONE of the points have a value to plot (the caller should render
 * its own "unavailable" state in that case).
 *
 * The domain always spans at least [0, ...every point's value] (plus
 * padding), so zero is always a visible reference and negative values
 * stay on-scale instead of being clamped away, and every marker's `pct`
 * is clamped to [0, 100] so it can never render outside the rail.
 */
export function computeValuationRange(points: ValuationRangePoint[]): ValuationRangeResult | null {
  const withValues = points.filter(
    (point): point is ValuationRangePoint & { value: number } => point.value !== null
  );
  if (withValues.length === 0) return null;

  const values = withValues.map((point) => point.value);
  const rawLow = Math.min(0, ...values);
  const rawHigh = Math.max(0, ...values);
  const range = rawHigh - rawLow;
  // When every point (and zero) coincides, fall back to a small fixed
  // pad so the domain isn't a zero-width point.
  const pad = range > 0 ? range * PAD_FRACTION : 1;
  const domainMin = rawLow - pad;
  const domainMax = rawHigh + pad;
  const span = domainMax - domainMin || 1;

  const toPct = (value: number) => Math.min(100, Math.max(0, ((value - domainMin) / span) * 100));

  const withPct = withValues.map((point) => ({ ...point, pct: toPct(point.value) }));

  // Group markers (sorted by position) into clusters: maximal runs where
  // each marker is within CLUSTER_THRESHOLD_PCT of the PREVIOUS one. This
  // still transitively chains near-ties (e.g. 0, 3, 6, 9 with a threshold
  // of 4 all group into one cluster, even though the first and last are
  // 9 apart), but each cluster is then fanned out symmetrically around
  // its own center rather than stacked monotonically in one direction —
  // so a cluster of near-identical markers never drifts only downward
  // into whatever sits below the rail (e.g. its legend).
  const sortedIndices = withPct.map((_, index) => index).sort((a, b) => withPct[a].pct - withPct[b].pct);
  const clusters: number[][] = [];
  for (let i = 0; i < sortedIndices.length; i++) {
    const index = sortedIndices[i];
    if (i > 0) {
      const previousIndex = sortedIndices[i - 1];
      if (withPct[index].pct - withPct[previousIndex].pct < CLUSTER_THRESHOLD_PCT) {
        clusters[clusters.length - 1].push(index);
        continue;
      }
    }
    clusters.push([index]);
  }

  const stackLevels = new Array<number>(withPct.length).fill(0);
  for (const cluster of clusters) {
    const n = cluster.length;
    if (n === 1) continue; // no close neighbors — no offset needed.
    cluster.forEach((index, i) => {
      stackLevels[index] = i - (n - 1) / 2;
    });
  }

  return {
    domainMin,
    domainMax,
    markers: withPct.map((point, index) => ({ ...point, stackLevel: stackLevels[index] })),
  };
}
