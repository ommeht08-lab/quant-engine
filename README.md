<div align="center">

# Autonomous S&P 100 Valuation & Backtesting Engine

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![yfinance](https://img.shields.io/badge/Data-yfinance-333333)](https://github.com/ranaroussi/yfinance)
[![Status](https://img.shields.io/badge/status-research_%2F_phase_1-yellow)]()

</div>

---

## Objective

This repository is a Python-based quantitative screener that evaluates the S&P 100 using a **dynamic Discounted Cash Flow (DCF) model** paired with a **Growth At a Reasonable Price (GARP) Conviction framework**. Rather than applying one generic set of growth/margin assumptions across every name, the engine derives each company's DCF inputs from its own historical financials, screens the resulting universe against a sector-relative valuation filter, ranks survivors by a composite conviction score, and backtests the resulting portfolio against the S&P 500 (SPY).

---

## Core Architecture

The pipeline runs in three stages, each its own module:

1. **Point-in-time data ingestion** (`src/data_ingestion/`, `src/backtesting/historical_tester.py`) — pulls income statements, balance sheets, cash flow statements, price, and share count via `yfinance`. For backtesting, every statement is filtered to only the fiscal periods that existed on or before the target date, so no future data ever leaks into a historical valuation.

2. **Dynamic DCF valuation** (`src/dcf_model/dcf.py`) — instead of static assumptions, each ticker's inputs are derived from its own history:
   - **WACC** is calculated per company via CAPM cost of equity and after-tax cost of debt, weighted by market-value capital structure.
   - **Revenue growth** defaults to the company's own historical Revenue CAGR — **capped at a 25% ceiling** so a hyper-growth outlier (e.g. a semiconductor name mid-AI-cycle) can't distort the terminal value math or produce a divergent WACC-minus-growth spread.
   - **Operating margin** defaults to the company's own historical average Operating Margin (EBIT / Revenue) across all available periods, rather than a single generic figure.
   - Any input that can't be derived (missing EBIT, insufficient history, etc.) falls back to a conservative default and is logged, rather than silently failing.

3. **Screening & backtesting** (`src/backtesting/historical_tester.py`) — applies a two-pass sector-relative valuation filter (see below), computes a Conviction Score for survivors, ranks the top N, and measures their actual forward price performance against SPY over the same window.

### Sector-Relative Architecture

The engine originally screened the universe with a static Margin of Safety cutoff — every ticker had to clear the same absolute **P/IV ≤ 1.0** bar regardless of sector. That was refactored into a **two-pass dynamic system**:

- **Pass 1 — value everything.** Every ticker in the universe is run through the DCF to produce a Price / Intrinsic Value (P/IV) ratio, tagged with its GICS sector (`ticker.info.get('sector')` via `yfinance`, falling back to `"Unknown"` if missing). P/IV ratios are grouped by sector and the **median P/IV per sector** is calculated from the tickers actually present in the current universe — e.g. Technology came in at **4.18x** versus Utilities at **0.41x** in the August 2024 run.
- **Pass 2 — filter relative to peers.** A ticker is only eligible to be scored and ranked if its own P/IV is **less than or equal to its specific sector's median P/IV** — not a fixed absolute number. A ticker trading at 2.5x that sits in a sector medianing 4.2x passes; the same 2.5x ticker in a sector medianing 1.0x would fail.

This means structurally higher-multiple sectors (technology, real estate) are no longer uniformly excluded — they're judged against their own peer group instead of an economy-wide flat threshold.

---

## Phase 1 Backtest Findings — August 2024

**Universe:** The 100 largest S&P 500 constituents by market capitalization. **Target date:** 2024-08-01.

### From defensive value trap to market-beating GARP strategy

The original static Margin of Safety filter (P/IV ≤ 1.0) excluded every mega-cap tech name in the universe outright — NVDA, AAPL, MSFT, AMZN, GOOGL/GOOG, META, TSLA all traded above their absolute DCF-derived intrinsic value and were rejected at the gate, regardless of how they compared to their own sector. That turned the engine into a defensive value portfolio during a tech- and AI-led bull market: the opposite of the market's actual leadership, and a straightforward structural blind spot rather than a modeling error.

Switching to the **sector-relative filter** fixed this directly. **ANET** (Arista Networks, P/IV 2.51x) and **AMAT** (Applied Materials, P/IV 2.35x) both would have failed the old flat 1.0x cutoff — but both trade well below Technology's own 4.18x sector median, so the sector-relative filter correctly identifies them as *relatively* undervalued, high-quality growth: exactly the "Growth At a Reasonable Price" names the framework is meant to surface. Both went on to deliver outsized forward returns (**ANET +118.4%**, **AMAT +168.4%**), pulling the whole portfolio's return up materially. This is the algorithmic pivot in one sentence: the engine stopped asking "is this stock cheap in absolute terms?" and started asking "is this stock cheap *relative to its own sector*?" — which is what let it participate in the exact tech-led rally the static filter had shut it out of.

### The new Alpha number: +5.4% vs. SPY

With the sector-relative filter, the Top 10 Conviction Score portfolio (DE, ANET, META, TJX, MCD, AMAT, T, PEP, PGR, VZ) returned **+48.3%** from August 2024 through today, against SPY's +42.8% — an Alpha of **+5.4%**. Nothing else changed between the two runs (same universe, same target date, same DCF assumptions) — the entire swing from −20.3% to +5.4% Alpha came from replacing one filtering mechanism with another, which is a useful reminder of how much a screening framework's *selection rule*, not just its valuation math, drives realized performance.

### Engineering fix: retroactive stock-split asymmetry

While validating these results, NFLX initially screened as one of the cheapest names in the universe (P/IV ≈ 0.33x) — which didn't hold up under scrutiny. The root cause: NFLX executed a 10-for-1 stock split in November 2025. Standard financial APIs like `yfinance` retroactively split-adjust *price* history through the present day, but the historical *shares outstanding* series (`Ticker.get_shares_full`) is **not** adjusted for splits that happen after the queried date. Naively combining the two understated NFLX's true August 2024 market capitalization by roughly 10x (~$27B computed vs. ~$268B actual), which cascaded into a distorted WACC and a falsely cheap-looking valuation.

The fix (`_cumulative_split_factor_since` in `src/backtesting/historical_tester.py`) detects every stock split that occurred between the target date and today and scales the historical share count by that cumulative ratio, bringing it back onto the same split-adjusted basis as the price series. This restored NFLX's correct ~$268B historical market cap and removed it from the eligible universe entirely — a reminder that point-in-time backtesting on free data sources requires actively auditing for exactly this class of silent data integrity issue.

---

## Development Roadmap

- [x] **Sector-Relative Valuation Filtering** — **Completed.** Replaced the absolute P/IV ≤ 1.0 cutoff with a sector-relative threshold, so structurally higher-multiple sectors (e.g. technology) aren't uniformly excluded regardless of relative attractiveness within their peer group. See [Sector-Relative Architecture](#sector-relative-architecture) above; validated with a +5.4% Alpha vs. SPY on the August 2024 backtest.
- [ ] **Live Paper Trading Execution via the Alpaca API** — automate execution of the top-N Conviction Score picks into a paper trading account, enabling continuous, forward (rather than purely historical) validation of the screening methodology.
- [ ] **Next.js Frontend Dashboard** — expand the existing single-ticker valuation dashboard (`frontend/`) into a full research UI covering universe screening, Conviction Score rankings, and backtest visualization.

---

## Disclaimer

This tool is intended for **educational and research purposes only**. All outputs are model-based estimates dependent on historical data quality, the completeness of third-party data sources, and the stated assumptions. Nothing in this repository constitutes investment advice, and past backtested performance is not indicative of future results.
