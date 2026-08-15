// Schemes safe to render as a clickable `<a href>` for an externally
// supplied headline link (from Yahoo Finance via
// `getTickerSentimentAndMacro`). Anything else (`javascript:`, `data:`,
// `vbscript:`, ...) must be rendered as inert plain text instead, never
// as a followable link.
const SAFE_HEADLINE_URL_SCHEMES = new Set(["http:", "https:"]);

/**
 * Returns `rawLink` unchanged if it's a valid, `http:`/`https:` URL —
 * the only shape safe to use as an `<a href>` — or `null` for anything
 * else (missing, malformed, or an unsafe scheme like `javascript:`),
 * signaling the caller to render plain inert text instead of a link.
 */
export function safeHeadlineHref(rawLink: string | null | undefined): string | null {
  if (!rawLink) return null;
  try {
    const parsed = new URL(rawLink);
    return SAFE_HEADLINE_URL_SCHEMES.has(parsed.protocol) ? rawLink : null;
  } catch {
    return null;
  }
}
