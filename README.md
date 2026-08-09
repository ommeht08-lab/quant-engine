# Om Mehta Equity Research

## Introduction

This is a personal, student-built project I use to learn quantitative finance and full-stack software engineering at the same time. It's not a commercial product, and it isn't run by a fund or a team — it's one person's sandbox for actually implementing the ideas covered in corporate finance and investing coursework (DCF valuation, CAPM/WACC, factor-based screening, risk-adjusted position sizing) instead of just reading about them, and then wiring the result up to a real (paper-money) trading account and a real database so I can see whether the theory actually holds up over time.

Everything here should be read in that spirit: a learning project with working code, not investment advice, and not a track record. See the [Disclaimer](#disclaimer) at the bottom.

## Architecture

The project is split into a Python research/execution engine and a Next.js dashboard, connected through a shared Postgres database.

**Frontend**
- [Next.js](https://nextjs.org/) (App Router, TypeScript) — the dashboard at `frontend/`
- [Tailwind CSS](https://tailwindcss.com/) — dark-themed styling throughout
- [Recharts](https://recharts.org/) — the strategy-vs-S&P 500 backtest equity curve chart

**Backend**
- A Python execution engine (`src/`) covering data ingestion, DCF valuation, point-in-time backtesting, and trade execution
- [yfinance](https://github.com/ranaroussi/yfinance) — market prices, financial statements, shares outstanding, beta, and sector data
- [Alpaca Trading API](https://alpaca.markets/) (`alpaca-py`) — autonomous **paper trading** execution of the strategy's top picks, plus a live account/positions read for the dashboard
- [FastAPI](https://fastapi.tiangolo.com/) — a small HTTP API (`src/api/main.py`) exposing single-ticker DCF valuations to the frontend

**Database**
- [Supabase](https://supabase.com/) (hosted PostgreSQL) — two tables written by the Python engine (`src/utils/db.py`) and read by the Next.js API routes:
  - `trade_logs` — every order the paper-trading engine actually submits, with the WACC, beta, and Conviction Score behind the decision
  - `backtest_curve` — the strategy's equity curve vs. a same-notional SPY curve from the most recent backtest run

## Core Features

**Live DCF / WACC valuation math**
Rather than applying one static set of assumptions to every company, each ticker's discount rate is derived from a live CAPM calculation (current beta, a live 10-year Treasury yield as the risk-free rate, and market-value capital structure for WACC), and its revenue growth / operating margin assumptions default to that company's own historical figures instead of a single generic number. A two-pass sector-relative filter and a composite Conviction Score are then used to rank and screen the universe, both in live use and in point-in-time historical backtests.

**Dynamic risk management (inverse volatility / beta weighting)**
Position sizing in the execution engine isn't equal-weighted. Each Top-N pick's target portfolio weight is proportional to its inverse beta (`1 / beta`, floored so an anomalously low beta can't dominate the allocation), so lower-volatility picks receive a larger share of capital and higher-volatility picks receive a smaller one — a simple, explicit form of risk-adjusted sizing rather than treating every position as equally risky.

**End-to-end database logging**
Every trade the execution engine actually places (paper trading only) and every backtest run's equity curve are written to Postgres and surfaced live on the dashboard: a trade history table, a live portfolio allocation view (pulled directly from Alpaca), and a strategy-vs-SPY backtest chart — so the pipeline is genuinely connected front-to-back rather than being a script that only prints to a terminal.

## Local Setup

This is a two-process local setup: the Python engine and the Next.js frontend run independently and talk to the same Supabase database.

### 1. Python (repository root)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Useful entry points once dependencies are installed:

```bash
# Run the DCF valuation API (used by the frontend's single-ticker view)
uvicorn src.api.main:app --reload

# Run a point-in-time historical backtest
python -m src.backtesting.historical_tester

# Run the autonomous paper-trading engine (--dry-run previews orders without submitting them)
python -m src.trading.alpaca_execution --dry-run
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

### 3. Environment variables

Both the Python engine and the frontend need their own local `.env` file — Next.js only loads environment variables from its own directory, not the project root, so credentials have to be duplicated across both.

- **Root `.env`** (copy from `.env.example`): Alpaca paper trading credentials (`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_API_BASE_URL`) and a Supabase Postgres connection string (`DATABASE_URL`). The Alpaca keys should be **paper trading** keys — this project never places live trades.
- **`frontend/.env.local`** (copy from `frontend/.env.local.example`): the same `DATABASE_URL`, plus the same Alpaca credentials again — the dashboard's live portfolio allocation view calls Alpaca's REST API directly from a Next.js API route.

Both `.env` files are already covered by `.gitignore` and should never be committed.

## Disclaimer

This project is for **educational purposes only**. It trades exclusively against Alpaca's paper trading (simulated money) environment, and nothing in this repository is financial advice or a recommendation to buy or sell any security. Model outputs depend on the quality and completeness of free third-party data sources and are not guaranteed to be accurate.
