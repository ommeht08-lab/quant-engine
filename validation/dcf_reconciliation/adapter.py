"""
Frozen-snapshot -> financial_data adapter for Track A Phase 2B reconciliation.

Deterministically converts each committed JSON snapshot in
validation/independent_dcf/snapshots/*.json into the `financial_data` dict
shape expected by `src.dcf_model.dcf.run_dcf_valuation` -- and nothing else.
No network access, no calls to fetch_company_financials, no import of
src/data_ingestion/fetch_financials.py.

Every value placed into the constructed `income_statement` / `balance_sheet`
DataFrames or the top-level market-data keys is asserted equal to its source
value in the frozen snapshot immediately after construction
(`assert_adapter_matches_snapshot`), so a transcription bug here cannot
silently feed the production DCF a different number than the one the
independent workbook also freezes.

Historical periods keep their real frozen fiscal_period_end dates (as
pandas Timestamps) rather than synthetic evenly-spaced dates, because
`calculate_historical_revenue_cagr` (src/dcf_model/dcf.py) divides by
actual elapsed calendar days between the earliest and latest period.
"""
import json
from pathlib import Path

import pandas as pd

SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[1] / "independent_dcf" / "snapshots"
)
TICKERS = ("MSFT", "CAT", "INTC", "VZ")

# Row labels used in the constructed income_statement -- chosen as the
# highest-priority candidate label `_get_row_value` (src/dcf_model/dcf.py)
# tries for each field, so the adapter's intent is unambiguous from the
# DataFrame alone.
ROW_REVENUE = "Total Revenue"
ROW_EBIT = "EBIT"
ROW_PRETAX_INCOME = "Pretax Income"
ROW_TAX_PROVISION = "Tax Provision"
ROW_INTEREST_EXPENSE = "Interest Expense Non Operating"
ROW_TOTAL_DEBT = "Total Debt"
ROW_CASH = "Cash And Cash Equivalents"


def load_snapshot(ticker: str) -> dict:
    path = SNAPSHOT_DIR / f"{ticker}_snapshot.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_financial_data(snapshot: dict) -> dict:
    """
    Construct the `financial_data` dict `run_dcf_valuation` expects, using
    only fields already present in the frozen snapshot. Raises no network
    calls and imports nothing from src/data_ingestion.
    """
    historical = snapshot["historical_annual_data"]
    latest = snapshot["latest_year_facts"]
    market = snapshot["market_data"]

    periods = [pd.Timestamp(row["fiscal_period_end"]) for row in historical]
    latest_period = pd.Timestamp(snapshot["latest_complete_fiscal_year_end"])
    if latest_period not in periods:
        raise ValueError(
            f"{snapshot['ticker']}: latest_complete_fiscal_year_end "
            f"{latest_period} not found among historical_annual_data periods {periods}."
        )

    revenue_row = {p: row["revenue_raw_usd"] for p, row in zip(periods, historical)}
    ebit_row = {p: row["ebit_raw_usd"] for p, row in zip(periods, historical)}

    # Pretax Income / Tax Provision / Interest Expense are frozen only for
    # the latest period (per the reconciliation-plan spec: `dcf.py`'s
    # extract_valuation_inputs reads these via the *default* -- i.e. most
    # recent -- column only). Other periods are left as NaN.
    pretax_row = {p: float("nan") for p in periods}
    pretax_row[latest_period] = latest["pretax_income_usd"]

    tax_provision_row = {p: float("nan") for p in periods}
    tax_provision_row[latest_period] = latest["tax_provision_usd"]

    interest_expense_row = {p: float("nan") for p in periods}
    interest_expense_row[latest_period] = latest["interest_expense_usd"]

    income_statement = pd.DataFrame(
        {
            ROW_REVENUE: revenue_row,
            ROW_EBIT: ebit_row,
            ROW_PRETAX_INCOME: pretax_row,
            ROW_TAX_PROVISION: tax_provision_row,
            ROW_INTEREST_EXPENSE: interest_expense_row,
        }
    ).T  # rows = line items, columns = fiscal periods

    balance_sheet = pd.DataFrame(
        {
            latest_period: {
                ROW_TOTAL_DEBT: latest["total_debt_usd"],
                ROW_CASH: latest["cash_and_equivalents_usd"],
            }
        }
    )

    # FCF yield is out of scope for this reconciliation (per task
    # instructions); the frozen snapshots carry no operating_cash_flow /
    # capital_expenditures fields, so cash_flow stays None -- never
    # fabricated.
    cash_flow = None

    return {
        "income_statement": income_statement,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "current_price": market["current_price_usd_per_share"],
        "shares_outstanding": market["shares_outstanding"],
        "beta": market["beta_levered_equity"],
        "ticker": snapshot["ticker"],
        "sector": market.get("sector_gics"),
    }


def assert_adapter_matches_snapshot(snapshot: dict, financial_data: dict) -> None:
    """
    Explicit, exhaustive assertions that every value the adapter placed
    into `financial_data` equals its source value in the frozen snapshot
    verbatim -- catches a transcription bug (wrong row, wrong period,
    wrong field) that unit tests exercising only `run_dcf_valuation`'s
    final output might not localize.
    """
    historical = snapshot["historical_annual_data"]
    latest = snapshot["latest_year_facts"]
    market = snapshot["market_data"]
    ticker = snapshot["ticker"]

    income_statement = financial_data["income_statement"]
    balance_sheet = financial_data["balance_sheet"]
    latest_period = pd.Timestamp(snapshot["latest_complete_fiscal_year_end"])

    for row in historical:
        p = pd.Timestamp(row["fiscal_period_end"])
        got_revenue = income_statement.loc[ROW_REVENUE, p]
        assert got_revenue == row["revenue_raw_usd"], (
            f"{ticker} {p}: adapter revenue {got_revenue!r} != "
            f"snapshot revenue_raw_usd {row['revenue_raw_usd']!r}"
        )
        got_ebit = income_statement.loc[ROW_EBIT, p]
        assert got_ebit == row["ebit_raw_usd"], (
            f"{ticker} {p}: adapter EBIT {got_ebit!r} != "
            f"snapshot ebit_raw_usd {row['ebit_raw_usd']!r}"
        )

    got_pretax = income_statement.loc[ROW_PRETAX_INCOME, latest_period]
    assert got_pretax == latest["pretax_income_usd"], (
        f"{ticker}: adapter pretax income {got_pretax!r} != "
        f"snapshot pretax_income_usd {latest['pretax_income_usd']!r}"
    )
    got_tax = income_statement.loc[ROW_TAX_PROVISION, latest_period]
    assert got_tax == latest["tax_provision_usd"], (
        f"{ticker}: adapter tax provision {got_tax!r} != "
        f"snapshot tax_provision_usd {latest['tax_provision_usd']!r}"
    )
    got_interest = income_statement.loc[ROW_INTEREST_EXPENSE, latest_period]
    assert got_interest == latest["interest_expense_usd"], (
        f"{ticker}: adapter interest expense {got_interest!r} != "
        f"snapshot interest_expense_usd {latest['interest_expense_usd']!r}"
    )

    got_total_debt = balance_sheet.loc[ROW_TOTAL_DEBT, latest_period]
    assert got_total_debt == latest["total_debt_usd"], (
        f"{ticker}: adapter total debt {got_total_debt!r} != "
        f"snapshot total_debt_usd {latest['total_debt_usd']!r}"
    )
    got_cash = balance_sheet.loc[ROW_CASH, latest_period]
    assert got_cash == latest["cash_and_equivalents_usd"], (
        f"{ticker}: adapter cash {got_cash!r} != "
        f"snapshot cash_and_equivalents_usd {latest['cash_and_equivalents_usd']!r}"
    )

    assert financial_data["current_price"] == market["current_price_usd_per_share"]
    assert financial_data["shares_outstanding"] == market["shares_outstanding"]
    assert financial_data["beta"] == market["beta_levered_equity"]
    assert financial_data["cash_flow"] is None
    assert financial_data["ticker"] == ticker


def load_all() -> dict:
    """Return {ticker: (snapshot_dict, financial_data_dict)} for every frozen company,
    each pair verified via assert_adapter_matches_snapshot before being returned."""
    out = {}
    for ticker in TICKERS:
        snapshot = load_snapshot(ticker)
        if snapshot["ticker"] != ticker:
            raise ValueError(f"Snapshot file for {ticker} has ticker field {snapshot['ticker']!r}.")
        financial_data = build_financial_data(snapshot)
        assert_adapter_matches_snapshot(snapshot, financial_data)
        out[ticker] = (snapshot, financial_data)
    return out


if __name__ == "__main__":
    for ticker, (snapshot, financial_data) in load_all().items():
        print(f"{ticker}: OK -- {len(financial_data['income_statement'].columns)} periods, "
              f"latest revenue {financial_data['income_statement'].loc[ROW_REVENUE].dropna().iloc[-1]:,.0f}")
