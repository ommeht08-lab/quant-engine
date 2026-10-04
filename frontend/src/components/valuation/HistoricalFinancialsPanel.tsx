"use client";
import { useState } from "react";
import { chartDomain } from "@/lib/cash-flow-chart";
import { historicalMetrics, type HistoricalFinancials, type HistoricalMetric } from "@/lib/historical-financials";
import ScrollHintTable from "./ScrollHintTable";

export default function HistoricalFinancialsPanel({history,source}:{history:HistoricalFinancials|null;source:"sec"|"yahoo"}) {
  const [metric,setMetric] = useState<HistoricalMetric>("free_cash_flow");
  const rows=history?.periods ?? [];
  const isTTM=history?.period_basis==="trailing_twelve_months" || (source==="sec" && history?.period_basis===undefined);
  const periodLabel=isTTM?"Trailing twelve-month":"Annual";
  const label=historicalMetrics.find(([key])=>key===metric)![1];
  const values=rows.map(row=>row[metric]).filter((v):v is number=>v!==null);
  const {min,max}=chartDomain(values.length ? values : [0]);
  const y=(value:number)=>24+(max-value)/(max-min)*220;
  const format=(value:number|null)=>value===null?"Not reported":new Intl.NumberFormat("en-US",{notation:"compact",maximumFractionDigits:1}).format(value);
  return <section className="panel model-chart-panel historical-panel">
    <div className="model-panel-heading"><div><h2>Reported financial history</h2><p>{source==="sec"?"SEC filings":"Yahoo statements"} · {periodLabel} periods · {history?.currency ? `${history.currency}, compact amounts` : "Statement currency unavailable; amounts as supplied"}</p></div><label className="model-year-control">Chart<select value={metric} onChange={e=>setMetric(e.target.value as HistoricalMetric)}>{historicalMetrics.map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label></div>
    {rows.length ? <>
      {values.length ? <div className="model-chart-scroll" tabIndex={0} role="region" aria-label={`${label} historical chart; scroll horizontally on small screens`}><svg className="model-annual-chart" viewBox="0 0 760 310" role="img" aria-label={`${label} by reported fiscal period. Exact values are in the table below.`}>
        {[min,0,max].filter((v,i,a)=>a.indexOf(v)===i).map(tick=><g key={tick}><line x1="75" x2="746" y1={y(tick)} y2={y(tick)} className={tick===0?"chart-zero":"chart-gridline"}/><text x="66" y={y(tick)+5} textAnchor="end">{format(tick)}</text></g>)}
        {rows.map((row,index)=>{const value=row[metric];const x=90+index*(640/rows.length);const width=Math.min(68,450/rows.length);return <g key={row.period_end}>{value===null?<text x={x+width/2} y={y(0)-12} textAnchor="middle">N/A</text>:<><rect x={x} y={Math.min(y(value),y(0))} width={width} height={Math.max(value===0?1:0,Math.abs(y(value)-y(0)))} rx="3" fill="#6172f3"/><text className="chart-value" x={x+width/2} y={value>=0?y(value)-10:y(value)+20} textAnchor="middle">{format(value)}</text></>}<text x={x+width/2} y="281" textAnchor="middle">{row.period_end}</text></g>})}
      </svg></div> : <p>{label} is unavailable for these reported periods. Choose another metric or inspect the table.</p>}
      <ScrollHintTable><table className="data-table historical-table"><caption>Reported {periodLabel.toLowerCase()} inputs and calculated cash FCF · full amounts</caption><thead><tr><th scope="col">Fiscal period end</th>{historicalMetrics.map(([key,name])=><th scope="col" key={key}>{name}</th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.period_end}><th scope="row">{row.period_end}</th>{historicalMetrics.map(([key])=><td key={key}>{row[key]===null?"Not reported":new Intl.NumberFormat("en-US",{maximumFractionDigits:2}).format(row[key]!)}</td>)}</tr>)}</tbody></table></ScrollHintTable>
      <p>{isTTM && "Each period covers the preceding twelve months; consecutive periods overlap and must not be added together. "}Historical FCF is calculated as operating cash flow minus cash CapEx. Missing amounts remain unavailable. These reported cash flows use a different definition from the model’s projected unlevered FCF.</p>
    </>:<p>No statement history was returned by the selected source. Historical values have not been substituted with forecasts.</p>}
  </section>;
}
