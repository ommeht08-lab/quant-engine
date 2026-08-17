#!/usr/bin/env python3
"""
shadow_calc.py -- Independent Python re-implementation of the DCF/WACC formulas
described in docs/model-specifications/dcf.md and docs/model-specifications/wacc-capm.md.

Purpose: (1) produce the cached <v> values embedded next to each Excel <f> formula
so the workbook shows sensible numbers even before a spreadsheet engine recalculates
it (no LibreOffice/Excel/Numbers is available in this environment); (2) provide an
independent sanity/direction check on the workbook's own formula logic.

This module was written directly from the two specification documents' prose and
equations -- NOT from reading src/dcf_model/dcf.py, and it never imports from this
repository's src/ tree. It is deliberately a *second* implementation: differences in
implementation detail (e.g. loop vs. closed-form) from the real codebase are expected
and fine, since the two are never compared against each other in this session (that
comparison is explicitly out of scope -- see docs/independent-validation-plan.md).
"""

RISK_FREE_RATE = 0.04
MARKET_RISK_PREMIUM = 0.055
PROJECTION_YEARS = 5
TERMINAL_GROWTH = 0.025
DA_PCT_REVENUE = 0.03
CAPEX_PCT_REVENUE = 0.04
NWC_PCT_REVENUE_CHANGE = 0.01

MAX_REVENUE_GROWTH_RATE = 0.25   # capped above only, per A-002
DEFAULT_REVENUE_GROWTH_FALLBACK = 0.08
DEFAULT_OPERATING_MARGIN_FALLBACK = 0.15
DEFAULT_TAX_RATE = 0.21
DEFAULT_COST_OF_DEBT = 0.05
DEFAULT_BETA = 1.0
WACC_MIN, WACC_MAX = 0.05, 0.20
TERM_GROWTH_MIN, TERM_GROWTH_MAX = 0.0, 0.05


def historical_revenue_cagr(history):
    """history: list of dicts sorted ascending by period, each with revenue_raw_usd.
    CAGR = (Revenue_latest / Revenue_earliest) ** (1/years_elapsed) - 1
    Capped at 25% above; NOT floored below (A-002)."""
    if len(history) < 2:
        return None, None
    rev0 = history[0]["revenue_raw_usd"]
    revN = history[-1]["revenue_raw_usd"]
    years = len(history) - 1
    if rev0 <= 0 or revN <= 0:
        return None, None
    raw = (revN / rev0) ** (1.0 / years) - 1.0
    capped = min(raw, MAX_REVENUE_GROWTH_RATE)
    return raw, capped


def historical_average_operating_margin(history):
    margins = [h["ebit_raw_usd"] / h["revenue_raw_usd"] for h in history if h["revenue_raw_usd"] > 0]
    if not margins:
        return None
    return sum(margins) / len(margins)


def derive_tax_rate(pretax_income, tax_provision):
    """tax_rate = tax_provision / pretax_income, must be in [0,1); else fallback 21% (flagged)."""
    if pretax_income is None or tax_provision is None or pretax_income == 0:
        return DEFAULT_TAX_RATE, "fallback:missing_or_zero_pretax"
    rate = tax_provision / pretax_income
    if not (0 <= rate < 1):
        return DEFAULT_TAX_RATE, f"fallback:out_of_range_raw={rate:.4f}"
    return rate, "derived"


def derive_cost_of_debt(interest_expense, total_debt):
    if interest_expense is None or total_debt is None or total_debt == 0:
        return DEFAULT_COST_OF_DEBT, "fallback:missing_or_zero_debt"
    rate = abs(interest_expense) / total_debt
    if rate < 0:
        return DEFAULT_COST_OF_DEBT, "fallback:negative"
    return rate, "derived"


def calculate_wacc(current_price, shares_outstanding, beta, total_debt, cost_of_debt, tax_rate,
                    risk_free_rate=RISK_FREE_RATE, market_risk_premium=MARKET_RISK_PREMIUM):
    market_cap = current_price * shares_outstanding
    total_capital = market_cap + total_debt
    if total_capital <= 0:
        raise ValueError("total capital must be positive")
    cost_of_equity = risk_free_rate + beta * market_risk_premium
    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)
    we = market_cap / total_capital
    wd = total_debt / total_capital
    raw_wacc = we * cost_of_equity + wd * after_tax_cost_of_debt
    final_wacc = min(max(raw_wacc, WACC_MIN), WACC_MAX)
    return {
        "market_cap": market_cap, "total_capital": total_capital,
        "cost_of_equity": cost_of_equity, "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "we": we, "wd": wd, "raw_wacc": raw_wacc, "final_wacc": final_wacc,
    }


def project_fcf(base_revenue, revenue_growth_rate, operating_margin, tax_rate,
                 da_pct=DA_PCT_REVENUE, capex_pct=CAPEX_PCT_REVENUE, nwc_pct=NWC_PCT_REVENUE_CHANGE,
                 years=PROJECTION_YEARS):
    rows = []
    prev_revenue = base_revenue
    for t in range(1, years + 1):
        revenue = prev_revenue * (1 + revenue_growth_rate)
        ebit = revenue * operating_margin
        nopat = ebit * (1 - tax_rate)
        da = revenue * da_pct
        capex = revenue * capex_pct
        d_nwc = nwc_pct * (revenue - prev_revenue)
        fcf = nopat + da - capex - d_nwc
        rows.append({"year": t, "revenue": revenue, "ebit": ebit, "nopat": nopat,
                      "da": da, "capex": capex, "d_nwc": d_nwc, "fcf": fcf})
        prev_revenue = revenue
    return rows


def terminal_value(final_year_fcf, wacc, terminal_growth=TERMINAL_GROWTH):
    if wacc <= terminal_growth:
        raise ValueError("WACC must exceed terminal growth rate")
    return final_year_fcf * (1 + terminal_growth) / (wacc - terminal_growth)


def discount_and_bridge(fcf_rows, tv, wacc, total_debt, cash, shares_outstanding, current_price):
    pv_fcf = [row["fcf"] / (1 + wacc) ** row["year"] for row in fcf_rows]
    n = len(fcf_rows)
    pv_tv = tv / (1 + wacc) ** n
    enterprise_value = sum(pv_fcf) + pv_tv
    equity_value = enterprise_value - total_debt + cash
    intrinsic_value_per_share = equity_value / shares_outstanding
    price_to_iv = current_price / intrinsic_value_per_share if intrinsic_value_per_share else None
    return {
        "pv_fcf": pv_fcf, "pv_tv": pv_tv, "enterprise_value": enterprise_value,
        "equity_value": equity_value, "intrinsic_value_per_share": intrinsic_value_per_share,
        "price_to_intrinsic_value": price_to_iv,
    }


def run_full_dcf(snapshot):
    """Run the full independent DCF using a company snapshot dict (as produced by
    build_snapshots.py). Returns a dict of every intermediate + final value, plus
    the derivation-method notes needed for prominent fallback disclosure."""
    hist = snapshot["historical_annual_data"]
    lyf = snapshot["latest_year_facts"]
    mkt = snapshot["market_data"]

    raw_cagr, capped_cagr = historical_revenue_cagr(hist)
    avg_margin = historical_average_operating_margin(hist)
    tax_rate, tax_method = derive_tax_rate(lyf["pretax_income_usd"], lyf["tax_provision_usd"])
    cod, cod_method = derive_cost_of_debt(lyf["interest_expense_usd"], lyf["total_debt_usd"])

    beta = mkt["beta_levered_equity"] if mkt["beta_levered_equity"] is not None else DEFAULT_BETA
    wacc_result = calculate_wacc(
        current_price=mkt["current_price_usd_per_share"],
        shares_outstanding=mkt["shares_outstanding"],
        beta=beta,
        total_debt=lyf["total_debt_usd"],
        cost_of_debt=cod,
        tax_rate=tax_rate,
    )

    base_revenue = hist[-1]["revenue_raw_usd"]
    fcf_rows = project_fcf(base_revenue, capped_cagr, avg_margin, tax_rate)
    tv = terminal_value(fcf_rows[-1]["fcf"], wacc_result["final_wacc"])
    bridge = discount_and_bridge(
        fcf_rows, tv, wacc_result["final_wacc"],
        total_debt=lyf["total_debt_usd"], cash=lyf["cash_and_equivalents_usd"],
        shares_outstanding=mkt["shares_outstanding"], current_price=mkt["current_price_usd_per_share"],
    )

    return {
        "ticker": snapshot["ticker"],
        "raw_cagr": raw_cagr, "capped_cagr": capped_cagr,
        "avg_operating_margin": avg_margin,
        "tax_rate": tax_rate, "tax_rate_method": tax_method,
        "cost_of_debt": cod, "cost_of_debt_method": cod_method,
        "beta_used": beta,
        "wacc": wacc_result,
        "fcf_rows": fcf_rows,
        "terminal_value": tv,
        "bridge": bridge,
        "base_revenue": base_revenue,
    }


# ---------------- Sensitivity helpers ----------------

def sensitivity_wacc(fcf_rows, wacc_grid, total_debt, cash, shares, terminal_growth=TERMINAL_GROWTH):
    out = []
    for w in wacc_grid:
        if w <= terminal_growth:
            out.append({"wacc": w, "ivps": None, "note": "invalid: WACC<=g"})
            continue
        tv = terminal_value(fcf_rows[-1]["fcf"], w, terminal_growth)
        bridge = discount_and_bridge(fcf_rows, tv, w, total_debt, cash, shares, current_price=0)
        out.append({"wacc": w, "ivps": bridge["intrinsic_value_per_share"]})
    return out


def sensitivity_terminal_growth(fcf_rows, g_grid, wacc, total_debt, cash, shares):
    out = []
    for g in g_grid:
        if wacc <= g:
            out.append({"g": g, "ivps": None, "note": "invalid: g>=WACC"})
            continue
        tv = terminal_value(fcf_rows[-1]["fcf"], wacc, g)
        bridge = discount_and_bridge(fcf_rows, tv, wacc, total_debt, cash, shares, current_price=0)
        out.append({"g": g, "ivps": bridge["intrinsic_value_per_share"]})
    return out


def sensitivity_revenue_growth(base_revenue, g_grid, margin, tax_rate, wacc, total_debt, cash, shares,
                                terminal_growth=TERMINAL_GROWTH):
    out = []
    for g in g_grid:
        rows = project_fcf(base_revenue, g, margin, tax_rate)
        tv = terminal_value(rows[-1]["fcf"], wacc, terminal_growth)
        bridge = discount_and_bridge(rows, tv, wacc, total_debt, cash, shares, current_price=0)
        out.append({"g": g, "ivps": bridge["intrinsic_value_per_share"]})
    return out


def sensitivity_operating_margin(base_revenue, margin_grid, growth, tax_rate, wacc, total_debt, cash, shares,
                                  terminal_growth=TERMINAL_GROWTH):
    out = []
    for m in margin_grid:
        rows = project_fcf(base_revenue, growth, m, tax_rate)
        tv = terminal_value(rows[-1]["fcf"], wacc, terminal_growth)
        bridge = discount_and_bridge(rows, tv, wacc, total_debt, cash, shares, current_price=0)
        out.append({"margin": m, "ivps": bridge["intrinsic_value_per_share"]})
    return out


if __name__ == "__main__":
    import json, glob
    for path in sorted(glob.glob("snapshots/*_snapshot.json")):
        snap = json.load(open(path))
        result = run_full_dcf(snap)
        print(f"=== {result['ticker']} ===")
        print(f"  raw CAGR={result['raw_cagr']:.4%} capped={result['capped_cagr']:.4%}")
        print(f"  avg margin={result['avg_operating_margin']:.4%}")
        print(f"  tax_rate={result['tax_rate']:.4%} ({result['tax_rate_method']})")
        print(f"  cost_of_debt={result['cost_of_debt']:.4%} ({result['cost_of_debt_method']})")
        print(f"  beta={result['beta_used']}")
        w = result['wacc']
        print(f"  raw WACC={w['raw_wacc']:.4%} final WACC={w['final_wacc']:.4%}")
        print(f"  We={w['we']:.4%} Wd={w['wd']:.4%} Re={w['cost_of_equity']:.4%} Rd_at={w['after_tax_cost_of_debt']:.4%}")
        for row in result['fcf_rows']:
            print(f"  Y{row['year']}: Rev={row['revenue']:,.0f} EBIT={row['ebit']:,.0f} FCF={row['fcf']:,.0f}")
        print(f"  Terminal Value={result['terminal_value']:,.0f}")
        b = result['bridge']
        print(f"  PV(FCF) sum={sum(b['pv_fcf']):,.0f} PV(TV)={b['pv_tv']:,.0f}")
        print(f"  Enterprise Value={b['enterprise_value']:,.0f}")
        print(f"  Equity Value={b['equity_value']:,.0f}")
        print(f"  Intrinsic Value/Share=${b['intrinsic_value_per_share']:,.2f}")
        print(f"  Price/IV={b['price_to_intrinsic_value']:.3f}")
        print()
