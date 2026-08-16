# Model Specification: Risk Models (Monte Carlo VaR/CVaR and SPY Put Hedging)

Sources: [`src/risk/monte_carlo.py`](../../src/risk/monte_carlo.py),
[`src/risk/hedging.py`](../../src/risk/hedging.py)
Tests: [`tests/risk/test_monte_carlo.py`](../../tests/risk/test_monte_carlo.py)
(26 methods), [`tests/risk/test_hedging.py`](../../tests/risk/test_hedging.py)
(10 methods)
Consumers: [`src/trading/alpaca_execution.py`](../../src/trading/alpaca_execution.py)
(end-of-run portfolio risk snapshot and SPY hedge sizing)

## 1. Monte Carlo Value at Risk (VaR) / Conditional VaR (CVaR)

### Objective

Estimate a portfolio's 95% 1-month VaR and CVaR (Expected Shortfall) via Monte
Carlo simulation of correlated forward return paths drawn from the assets' own
historical mean/covariance of daily log returns — not a closed-form parametric VaR,
and not an assumption of independent per-asset risk.

### Formula and calculation sequence

1. **Fetch history.** For each holding, fetch ~1 trading year (252 days) of daily
   closes and compute daily log returns:
   `log_return_t = ln(Close_t / Close_{t-1})` (`_log_returns_by_ticker`,
   [`monte_carlo.py:158`](../../src/risk/monte_carlo.py)). A ticker whose history
   can't be fetched, or has fewer than 2 usable closes, is dropped (not aborted).
2. **Align on common dates** across all surviving tickers
   (`returns_df = pd.DataFrame(...).dropna(how="any")`).
3. **Apply the missing-history weight policy** (see below) before simulating.
4. **Simulate**: for 2+ assets, draw `simulations` (default 10,000) paths of
   `horizon_days` (default 21, ~1 trading month) daily returns from a multivariate
   normal distribution parameterized by the assets' historical mean vector and
   covariance matrix (`_simulate_multivariate`,
   [`monte_carlo.py:245`](../../src/risk/monte_carlo.py)). For exactly 1 surviving
   asset, a univariate normal fallback uses that asset's own mean/std
   (`_simulate_univariate`, [`monte_carlo.py:217`](../../src/risk/monte_carlo.py)).
5. **Convert log → simple returns, per asset, before weighting.** Each asset's
   cumulative log return over the horizon (`sum` across simulated days) is
   converted to a simple return via `expm1` **before** being weighted and summed
   into a portfolio return — never the reverse order. See "Why conversion order
   matters" below.
6. **Summarize** (`_summarize`, [`monte_carlo.py:193`](../../src/risk/monte_carlo.py)):
   ```
   VaR_95  = 5th percentile of the simulated portfolio simple-return distribution
   CVaR_95 = mean of all simulated returns <= VaR_95 (the tail)
   ```

### Why conversion order matters

Simple (arithmetic) returns combine linearly across a portfolio:
`portfolio_return = sum(w_i * R_i)`. Log returns do not — summing weighted log
returns is only a first-order approximation whose error grows with the size of the
move, which is exactly the regime a tail-risk (VaR) calculation cares about most.
The module docstring documents that an earlier version of this code aggregated log
returns across assets and converted only once at the end; the current
implementation converts per-asset, per-path, before the weighted sum, specifically
to avoid understating losses in the tail. See `TestLogToSimpleReturnConversion` and
`TestPortfolioReturnAggregationCorrectness` in
[`tests/risk/test_monte_carlo.py`](../../tests/risk/test_monte_carlo.py).

### Missing-history weight policy

A ticker without usable price history is a real, non-zero-weight risky position
that simply couldn't be modeled — not the same as cash (which was never in the
`holdings` dict). `calculate_portfolio_var`
([`monte_carlo.py:287`](../../src/risk/monte_carlo.py)) applies one policy,
identically before branching into either simulation path:

1. **Coverage floor**: if the surviving tickers' combined original weight is less
   than `MIN_PORTFOLIO_COVERAGE_FRACTION` (50%) of the total requested weight,
   return `status="insufficient_data"` rather than a number computed from an
   unrepresentative remnant.
2. **Otherwise, renormalize** survivors' weights to sum to the *original total
   requested weight* (not hardcoded to 1.0) — redistributing exactly the weight
   lost to exclusions across the modelable tickers, leaving any genuine cash/
   unallocated headroom untouched. When nothing was dropped, this scale factor is
   exactly 1.0 (a no-op).

See `TestMissingHistoryWeightPolicy` in
[`tests/risk/test_monte_carlo.py`](../../tests/risk/test_monte_carlo.py).

### Result contract

`VaRResult` (dataclass, [`monte_carlo.py:115`](../../src/risk/monte_carlo.py)) has
a `status` field distinguishing:

| Status | Meaning |
|---|---|
| `"ok"` | `var_95`/`cvar_95` are populated, valid simple-return fractions |
| `"insufficient_data"` | Not enough usable price history — an everyday, expected outcome (e.g. brand-new ticker), not an error |
| `"error"` | The simulation itself failed (non-finite output, uninvertible covariance matrix) |

This exists specifically so a caller cannot mistake "VaR is unavailable" for "VaR
is exactly zero" — a plain `{"var_95": 0.0}` sentinel would make that distinction
impossible. See `TestMissingDataIsDistinguishableFromZeroRisk`.

### Inputs and units

| Input | Units | Notes |
|---|---|---|
| `holdings` | `{ticker: weight}` | Weight is a decimal fraction, need not sum to 1.0 (cash is implicit) |
| `simulations` | integer | Default 10,000; must be a genuine positive whole number (an `int`, or a whole-number `float` like `10.0`) |
| `horizon_days` | trading days | Default 21 (~1 month); must be a genuine positive whole number, same rule as `simulations` |
| `rng` | `numpy.random.Generator` | Optional, for deterministic test output |

### Outputs

`var_95`, `cvar_95` — **simple (arithmetic) returns**, not log returns (e.g.
`-0.05` means -5%). Negative values indicate losses.

### Numerical safeguards

- `_summarize` rejects (returns `status="error"`) any simulated output containing a
  non-finite value.
- A narrowly-scoped `RuntimeWarning` suppression around the large batched
  `rng.multivariate_normal`/`rng.normal` calls is documented as a verified-benign
  BLAS quirk (spurious overflow/divide-by-zero warnings from discarded
  intermediate buffers on some backends, e.g. macOS Accelerate) — not evidence of
  an actual numerical problem; every actual output is checked finite by
  `_summarize` regardless.
- `simulations`/`horizon_days` are validated as genuine positive whole numbers —
  rejecting a fractional value (e.g. `10.5`), NaN, infinity, or a `bool` (Python
  treats `bool` as an `int` subclass) — and holdings weights must be finite and
  non-negative. All are caller-bug conditions that raise the documented
  `ValueError`, distinct from the `"insufficient_data"`/`"error"` statuses used
  for ordinary real-world data gaps. This validation happens up front, before any
  ticker/network lookup — a fractional `simulations` previously passed a bare
  `simulations <= 0` check (since `10.5 > 0`) and was only caught much later, deep
  inside the NumPy random-number call, as an unhelpful internal `TypeError`
  instead of this function's own documented `ValueError`; NaN would have passed
  that same bare check entirely undetected, since `float("nan") <= 0` is `False`
  in Python. See `_coerce_positive_int`,
  [`monte_carlo.py`](../../src/risk/monte_carlo.py).

### Known simplifications

- **Normal-distribution assumption.** Returns are simulated from a (multivariate)
  normal distribution — no fat tails, skew, or regime-switching. Real equity
  returns are well known to exhibit fatter tails than the normal distribution
  predicts, meaning this VaR/CVaR likely *understates* true tail risk. This is the
  single largest documented risk-model simplification in the codebase — see
  `L-006` and the roadmap's Track B item 10 (comparison against historical-
  simulation and bootstrap VaR, not yet built).
- **Historical covariance is used as the forward covariance estimate** — no
  shrinkage, no regime-conditional covariance, no forward-looking (e.g. implied)
  volatility input.
- 24-hour price-history cache TTL (`VAR_PRICE_HISTORY_CACHE_TTL_SECONDS`) means a
  same-day re-run reuses that day's first-fetched history rather than a fresher
  intraday pull.

## 2. SPY Put Hedge Sizing (Black-Scholes-Merton)

### Objective

Size a SPY put option hedge intended to offset a stated dollar amount of portfolio
VaR, using a scenario-based (not linear Delta-only) sizing method that captures the
put's convexity (gamma) within an assumed stress move.

### Formula

Standard Black-Scholes-Merton European put pricing
(`calculate_bsm_put_price`, [`hedging.py:63`](../../src/risk/hedging.py)):

```
d1 = (ln(S/K) + (r + σ²/2) * T) / (σ * √T)
d2 = d1 - σ * √T
P  = K * e^(-rT) * N(-d2) - S * N(-d1)
```

where `S` = spot price, `K` = strike, `T` = time to expiry (years), `r` =
risk-free rate, `σ` = implied volatility, `N` = standard normal CDF.

### Hedge sizing sequence

1. Price the candidate put at the current spot (`current_put_price`).
2. Price the same put at a stressed spot: `stressed_spot = spy_price * (1 -
   stress_move_fraction)` (default `stress_move_fraction = 7%`).
3. `pnl_per_contract = (stressed_put_price - current_put_price) * 100` (one
   contract = 100 shares, `CONTRACT_MULTIPLIER`).
4. `contracts = ceil(portfolio_var_dollars / pnl_per_contract)` — rounds **up**
   (`ceil`, not `floor`), since under-hedging a stated loss by a fraction of a
   contract defeats the purpose.
5. Apply `hedge_budget_dollars` (max premium spend) and `max_contracts` hard caps,
   if given — either can leave the hedge covering less than the full stated VaR;
   that is the intended behavior of having a budget, not a bug.

(`calculate_spy_hedge`, [`hedging.py:111`](../../src/risk/hedging.py))

### Why scenario-based, not Delta-linear

A pure Delta approximation (`contracts ≈ VaR / (Delta * 100 * spot_move)`)
implicitly treats the put's payoff as linear in the underlying's move. Pricing the
put at both the current and stressed spot and sizing from the *actual modeled
payoff difference* captures the put's real convexity (gamma) within the specific
stress move being hedged against — more accurate for a meaningfully-sized stress
scenario (7% here) than a linear approximation would be, though it still ignores
theta decay and changes in implied volatility over the hedge's life. See
`TestScenarioBasedSizing` in [`tests/risk/test_hedging.py`](../../tests/risk/test_hedging.py).

### Inputs and units

| Input | Units | Source |
|---|---|---|
| `portfolio_var_dollars` | USD, positive | `abs(equity * var_95)` from the Monte Carlo VaR result |
| `spy_price` | USD | Live SPY price (yfinance) |
| `strike_price` | USD | The *actual listed contract's* strike (Alpaca option chain), once selected — not a synthetic ATM value |
| `days_to_expiry` | calendar days | Default 30; live callers use the actual selected contract's real days to expiry |
| `implied_vol` | decimal, annualized | Assumed/configured (`HEDGE_IMPLIED_VOL = 15%` in the live execution engine) — not the contract's real live IV (Alpaca's trading-only client doesn't reliably expose it) |
| `risk_free_rate` | decimal, annualized | Default 4% |
| `stress_move_fraction` | decimal, `(0, 1)` | Default 7% — a stated modeling assumption, not derived from the VaR horizon |
| `hedge_budget_dollars` | USD, optional | Live: `HEDGE_BUDGET_FRACTION_OF_EQUITY` (2%) of equity, minus existing SPY put market value. **A theoretical modeled-premium ceiling, not an enforceable actual-spend ceiling** — see "Known simplifications" below. |
| `max_contracts` | integer, optional | Live: `HEDGE_MAX_CONTRACTS = 50` |

### Output

Integer number of put contracts to buy, `>= 0`. Returns `0` (not an error) for any
non-positive/invalid input, a non-positive modeled payoff, or a non-positive
budget — "no hedge needed/possible" is treated as a valid everyday outcome.

### Missing-data / failure behavior

`calculate_spy_hedge` **never raises — this is now a substantiated contract**,
verified against both non-finite AND extreme-but-finite adversarial inputs (see
"Test coverage" below), not just the originally-reproduced cases. Every invalid or
degenerate input returns `0` contracts, uniformly:

- Non-positive values.
- Non-finite inputs (NaN/infinity — e.g. a non-finite `hedge_budget_dollars`
  previously raised `OverflowError` from `math.floor(inf)`).
- Booleans passed where a number is expected.
- Fractional values for `days_to_expiry`/`max_contracts` (both must be genuine
  whole numbers — a fractional `max_contracts` previously leaked a `float` return
  value instead of the documented `int`, since `min(int, 1.5)` can itself be a
  `float`; see `_coerce_whole_number`, [`hedging.py`](../../src/risk/hedging.py)).
  A whole-number `float` (e.g. `3.0`) is still accepted and coerced to `int` —
  only a genuinely fractional value is rejected.
- **Extreme-but-finite `implied_vol`/`risk_free_rate`** (e.g. `implied_vol=1e308`,
  `risk_free_rate=-1e308`) — both pass `math.isfinite` but previously overflowed
  the BSM math itself (`implied_vol ** 2` and `math.exp(-risk_free_rate * T)` both
  raise `OverflowError` for an astronomically large-but-finite operand, even
  though the operand itself is technically finite). Rejected up front by
  `MAX_ABS_IMPLIED_VOL` (1,000%) / `MAX_ABS_RISK_FREE_RATE` (±500%) — deliberately
  generous bounds, since real values are never remotely close to them.
- **Any other extreme-but-finite input combination** the two range checks above
  didn't individually anticipate (e.g. an extreme `days_to_expiry` combined with a
  boundary-but-valid `risk_free_rate`, which can still overflow
  `math.exp(-risk_free_rate * T)` through the `T` side of the product) — caught by
  a defensive `try`/`except (ArithmeticError, OverflowError, ValueError)` wrapped
  around the BSM computation itself, as a second layer behind the range checks
  (`OverflowError` is already an `ArithmeticError` subclass; listed separately in
  the code so the contract is self-documenting). `BaseException`,
  `KeyboardInterrupt`, and `SystemExit` are never caught.
- **(Track A Phase 1.5C) An astronomically large Python `int`** (e.g. `10**10000`
  for `spy_price`, `implied_vol`, `risk_free_rate`, etc.) — a Python `int` is
  arbitrary-precision and always finite by definition (it has no NaN/infinity
  representation), so this is correctly accepted as "finite" by the input-
  validation helper `_is_finite_number`. Until this pass, however,
  `_is_finite_number` itself called `math.isfinite(value)` unconditionally — and
  `math.isfinite` on an `int` this large raises `OverflowError` while converting
  it to a C `double`, making the supposedly non-raising validation CHECK ITSELF
  raise, before the function's own try/except (further downstream) ever had a
  chance to catch anything. `_is_finite_number` now special-cases `int` (always
  finite, no conversion needed) vs. `float` (safe to pass to `math.isfinite`
  directly) so the check genuinely never raises, restoring the "never raises"
  contract for this class of input too.
- `current_put_price`, `stressed_put_price`, `pnl_per_contract`, and
  `raw_contracts` are each explicitly checked finite before being used further,
  even after the try/except — `norm.cdf` can in principle return `nan` for a
  degenerate input without raising at all.

In the live execution engine (`execute_spy_var_hedge`,
[`alpaca_execution.py:1578`](../../src/trading/alpaca_execution.py)), an
unavailable SPY price, no listed contract found within the search window, a
failed/rejected order submission, or a failed fresh buying-power read each skip the
hedge for that run (logged as a warning) rather than raising or blocking the
already-completed equity rebalance.

**The lower-level BSM helpers (`calculate_bsm_d1_d2`, `calculate_bsm_put_price`,
`calculate_bsm_put_delta`) are NOT covered by this "never raises" contract** — they
have no hardening of their own and are thin, direct implementations of the
textbook formulas. Called directly with an invalid/extreme input, they can raise
`ZeroDivisionError`, `ValueError` (`math.log`/`math.sqrt` on a non-positive
argument), or `OverflowError` (an extreme-but-finite `implied_vol`/
`risk_free_rate`). Only `calculate_spy_hedge` validates and defends against these
before calling into them — see each function's own docstring in
[`hedging.py`](../../src/risk/hedging.py) for its exact failure modes.

### Numerical safeguards

- Every numeric input (`portfolio_var_dollars`, `spy_price`, `strike_price`,
  `implied_vol`, `risk_free_rate`, `stress_move_fraction`, `hedge_budget_dollars`)
  is validated finite before any computation — a non-finite value returns `0`
  rather than propagating into `math.floor`/`math.ceil`, either of which raises on
  `inf`/`nan`.
- `implied_vol`/`risk_free_rate` are additionally bounded to
  `MAX_ABS_IMPLIED_VOL`/`MAX_ABS_RISK_FREE_RATE` — a value that is finite but
  economically absurd (and overflow-prone in the BSM arithmetic) is rejected the
  same way a non-finite one is.
- `days_to_expiry` and `max_contracts` are validated as genuine whole numbers
  (rejecting a fractional value or a `bool`, which Python treats as an `int`
  subclass) before use, so the function's own documented `int` return contract
  cannot be silently violated by an invalid input.
- The BSM pricing calls are wrapped in a defensive `try`/`except` (see
  "Missing-data / failure behavior" above) as a second layer behind the range
  checks, for any extreme-but-finite combination the range checks alone don't
  catch.
- `contracts = ceil(...)`, never `floor` — never under-hedges due to rounding.
- The final return value is explicitly cast to `int` (`int(max(contracts, 0))`) —
  never a `bool`, never a `float`, regardless of how the intermediate `contracts`
  value was produced.
- Existing SPY put exposure is split into "exact same contract" (matched by exact
  OCC option symbol) vs. "other contract" (different strike/expiry) — only the
  matching contract's held quantity is subtracted from this run's target size,
  since different strikes/expiries are not fungible, contract-for-contract
  (`ExistingHedgeExposure`, [`alpaca_execution.py:1480`](../../src/trading/alpaca_execution.py)).
  See `TestHedgeContractNonFungibility` in
  [`tests/trading/test_rebalance.py`](../../tests/trading/test_rebalance.py).
- A fresh, broker-reported buying-power reading is taken immediately before sizing
  (live runs only) as an additional ceiling, so the hedge is never sized against
  stale capacity left over from before the equity-rebalance phase settled — see
  `TestHedgeJointBuyingPowerSafety`.

### Known simplifications

- **`hedge_budget_dollars` is a theoretical premium ceiling, not an enforceable
  actual-spend ceiling.** The budget check compares against this module's own
  BSM-modeled `current_put_price`, computed from `HEDGE_IMPLIED_VOL` and the
  selected contract's strike/expiry — but `execute_spy_var_hedge` submits a real
  MARKET order, which fills at whatever the option's real bid/ask is at that
  moment. A real fill can cost more (or less) per contract than the modeled price
  this budget was checked against; actual premium spent is therefore not
  guaranteed to stay within `HEDGE_BUDGET_FRACTION_OF_EQUITY` of equity, only the
  *modeled estimate* is. Tracked as `L-016` (High severity) in
  [`docs/limitations-register.md`](../limitations-register.md). Closing this
  properly would require fetching a live option quote and submitting a LIMIT order
  bounded by it — not implemented, since no option-quote data client exists
  anywhere in this codebase today and adding one is a genuine scope expansion, not
  a bounded fix; proposed as a follow-up task.
- `implied_vol` is a fixed assumption (15%), not the contract's actual live implied
  volatility (a related but distinct gap from the budget-enforcement one above —
  this affects the SIZING calculation itself, not just the budget check).
- Ignores theta decay over the hedge's holding period.
- `stress_move_fraction` (7%) is a stated, fixed assumption — not derived from or
  reconciled against the VaR horizon/confidence level it is meant to hedge.
- No dynamic re-hedging within a day; sizing happens once per scan run.

## Test coverage

`tests/risk/test_monte_carlo.py` (30 test functions; 39 pytest-collected cases
once parametrization is expanded): `TestDeterministicRNG`, `TestSingleAssetVaR`,
`TestMissingHistoryWeightPolicy`, `TestLogToSimpleReturnConversion`,
`TestPortfolioReturnAggregationCorrectness`,
`TestMissingDataIsDistinguishableFromZeroRisk`, `TestValidation`,
`TestNumericBoundaryHardening` (adversarial `simulations`/`horizon_days` inputs —
fractional, NaN, infinity, bool).
`tests/risk/test_hedging.py` (22 test functions; 68 pytest-collected cases once
parametrization is expanded): `TestBSMSanity`, `TestScenarioBasedSizing`,
`TestBudgetAndMaxContractCaps`, `TestInvalidInputsReturnZero`,
`TestNumericBoundaryHardening` (adversarial `hedge_budget_dollars`/`max_contracts`/
`days_to_expiry` and every other numeric argument — non-finite, fractional, bool;
also asserts the return value is always a genuine `int`),
`TestExtremeButFiniteInputsNeverRaise` (Track A Phase 1.5B: `implied_vol=1e308`,
`risk_free_rate=-1e308`, and other extreme-but-finite magnitudes that pass
`math.isfinite` but previously overflowed the BSM arithmetic itself — the
`days_to_expiry=10**15` + boundary-`risk_free_rate` case specifically exercises
the defensive try/except, not either range check alone; extended in Track A Phase
1.5C with `test_huge_python_integer_never_leaks_a_raw_overflow_error`, proving
`10**10000` across every numeric argument no longer breaks the "never raises"
validation check itself). Hedge non-fungibility and joint buying-power safety are
covered in `tests/trading/test_rebalance.py`
(`TestHedgeContractNonFungibility`, `TestHedgeJointBuyingPowerSafety`,
`TestHedgeIncrementalSizing`), not in `tests/risk/`, since those behaviors live in
the execution-engine call site rather
than in `src/risk/hedging.py` itself.
