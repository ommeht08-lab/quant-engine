// The ONLY hostname any Alpaca-credentialed request in this app is
// allowed to be sent to. Paper trading only, matching every other
// paper-only enforcement point in this project (see
// src/trading/alpaca_execution.py's identical PAPER_TRADING_HOSTNAME
// check).
export const PAPER_ALPACA_HOSTNAME = "paper-api.alpaca.markets";

/**
 * Validate an Alpaca base URL (from `APCA_API_BASE_URL`) before it's
 * ever used in a fetch that carries Alpaca credentials. Rejects
 * anything that isn't EXACTLY `https://paper-api.alpaca.markets` — a
 * malformed URL, a non-HTTPS scheme, embedded userinfo
 * (`https://user:pass@paper-api.alpaca.markets` — a valid URL whose
 * credentials-bearing fetch could leak the userinfo to a party
 * controlling DNS/routing for it), a lookalike suffix
 * (`paper-api.alpaca.markets.evil.com`), a non-default port, or the
 * live-trading hostname. Returns a freshly reconstructed origin-only
 * URL — built from the validated parts, never the raw input — so any
 * path/query smuggled into the env var can't ride along into the
 * request.
 */
export function assertSafeAlpacaBaseUrl(rawBaseUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(rawBaseUrl);
  } catch {
    throw new Error("APCA_API_BASE_URL is not a valid URL.");
  }

  if (parsed.protocol !== "https:") {
    throw new Error("APCA_API_BASE_URL must use the https: scheme.");
  }
  if (parsed.username !== "" || parsed.password !== "") {
    throw new Error("APCA_API_BASE_URL must not contain userinfo.");
  }
  if (parsed.hostname !== PAPER_ALPACA_HOSTNAME) {
    throw new Error(
      `APCA_API_BASE_URL must be exactly https://${PAPER_ALPACA_HOSTNAME} (paper trading only).`
    );
  }
  if (parsed.port !== "") {
    throw new Error("APCA_API_BASE_URL must not specify a non-default port.");
  }

  return `https://${PAPER_ALPACA_HOSTNAME}`;
}
