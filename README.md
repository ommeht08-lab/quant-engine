<div align="center">

# Autonomous S&P 100 Valuation & Backtesting Engine

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![yfinance](https://img.shields.io/badge/Data-yfinance-333333)](https://github.com/ranaroussi/yfinance)
[![Status](https://img.shields.io/badge/status-research_%2F_phase_1-yellow)]()

</div>

---

## Objective

This repository is a Python-based quantitative screener that evaluates the S&P 100 using a **dynamic Discounted Cash Flow (DCF) model** paired with a **Growth At a Reasonable Price (GARP) Conviction framework**. Rather than applying one generic set of growth/margin assumptions across every name, the engine derives each company's DCF inputs from its own historical financials, screens the resulting universe for a strict margin of safety, ranks survivors by a composite conviction score, and backtests the resulting portfolio against the S&P 500 (SPY).

---

## Core Architecture

The pipeline runs in three stages, each its own module:

1. **Point-in-time data ingestion** (`src/data_ingestion/`, `src/backtesting/historical_tester.py`) — pulls income statements, balance sheets, cash flow statements, price, and share count via `yfinance`. For backtesting, every statement is filtered to only the fiscal periods that existed on or before the target date, so no future data ever leaks into a historical valuation.

2. **Dynamic DCF valuation** (`src/dcf_model/dcf.py`) — instead of static assumptions, each ticker's inputs are derived from its own history:
   - **WACC** is calculated per company via CAPM cost of equity and after-tax cost of debt, weighted by market-value capital structure.
   - **Revenue growth** defaults to the company's own historical Revenue CAGR — **capped at a 25% ceiling** so a hyper-growth outlier (e.g. a semiconductor name mid-AI-cycle) can't distort the terminal value math or produce a divergent WACC-minus-growth spread.
   - **Operating margin** defaults to the company's own historical average Operating Margin (EBIT / Revenue) across all available periods, rather than a single generic figure.
   - Any input that can't be derived (missing EBIT, insufficient history, etc.) falls back to a conservative default and is logged, rather than silently failing.

3. **Screening & backtesting** (`src/backtesting/historical_tester.py`) — applies a Margin of Safety filter, computes a Conviction Score for survivors, ranks the top N, and measures their actual forward price performance against SPY over the same window.

---

## Phase 1 Backtest Findings — August 2024

**Universe:** The 100 largest S&P 500 constituents by market capitalization. **Target date:** 2024-08-01.

### The filter worked as designed

A strict Margin of Safety filter (**P/IV ≤ 1.0** — only tickers trading at or below their model-derived intrinsic value are eligible) cut the 100-name universe down to 20 survivors. The top 10 by Conviction Score — a defensive, value-tilted basket (DE, MCD, T, PEP, PGR, VZ, BRK-B, CB, PG, BMY) — returned **+22.5%** from August 2024 through today, successfully identifying a set of undervalued compounders that appreciated meaningfully.

### The honest Alpha number: −20.3% vs. SPY

Over the same window, SPY returned +42.8%, putting the portfolio **20.3 percentage points behind the benchmark**. This is not a modeling error — it is the direct, structural consequence of the filter itself. Every mega-cap tech name in the universe (NVDA, AAPL, MSFT, AMZN, GOOGL/GOOG, META, TSLA) was excluded at the P/IV ≤ 1.0 gate, each trading well above its DCF-derived intrinsic value at the time. Because 2024–2026 was a tech- and AI-led bull market disproportionately driven by exactly those excluded names, a strict absolute-valuation cutoff effectively turned this engine into a **defensive value portfolio during a growth-led rally** — the opposite of the market's actual leadership. The takeaway isn't that the valuation logic is wrong; it's that an absolute (rather than sector-relative) margin-of-safety threshold has a structural blind spot for periods when richly-valued growth sectors are also the sectors doing the winning. This directly motivates the sector-relative filtering work in the roadmap below.

### Engineering fix: retroactive stock-split asymmetry

While validating these results, NFLX initially screened as one of the cheapest names in the universe (P/IV ≈ 0.33x) — which didn't hold up under scrutiny. The root cause: NFLX executed a 10-for-1 stock split in November 2025. Standard financial APIs like `yfinance` retroactively split-adjust *price* history through the present day, but the historical *shares outstanding* series (`Ticker.get_shares_full`) is **not** adjusted for splits that happen after the queried date. Naively combining the two understated NFLX's true August 2024 market capitalization by roughly 10x (~$27B computed vs. ~$268B actual), which cascaded into a distorted WACC and a falsely cheap-looking valuation.

The fix (`_cumulative_split_factor_since` in `src/backtesting/historical_tester.py`) detects every stock split that occurred between the target date and today and scales the historical share count by that cumulative ratio, bringing it back onto the same split-adjusted basis as the price series. This restored NFLX's correct ~$268B historical market cap and removed it from the eligible universe entirely — a reminder that point-in-time backtesting on free data sources requires actively auditing for exactly this class of silent data integrity issue.

---

## Development Roadmap

- **Sector-Relative Valuation Filtering** — replace (or supplement) the absolute P/IV ≤ 1.0 cutoff with a sector-relative threshold, so structurally higher-multiple sectors (e.g. technology) aren't uniformly excluded regardless of relative attractiveness within their peer group.
- **Live Paper Trading Execution via the Alpaca API** — automate execution of the top-N Conviction Score picks into a paper trading account, enabling continuous, forward (rather than purely historical) validation of the screening methodology.
- **Next.js Frontend Dashboard** — expand the existing single-ticker valuation dashboard (`frontend/`) into a full research UI covering universe screening, Conviction Score rankings, and backtest visualization.

---

## Disclaimer

This tool is intended for **educational and research purposes only**. All outputs are model-based estimates dependent on historical data quality, the completeness of third-party data sources, and the stated assumptions. Nothing in this repository constitutes investment advice, and past backtested performance is not indicative of future results.
