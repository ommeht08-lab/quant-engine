# Data Dictionary

Status: living document. Every field this system currently consumes, its source,
and its known quirks — centralized here rather than re-derived from reading each
call site, per [`docs/model-development-roadmap.md`](model-development-roadmap.md)'s
Track A scope.

**Current production-facing data sources are yfinance (financial statements,
prices, shares, beta, sector, news, macro indices) and Alpaca (paper trading
account/positions/orders/option chain).** An offline SEC EDGAR Company Facts
path now downloads, normalizes, exact-calendar classifies, selects, reconciles,
and converts a bounded point-in-time dataset into a typed valuation-fundamentals
snapshot. An offline composition module can combine that snapshot with typed
market-only observations and compare its DCF result against the legacy path, but
it is not connected to the live valuation or API paths. Controlled Apple
validations were run without publishing or retaining downloaded payloads.

## 1. Financial statement fields (yfinance)

All statement fields are fetched via `src.data_ingestion.fetch_financials`
(`get_income_statement`, `get_balance_sheet`, `get_cash_flow_statement`,
[`fetch_financials.py:43-109`](../src/data_ingestion/fetch_financials.py)), each
wrapped in `src.utils.cache.cached` with a 24-hour TTL
(`STATEMENT_CACHE_TTL_SECONDS`) — annual statements change rarely, so this is a
low-risk staleness window. Row lookups try multiple candidate labels via
`_get_row_value` ([`dcf.py:91`](../src/dcf_model/dcf.py)) because yfinance's exact
label text varies across tickers/versions (e.g. `"Total Revenue"` vs.
`"TotalRevenue"`).

| Canonical field | Raw yfinance row label(s) tried | Type | Unit | Frequency | Downstream consumers |
|---|---|---|---|---|---|
| Total Revenue | `Total Revenue`, `TotalRevenue` | float | USD | Annual | DCF base revenue, FCF projection, ROIC, Altman X5, Piotroski asset turnover/gross margin |
| Pretax Income | `Pretax Income`, `PretaxIncome` | float | USD | Annual | Tax rate derivation |
| Tax Provision | `Tax Provision`, `TaxProvision`, `Income Tax Expense` | float | USD | Annual | Tax rate derivation |
| Interest Expense | `Interest Expense`, `InterestExpense`, `Interest Expense Non Operating` | float | USD | Annual | Cost of debt derivation |
| EBIT / Operating Income | `EBIT`, `Ebit`, `Operating Income`, `OperatingIncome` | float | USD | Annual | Operating margin, ROIC NOPAT, Altman X3 |
| Net Income | `Net Income`, `NetIncome`, `Net Income Common Stockholders` | float | USD | Annual | Piotroski ROA, CFO-vs-NI check |
| Gross Profit | `Gross Profit` (or derived: `Revenue - Cost Of Revenue`) | float | USD | Annual | Piotroski gross margin |
| Cost Of Revenue | `Cost Of Revenue`, `Reconciled Cost Of Revenue` | float | USD | Annual | Gross Profit fallback derivation |
| Total Debt | `Total Debt`, `TotalDebt` | float | USD | Annual (balance sheet) | WACC weights, Equity Value bridge, ROIC invested capital |
| Cash & Equivalents | `Cash And Cash Equivalents`, `CashAndCashEquivalents`, `Cash Cash Equivalents And Short Term Investments` | float | USD | Annual | Equity Value bridge, market EV, ROIC invested capital |
| Current Assets | `Current Assets` | float | USD | Annual | Altman X1, Piotroski current ratio |
| Current Liabilities | `Current Liabilities` | float | USD | Annual | Altman X1, Piotroski current ratio |
| Total Assets | `Total Assets` | float | USD | Annual | Altman X1/X3/X5, Piotroski ROA/leverage/turnover |
| Retained Earnings | `Retained Earnings` | float | USD | Annual | Altman X2 |
| Total Liabilities | `Total Liabilities Net Minority Interest`, `Total Liab` | float | USD | Annual | Altman X4 |
| Stockholders' Equity | `Stockholders Equity`, `Common Stock Equity`, `Total Stockholder Equity`, `Total Equity Gross Minority Interest` | float | USD | Annual | ROIC invested capital |
| Long-Term Debt | `Long Term Debt`, `LongTermDebt` | float | USD | Annual | Piotroski leverage (missing → treated as 0 debt) |
| Shares Issued | `Share Issued`, `Ordinary Shares Number`, `Common Stock Shares Outstanding` | float | shares | Annual | Piotroski dilution check |
| Operating Cash Flow | `Operating Cash Flow`, `Cash Flow From Continuing Operating Activities`, `Total Cash From Operating Activities` | float | USD | Annual | FCF derivation, Piotroski CFO checks, FCF Yield |
| Capital Expenditure | `Capital Expenditure`, `CapitalExpenditure`, `Purchase Of PPE` | float | USD (reported **negative**, an outflow) | Annual | FCF derivation, FCF Yield |
| Free Cash Flow (direct) | `Free Cash Flow`, `FreeCashFlow` | float | USD | Annual | Preferred over the OCF−CapEx derivation when present |

**Provider quirk**: CapEx is reported as a *negative* number by yfinance (an
outflow); every consumer that combines it with OCF adds rather than subtracts it
(`operating_cf + capital_expenditures`), documented inline at each call site.

**Point-in-time availability**: annual statements only reflect a **trailing ~4-5
fiscal years from today** — yfinance mirrors what Yahoo Finance currently shows,
not a true historical archive. A backtest target date old enough that the required
fiscal period has rolled out of that window causes the ticker to be skipped (see
[`docs/model-specifications/backtesting.md`](model-specifications/backtesting.md)).
**Filing lag**: no true per-filing availability date is exposed by yfinance; a
conservative fixed 90-day lag after fiscal period-end is applied by the backtester
(`STATEMENT_FILING_LAG_DAYS`) as an approximation of real SEC filing deadlines.

**Missing/NaN handling**: `_get_row_value` returns `None` for a missing row, an
`NaN` cell, or a label not found under any candidate name — never raises. Every
consumer treats `None` explicitly (documented per-model in
[`docs/model-specifications/`](model-specifications/)) rather than silently
coercing it to 0.

**Suitability for historical research (Track B)**: **not yet suitable** as-is.
yfinance's trailing-window-only annual statements, lack of true filing dates, and
lack of point-in-time restatement history make it a workable source for *live*
valuation and for the backtester's conservative approximations, but not for
rigorous point-in-time historical research — this is exactly the gap Track B item 1
(point-in-time SEC/XBRL fundamentals) is scoped to close.

## 2. SEC Company Facts and valuation-fundamentals snapshot (offline)

The SEC boundary uses Company Facts plus current and referenced historical
Submissions documents. It requires an identifying SEC user agent, bounds retries,
payload size, accepted content types, and request pacing, and refuses partial issuer
downloads. Filing acceptance time is the knowledge cutoff; future filings are
excluded before consistency checks.

The exact-calendar catalog covers Apple fiscal 2020 through fiscal 2024 and
fiscal 2024 for Microsoft and Walmart. Apple's coverage uses 20 official SEC
10-Q/10-K filing documents and preserves its 53-week fiscal 2023 exactly. Facts
outside an issuer policy's coverage are never reclassified through SEC `fy`/`fp`
labels or duration guesses. The versioned concept policy covers conservative
statement aggregates, and standalone Q4 is derived only as an exact annual
residual. Standalone Q2 and Q3 cash flows are derived from exact six- and
nine-month YTD differences when issuers do not report those quarters directly.
A publishing seam can append one complete classified batch atomically and
idempotently, but no live database or production workflow is enabled here.

The controlled Apple run fetched Company Facts plus current and historical
Submissions, extracted and classified 136 cutoff-eligible facts, assembled
reported Q1 through Q3 revenue, derived Q4, and reconciled trailing-twelve-month
revenue to the reported full-year total of $391.035 billion. The contact address
used for SEC request identification is intentionally not stored in the repository.

A subsequent integration run assembled all nine required duration concepts and
linked Q2 through Q4 to exact balance-sheet snapshots. Historical free cash flow
was $20.694 billion, $26.707 billion, and $23.903 billion for those quarters.
Q1 remained intentionally unlinked in that original one-year run because its
beginning-cash snapshot belonged to the prior fiscal-year policy. These are
historical reconciliations, not forecasts and not inputs to the live DCF yet.

The first production-integration seam is
[`valuation_snapshot.py`](../src/fundamentals/valuation_snapshot.py). It reads
bounded repository candidates, keeps the central point-in-time selector
authoritative, assembles exact quarterly values, aligns revenue, operating
income, operating cash flow, and capital expenditures into TTM periods, and
requires at least one like-for-like revenue comparison ending four fiscal
quarters apart. Missing or incompatible statement/balance inputs refuse the
whole snapshot; the module never fills a missing SEC field from yfinance.

A controlled Apple FY2020-FY2024 run on 2026-09-09 classified 1,317 eligible
facts, produced 80 standalone-quarter values and 68 concept-level TTM values,
and aligned 17 valuation TTM periods with 13 year-over-year comparisons. The
latest fiscal-2024 snapshot reported $391.035 billion TTM revenue, $108.807
billion free cash flow, and 2.022% comparable TTM revenue growth. The balance
position contained $29.943 billion cash and cash equivalents and $96.662 billion
of current plus noncurrent term debt. The latter is deliberately named
`reported_term_debt`: it does not claim to include every form of indebtedness.

Capital expenditures in this SEC domain use a positive-spend convention, so
historical free cash flow is `operating_cash_flow - capital_expenditures`. This is
deliberately different from the negative-outflow yfinance convention above.

## 3. Price, shares, beta, and sector (yfinance)

| Canonical field | Source | Type | Unit | Adjusted? | Downstream consumers |
|---|---|---|---|---|---|
| Current price | `Ticker.fast_info["last_price"]`, fallback `Ticker.info["currentPrice"]`/`["regularMarketPrice"]` | float | USD/share | n/a (live quote) | WACC market cap, DCF market EV, Altman X4, all live valuations |
| Historical daily close | `Ticker.history(...)["Close"]` | float | USD/share | **Split-adjusted through today** | VaR log returns, RSI, 200-SMA trend filter, point-in-time price lookups |
| Shares outstanding (current) | `Ticker.fast_info["shares"]`, fallback `Ticker.info["sharesOutstanding"]` | float | shares | n/a | WACC, DCF, Altman X4, current-basis fallback for historical shares |
| Shares outstanding (historical) | `Ticker.get_shares_full(start, end)` | float | shares | **Not** split-adjusted — scaled by `_cumulative_split_factor_since` to match split-adjusted price basis | Point-in-time DCF (backtester only) |
| Beta | `Ticker.info["beta"]` (levered equity beta) | float | unitless | n/a | WACC CAPM, inverse-beta position sizing |
| Sector (GICS) | `Ticker.info["sector"]` | string | n/a (category) | n/a — defaults to `"Unknown"` if unavailable | Sector-relative filter, Altman sector-exclusion list, sector-cap position sizing |
| Splits | `Ticker.splits` | Series (date → ratio) | ratio (e.g. `10.0` for 10:1) | n/a | Historical share count reconstruction |

When the SEC snapshot is eventually composed into the live DCF, yfinance is
limited to market observations that Company Facts does not supply on a live
basis: current price, current shares outstanding, levered beta, and sector.
Treasury yield remains a separate macro-market observation. Financial-statement
fields must come from the SEC snapshot as one source or the valuation must refuse;
there is no field-by-field yfinance fallback.

The offline SEC candidate records the risk-free rate as a separate market
observation with its own adapter identity. The current implementation obtains
the 10-Year Treasury proxy from Yahoo's `^TNX`; it is not treated as an issuer
financial-statement field.

**Point-in-time availability**: current price/close history is genuinely
point-in-time correct (a historical close on a given date is that date's actual
close). **Beta and sector are NOT** — yfinance exposes no historical beta or
sector-classification endpoint, so the *current* value is used as a best-effort
proxy for every backtest target date (recorded in each result's `approximations`
list — see [`docs/model-specifications/backtesting.md`](model-specifications/backtesting.md)).

**Missing/NaN handling**: yfinance occasionally returns a `NaN` Close for the most
recent bar (an unsettled/incomplete session) — every price-history consumer drops
`NaN` rows via `.dropna(subset=["Close"])` before using the series, rather than
trusting the final row blindly. `get_current_price`/`get_shares_outstanding`/
`get_beta` each degrade to `None` on any fetch failure rather than raising.

## 4. Macro indicators (yfinance)

| Canonical field | Source ticker | Type | Unit | Frequency | Downstream consumers |
|---|---|---|---|---|---|
| Risk-free rate | `^TNX` (10-Year Treasury Note yield) | float | decimal (raw quote ÷ 100; `^TNX` is quoted in yield points, e.g. `4.2` for 4.2%) | Daily close | WACC CAPM cost-of-equity leg (all live/point-in-time valuations) |
| VIX | `^VIX` (CBOE Volatility Index) | float | index level (e.g. `15.3`) | Daily close | Qualitative macro context only (`src.valuation.macro_sentiment`) — not a scored model input |

**Caching**: `get_risk_free_rate` is `@cached` with a 1-hour TTL
(`RISK_FREE_RATE_CACHE_TTL_SECONDS`), keyed separately per `as_of_date` so a
historical date's answer and "today"'s answer never collide.
**Missing-data fallback**: `DEFAULT_RISK_FREE_RATE_FALLBACK = 4.2%` if the fetch
fails or returns no usable close on/before the requested date — distinct from
`src/dcf_model/dcf.py`'s own `DEFAULT_RISK_FREE_RATE = 4%` constant (used only when
a caller does not override `calculate_wacc`'s parameter at all); see `L-011` in the
limitations register for why these two independently-configured constants being
close but not identical is a tracked inconsistency, not a bug.

## 5. News headlines (yfinance)

| Canonical field | Source | Type | Notes |
|---|---|---|---|
| Recent headlines | `Ticker.news` | list of `{title, publisher, link, published_at}` | Up to `RECENT_HEADLINE_COUNT = 5`, most recent first. Supplementary qualitative context (`src.valuation.macro_sentiment`) — **not** a scored input to the DCF or Conviction Score pipeline. Degrades to an empty list on any failure. |

## 6. Trading/brokerage fields (Alpaca)

| Canonical field | Source | Type | Notes |
|---|---|---|---|
| Account equity, buying power | `TradingClient.get_account()` | float, USD | Refetched at multiple points per run — see position-sizing docs for why staleness matters here |
| Open positions | `TradingClient.get_all_positions()` | list of Position objects | Keyed by symbol; `asset_class` distinguishes equity vs. option (hedge) positions |
| Open orders | `TradingClient.get_orders(...)` | list of Order objects | Used for duplicate-submission protection |
| Market clock | `TradingClient.get_clock()` | bool (`is_open`) | Rechecked immediately before every individual order submission |
| Option contracts | `TradingClient.get_option_contracts(...)` | list of OptionContract objects | SPY put chain lookup for VaR hedge sizing |

**Environment**: Alpaca is used exclusively via its **paper trading** endpoint —
see [`docs/security-threat-model.md`](security-threat-model.md) and
`load_config`'s fail-closed paper-host check in
[`src/trading/alpaca_execution.py:310`](../src/trading/alpaca_execution.py). This
is telemetry/execution data, not research/backtesting input.

## 7. Database fields (Supabase/Postgres, written by this project)

`trade_logs` and `backtest_curve`, written by `src/utils/db.py`
([`db.py:39-93`](../src/utils/db.py)) — these are this project's own **output**
telemetry, not an ingested data source, included here for completeness since the
dashboard reads them back. Schema: ticker, action, quantity, execution_price, wacc,
beta, conviction_score, altman_z_score, var_95/cvar_95 (portfolio-level, populated
only on the synthetic `"RISK_SNAPSHOT"` row) for `trade_logs`; date,
strategy_value, spy_value for `backtest_curve`.

## 8. Planned or incomplete data sources

| Planned field | Planned source | Status |
|---|---|---|
| Point-in-time financial statements | SEC EDGAR / XBRL | **Foundation, Apple multi-year snapshot, SEC-to-DCF composition, and offline shadow comparison implemented.** Scheduled publishing, broader issuers, reviewed comparison tolerances, and live cutover remain. |
| Survivorship-corrected historical universe | TBD (e.g. a maintained historical index-membership dataset) | **Not implemented.** Track B item 2. |
| Corporate-actions feed (splits/spin-offs/ticker changes) beyond `Ticker.splits` | TBD | **Not implemented** beyond the existing split-only handling. Track B item 3. |
| Realistic transaction cost / slippage model | TBD | **Not implemented.** Track B item 6. |
