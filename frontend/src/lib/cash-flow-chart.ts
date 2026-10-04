export interface CashFlowInput { year:number; nopat:number; da:number; capex:number; change_in_nwc:number; fcf:number }
export function cashFlowBridge(row:CashFlowInput) {
  if (![row.year,row.nopat,row.da,row.capex,row.change_in_nwc,row.fcf].every(Number.isFinite)) throw new Error("Cash flow components are missing or non-finite.");
  const calculated=row.nopat+row.da-row.capex-row.change_in_nwc;
  const tolerance=Math.max(0.01,Math.abs(row.fcf)*1e-8);
  if(Math.abs(calculated-row.fcf)>tolerance) throw new Error("The reported cash flow components do not reconcile.");
  const inputs=[['NOPAT',row.nopat],['D&A',row.da],['CapEx',-row.capex],['Δ NWC',-row.change_in_nwc]] as const;
  let running=0;
  const steps=inputs.map(([label,value])=>{const start=running;running+=value;return {label,value,start,end:running,total:false};});
  return [...steps,{label:'FCF',value:row.fcf,start:0,end:row.fcf,total:true}];
}
export function chartDomain(values:number[]) {
  if(!values.length||values.some(v=>!Number.isFinite(v))) throw new Error("Chart values are missing or non-finite.");
  const min=Math.min(0,...values),max=Math.max(0,...values),extent=max-min||1;
  return {min:min<0?min-extent*.15:0,max:max>0?max+extent*.18:min===0?1:0};
}
