const ACCOUNT_EPOCH_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

export const ACCOUNT_RISK_QUERY = `SELECT timestamp, var_95, cvar_95 FROM trade_logs
  WHERE action = 'RISK_SNAPSHOT'
    AND account_epoch = $1
    AND account_fingerprint IS NOT NULL
  ORDER BY timestamp DESC, id DESC
  LIMIT 1`;

/** An unlabelled or malformed epoch must never expose another account's risk. */
export function configuredRiskEpoch(raw: string | undefined): string | null {
  const epoch = raw?.trim();
  if (!epoch || epoch === "unlabelled" || !ACCOUNT_EPOCH_PATTERN.test(epoch)) {
    return null;
  }
  return epoch;
}

/** A different paper account must not inherit the previous account's Redis entry. */
export function riskCacheKey(epoch: string): string {
  const configured = configuredRiskEpoch(epoch);
  if (!configured) {
    throw new Error("A valid account epoch is required for the risk cache.");
  }
  return `risk:latest:account:${configured}`;
}
