"""Read-only production comparison. Run only after frozen-results.json is sealed."""
import json
import math
from pathlib import Path

from src.dcf_model.dcf import (
    MultiStageForecastPolicy, calculate_terminal_value, calculate_wacc,
    discount_to_present_value, project_free_cash_flows_from_path,
)
from src.dcf_model.scenarios import ScenarioInputs, compute_dcf_scenarios

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "validation/independent_dcf"
frozen = json.loads((BASE / "staged_v3/frozen-results.json").read_text())

def close(a, b, kind):
    # Rates: 1e-10 abs. Money: $0.01 or 1e-10 relative. Share price: $0.01.
    tol = 1e-10 if kind == "rate" else .01 if kind == "share" else max(.01, 1e-10 * abs(a))
    return abs(a - b) <= tol, a - b, tol

all_rows=[]
scenario_differences=[]
for ticker, entry in frozen["companies"].items():
    if ticker == "NEGATIVE":
        years=[(1000+50*i)*1e6 for i in range(5)]
        debt,cash,price,shares,beta,interest,pretax,taxprovision=100e6,50e6,50,100e6,1,5e6,220e6,55e6
    else:
        snap=json.loads((BASE / f"snapshots/{ticker}_snapshot.json").read_text())
        years=[v["revenue_raw_usd"] for v in snap["historical_annual_data"]]
        fact, market=snap["latest_year_facts"],snap["market_data"]
        debt,cash,price,shares,beta,interest,pretax,taxprovision=(fact["total_debt_usd"],fact["cash_and_equivalents_usd"],market["current_price_usd_per_share"],market["shares_outstanding"],market["beta_levered_equity"],fact["interest_expense_usd"],fact["pretax_income_usd"],fact["tax_provision_usd"])
    tax=taxprovision/pretax if pretax>0 else .21
    wd=abs(interest)/debt if debt>0 else .05
    wacc=calculate_wacc(price,shares,debt,beta,risk_free_rate=.04,market_risk_premium=.055,cost_of_debt=wd,tax_rate=tax)
    path=MultiStageForecastPolicy().build_path(entry["inputs"]["growth"],entry["inputs"]["margin"],5)
    cashflows=project_free_cash_flows_from_path(years[-1],path,tax_rate=tax,da_pct_revenue=.03,capex_pct_revenue=.04,nwc_pct_revenue_change=.01)
    tv=calculate_terminal_value(float(cashflows.fcf.iloc[-1]),wacc,.025)
    discounted=discount_to_present_value(cashflows,tv,wacc)
    direct={
      "inputs.wacc":wacc,
      **{f"base.growth.{i+1}":step.revenue_growth_rate for i,step in enumerate(path)},
      **{f"base.margin.{i+1}":step.operating_margin for i,step in enumerate(path)},
      **{f"base.revenue.{i+1}":float(cashflows.revenue.iloc[i]) for i in range(5)},
      **{f"base.fcf.{i+1}":float(cashflows.fcf.iloc[i]) for i in range(5)},
      "base.terminalValue":tv,"base.pvTerminal":float(discounted["pv_terminal_value"]),
      "base.pvAnnual":float(discounted["pv_fcf"].sum()),"base.enterprise":float(discounted["enterprise_value"]),
    }
    direct["base.equity"]=direct["base.enterprise"]-debt+cash
    direct["base.perShare"]=direct["base.equity"]/shares
    direct["base.terminalShare"]=direct["base.pvTerminal"]/direct["base.enterprise"] if direct["base.enterprise"]>0 else "N/A: EV<=0"
    scenarios=compute_dcf_scenarios(ScenarioInputs(
        base_revenue=years[-1],baseline_revenue_growth_rate=entry['inputs']['growth'],
        baseline_operating_margin=entry['inputs']['margin'],baseline_wacc=wacc,
        baseline_terminal_growth_rate=.025,tax_rate=tax,da_pct_revenue=.03,
        capex_pct_revenue=.04,nwc_pct_revenue_change=.01,projection_years=5,
        total_debt=debt,cash_and_equivalents=cash,shares_outstanding=shares,
        baseline_forecast_path=path,
    ))
    for name in ('bear','bull'):
        prod=getattr(scenarios,name).intrinsic_value_per_share
        scenario_differences.append(dict(ticker=ticker,scenario=name,validator_per_share=entry[name],production_per_share=prod,delta_validator_minus_production=entry[name]-prod if prod is not None else None,reason='Written specification omits numeric scenario shifts; validator ±2pp growth/±1pp margin versus production ±3pp/±2pp'))
    for key,observed in direct.items():
        node=entry
        for part in key.split('.'):
            node=node[int(part)-1] if part.isdigit() else node[part]
        if isinstance(node,str):ok,diff,tol=(node==observed,None,None)
        else:ok,diff,tol=close(node,observed,'share' if key.endswith('perShare') else 'rate' if 'growth' in key or 'margin' in key or 'wacc' in key or key.endswith('terminalShare') else 'money')
        all_rows.append(dict(ticker=ticker,metric=key,workbook=node,production=observed,delta=diff,tolerance=tol,pass_=ok))

fails=[r for r in all_rows if not r['pass_']]
report={"frozen_source":str(BASE / 'staged_v3/frozen-results.json'),"comparisons":len(all_rows),"failures":len(fails),"max_abs_delta":max((abs(r['delta']) for r in all_rows if r['delta'] is not None),default=0),"failed_rows":fails,"scenario_differences_not_failures":scenario_differences,"tolerances":"rate 1e-10 absolute; money max($0.01,1e-10 x absolute workbook dollars); per share $0.01"}
print(json.dumps(report,indent=2))
if fails:raise SystemExit(1)
