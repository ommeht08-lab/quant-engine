import assert from "node:assert/strict";
import test from "node:test";
import { clearSavedWorkspace, isWorkspaceResult, readSavedWorkspace, writeSavedWorkspace, SAVED_WORKSPACE_KEY } from "./workspace-storage.ts";
import type { EvaluationResponse } from "./workspace-result";
import { isHistoricalFinancials } from "./historical-financials.ts";

const row = {year:1,revenue:100,ebit:20,nopat:15,da:2,capex:4,change_in_nwc:1,fcf:12};
const scenario = {is_valid:true,intrinsic_value_per_share:40,implies_negative_equity_value:false,invalid_reason:null,assumptions:{wacc:.1,revenue_growth_rate:.08,operating_margin:.2,terminal_growth_rate:.025}};
const result = {
  ticker:"TEST",forecast_method:"maturation",projected_free_cash_flows:[row],forecast_path:[{year:1,stage:"near_term",revenue_growth_rate:.08,operating_margin:.2}],
  wacc:.1,wacc_pre_clamp:.1,wacc_was_clamped:false,implies_negative_equity_value:false,price_to_intrinsic_value:null,sector_median_p_iv:null,sector_median_unavailable_code:"snapshot_unavailable",sector_median_unavailable_reason:null,sector_median_snapshot:null,enterprise_value:100,equity_value:90,intrinsic_value_per_share:40,current_price:30,
  assumptions:{revenue_growth_rate:.08,operating_margin:.2,terminal_growth_rate:.025,projection_years:1},revenue_growth_rate_source:"historical",operating_margin_source:"historical",sector:"Technology",
  valuation_quality:{level:"ordinary",codes:[],allows_market_comparison:true,terminal_value_share_of_enterprise_value:.5,observed_effective_tax_rate:.25},valuation_input_provenance:{source:"yahoo",statement_period_end:"2024-12-31",source_selection_reason:"test",knowledge_cutoff:"2026-10-04T01:00:00Z",policy_version:"test",ingestion_batch_ids:[]},
  sensitivity:{cells:[[40]],wacc_axis:{values:[.1],label:"WACC",baseline_index:0},terminal_growth_axis:{values:[.025],label:"Growth",baseline_index:0},baseline_row:0,baseline_col:0,baseline_wacc:.1,baseline_terminal_growth_rate:.025,baseline_intrinsic_value_per_share:40},scenarios:{base:{...scenario,name:"base"},bear:{...scenario,name:"bear"},bull:{...scenario,name:"bull"}},
} as unknown as EvaluationResponse;
function memoryStorage() {
  const data=new Map<string,string>();
  return {getItem:(key:string)=>data.get(key)??null,setItem:(key:string,value:string)=>{data.set(key,value)},removeItem:(key:string)=>{data.delete(key)}};
}
test("a completed result restores its original date and selected scenario; clear removes it",()=>{
  const storage=memoryStorage(); const saved={result,marketHistory:null,selectedScenario:"bear" as const,savedAt:"2026-10-04T01:00:00Z"};
  assert.equal(writeSavedWorkspace(saved,storage),true);
  assert.deepEqual(readSavedWorkspace(storage),saved);
  clearSavedWorkspace(storage); assert.equal(readSavedWorkspace(storage),null);
});
test("invalid or incompatible saved model data is refused without throwing",()=>{
  const storage=memoryStorage();
  for(const value of ["broken",JSON.stringify({version:2,result}),JSON.stringify({version:1,result:{...result,scenarios:null},selectedScenario:"base",savedAt:"2026-10-04"})]){
    storage.setItem(SAVED_WORKSPACE_KEY,value);assert.equal(readSavedWorkspace(storage),null);
  }
  assert.equal(isWorkspaceResult({...result,projected_free_cash_flows:[{...row,fcf:Infinity}]}),false);
});
test("disabled storage leaves the displayed run usable",()=>{
  const storage={getItem:()=>{throw Error("denied")},setItem:()=>{throw Error("quota")},removeItem:()=>{throw Error("denied")}};
  assert.equal(writeSavedWorkspace({result,marketHistory:null,selectedScenario:"base",savedAt:"2026-10-04"},storage),false);
  assert.equal(readSavedWorkspace(storage),null);assert.doesNotThrow(()=>clearSavedWorkspace(storage));
});
test("historical FCF must reconcile; missing values stay null",()=>{
  const history={currency:"USD",periods:[{period_end:"2024-12-31",revenue:null,operating_cash_flow:20,capital_expenditures:30,free_cash_flow:-10}]};
  assert.equal(isHistoricalFinancials(history),true);
  assert.equal(isHistoricalFinancials({...history,periods:[{...history.periods[0],free_cash_flow:10}]}),false);
  assert.equal(isHistoricalFinancials({...history,periods:[{...history.periods[0],operating_cash_flow:null}]}),false);
});

test("nested saved-data corruption cannot reach analytical pages",()=>{
  const invalid=[
    {...result,price_to_intrinsic_value:undefined},
    {...result,sector_median_p_iv:undefined},
    {...result,sector_median_snapshot:{generated_at:"now"}},
    {...result,valuation_quality:{...result.valuation_quality,codes:[{}]}},
    {...result,valuation_input_provenance:{...result.valuation_input_provenance,policy_version:undefined}},
    {...result,scenarios:{...result.scenarios,base:{...result.scenarios.base,assumptions:null}}},
    {...result,sensitivity:{...result.sensitivity,cells:[null]}},
    {...result,sensitivity:{...result.sensitivity,wacc_axis:null}},
  ];
  const storage=memoryStorage();
  for(const candidate of invalid){
    assert.doesNotThrow(()=>isWorkspaceResult(candidate));
    assert.equal(isWorkspaceResult(candidate),false);
    storage.setItem(SAVED_WORKSPACE_KEY,JSON.stringify({version:1,result:candidate,selectedScenario:"base",savedAt:"2026-10-04"}));
    assert.equal(readSavedWorkspace(storage),null);
  }
});
