<div align="center">

# 📊 Automated Corporate Finance Valuation Engine

**A full-stack, model-driven intrinsic valuation platform — from live market data to a discounted cash flow verdict, end to end.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)](https://nextjs.org/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-v4-06B6D4?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![Status](https://img.shields.io/badge/status-active_development-yellow)]()

</div>

---

## Executive Summary

The Automated Corporate Finance Valuation Engine ingests live financial statement and market data for any publicly traded company and produces a fully-modeled, discounted cash flow (DCF) intrinsic valuation in seconds. It pairs a modular Python valuation core — WACC estimation, five-year Free Cash Flow projection, and Gordon Growth terminal value — with a real-time Next.js dashboard, so analysts can adjust core assumptions and see the resulting intrinsic value per share instantly.

---

## Table of Contents

1. [Features](#features)
2. [Financial Methodology](#financial-methodology)
3. [Tech Stack](#tech-stack)
4. [Project Structure](#project-structure)
5. [Local Setup Instructions](#local-setup-instructions)
6. [API Reference](#api-reference)
7. [Disclaimer](#disclaimer)

---

## Features

### 🧮 Automated DCF Math
The valuation core (`src/dcf_model/`) is a fully modular, independently-testable implementation of a standard unlevered DCF: CAPM-based WACC, a configurable five-year Free Cash Flow projection, Gordon Growth terminal value, and present-value discounting down to an intrinsic value per share — no spreadsheet required.

### 📡 Live Data Ingestion
The ingestion layer (`src/data_ingestion/`) pulls income statements, balance sheets, cash flow statements, current share price, shares outstanding, and beta directly from Yahoo Finance via `yfinance`, with every field degrading gracefully — never crashing — when a data point is unavailable for a given ticker.

### ⚡ Dynamic Next.js Dashboard
The frontend (`frontend/`) is a dark, institutional-grade dashboard built on Next.js 16 (App Router) and Tailwind CSS v4. Analysts can adjust Revenue Growth Rate, Operating Margin, and Terminal Growth Rate via live sliders and re-run the full valuation against the FastAPI backend on demand, with loading and error states handled gracefully.

### 🔗 Decoupled, API-First Architecture
The backend exposes a single, well-typed REST endpoint (`GET /api/evaluate/{ticker}`) via FastAPI, with CORS enabled so the valuation engine can be consumed by any frontend, internal tool, or downstream service — not just the bundled dashboard.

---

## Financial Methodology

All valuation logic lives in [`src/dcf_model/dcf.py`](src/dcf_model/dcf.py). Each step below is an independent, unit-testable function.

### 1. Weighted Average Cost of Capital (WACC)

Cost of equity is estimated via the **Capital Asset Pricing Model (CAPM)**:

```
Cost of Equity (Re) = Risk-Free Rate + Beta × Equity Risk Premium
```

Cost of debt is applied on an **after-tax basis**, using the effective tax rate derived from the income statement:

```
After-Tax Cost of Debt (Rd) = Pre-Tax Cost of Debt × (1 − Tax Rate)
```

The two are blended using market-value capital weights — equity weighted by market capitalization (current price × shares outstanding), debt weighted by book value of total debt:

```
WACC = (E / (E + D)) × Re  +  (D / (E + D)) × Rd
```

If beta, cost of debt, or tax rate cannot be derived from the fetched financials, the engine falls back to conservative defaults (β = 1.0, Kd = 5.0%, tax rate = 21.0%) and logs a warning rather than failing the valuation.

### 2. Free Cash Flow Projection

Free Cash Flow is projected forward for a configurable number of years (five, by default) using a constant revenue growth rate and operating margin:

```
Revenue(t)  = Revenue(t-1) × (1 + Revenue Growth Rate)
EBIT(t)     = Revenue(t) × Operating Margin
NOPAT(t)    = EBIT(t) × (1 − Tax Rate)
FCF(t)      = NOPAT(t) + D&A(t) − CapEx(t) − ΔNWC(t)
```

Depreciation & Amortization, Capital Expenditures, and the change in Net Working Capital are each modeled as a constant percentage of projected revenue, isolating **Revenue Growth Rate** and **Operating Margin** as the two primary levers exposed to the analyst.

### 3. Terminal Value — Gordon Growth Model

Value beyond the explicit forecast horizon is captured via the **perpetuity growth (Gordon Growth) method**:

```
Terminal Value = [ FCF(n) × (1 + Terminal Growth Rate) ] / ( WACC − Terminal Growth Rate )
```

The engine enforces `WACC > Terminal Growth Rate` at runtime — the model raises a descriptive error rather than returning a divergent, meaningless result.

### 4. Present Value & Intrinsic Value per Share

Each projected FCF and the terminal value are discounted back to the present at the WACC, summed into Enterprise Value, and bridged to Equity Value:

```
Enterprise Value = Σ [ FCF(t) / (1 + WACC)^t ]  +  [ Terminal Value / (1 + WACC)^n ]
Equity Value      = Enterprise Value − Total Debt + Cash & Equivalents
Intrinsic Value / Share = Equity Value / Diluted Shares Outstanding
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Frontend** | [Next.js](https://nextjs.org/) 16 (App Router, TypeScript) | Interactive, client-rendered valuation dashboard |
| **Styling** | [Tailwind CSS](https://tailwindcss.com/) v4 | Dark, institutional-grade UI system |
| **Backend API** | [FastAPI](https://fastapi.tiangolo.com/) | Typed, async REST layer exposing the DCF engine |
| **Language** | [Python](https://www.python.org/) 3.10+ | Core valuation and data ingestion logic |
| **Market Data** | [yfinance](https://github.com/ranaroussi/yfinance) | Live financial statements, pricing, and beta |
| **Data Handling** | pandas, numpy | Statement parsing and numerical computation |
| **Server** | Uvicorn | ASGI server for local and production FastAPI deployment |

---

## Project Structure

```
Valuation Engine/
├── src/
│   ├── data_ingestion/
│   │   └── fetch_financials.py   # yfinance ingestion: statements, price, shares, beta
│   ├── dcf_model/
│   │   └── dcf.py                # WACC, FCF projection, terminal value, PV, orchestration
│   └── api/
│       └── main.py               # FastAPI application (GET /api/evaluate/{ticker})
├── frontend/
│   └── src/app/
│       └── page.tsx              # Next.js valuation dashboard (client component)
├── tests/                        # Unit and integration tests
├── requirements.txt              # Backend Python dependencies
└── README.md
```

---

## Local Setup Instructions

The engine runs as two independent services — a FastAPI backend and a Next.js frontend — that must be running **simultaneously** in separate terminal sessions.

### Prerequisites

- Python 3.10+
- Node.js 18.18+ and npm

### 1 — Start the Backend (Terminal 1)

```bash
# From the project root
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload
```

The API is now live at **`http://127.0.0.1:8000`**. Interactive Swagger docs are available at `http://127.0.0.1:8000/docs`.

### 2 — Start the Frontend (Terminal 2)

```bash
# From the project root
cd frontend
npm install
npm run dev
```

The dashboard is now live at **`http://localhost:3000`**.

### 3 — Run a Valuation

With both services running, open `http://localhost:3000`, enter a ticker (e.g. `AAPL`), optionally adjust the Revenue Growth Rate, Operating Margin, and Terminal Growth Rate sliders, and click **Run Valuation**.

> **Note:** The frontend calls the backend directly at `http://localhost:8000`. Both services must be running locally for the dashboard to return results.

---

## API Reference

### `GET /api/evaluate/{ticker}`

Runs a full DCF valuation for the given ticker using live market data.

**Query Parameters** *(all optional)*

| Parameter | Type | Default | Description |
|---|---|---|---|
| `revenue_growth_rate` | float | `0.08` | Constant annual revenue growth assumption |
| `operating_margin` | float | `0.25` | Constant EBIT margin applied to projected revenue |
| `terminal_growth_rate` | float | `0.025` | Perpetuity growth rate used in the terminal value |

**Example**

```bash
curl "http://127.0.0.1:8000/api/evaluate/AAPL?revenue_growth_rate=0.08&operating_margin=0.30&terminal_growth_rate=0.025"
```

**Response**

```json
{
  "ticker": "AAPL",
  "current_price": 303.42,
  "wacc": 0.0985,
  "enterprise_value": 1704647222534.16,
  "equity_value": 1641924222534.16,
  "intrinsic_value_per_share": 111.79,
  "projected_free_cash_flows": [ { "year": 1, "revenue": 449453880000.0, "...": "..." } ],
  "assumptions": { "revenue_growth_rate": 0.08, "operating_margin": 0.3, "terminal_growth_rate": 0.025, "projection_years": 5 }
}
```

Missing or insufficient financial data for a ticker returns a `422` with a descriptive `detail` message rather than a server error.

---

## Disclaimer

This tool is intended for **educational and research purposes only**. All outputs are model-based estimates that depend entirely on user-supplied assumptions and the completeness of third-party data sources. Nothing produced by this engine constitutes investment advice, and it should not be relied upon for any actual investment or trading decision.
