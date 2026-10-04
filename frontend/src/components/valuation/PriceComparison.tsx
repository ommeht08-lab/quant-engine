import {chartDomain} from "@/lib/cash-flow-chart";
import {computeMarketSpread,formatMarketSpread} from "@/lib/market-spread";
import type {ValuationQuality} from "@/lib/valuation-quality";
import type {ScenarioResult} from "./ValuationSpectrum";
import {formatPreciseCurrency} from "./format";
export default function PriceComparison({marketPrice,scenario,quality}:{marketPrice:number|null;scenario:ScenarioResult;quality:ValuationQuality}){
 const value=scenario.is_valid?scenario.intrinsic_value_per_share:null;
 const allowed=quality.allows_market_comparison && marketPrice!==null && Number.isFinite(marketPrice) && marketPrice>0 && value!==null && Number.isFinite(value) && value>=0;
 if(!allowed) return <section className="panel model-chart-panel"><h2>Market price &amp; intrinsic estimate</h2><p>{!quality.allows_market_comparison?'Market comparison is withheld by valuation-quality cautions.':value===null?'This scenario is not computable.':value<0?'Negative modeled equity is a distress diagnostic; no price comparison is plotted.':'An observed market price is unavailable.'}</p></section>;
 const max=chartDomain([marketPrice!,value!]).max;
 const spread=computeMarketSpread({value:value!,marketPrice:marketPrice!});
 return <section className="panel model-chart-panel"><h2>Market price &amp; intrinsic estimate</h2><p>{scenario.name[0].toUpperCase()+scenario.name.slice(1)} case · USD per share · Both bars share a zero baseline</p><div className="model-price-chart" role="img" aria-label={`Market price ${formatPreciseCurrency(marketPrice)}; intrinsic estimate ${formatPreciseCurrency(value)}`}>
  {[["Market price",marketPrice,"market"],["Intrinsic estimate",value,"estimate"]].map(([label,v,tone])=><div className="model-price-row" key={String(label)}><div><span>{label}</span><strong>{formatPreciseCurrency(v as number)}</strong></div><div className="model-price-track"><div className={`model-price-bar ${tone}`} style={{width:`${(v as number)/max*100}%`}} /></div></div>)}
  <div className="model-price-axis"><span>$0</span><span>{formatPreciseCurrency(max/2)}</span><span>{formatPreciseCurrency(max)}</span></div></div><p>{formatMarketSpread(spread).accessible}. A model estimate is not a price target.</p></section>;
}
