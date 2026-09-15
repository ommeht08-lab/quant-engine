/**
 * Browser-local history of explicit valuation runs from `/workspace`,
 * shown on the research home page (`/overview`) as "Recent valuations —
 * saved in this browser."
 *
 * This repository has no server-side persistence for valuation *runs*
 * (as opposed to `trade_logs`/`backtest_curve`, which belong to the
 * autonomous trader — see `docs/limitations-register.md`). Rather than
 * pretend server history exists, this module is an honestly-scoped
 * substitute: `localStorage` only, private to this browser, never
 * synced or sent anywhere. It stores a compact, non-sensitive snapshot
 * of each run — never credentials, full API responses, or financial
 * statements.
 *
 * The storage key is versioned (`:v1`). A future incompatible schema
 * change should use a new key rather than migrate this one in place —
 * old-shaped data simply stops being read (and is overwritten on the
 * next write), which is safer than trying to upgrade untrusted,
 * hand-editable `localStorage` content in place.
 */

const STORAGE_KEY = "valuation-engine:valuation-history:v1";

// Enough to feel like a real working history without the home page
// turning into a scroll of stale runs.
const MAX_ENTRIES = 12;

// A second call that reports the exact SAME outcome as the immediately
// preceding entry, within this window, is treated as one logical event —
// e.g. a rapid double click on "Run Valuation" before the button's
// `disabled` state takes effect, firing the handler (and this recording
// call) twice for what is, from the model's own numbers, identical
// output. It overwrites the most recent entry in place rather than
// appending a second row. Matching on ticker ALONE would be wrong here:
// a user legitimately re-running the same ticker moments later under a
// different assumption mode (or getting a different result after
// changing an assumption) is two real events, not one — see
// `isSameOutcome` below, which requires every OTHER field to match too,
// not just the ticker.
const DUPLICATE_WINDOW_MS = 1500;

export type AssumptionMode = "historical" | "custom";
export type ValuationQualityLevel = "ordinary" | "caution" | "diagnostic_only";

export interface ValuationHistoryEntry {
  ticker: string;
  /** `Date.now()` at the time this run was recorded. */
  timestamp: number;
  /** Base-case intrinsic value per share; `null` when the base case was not economically computable (see `is_valid` on the API's scenario result). */
  baseIntrinsicValuePerShare: number | null;
  /** Observed market price at the time of the run; `null` when unavailable. */
  marketPrice: number | null;
  /** `(intrinsic - price) / price`; `null` when withheld by valuation quality or unavailable. */
  marketGapPct: number | null;
  qualityLevel: ValuationQualityLevel;
  assumptionMode: AssumptionMode;
  terminalGrowthRate: number;
}

/** The fields the caller supplies; `timestamp` is assigned by `recordValuationRun` itself. */
export type ValuationRunSnapshot = Omit<ValuationHistoryEntry, "timestamp">;

function isFiniteNumberOrNull(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isValidTicker(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= 10;
}

function isValidQualityLevel(value: unknown): value is ValuationQualityLevel {
  return value === "ordinary" || value === "caution" || value === "diagnostic_only";
}

function isValidAssumptionMode(value: unknown): value is AssumptionMode {
  return value === "historical" || value === "custom";
}

/**
 * Structural + finiteness validation for one parsed history entry.
 * `localStorage` content can be hand-edited, left over from a previous
 * (possibly incompatible) build, or corrupted — this never trusts it.
 */
function isValidEntry(value: unknown): value is ValuationHistoryEntry {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    isValidTicker(candidate.ticker) &&
    typeof candidate.timestamp === "number" &&
    Number.isFinite(candidate.timestamp) &&
    candidate.timestamp > 0 &&
    isFiniteNumberOrNull(candidate.baseIntrinsicValuePerShare) &&
    isFiniteNumberOrNull(candidate.marketPrice) &&
    isFiniteNumberOrNull(candidate.marketGapPct) &&
    isValidQualityLevel(candidate.qualityLevel) &&
    isValidAssumptionMode(candidate.assumptionMode) &&
    typeof candidate.terminalGrowthRate === "number" &&
    Number.isFinite(candidate.terminalGrowthRate)
  );
}

// Guards on `localStorage` itself (not `typeof window`) so this module
// works unmodified in both a server-render pass (no `localStorage` global
// at all) and a plain Node test environment with a small in-memory
// `globalThis.localStorage` stand-in — see `valuation-history.test.ts`.
function hasLocalStorage(): boolean {
  return typeof localStorage !== "undefined";
}

function readStorage(): ValuationHistoryEntry[] {
  if (!hasLocalStorage()) return [];
  let raw: string | null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage blocked (private browsing, disabled site data) — treat as empty.
    return [];
  }
  if (!raw) return [];

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];

  return parsed.filter(isValidEntry).slice(0, MAX_ENTRIES);
}

function writeStorage(entries: ValuationHistoryEntry[]): void {
  if (!hasLocalStorage()) return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
  } catch {
    // Quota exceeded or storage otherwise unavailable — the valuation run
    // itself already succeeded; losing its local history record is not
    // worth surfacing an error over.
  }
}

/**
 * Every valid, saved valuation run, most recent first. Never throws —
 * any unreadable or malformed stored value degrades to an empty list
 * (or, for a mixed-validity array, silently drops only the invalid
 * entries) rather than breaking the home page.
 */
export function readValuationHistory(): ValuationHistoryEntry[] {
  return readStorage();
}

/**
 * Records one explicit, successful valuation run. Call this ONLY after a
 * `/workspace` run has produced a validated API response — never for an
 * automatic page load (e.g. `/overview/[ticker]`'s own fetch-on-mount).
 *
 * `now` is an injectable clock (defaults to `Date.now()`) purely so tests
 * can control duplicate-window timing deterministically; production
 * callers should never need to pass it.
 *
 * Returns the updated history (already capped and persisted) so a caller
 * can update its own state without a second read.
 */
/**
 * True when `snapshot` reports the exact same result as `entry` on every
 * field the model actually computed (everything but the timestamp) —
 * the narrow definition of "this is the same event fired twice", not
 * merely "this is the same ticker again".
 */
function isSameOutcome(snapshot: ValuationRunSnapshot, entry: ValuationHistoryEntry): boolean {
  return (
    snapshot.ticker === entry.ticker &&
    snapshot.assumptionMode === entry.assumptionMode &&
    snapshot.terminalGrowthRate === entry.terminalGrowthRate &&
    snapshot.baseIntrinsicValuePerShare === entry.baseIntrinsicValuePerShare &&
    snapshot.marketPrice === entry.marketPrice &&
    snapshot.marketGapPct === entry.marketGapPct &&
    snapshot.qualityLevel === entry.qualityLevel
  );
}

export function recordValuationRun(
  snapshot: ValuationRunSnapshot,
  now: number = Date.now()
): ValuationHistoryEntry[] {
  const existing = readStorage();
  const [mostRecent, ...rest] = existing;

  const isLikelyDuplicate =
    mostRecent !== undefined &&
    isSameOutcome(snapshot, mostRecent) &&
    now - mostRecent.timestamp >= 0 &&
    now - mostRecent.timestamp < DUPLICATE_WINDOW_MS;

  const updated = isLikelyDuplicate
    ? [{ ...snapshot, timestamp: mostRecent.timestamp }, ...rest]
    : [{ ...snapshot, timestamp: now }, ...existing];

  const capped = updated.slice(0, MAX_ENTRIES);
  writeStorage(capped);
  return capped;
}
