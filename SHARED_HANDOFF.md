# Monte Carlo DCF handoff

Scope: this file records the state of the Monte Carlo DCF work on branch `codex/monte-carlo-dcf` only. It is a trimmed
extract; the project-wide handoff (research, SEC pilot, other branches) lives in the primary checkout and is not part of this commit.

## Monte Carlo DCF, durable worktree and status (October 19, 2026)

**Durable worktree:** `/Users/om/Downloads/Valuation Engine/.claude/worktrees/monte-carlo-dcf`, a registered Git worktree
of the primary repository on branch `codex/monte-carlo-dcf` (base `origin/main` `bffc2e6`). It replaces the temporary
scratchpad location, which no longer exists. **Git state:** the implementation is a single local checkpoint commit ("Add reproducible Monte Carlo DCF analysis") on top of
`bffc2e6`; `git log -1` in the worktree shows it. It is **not pushed, not in a PR, not deployed**. `.claude/worktrees/` is excluded in the
primary's `.git/info/exclude`, so the primary checkout does not show this worktree. Untracked and ignored items (evidence archives,
tarballs, tooling environments, `node_modules`) are deliberately not in the commit.

**Preservation record (October 19, 2026):** the worktree was copied (not moved) from temporary storage, then re-registered with
`git worktree repair`. Before/after comparison: same branch and HEAD, identical `git status --short`, identical SHA-256 for all 45
modified/untracked files. The temporary copy was removed only after that check. Records:
`/Users/om/Documents/Valuation Engine Evidence/monte-carlo-dcf/worktree-backup/` holds `before-move.sha256`, `before-move.status`
and two tarballs of the uncommitted files (taken before the later edits listed below). `node_modules` and caches were not copied
(regenerated with `npm ci`).

**Why this note exists:** the original temporary worktree vanished with its uncommitted work. It was rebuilt from the session
transcript and verified against the archived evidence (all three archived replay bundles reproduce their digests; the golden digest
recovered from the transcript matches). Do not keep this work only in a temporary directory again.

**Decisions (user):** maturation forecast; vectorized NumPy kernel; 4 x 10,000 valid draws; allowlist AAPL/KO/JNJ (MSFT excluded); the
Yahoo `EBIT` row (fallback: `Operating Income` for a period with no EBIT) is the **v1 margin default**, labeled "Yahoo EBIT basis"
everywhere; Operating Income is a separately labeled offline diagnostic only; neither is presented as normalized operating earnings.
Findings: JNJ's single-input sensitivity ordering depends materially on the margin definition (see `docs/model-specifications/monte-carlo-dcf.md`).

**Changes since the live validation:**
* `baseline_provenance` (monte_carlo_provenance.py): rows, periods, fallbacks, fixed assumptions, cash definition, WACC clamp vs sampled
  interval, dates. Derived from the deterministic model's resolution path; `dcf.py` is unmodified. Quote time is reported as not provided
  (capture time is never shown as a quote time). The risk-free lookup path is recorded as **unknown** and is not inferred from the value.
* Digest scope: `result_digest` covers the numerical payload only. Provenance (own `provenance_digest`) and disclosure copy are outside it.
  `monte_carlo_digest_compat.py` keeps digests of bundles exported earlier verifying (`matched_digest_scheme: legacy_v1`).
* Replay bundle integrity: `bundle_digest` binds the snapshot id, the whole `replay` section (seed, configuration, config hash, model
  version, expected digests) and the provenance digest; the exported provenance block is checked against its own digest and the digest the
  replay section expects. Edits are refused with `SnapshotError`. It detects accidental or partial edits; it carries no secret and is
  **not** authentication. Bundles exported before this carry no `bundle_digest` and replay as `bundle_integrity_ok: null`.
* Result cache identity now includes `RESULT_FORMAT_VERSION` (2), so results cached by older code are never served as current.
* Export route (`frontend/src/app/api/monte-carlo/proxy.ts`): the replay bundle is forwarded byte-for-byte. A JavaScript parse and
  re-serialize rewrites `1.0` as `1`, which changes the hashed text and made a downloaded bundle fail its own snapshot check. Found by the
  integration recheck, fixed, covered by tests.
* Backend rejects malformed snapshot ids (404) before touching the store.

**Verification (local, October 19, 2026):** Python 2,414 tests (28 skipped, as in CI), frontend 411 tests, tsc, lint and build pass (see the final report for exact
counts). Integration recheck against real Redis (through a local Upstash-REST bridge), the real FastAPI app and the real Next proxy, with the
provider replaced by archived snapshot statements: `/Users/om/Documents/Valuation Engine Evidence/monte-carlo-dcf/integration-recheck-*/`
(55/55 after the fixes; `run1_before_fixes_46of55.json` is the earlier failing run, kept). It covers concurrent cold requests (one provider
fetch, one computation), cache hit/miss and seed identity, rate-limit quotas and TTLs, lock ownership (real Lua compare-and-delete) and crash
recovery, store-outage 503, export, offline replay with the network blocked, and tamper refusal.

**Remaining deployment checks (not done; each needs authorization):**
1. Linux x86 CI run (golden digest `01437171...` and exact scalar/kernel equality are the canaries; legacy digest `477d8ddf...` for the same fixture).
2. Preview-deployment benchmark against the 40 s limit (target p95 <= 20 s, cold and warm) with real Vercel and Upstash latency; Upstash request size
   for a real snapshot and result (results now include provenance, so they are larger); function bundle size with scipy.
3. Real Upstash behavior (the local bridge is test tooling, not Upstash): `SET NX EX`, `EVAL`, base64 encoding, pipelines.
4. Production-mode Next: fail-closed rate limiter with the real store, `assertSafeValuationApiUrl` with the real https origin, `MONTE_CARLO_REQUIRE_SHARED_STORE` default on Vercel.
5. Real-device and production-mode UI check.
6. Proposed, not implemented: record the quote time (`regularMarketTime`) and the risk-free lookup path in future snapshots; both need an ingestion/snapshot change.
