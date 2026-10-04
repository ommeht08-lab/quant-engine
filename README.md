# DCF Valuation Model

A personal, student-built project for learning corporate finance and full-stack software engineering through a working valuation and research workspace. It brings company financial statements, explicit DCF assumptions, scenario analysis and source evidence into one place. It is an educational project, not a commercial product or an investment track record.

[Open the live workspace](https://quant-engine-taupe.vercel.app/workspace).

## The workspace

Enter a ticker and select **Run Valuation**. Historical mode derives growth and operating margin from company history; Custom mode uses your explicit overrides. Terminal growth remains an explicit assumption in both modes. Navigation and restoring a saved result do not run the model.

- **Overview:** reported financial history and the valuation summary.
- **Valuation:** market price, intrinsic-value estimates, Bear/Base/Bull scenarios, sensitivity and available peer context.
- **Cash flow forecast:** Base-case projected free cash flow and a year-selectable cash-flow bridge.
- **Projection detail:** annual forecast assumptions and cash-flow components.
- **Evidence & sources:** selected source, reporting period, knowledge cutoff, policy version and ingestion batches.
- **Model assumptions:** inputs and model settings.

The latest completed result, inputs, selected scenario and available market history are saved in the current browser. Reloading restores the original run time and displays an age warning. Prices and statements refresh only after another explicit run. **Clear saved result** removes the stored result. If browser storage is unavailable, the workspace explains that a refresh will lose it.

## Data and model boundaries

Yahoo provides financial statements and market observations for the default live workflow. SEC ingestion and an explicit SEC valuation path also exist. Automatic SEC selection is controlled separately for each issuer: it stays pending until the declared repeated, period-aligned SEC/Yahoo comparison gate passes. Scheduled ingestion does not itself approve a source change. Missing or refused inputs are disclosed rather than silently invented.

Historical cash FCF is calculated as operating cash flow minus cash CapEx. Yahoo history uses annual statement periods; SEC history uses trailing twelve-month periods. Adjacent SEC periods overlap and must not be added together. This reported cash-flow measure differs from the model's projected unlevered FCFF.

The public workspace uses a five-year maturation forecast: two near-term years followed by three maturation years. Growth above the policy target fades during maturation; weak or negative growth is not replaced with an assumed recovery. Terminal growth is a separate perpetuity assumption. Bear/Base/Bull re-project the same stage timing with their own assumptions. Quality cautions, invalid scenarios, negative modeled equity and WACC limits remain visible. Peer comparisons require a compatible forecast-policy snapshot.

Historical statement values and market observations are provider data. Forecasts and intrinsic values are conditional model estimates. A complete API response does not establish that every issuer line or financial assumption has been independently reconciled.

Archived research cases remain available at their direct URLs and retain their own disclosures. They are separate from live valuation results. The MSFT study is no longer promoted in workspace navigation.

## Architecture

- **Frontend:** Next.js App Router, React and TypeScript in `frontend/`. The dark workspace uses shared app styles, SVG financial charts and existing analytical components.
- **Valuation service:** FastAPI in `src/api/main.py`, backed by the Python DCF, scenario, sensitivity and data-selection modules. Next.js calls it server-to-server; the service token stays on the server.
- **Fundamentals:** point-in-time SEC extraction, fiscal classification, immutable ingestion lineage and PostgreSQL storage in `src/fundamentals/`.
- **Supporting modules:** historical backtesting, risk analysis and Alpaca paper execution remain in the repository. They are separate from running a public single-company valuation. Account APIs remain session-protected; account pages are absent from workspace navigation.
- **Deployment:** separate Vercel frontend and Python API projects. `frontend/vercel.json` configures Next.js; root `vercel.json` and `pyproject.toml` configure the Python service. The deployed API uses its own trimmed dependencies and Python runtime declaration.

## Local setup

Use Python 3.11 for the development/test environment and Node.js 24 for the frontend, matching CI. The Vercel Python deployment uses the runtime specified in `pyproject.toml`. Run these commands from the repository root unless indicated otherwise.

### Python service

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Replace the example values before starting the service. Set a non-placeholder `VALUATION_API_TOKEN` of at least 32 characters; the frontend must use the same token. Database and paper-account settings are needed only for the corresponding integrations. For an unconnected local public-model preview, leave `DATABASE_URL` and broker credentials empty.

```bash
uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

### Next.js frontend

In a second terminal:

```bash
cd frontend
npm ci
cp .env.local.example .env.local
```

Configure `VALUATION_API_URL=http://127.0.0.1:8000`, the matching `VALUATION_API_TOKEN`, and a non-placeholder `SESSION_SECRET` of at least 32 characters. `DASHBOARD_PASSWORD` applies to protected operator pages. The public workspace does not require signing in. Next.js loads its configuration from `frontend/.env.local`; Python uses the root `.env`. Real environment files are gitignored.

```bash
npm run dev -- --hostname 127.0.0.1
```

Open `http://127.0.0.1:3000/workspace`. Optional `LOCAL_PASSWORDLESS=1` opens account page shells only in loopback development and blocks their private APIs before handlers. It has no effect in production.

Production requires configured rate limiting for public evaluations and login; see `frontend/.env.local.example`. Upstash caching is optional, but production rate-limit failures are handled separately. Secrets remain server-only.

## Checks

```bash
# Repository root, with the Python environment activated
python -m pytest -q

# frontend/
npm test
npx tsc --noEmit
npm run lint
npm run build
```

Tests isolate external accounts and services. Synthetic PostgreSQL integration is a separate CI check. Passing tests and a build establish software checks, not financial-model accuracy, research validity or investment performance.

## Further reading

- [DCF specification](docs/model-specifications/dcf.md)
- [SEC comparison policy](docs/model-specifications/sec-dcf-integration.md)
- [SEC ingestion operations](docs/model-specifications/sec-ingestion-operations.md)
- [Assumptions register](docs/assumptions-register.md)
- [Limitations register](docs/limitations-register.md)

## Disclaimer

This project is for educational purposes only. Model outputs depend on source completeness and explicit assumptions and are not financial advice or a recommendation to buy or sell securities. The execution modules are restricted to Alpaca paper trading; public valuation requests do not submit orders.
