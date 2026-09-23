import assert from "node:assert/strict";
import test from "node:test";
import { ACCOUNT_RISK_QUERY, configuredRiskEpoch, riskCacheKey } from "./risk-account.ts";

test("risk requires a configured, labelled paper-account epoch", () => {
  assert.equal(configuredRiskEpoch(" alpaca-paper-100k-v1 "), "alpaca-paper-100k-v1");
  for (const value of [undefined, "", "unlabelled", "OLD_ACCOUNT", "bad:epoch", "a".repeat(65)]) {
    assert.equal(configuredRiskEpoch(value), null);
  }
});

test("risk cache entries cannot be reused across account epochs", () => {
  assert.notEqual(riskCacheKey("alpaca-paper-100k-v1"), riskCacheKey("alpaca-paper-100k-v2"));
  assert.equal(riskCacheKey(" alpaca-paper-100k-v1 "), riskCacheKey("alpaca-paper-100k-v1"));
  assert.throws(() => riskCacheKey("unlabelled"));
});

test("risk lookup is restricted to labelled snapshots for one account epoch", () => {
  assert.match(ACCOUNT_RISK_QUERY, /action = 'RISK_SNAPSHOT'/);
  assert.match(ACCOUNT_RISK_QUERY, /account_epoch = \$1/);
  assert.match(ACCOUNT_RISK_QUERY, /account_fingerprint IS NOT NULL/);
  assert.match(ACCOUNT_RISK_QUERY, /ORDER BY timestamp DESC, id DESC/);
});
