/**
 * Validates a raw `?ticker=` query-string value (e.g. `/workspace?ticker=MSFT`,
 * built by the "Value again" link on the research home page) before it is
 * used to prefill the workspace's ticker field.
 *
 * Deliberately stricter than `overview-response.ts#normalizeOverviewTicker`
 * (trim + uppercase only): a query parameter is attacker-reachable simply by
 * editing the URL, and this value flows into a text input's initial value —
 * not into a DB query or an outbound fetch — so the concern here is a
 * confusing/garbage prefill (e.g. an overlong string, embedded whitespace,
 * or path-separator characters), not injection. Malformed input is treated
 * as ABSENT, never partially accepted.
 */

// Real ticker symbols are short and drawn from a small character set;
// `.`/`-` cover real-world share classes (e.g. "BRK.B") and a small number
// of hyphenated OTC symbols.
const VALID_TICKER_PATTERN = /^[A-Z0-9.-]{1,10}$/;

/**
 * Next.js's `searchParams` types a single key as `string | string[] | undefined`
 * (repeated query keys collapse to an array) — this function accepts that
 * whole shape so a caller can pass `searchParams.ticker` directly.
 */
export type RawTickerQueryParam = string | string[] | undefined | null;

/**
 * Returns a normalized, validated ticker from a raw query-parameter value,
 * or `null` if the parameter is absent, repeated, empty, too long, or
 * contains a character outside the ticker-shaped allowlist above — the
 * caller should fall back to its own normal default in every `null` case,
 * never render `null`/`undefined` into the input.
 */
export function parseTickerQueryParam(raw: RawTickerQueryParam): string | null {
  if (typeof raw !== "string") return null;
  const candidate = raw.trim().toUpperCase();
  return VALID_TICKER_PATTERN.test(candidate) ? candidate : null;
}
