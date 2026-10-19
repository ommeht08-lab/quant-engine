# Monte Carlo DCF (`mc-dcf-v1`)

Answers one question: **how much does uncertainty in revenue growth, operating
margin and the discount rate change estimated equity value per share?** It is a
sensitivity simulation under stated ranges. It is not a forecast, a price
target, a probability of mispricing, or a formal variance attribution.

Code: `src/dcf_model/monte_carlo*.py`, `src/api/monte_carlo_api.py`; UI at
`/workspace/uncertainty`. The deterministic DCF (`src/dcf_model/dcf.py`) is
unchanged.

## What is simulated

Four experiments, each targeting 10,000 **valid** valuations from the same seed:

| Experiment | Sampled | Held at the deterministic baseline |
|---|---|---|
| `growth` | growth | margin, WACC |
| `margin` | margin | growth, WACC |
| `wacc` | WACC | growth, margin |
| `combined` | all three, independently | none |

Terminal growth (2.5%), tax, D&A, capex, working capital, debt, cash and shares
are fixed. Each draw applies **one** growth/margin/WACC triple across the
forecast. The forecast is the workspace's `maturation` policy: the sampled
growth is the *starting* rate, held for 2 years, then faded toward
`min(g, 3%)`; margin is held constant.

## Baseline and kernel

`run_dcf_valuation` is called once to resolve every input. A vectorized NumPy
kernel (`monte_carlo_kernel.py`) then re-values draws using the same operation
order as the scalar functions (`build_path`, `project_free_cash_flows_from_path`,
`calculate_terminal_value`, `discount_to_present_value`,
`calculate_intrinsic_value_per_share`). A draw's single sampled WACC is the
terminal-value denominator, every year's discount factor and the terminal-value
discount. Sampled WACC replaces the CAPM-derived rate.

Every run first values the baseline with the kernel and compares it with the
scalar result (`kernel_baseline_check`); a relative disagreement above 1e-9
refuses the run. Tests assert **exact** float64 equality (no ULP tolerance) at
zero uncertainty and over 500 random in-bounds triples, in both forecast modes.

## Distributions

Three quantities are kept distinct and reported separately: the **historical
center** (uncapped), the **deterministic baseline** (growth capped at 25%), and
the **truncated distribution** actually sampled.

* **Growth**: normal(center = uncapped revenue CAGR, sd = sample sd of
  year-over-year growth), truncated to [-10%, 25%].
* **Margin**: normal(center = historical average EBIT margin, sd = sample sd of
  those margins), truncated to [0%, 60%].
* **WACC**: uniform on baseline ±1 point, intersected with [5%, 20%] and kept at
  least 1 point above terminal growth.

Truncation is by inverse CDF: no clipping, no rejected proposals
(`rejected_proposals` is always 0). The probability mass of the *untruncated*
normal outside the bounds is reported per input; it is a probability, not a
count of draws. A center at or above the growth ceiling is **not** refused: it
yields a distribution truncated below its center. An input is refused only when
its admissible mass is below `1e-6`, or its spread is zero and its center is
outside the bounds. A clamped baseline WACC is simulated within the intersected
interval and disclosed; only an empty or too-narrow (<1 bp) interval is refused.

## History rules

Yahoo annual statements only (SEC history is trailing-twelve-month and
overlapping). Growth uses the same revenue periods as the deterministic CAGR
(dropped NaN periods, date-sorted); consecutive periods must be 335-395 days
apart. Margins use exactly the columns the deterministic average uses. Spreads
use `ddof=1`; at least 3 growth and 3 margin observations are required.
Negative growth and negative margins are valid observations. Nothing is ever
substituted for a missing spread: the affected experiments are refused with a
typed code and the rest still run.

## Failures and retries

A draw whose valuation is not finite is a *failure* (counted by cause), distinct
from truncation. Failed draws are redrawn: at most 3 rounds and 500 extra
attempts. If the target is not reached the experiment is `incomplete` with its
true valid count; every denominator in the result and UI is that count.
Redrawing conditions the distribution on success (disclosed). Negative values
per share are valid.

## Output

Per experiment: valid / attempted / failed-by-cause counts, mean, P10 / P50 / P90
(`np.percentile`, linear), min/max, count **strictly above** the snapshot market
price with its denominator, realized input ranges, and a histogram on a shared
40-bin axis (from the combined run, else pooled across experiments, so edges
exist when combined is refused or incomplete). The UI states: "Simulated
valuations above market price under these assumptions".

## Reproducibility

Same snapshot + configuration + seed + supported software environment give an
identical **canonical numerical payload** (the result minus `meta`, which holds
cache state, timestamps, timings and environment). `result_digest` is the
SHA-256 of that payload. Streams come from `SeedSequence(seed).spawn(3)` (growth,
margin, WACC); every experiment restarts them, so growth-only and combined share
growth draws. Seeds are integers in [0, 2^53 - 1].

A **snapshot** (`mc-dcf-snapshot-v1`) freezes the three statements, quote fields,
risk-free rate, provenance, capture time and capture environment; its id is the
SHA-256 of its canonical content. Simulations always run from the decoded
snapshot. A **replay bundle** (`mc-dcf-replay-bundle-v1`) adds the seed, the full
configuration, its hash, the model version and the expected digest:

    python -m src.dcf_model.monte_carlo --bundle FILE [--output RESULT.json]

recomputes the result and exits non-zero if the digest differs.

## API

All routes require the service token (as `/api/evaluate`).

* `GET /api/monte-carlo`: allowlist (`MONTE_CARLO_TICKERS`, 1-5 tickers),
  configuration, disclosures.
* `GET /api/monte-carlo/{ticker}?seed=N`: the four experiments (200, including
  partial results with per-experiment refusals). Errors: 404 unsupported ticker,
  422 invalid seed/incomplete input, 409 retryable `*_in_progress`, 503 cache
  unavailable.
* `GET /api/monte-carlo/snapshots/{id}?seed=N`: replay bundle.

The Next.js proxies live under `frontend/src/app/api/monte-carlo/`, keep the
service token server-side, forward only a validated `seed`, and apply their own
rate limits (10 runs / 60 light requests per 15 minutes per client).

## Caching and concurrency

Redis keys: snapshot pointer per ticker (6 h), snapshot (30 d), result (30 d,
keyed by ticker, snapshot id, seed, configuration hash and model version). Both
the provider fetch (per ticker) and the simulation (per result key) are guarded
by `SET NX EX` locks with random ownership tokens and a compare-and-delete
release. Waiters re-check the cache, take over an expired lock, and otherwise get
a 409 with `Retry-After`. The provider is called once per snapshot, never per
draw. On Vercel (or with `MONTE_CARLO_REQUIRE_SHARED_STORE=1`) a missing shared
store is a 503; locally an in-process store is used.

## Known limitations

* The growth spread is year-to-year volatility but is applied as one persistent
  rate, which likely overstates uncertainty in a persistent rate.
* Spreads are noisy. Illustration only: if 3 growth observations were
  independent draws from one normal distribution, a 90% interval for the true
  spread would be about 0.58x to 4.4x the estimate (4 observations: 0.62x to
  2.9x). This is not a distribution-free confidence statement; real history is
  autocorrelated, skewed or shifting, and the true uncertainty can be larger.
* The margin uses the provider's EBIT row, else Operating Income, per period.
  The rows differ for some companies (e.g. JNJ, KO) and the margin center and
  spread depend on the row; see the offline margin-basis diagnostic.
* `baseline_provenance` (rows, periods, fallbacks, fixed assumptions, cash
  definition, WACC clamp vs sampled-interval restriction, dates) is derived
  from the deterministic model's resolution path. It and the disclosure text
  sit outside `result_digest`; provenance has its own `provenance_digest`.
  Digests of bundles exported before this change still verify (`legacy_v1`).
* Inputs are independent; correlations are ignored.
* The center is a geometric CAGR; the spread is an arithmetic-growth sd.
* The market price is the quote captured with the snapshot; the provider gives
  no quote time, so the price time is reported as unknown (capture time is never
  shown as a quote time).
* Exact reproduction is guaranteed only within the recorded software
  environment (`meta.environment`, `capture_environment`).

## Margin definition: v1 decision and findings

**Decision (2026-10-19):** the v1 default margin basis is Yahoo's `EBIT` row, falling back to its
`Operating Income` row only for a period with no EBIT value. It is labeled "Yahoo EBIT basis" in the
interface and findings. Neither row is presented as normalized or adjusted operating earnings. The
Operating Income comparison remains a separately labeled offline diagnostic, not a replacement.

**What the saved rows show (AAPL, KO, JNJ; annual, to 2025):**

* AAPL: EBIT equals Operating Income in every period, so the basis cannot matter.
* KO and JNJ: EBIT minus Operating Income equals `Other Income Expense` plus `Interest Income` (KO exactly,
  JNJ to within $9M). That bundle contains items of both signs and different natures (interest income and
  investment gains, but also equity-method earnings, restructuring, impairments and litigation accruals), so
  the gap is not simply non-operating income, and EBIT can sit above or below Operating Income.

**Sensitivity depends materially on the definition, for JNJ in particular.** On the EBIT basis JNJ's margin
spread is 7.8% and margin is the widest single input (P90−P10 about $213 per share against about $113 for
WACC and $20 for growth). On the Operating Income basis the spread is 1.4%, margin-only width falls about
82% to about $38, and the ordering becomes WACC, then margin, then growth. KO's margin-only width falls about
58% and margin drops from second to third; its baseline moves from $68.44 to $61.34. These orderings are
sensitivities under the chosen input ranges and each basis's own history, not variance attributions, and
they do not show which basis is more correct. Source: the offline margin-basis diagnostic (evidence
directory `margin-basis-diagnostic-20261019T160221Z`).

Any claim about which input dominates the valuation uncertainty must name the margin basis it used.
