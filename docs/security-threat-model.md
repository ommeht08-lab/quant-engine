# Security Threat Model

Status: living document. Last updated alongside the security hardening pass on branch
`remediation/valuation-engine-production-quality`. This describes the system as coded on
that branch, not a claim about any specific deployment's actual configuration.

This project is a single-operator, paper-trading-only research/portfolio dashboard. It is
not a regulated financial product, does not hold customer funds, and makes no claim of
institutional-grade security. This document exists so that claim is never made implicitly
either — every trust boundary and residual risk below is written down rather than assumed.

## 1. Assets

In rough order of what an attacker would actually want:

1. **Alpaca paper-trading API credentials** (`APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`) —
   control of the paper account: read positions, submit/cancel orders, liquidate. No real
   money is at risk (paper-only, enforced at multiple layers — see §5), but an attacker
   could still corrupt the research record (fake trade history) or deny service.
2. **Postgres (`DATABASE_URL`) credentials** — read/write access to `trade_logs` /
   `backtest_curve`. Tampering here corrupts the record this project's research
   conclusions are built on.
3. **Upstash Redis credentials** (`UPSTASH_REDIS_REST_URL`/`TOKEN`) — a compromised cache
   is a low-value target on its own (see §5's cache serialization fix), but shared
   read/write access could be used to poison cached financial data feeding into
   valuations.
4. **`SESSION_SECRET`** — the HMAC key signing the dashboard's session cookie. Compromise
   lets an attacker forge a valid session and view portfolio data (read-only exposure; no
   session-authenticated route submits trades or spends money).
5. **`DASHBOARD_PASSWORD`** — the shared passphrase gating the dashboard UI.
6. **`VALUATION_API_TOKEN`** — the service-to-service bearer secret between the Next.js
   app and the Python FastAPI valuation backend.
7. **Research integrity** — the backtest/valuation code itself, and the historical
   record it produces. Not a credential, but an asset: a subtly wrong DCF/VaR/backtest
   calculation, or a look-ahead bug, is a more consequential failure mode for a research
   project's credibility than most of the credentials above.

## 2. Trust boundaries and external services

```
Browser ──(session cookie)──> Next.js app (Vercel)
                                  │
                                  ├──(server-side fetch, Alpaca creds)──> Alpaca Paper API
                                  ├──(pg connection string)──────────────> Postgres (Supabase)
                                  ├──(REST, Upstash token)────────────────> Upstash Redis
                                  └──(bearer token)───────────────────────> Python FastAPI backend
                                                                                │
                                                                                ├──(yfinance/curl_cffi)──> Yahoo Finance
                                                                                ├──(pg connection string)──> Postgres
                                                                                └──(REST, Upstash token)────> Upstash Redis

GitHub Actions (rebalance.yml) ──(Alpaca/Postgres/Upstash secrets)──> same external services,
                                  manual-only (workflow_dispatch), dry-run by default (§5.5)
```

Trust boundaries an attacker would have to cross, and what currently guards each:

| Boundary | Guard |
|---|---|
| Browser → Next.js app | Session cookie (HMAC-signed, `httpOnly`, `secure` in production, `sameSite: lax`), enforced by `src/proxy.ts` for every route except `/login`, independently re-checked inside each private API route and the ticker tear-sheet page (defense in depth — see §5.4). |
| Next.js app → Python FastAPI backend | `VALUATION_API_TOKEN` bearer token, constant-time compared, fails closed if unconfigured/placeholder/too-short (identical requirement enforced on both sides — §5.2). No CORS middleware — the API is never meant to be called by a browser. The backend's own origin (`VALUATION_API_URL`) is shape-validated before every request (`assertSafeValuationApiUrl`, §5.6) — HTTPS required in production, no embedded credentials/fragment/path/query, a bounded `AbortController` timeout. |
| Next.js app → Alpaca | Exact-hostname/HTTPS validation before any credentialed fetch (`assertSafeAlpacaBaseUrl`, §5.3), mirroring the equivalent Python-side check in `src/trading/alpaca_execution.py`. |
| GitHub Actions → Alpaca/Postgres/Upstash | Manual `workflow_dispatch` only (no automatic schedule), dry-run by default, real secrets scoped only to the run that explicitly selects `execute` (§5.5). |
| Redis cache contents → Python process | JSON-envelope codec with a fixed, validated schema — no executable deserialization of any kind (§5.1). |

## 3. Attacker scenarios and mitigations

| # | Scenario | Mitigation | Residual risk |
|---|---|---|---|
| 1 | Attacker gains write access to the shared Redis instance (e.g. a leaked Upstash token, or a compromised dependency) and plants a malicious cached value. | The cache codec (`src/utils/cache.py`) only accepts a strict JSON envelope with a known schema/type tag; anything else — including the OLD pickle format — is rejected and treated as a cache miss. No `pickle`, `eval`, or `exec` exists in the caching path. | A malicious-but-schema-valid float/DataFrame value could still poison a valuation calculation. Cache poisoning at the *data* level (as opposed to *code execution*) is not fully eliminated by this fix alone — mitigated by TTL-bounded staleness and, longer-term, by validating fetched-vs-cached financial data against sanity bounds (tracked as a Track A follow-up in the model roadmap). |
| 2 | Attacker probes `/api/evaluate/{ticker}` directly (bypassing the Next.js proxy), if the Python service is ever reachable from outside its private network. | Bearer-token auth (constant-time compare), fails closed if `VALUATION_API_TOKEN` is unset. No wildcard CORS. | If the service is deployed on a genuinely public interface, an attacker who obtains the token (e.g. via a misconfigured deployment leaking env vars) still has full read access to the valuation endpoint. The token should be rotated per deployment and never logged (verified: no log statement includes it). |
| 3 | Attacker supplies a malicious `APCA_API_BASE_URL` (env misconfiguration, or a compromised deployment platform env-var UI) to redirect Alpaca API credentials to an attacker-controlled host. | `assertSafeAlpacaBaseUrl` requires exact match on `https://paper-api.alpaca.markets` — rejects alternate hosts, lookalike suffixes/subdomains, embedded userinfo, non-default ports, and non-HTTPS schemes. Mirrors the equivalent, longer-standing Python-side check. | None known for this specific vector once the env var itself is trusted; the residual risk is entirely "is the deployment platform's env var store itself trustworthy," which is out of this application's control. |
| 4 | Attacker brute-forces `DASHBOARD_PASSWORD` via repeated `/login` submissions. | Upstash-Redis-backed fixed-window throttle (5 attempts / 15 minutes), incremented via a single atomic `EVAL` Lua script (`INCREMENT_WITH_TTL_LUA` in `src/lib/redis.ts` — INCR + conditional EXPIRE as ONE server-side atomic unit, never two separate round trips that could leave a key stuck without a TTL). Keyed by the client's IP, but the RAW IP is never stored or logged — it's normalized and HMAC-SHA256'd (`src/lib/client-identifier.ts`, keyed by a `SESSION_SECRET`-derived, domain-separated subkey) before ever becoming part of the Redis key. A successful login clears its own counter (`resetRateLimitCounter`) so a legitimate operator can't self-lock; a failed attempt never does. In PRODUCTION, an unavailable rate limiter (Redis unconfigured or erroring) fails CLOSED by default (`src/lib/rate-limit-policy.ts`) — login itself is refused with a generic error rather than silently proceeding unthrottled; `LOGIN_RATE_LIMIT_FAIL_OPEN=true` is an explicit, intentionally-named override for an operator who has decided availability outweighs throttling. Outside production, an unavailable rate limiter fails open (local dev doesn't require Upstash). | **One known gap, documented rather than hidden:** the client identifier is read from `x-forwarded-for`, which is only trustworthy because this app is deployed on Vercel (Vercel's edge overwrites this header rather than passing through a client-supplied value) — deploying elsewhere without re-verifying that assumption would let an attacker spoof their own throttle key (though not bypass the fail-closed-in-production policy itself, which doesn't depend on the identifier). Passwords are still compared in constant time regardless of throttle state. |
| 5 | Attacker crafts a malicious `javascript:`/`data:` URL that ends up in a Yahoo Finance headline's link field, hoping it renders as a clickable, script-executing link on the ticker tear-sheet page. | `safeHeadlineHref` (`src/lib/headline-links.ts`) only renders `http:`/`https:` links as an `<a href>`; anything else renders as inert plain text. | None known — this is a complete allow-list, not a denylist. |
| 6 | Attacker who can reach the ticker tear-sheet URL directly (e.g. a leaked link, or a bug/regression in `src/proxy.ts`'s route matcher) tries to view trade telemetry without a valid session. | The page independently calls `requireSession()` before querying Postgres/Redis — the same check used by every private API route — rather than relying solely on the proxy layer. | None known beyond the general risk of `SESSION_SECRET` compromise (asset #4 above). |
| 7 | Compromised or malicious GitHub Actions runner/dependency attempts to submit real (paper) orders on an unreviewed schedule. | The rebalance workflow is `workflow_dispatch`-only (no automatic `schedule` trigger while hardening is underway), dry-run by default, and order submission requires an explicit `execute` selection per run. DB/Redis secrets are scoped out of the job's environment unless `execute` is selected. Alpaca credentials are still needed even for a dry run (it reads real paper-account state) but `load_config()` unconditionally refuses to construct a client against any non-paper hostname. | Real money is never at risk (paper-only is enforced in application code, not just CI config), but a malicious workflow run with `execute` selected could still churn/liquidate the paper account. Standard GitHub Actions supply-chain risk (Action tag pinning rather than SHA pinning) remains open — see §6. |
| 8 | Dependency confusion / supply-chain compromise via an unpinned `requirements.txt`. | Not mitigated in this pass — see §6 (open item, requires a reviewed, hash-locked dependency pass, deliberately out of scope for this offline-capable batch). | Open. Treat as the top remaining risk in this document until addressed. |

## 4. Credential roles and least privilege

| Credential | Used by | Scope needed | Notes |
|---|---|---|---|
| `APCA_API_KEY_ID`/`SECRET_KEY` | `src/trading/alpaca_execution.py`, `frontend/src/app/api/positions/route.ts` | Paper trading only | Should be a **paper-only** Alpaca key pair — Alpaca's own account model already prevents a paper key from touching a live account, which is the primary real-money safety net beneath this project's own hostname checks. |
| `DATABASE_URL` | `src/utils/db.py`, `frontend/src/app/ticker/[symbol]/page.tsx` | Read/write `trade_logs`, `backtest_curve` only | Currently a single role for both read (dashboard) and write (execution engine) paths — role separation (a read-only role for the dashboard, write-only for the execution engine) is a recommended follow-up, tracked in §6. |
| `UPSTASH_REDIS_REST_URL`/`TOKEN` | `src/utils/cache.py`, `frontend/src/lib/redis.ts` | Cache + rate-limit-counter read/write only | Shared between the Python backend and Next.js app; a leak affects both. **Not uniformly optional**: genuinely optional for the yfinance/statement CACHE (every consumer there degrades to a safe passthrough if unset/unreachable), but NOT optional for the login rate limiter in PRODUCTION specifically — see scenario #4 and `LOGIN_RATE_LIMIT_FAIL_OPEN` below. |
| `SESSION_SECRET` | `frontend/src/lib/auth.ts` | Sign/verify + subkey derivation | Never transmitted; used server-side to compute/verify the session HMAC, and (via `deriveSubkey`, domain-separated by a fixed label) to key the login rate limiter's client-identifier HMAC (`src/lib/client-identifier.ts`) — a second, cryptographically independent use of the same underlying secret, never the raw secret reused directly for both purposes. Validated at first use (`src/lib/secret-validation.ts`): must be set, not the example placeholder, at least 32 characters. |
| `DASHBOARD_PASSWORD` | `frontend/src/lib/auth.ts` | Compare only | Compared in constant time; never logged. Validated at first use: must be set; not whitespace-only or whitespace-padded; not the example placeholder; not a single character repeated; at least 12 characters (raised from an initial 8). |
| `VALUATION_API_TOKEN` | `src/api/main.py`, `frontend/src/app/api/evaluate/[ticker]/route.ts` | Shared secret between exactly these two services | Never sent to, or readable by, the browser. Validated identically on BOTH sides at first use: must be set; not whitespace-only or whitespace-padded; not the example placeholder; not a single character repeated; at least 32 characters — a value either side would accept, the other side accepts too. |
| `LOGIN_RATE_LIMIT_FAIL_OPEN` | `frontend/src/lib/rate-limit-policy.ts` | N/A (a policy flag, not a credential) | Unset/anything other than the exact string `"true"` = fail closed in production (the default). Included here because it directly controls whether the login rate limiter's failure mode is safe or not — see scenario #4. |

**Credential rotation and role separation are explicitly deferred** to a follow-up pass
after this hardened code/configuration lands — see the handoff's non-negotiable rules and
§6 below. This document does not claim rotation has happened.

## 5. What this hardening pass changed

Cross-references into the actual code, not a duplicate of it. Items 1–5 were the initial
hardening pass; items 6–9 were a first corrective pass over that same work (atomicity,
production failure policy, IP handling, destination validation, secret validation, and a
cache-codec edge case identified on review); items 10–13 are a SECOND corrective pass, over
gaps an adversarial read-only probe found in items 6 and 9's own fixes (an `OverflowError`
leak in the cache codec, a loopback-hostname-canonicalization bypass in the destination
validator, insufficiently strict secret-format checks, and test-claim precision for the
Lua-based rate limiter).

1. **Cache deserialization** (`src/utils/cache.py`) — replaced `pickle.loads(base64.b64decode(...))`
   with a versioned, schema-validated JSON envelope. See `tests/utils/test_cache.py` for the
   full contract (round-trip fidelity, and rejection of legacy/malformed/oversized/malicious
   payloads).
2. **Valuation service auth** (`src/api/main.py`) — bearer-token-gated `/api/evaluate/{ticker}`,
   no CORS middleware, health check (`/`) left public. See `tests/api/test_service_auth.py`.
3. **Alpaca URL validation** (`frontend/src/lib/alpaca-url.ts`) — exact-match paper hostname,
   HTTPS-only, no userinfo/non-default-port. See `frontend/src/lib/alpaca-url.test.ts`.
4. **Dashboard defense in depth** — ticker tear-sheet page independently checks
   `requireSession()`; headline links restricted to `http:`/`https:`
   (`frontend/src/lib/headline-links.ts`, tested in `headline-links.test.ts`). Login
   throttling itself is covered in depth by item 6 below (superseding the original,
   non-atomic, always-fail-open version).
5. **Workflow safety** (`.github/workflows/rebalance.yml`) — manual-only (`workflow_dispatch`,
   no `schedule` trigger), dry-run by default, real-order execution requires an explicit
   `execute` input, DB/Redis secrets scoped out of dry runs. See `tests/test_ci_config.py`.
6. **Atomic, HMAC'd, fail-closed-in-production login rate limiting** (`frontend/src/lib/redis.ts`,
   `frontend/src/lib/client-identifier.ts`, `frontend/src/lib/rate-limit-policy.ts`,
   `frontend/src/app/login/actions.ts`) — see scenario #4 above for the full mechanism. In
   short: a single atomic `EVAL` Lua script for increment+TTL (never two separate calls),
   the client identifier HMAC'd before ever reaching Redis or a log line, a successful login
   clears its own counter, and production fails closed by default when the limiter is
   unavailable. See `frontend/src/lib/redis-rate-limit.test.ts`,
   `frontend/src/lib/client-identifier.test.ts`, `frontend/src/lib/rate-limit-policy.test.ts`.
7. **Valuation backend destination validation** (`frontend/src/lib/valuation-api-url.ts`) —
   `VALUATION_API_URL` is shape-validated (not host-allowlisted — see that module's docstring
   for why an exact allowlist isn't possible from this repository) before every request:
   HTTPS required in production, no embedded credentials/fragment/path/query, loopback HTTP
   permitted only outside production. The actual outbound request is built from the
   validated origin via `new URL(path, origin)`, never string concatenation, and wrapped in a
   10-second `AbortController` timeout. See `frontend/src/lib/valuation-api-url.test.ts`.
   Its loopback-canonicalization gap (item 11 below) was found and fixed in the second pass.
8. **Authentication secret validation** (`frontend/src/lib/secret-validation.ts`,
   `src/api/main.py`) — `VALUATION_API_TOKEN`, `SESSION_SECRET`, and `DASHBOARD_PASSWORD` are
   all validated at first use: must be set, must not still be the literal `.env.example`
   placeholder value, and must meet a minimum length (32 characters for the two generated
   secrets, 12 for the passphrase — raised from an initial 8 in the second corrective pass,
   item 12 below). `VALUATION_API_TOKEN`'s requirement is enforced identically on both the
   Next.js and FastAPI sides. See `frontend/src/lib/secret-validation.test.ts` and
   `tests/api/test_service_auth.py`'s `TestServiceTokenFailsClosedWhenUnconfigured`.
9. **Cache codec edge cases** (`src/utils/cache.py`) — a Python `str` containing an unpaired
   Unicode surrogate could previously raise `UnicodeEncodeError` (escaping the codec's own
   `CacheDecodeError` boundary, and therefore the `cached` decorator's cache-miss fallback)
   instead of being rejected safely; now caught and converted. The envelope and every nested
   payload now enforce their EXACT key set (an unlisted extra field is rejected, not silently
   ignored) rather than only checking the keys the decoder happened to read. DataFrame
   round-tripping now also preserves `.index.name`/`.columns.name`, not just cell values —
   see the module docstring's precise (not "universally lossless") description of what's
   actually preserved. See `tests/utils/test_cache.py`'s
   `TestDecodeRejectsMalformedOrMaliciousPayloads` and index/column-name round-trip tests.
   A follow-on `OverflowError` leak in this same codec was found and fixed in the second pass
   (item 10 below).

### Second corrective pass (items 10–13)

10. **Cache decoder `OverflowError` leak, and DataFrame shape/decode precision**
    (`src/utils/cache.py`) — an adversarial read-only probe demonstrated that a schema-VALID
    JSON payload containing an astronomically large integer (e.g. 400 digits) made
    `decode_cache_value` raise a raw `OverflowError` — contradicting the "malformed cache
    values always become `CacheDecodeError`, therefore a safe cache miss" guarantee — for a
    top-level `float` payload, a `point_in_time_price.price`, and a DataFrame numeric cell
    targeting either a float or integer column dtype. Fixed with explicit,
    `CacheDecodeError`-raising bounds checks (`_finite_float_from_json_number`,
    originally `_assert_dataframe_cell_fits_dtype`) applied BEFORE any numpy/pandas conversion
    that could raise `OverflowError` itself, plus a blanket safety-net wrapper
    (`decode_cache_value`/`_decode_cache_value_inner`) converting ANY other unanticipated
    `Exception` (never `BaseException`-only subtypes like `KeyboardInterrupt`/`SystemExit`,
    which still propagate) into `CacheDecodeError` too. The same review also found the
    DataFrame codec's documented "string index" claim didn't match its behavior — a non-
    string, non-`DatetimeIndex` (e.g. a default integer `RangeIndex`) was silently
    `str()`-coerced rather than rejected, a non-`str` index/column `.name` could be encoded
    but was then rejected on decode, and duplicate column labels could corrupt per-column
    dtype assignment (label-based `df[column] = ...` assigns BOTH duplicate-labeled columns
    the same dtype). All three are now rejected explicitly at encode time (`_index_to_wire`,
    `_encode_dataframe`), duplicates are rejected as defense in depth at decode time too, and
    reconstruction now assigns dtypes by POSITION (`isetitem`) rather than by label. See
    `tests/utils/test_cache.py`'s `TestDecodeNeverLeaksOverflowOrOtherRawExceptions` (direct
    calls AND decorator-level `TestCachedDecoratorSafePassthrough` cases) and the duplicate-
    column/non-string-index/non-string-name tests in `TestEncodeRejectsUnsupportedValues`.
    **Follow-up correction:** a further adversarial pass found the decoder still accepted
    JSON scalar types INCOMPATIBLE with a cell's declared column dtype, relying on
    NumPy/pandas's own permissive coercion instead of the codec's own claimed exact-schema
    guarantee — e.g. `dtype="bool"` + JSON number `2` decoded (via NumPy's truthy cast) to
    `True`, `dtype="int64"` + JSON `true` decoded to `1`, and `dtype="float64"` + the JSON
    integer `9007199254740993` silently became the DIFFERENT value `9007199254740992.0`
    (loses precision — not representable exactly as float64). Fixed by replacing the former
    bounds-only check with `_assert_canonical_dataframe_cell`, which requires each cell to be
    the EXACT canonical JSON scalar shape `_encode_dataframe` itself would have written for
    that dtype (a JSON boolean only for `bool`; a JSON integer only, excluding `bool`, for
    `int32`/`int64`; a JSON float-literal token only, excluding `bool` and bare integers, or
    `null` for NaN, for `float32`/`float64`) — rejected as `CacheDecodeError` before
    `pd.DataFrame(...)`/`.astype()` ever run. See `tests/utils/test_cache.py`'s
    `TestDataFrameCellsRejectNonCanonicalTypes` (direct calls, including valid-boundary
    acceptance) and the new decorator-level cases in `TestCachedDecoratorSafePassthrough`.
11. **Loopback-hostname-canonicalization bypass** (`frontend/src/lib/valuation-api-url.ts`) —
    `assertSafeValuationApiUrl("https://localhost.", {isProduction:true})` previously
    succeeded: `URL#hostname` returns `"localhost."` (the DNS trailing root-label dot
    preserved verbatim) for that input, which didn't literal-match the loopback set and so
    was treated as some other, non-loopback production hostname. Fixed by canonicalizing
    (stripping trailing dot(s)) before the loopback comparison, and by explicitly recognizing
    the unspecified addresses (`0.0.0.0`, `[::]`) and the IPv4-mapped-IPv6 form of
    `127.0.0.1` as additional loopback-adjacent aliases rejected in production — separate from
    (narrower than) the small, deliberately curated `DEV_LOOPBACK_HOSTNAMES` set still
    allowed for the outside-production `http:` dev exception. Node's `URL` parser already
    canonicalizes numeric IPv4 forms (short/decimal/hex/octal) and IPv6 case/compression
    before this module ever reads `.hostname`, so those needed no additional handling.
    **Follow-up correction:** a further adversarial pass found this fix only checked the
    exact address `127.0.0.1`, not the ENTIRE 127.0.0.0/8 loopback range (RFC 5735) — e.g.
    `127.0.0.2` and `127.255.255.255` (and their IPv4-mapped-IPv6 forms) still passed as
    production hostnames. Fixed with a small IP-range helper
    (`isIPv4LoopbackRangeHostname`/`isIPv4MappedIPv6LoopbackRangeHostname`) rather than an
    ever-growing literal hostname set — every canonical address in the range is now rejected
    in production, in both plain-IPv4 and IPv4-mapped-IPv6 form; the narrow non-production
    `http:` dev allowance remains exactly `127.0.0.1` (not the whole range). This still covers
    ONLY the 127.0.0.0/8 and unspecified (`0.0.0.0`/`::`) address space — other private
    ranges (RFC 1918, link-local) are a distinct, not-yet-addressed class of finding. This
    remains explicitly a SHAPE validator, not a DNS-aware one: it performs no lookup, cannot
    detect DNS rebinding, and its loopback-alias list is not an exhaustive enumeration of
    every possible textual encoding — see the function's own docstring. See
    `frontend/src/lib/valuation-api-url.test.ts`'s loopback-canonicalization-bypass cases.
12. **Secret validation hardening** (`frontend/src/lib/secret-validation.ts`, `src/api/main.py`)
    — the validator previously accepted a whitespace-only string or a single character
    repeated to meet the length requirement (e.g. 32 `a`s) as a "valid" `VALUATION_API_TOKEN`/
    `SESSION_SECRET`, and accepted values with leading/trailing whitespace (a common copy-
    paste mistake) without complaint. Now rejects all of these explicitly, on BOTH the
    Next.js and FastAPI sides identically. `DASHBOARD_PASSWORD`'s minimum length was also
    raised from 8 to 12 characters. Reiterated explicitly (see item 8 above and this module's
    own top-of-file comment): these remain FORMAT/placeholder/length checks, never an
    entropy or strength measurement — a value that passes could still be weak in ways these
    checks don't catch (a dictionary word, a keyboard-walk pattern). See
    `frontend/src/lib/secret-validation.test.ts` and `tests/api/test_service_auth.py`'s new
    whitespace/repeated-character cases in `TestServiceTokenFailsClosedWhenUnconfigured`.
13. **Rate-limiter test-claim precision** (`frontend/src/lib/redis-rate-limit.test.ts`) — the
    existing tests exercise a faithful in-memory MODEL of `INCREMENT_WITH_TTL_LUA`'s intended
    semantics (via `FakeRedisStore`), proving this codebase's own calling code sends exactly
    one `eval` call with the correct key/window argument — they do NOT execute the actual Lua
    script through a real Redis/Upstash Lua engine, and never claim to. A new test asserts the
    exact script text, key, and fixed-window argument passed to `.eval(...)`, making that
    scope explicit rather than implicit. A real local-Redis integration test (actually running
    `INCREMENT_WITH_TTL_LUA` through `redis-server`'s own `EVAL`) remains optional future work,
    not something this offline pass could add (no real Redis contact permitted).

## 6. Open items (explicitly not addressed in this pass)

These were identified but deliberately deferred — each requires either network access this
offline pass was not authorized to use, or an external decision only the repository owner
can make:

- **Dependency hash locking.** `requirements.txt` is unpinned. Needs a reviewed lock
  (e.g. `pip-compile --generate-hashes`) run with real network access to package
  registries, which this pass did not have authorization to use.
- **GitHub Action SHA pinning.** Workflows currently pin Actions by tag (`@v4`/`@v5`), not
  full commit SHA. Needs verified upstream SHAs, not fabricated ones.
- **Credential rotation and database role separation** (§4) — deferred until after this
  hardened configuration is actually deployed, per the project's own stated sequencing.
- **Disabling the pre-existing remote scheduled workflow.** `origin/main` still contains
  the old, always-scheduled version of `rebalance.yml`. This is an external GitHub action
  the repository owner must take manually; this pass does not touch GitHub.
- **Login-throttle IP-trust assumption** (scenario #4) — `x-forwarded-for` is safe to trust
  on Vercel, not verified for any other hosting target. Narrower than before this corrective
  pass: the production fail-closed default no longer depends on this assumption holding
  (an attacker who spoofs their own identifier still can't make the limiter itself go
  unavailable), only the PER-CLIENT accuracy of the throttle does.
- **Frontend automated-test coverage gap.** Two code paths still have no automated test —
  the Next.js `/api/evaluate/[ticker]` route's actual token-forwarding behavior end-to-end,
  and the ticker tear-sheet page's `requireSession()` redirect — both depend on Next.js
  runtime APIs (`next/headers`, `next/navigation`) not easily exercised by Node's built-in
  test runner without a proper framework (Jest/Vitest) this pass was not authorized to
  install. Verified by code review and TypeScript checking only.
- **Cache-poisoning-at-the-data-level** (scenario #1's residual risk) — the codec fix
  eliminates *code execution* via the cache; validating cached financial data's plausibility
  is a separate, not-yet-built control.

## 7. Safe deployment mode

Until the items in §6 are resolved, this project should only be operated:

- With a **paper-only** Alpaca key pair (defense in depth beneath the application's own
  hostname enforcement).
- With the rebalance workflow's automatic schedule **disabled** on GitHub (manual
  `workflow_dispatch` only, as this branch's `rebalance.yml` already enforces in its own
  trigger configuration).
- With `VALUATION_API_TOKEN`, `SESSION_SECRET`, and `DASHBOARD_PASSWORD` all set to
  freshly generated, non-default values before any deployment. The code now actively
  validates this (§5.8) — it fails closed (rejects all requests / refuses to start a
  session) if any of these is missing, still the `.env.example` placeholder, or shorter
  than its required minimum length — but a deployment should still set these correctly
  the first time rather than relying on that validation as the only line of defense.
- With `UPSTASH_REDIS_REST_URL`/`TOKEN` genuinely configured in production, and
  `LOGIN_RATE_LIMIT_FAIL_OPEN` left unset. Without Upstash configured, PRODUCTION login is
  entirely blocked (fails closed by design — see scenario #4) rather than silently
  unprotected; setting `LOGIN_RATE_LIMIT_FAIL_OPEN=true` trades that away for availability
  and should be a deliberate, temporary operator decision, not a default configuration.
- Without any claim of profitability, institutional quality, regulatory compliance, or
  investment suitability — this system is a research and learning project, not investment
  advice, and must never be presented as more than that (see
  `docs/model-development-roadmap.md`).

## 8. Incident history

Recorded here so the same mistakes are recognizable if they start to recur:

1. Early test runs, before the isolation fixtures in `tests/conftest.py` existed,
   accidentally inserted 12 synthetic rows into the production `trade_logs` table (since
   verified deleted). Root cause: `_safe_log_trade` swallows its own failures, and nothing
   stopped `psycopg2.connect()` from reaching the real `DATABASE_URL`. Fixed by
   `tests/conftest.py`'s credential-poisoning + `psycopg2.connect` blocking fixtures.
2. During isolation-layer development, one real Yahoo Finance network request for AAPL
   escaped Python's `socket` module blocking because `yfinance` uses `curl_cffi` (a
   compiled libcurl backend that bypasses `socket.socket`). Fixed by additionally blocking
   `yfinance.Ticker.__init__`/`yfinance.download` and `curl_cffi.requests.Session`
   directly, at their own API surfaces rather than only at the transport layer.
