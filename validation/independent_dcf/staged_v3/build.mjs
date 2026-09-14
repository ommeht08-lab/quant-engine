import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = process.cwd();
const base = path.join(root, 'validation/independent_dcf');
const outDir = path.join(root, 'outputs/01a07d98-6e5e-73b3-b1ae-22f8ce21804a');
const previewDir = path.join('/private/tmp', 'staged-dcf-previews');
const manifest = JSON.parse(await fs.readFile(path.join(base,'snapshot_manifest.json'),'utf8'));
const named = ['MSFT','CAT','INTC','VZ'];
const snaps = [];
for (const ticker of named) {
  const file = path.join(base,'snapshots',`${ticker}_snapshot.json`);
  const bytes = await fs.readFile(file);
  const expected = manifest.files?.[`snapshots/${ticker}_snapshot.json`]?.sha256;
  if (!expected || crypto.createHash('sha256').update(bytes).digest('hex')!==expected) throw Error(`snapshot hash mismatch ${ticker}`);
  snaps.push(JSON.parse(bytes));
}
const synth = {
 ticker:'NEGATIVE', entity_name:'Synthetic persistent negative-margin issuer', reporting_currency:'USD',
 retrieval_timestamp_utc:'2026-09-13T00:00:00Z', latest_complete_fiscal_year_end:'2025-12-31',
 historical_annual_data:[2021,2022,2023,2024,2025].map((y,i)=>({fiscal_period_end:`${y}-12-31`,revenue_raw_usd:(1000+50*i)*1e6,ebit_raw_usd:-(1000+50*i)*2e5,revenue_filed_date:'2026-09-13',ebit_filed_date:'2026-09-13',revenue_source_url:'Synthetic fixture; no SEC filing',ebit_source_url:'Synthetic fixture; no SEC filing',revenue_xbrl_tag:'not applicable',ebit_xbrl_tag:'not applicable'})),
 latest_year_facts:{total_debt_usd:100e6,cash_and_equivalents_usd:50e6,interest_expense_usd:5e6,pretax_income_usd:220e6,tax_provision_usd:55e6,total_debt_rollup_filed_date_joined:'2026-09-13',cash_filed_date:'2026-09-13',interest_expense_filed_date:'2026-09-13',pretax_income_filed_date:'2026-09-13',tax_provision_filed_date:'2026-09-13'},
 market_data:{current_price_usd_per_share:50,shares_outstanding:100e6,beta_levered_equity:1,retrieved_at_utc:'2026-09-13T00:00:00Z',source:'Synthetic fixture; not observed market data'}
};
snaps.push(synth);
const wb=Workbook.create();
const summary=wb.worksheets.add('Scorecard');
const policy=wb.worksheets.add('Policy and limits');
for(const s of snaps) wb.worksheets.add(s.ticker);
const set=(sh,c,v)=>sh.getRange(c).values=[[v]];
const f=(sh,c,v)=>sh.getRange(c).formulas=[[v]];
const money='#,##0;[Red](#,##0);–';
const pct='0.00%;[Red](0.00%);–';
const fmt=(sh,r,form)=>sh.getRange(r).setNumberFormat(form);
function input(sh,row,label,value,unit,date,source,status,note=''){
 sh.getRange(`A${row}:G${row}`).values=[[label,value,unit,date,source,status,note]];
}
function dateOf(x){return (x??'unknown').slice(0,10)}
function createCompany(s){
 const sh=wb.worksheets.getItem(s.ticker); sh.showGridLines=false; sh.freezePanes.freezeRows(5);
 const y=s.latest_year_facts,m=s.market_data,synthetic=s.ticker==='NEGATIVE';
 set(sh,'A1',`${s.ticker} | independent staged DCF`);set(sh,'A2','Frozen evidence only • USD actual • scenarios are diagnostic, not price targets');
 sh.getRange('A5:L5').values=[['Fiscal end','Revenue USD','EBIT USD','EBIT margin','Revenue filed','EBIT filed','Revenue tag','EBIT tag','Revenue source','EBIT source','Revenue status','EBIT status / derivation']];
 for(let i=0;i<5;i++){
  const h=s.historical_annual_data[i],r=6+i;
  sh.getRange(`A${r}:C${r}`).values=[[h.fiscal_period_end,h.revenue_raw_usd,h.ebit_raw_usd]];
  f(sh,`D${r}`,`=C${r}/B${r}`);
  sh.getRange(`E${r}:J${r}`).values=[[h.revenue_filed_date,h.ebit_filed_date,h.revenue_xbrl_tag,h.ebit_xbrl_tag,h.revenue_source_url,h.ebit_source_url]];
  sh.getRange(`K${r}:L${r}`).values=[[synthetic?'SYNTHETIC; not reported':'Frozen SEC; no fallback',synthetic?'SYNTHETIC; not reported':h.ebit_derivation_note??'Frozen SEC; no fallback']];
 }
 sh.getRange('A12:G12').values=[['Input','Value','Unit','Source date','Source','Fallback / status','Note']];
 const sec=(key)=>synthetic?'Synthetic fixture; no reported SEC fact':(y[`${key}_source_url`]??y.total_debt_rollup_source_url_joined??'Frozen SEC snapshot');
 const st=synthetic?'SYNTHETIC; not reported':'Observed / frozen';
 input(sh,13,'Market price',m.current_price_usd_per_share,'USD/share',dateOf(m.retrieved_at_utc),m.source,st);
 input(sh,14,'Shares outstanding',m.shares_outstanding,'shares',dateOf(m.retrieved_at_utc),m.source,st);
 input(sh,15,'Levered beta',m.beta_levered_equity,'ratio',dateOf(m.retrieved_at_utc),m.source,st);
 input(sh,16,'Total debt',y.total_debt_usd,'USD',dateOf(y.total_debt_rollup_filed_date_joined??y.total_debt_reported_filed_date),sec('debt'),synthetic?'SYNTHETIC':y.total_debt_normalization_method??'reported',y.total_debt_normalization_equation??'');
 input(sh,17,'Cash and equivalents',y.cash_and_equivalents_usd,'USD',dateOf(y.cash_filed_date),sec('cash'),st);
 input(sh,18,'Interest expense',y.interest_expense_usd,'USD',dateOf(y.interest_expense_filed_date),sec('interest_expense'),synthetic?'SYNTHETIC':y.interest_expense_method??'reported');
 input(sh,19,'Pretax income',y.pretax_income_usd,'USD',dateOf(y.pretax_income_filed_date),sec('pretax_income'),st);
 input(sh,20,'Tax provision',y.tax_provision_usd,'USD',dateOf(y.tax_provision_filed_date),sec('tax_provision'),st);
 const fixed=[
  [21,'Risk-free rate',.04,'rate','Written WACC policy; static offline default','POLICY FALLBACK'],[22,'Market risk premium',.055,'rate','Written WACC policy','POLICY DEFAULT'],
  [23,'Base terminal growth',.025,'rate','Written DCF policy','POLICY DEFAULT'],[24,'D&A / revenue',.03,'rate','Written DCF policy','POLICY DEFAULT'],
  [25,'CapEx / revenue',.04,'rate','Written DCF policy','POLICY DEFAULT'],[26,'Incremental NWC / revenue change',.01,'rate','Written DCF policy','POLICY DEFAULT'],
  [27,'Mature growth ceiling',.03,'rate','Written DCF policy','POLICY DEFAULT'],[28,'Years',5,'years','Written DCF policy','POLICY DEFAULT'],
  [29,'Bear growth shift',-.02,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],[30,'Bear margin shift',-.01,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],
  [31,'Bear WACC shift',.01,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],[32,'Bear terminal-g shift',-.005,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],
  [33,'Bull growth shift',.02,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],[34,'Bull margin shift',.01,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],
  [35,'Bull WACC shift',-.01,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],[36,'Bull terminal-g shift',.005,'percentage points','Validator scenario convention; absent from written spec','SPEC GAP'],
  [37,'Tax fallback',.21,'rate','Written WACC policy','POLICY FALLBACK'],[38,'Cost-debt fallback',.05,'rate','Written WACC policy','POLICY FALLBACK'],
  [39,'Growth fallback',.08,'rate','Written DCF policy','POLICY FALLBACK'],[40,'Margin fallback',.15,'rate','Written DCF policy','POLICY FALLBACK']
 ];
 for(const [r,l,v,u,src,status] of fixed)input(sh,r,l,v,u,'2026-09-13',src,status);
 const derived=[
  [43,'Elapsed fiscal years','=YEARFRAC(DATEVALUE(A6),DATEVALUE(A10),3)'],
  [44,'Historical revenue CAGR','=IF(AND(B6>0,B10>0,B43>0),(B10/B6)^(1/B43)-1,NA())'],
  [45,'Resolved growth','=IF(ISNUMBER(B44),MIN(25%,B44),B39)'],
  [46,'Average EBIT margin','=AVERAGE(D6:D10)'],[47,'Resolved margin','=IF(ISNUMBER(B46),B46,B40)'],
  [48,'Observed tax ratio','=IF(B19>0,B20/B19,NA())'],[49,'Resolved tax','=IF(ISNUMBER(B48),B48,B37)'],
  [50,'Tax provenance','=IF(ISNUMBER(B48),"OBSERVED","FALLBACK")'],[51,'Cost of debt','=IF(B16>0,ABS(B18)/B16,B38)'],
  [52,'Debt-cost provenance','=IF(B16>0,"OBSERVED","FALLBACK")'],[53,'Market capitalization','=B13*B14'],[54,'Total capital','=B53+B16'],
  [55,'Cost of equity','=B21+B15*B22'],[56,'After-tax debt cost','=B51*(1-B49)'],[57,'Equity weight','=B53/B54'],
  [58,'Debt weight','=B16/B54'],[59,'Raw WACC','=B57*B55+B58*B56'],[60,'Bounded WACC','=MAX(5%,MIN(20%,B59))']
 ];
 for(const [r,l,formula] of derived){set(sh,`A${r}`,l);f(sh,`B${r}`,formula)}
 // DATEVALUE/YEARFRAC are not a proxy for the specification's exact days/365.25.
 // A43 is overwritten with an exact arithmetic day count using typed Excel dates below.
 const first=s.historical_annual_data[0].fiscal_period_end,last=s.historical_annual_data[4].fiscal_period_end;
 set(sh,'H43','CAGR date basis');set(sh,'I43',`${first} to ${last}; actual days / 365.25`);
 f(sh,'B43',`=(DATE(${last.slice(0,4)},${Number(last.slice(5,7))},${Number(last.slice(8,10))})-DATE(${first.slice(0,4)},${Number(first.slice(5,7))},${Number(first.slice(8,10))}))/365.25`);
 function block(start,name,shiftGrow,shiftMargin,shiftWacc,shiftG){
  set(sh,`A${start}`,`${name} • 5 annual forecast years`);
  sh.getRange(`C${start}:G${start}`).values=[['Year 1','Year 2','Year 3','Year 4','Year 5']];
  const labels=['Growth','EBIT margin','Revenue USD','EBIT USD','NOPAT USD','D&A USD','CapEx USD','Increase in NWC USD','FCF USD','PV of FCF USD'];
  for(let j=0;j<labels.length;j++)set(sh,`A${start+1+j}`,labels[j]);
  const cols=['C','D','E','F','G'];
  for(let i=0;i<5;i++){
   const c=cols[i],p=cols[i-1],r=start;
   let baseGrow=i<2?'$B$45':i===2?'IF($B$45>$B$27,$B$27+($B$45-$B$27)*2/3,$B$45)':i===3?'IF($B$45>$B$27,$B$27+($B$45-$B$27)/3,$B$45)':'IF($B$45>$B$27,$B$27,$B$45)';
   f(sh,`${c}${r+1}`,`=${baseGrow}${shiftGrow?`+$B$${shiftGrow}`:''}`);
   f(sh,`${c}${r+2}`,`=$B$47${shiftMargin?`+$B$${shiftMargin}`:''}`);
   f(sh,`${c}${r+3}`,`=${i?p+String(r+3):'$B$10'}*(1+${c}${r+1})`);
   f(sh,`${c}${r+4}`,`=${c}${r+3}*${c}${r+2}`);
   f(sh,`${c}${r+5}`,`=${c}${r+4}*(1-$B$49)`);
   f(sh,`${c}${r+6}`,`=${c}${r+3}*$B$24`);
   f(sh,`${c}${r+7}`,`=${c}${r+3}*$B$25`);
   f(sh,`${c}${r+8}`,`=(${c}${r+3}-${i?p+String(r+3):'$B$10'})*$B$26`);
   f(sh,`${c}${r+9}`,`=${c}${r+5}+${c}${r+6}-${c}${r+7}-${c}${r+8}`);
   f(sh,`${c}${r+10}`,`=${c}${r+9}/(1+$B$${r+13})^${i+1}`);
  }
  const rows=[[13,'WACC',`=MAX(5%,MIN(20%,$B$60${shiftWacc?`+$B$${shiftWacc}`:''}))`],
  [14,'Terminal growth',`=MAX(0%,MIN(5%,$B$23${shiftG?`+$B$${shiftG}`:''}))`],
  [15,'Terminal FCF',`=G${start+9}`],[16,'Undiscounted terminal value',`=B${start+15}*(1+B${start+14})/(B${start+13}-B${start+14})`],
  [17,'PV terminal value',`=B${start+16}/(1+B${start+13})^5`],[18,'PV annual FCF',`=SUM(C${start+10}:G${start+10})`],
  [19,'Enterprise value',`=B${start+17}+B${start+18}`],[20,'Equity value',`=B${start+19}-$B$16+$B$17`],
  [21,'Per-share value',`=B${start+20}/$B$14`],[22,'PV terminal / EV',`=IF(B${start+19}>0,B${start+17}/B${start+19},"N/A: EV<=0")`]];
  for(const [j,l,formula] of rows){set(sh,`A${start+j}`,l);f(sh,`B${start+j}`,formula)}
  fmt(sh,`C${start+1}:G${start+2}`,pct);fmt(sh,`C${start+3}:G${start+10}`,money);
  fmt(sh,`B${start+13}:B${start+14}`,pct);fmt(sh,`B${start+15}:B${start+20}`,money);fmt(sh,`B${start+22}`,pct);
 }
 block(64,'Base',0,0,0,0);block(94,'Bear',29,30,31,32);block(124,'Bull',33,34,35,36);
 const flags=[
  [151,'Nonpositive Base terminal FCF','=B79<=0'],[152,'Nonpositive Base enterprise value','=B83<=0'],
  [153,'Reversed Bear / Base / Bull','=NOT(AND(ISNUMBER(B115),ISNUMBER(B85),ISNUMBER(B145),B115<=B85,B85<=B145))'],
  [154,'Extreme observed tax >= 60%','=AND(B50="OBSERVED",B48>=60%)'],
  [155,'PV terminal / EV >= 80%','=IF(AND(B83>0,B81>=0),B86>=80%,FALSE)'],
  [156,'Quality classification','=IF(OR(B151,B152,B153),"DIAGNOSTIC ONLY",IF(OR(B154,B155),"CAUTION","ORDINARY"))'],
  [157,'Mathematically computable','=AND(ISNUMBER(B85),ISNUMBER(B115),ISNUMBER(B145))'],
  [158,'Market comparison allowed','=AND(B157,B156="ORDINARY")']
 ];
 set(sh,'A150','Quality flags (1 = triggered, 0 = clear)');
 for(const [r,l,formula] of flags){set(sh,`A${r}`,l);f(sh,`B${r}`,formula)}
 set(sh,'A160','Interpretation');set(sh,'B160','Values remain visible for diagnosis. Flagged cases are not actionable price targets.');
 fmt(sh,'D6:D10',pct);fmt(sh,'B6:C10',money);fmt(sh,'B13','$0.00');fmt(sh,'B14:B20',money);fmt(sh,'B15','0.000');fmt(sh,'B21:B27',pct);fmt(sh,'B29:B40',pct);fmt(sh,'B43','0.000');fmt(sh,'B44:B49',pct);fmt(sh,'B51',pct);fmt(sh,'B53:B54',money);fmt(sh,'B55:B60',pct);
 for(const r of [85,115,145])fmt(sh,`B${r}`,'$0.00;[Red]($0.00);–');
 sh.getRange('A:A').format.columnWidth=32;sh.getRange('B:B').format.columnWidth=24;sh.getRange('C:D').format.columnWidth=20;
 sh.getRange('E:F').format.columnWidth=19;sh.getRange('G:H').format.columnWidth=18;sh.getRange('I:J').format.columnWidth=42;sh.getRange('K:L').format.columnWidth=29;
 for(const r of [1,5,12,64,94,124,150]){sh.getRange(`A${r}:L${r}`).format.fill='#17365D';sh.getRange(`A${r}:L${r}`).format.font.color='#FFFFFF';sh.getRange(`A${r}:L${r}`).format.font.bold=true}
 sh.getRange('B13:B40').format.font.color='#1565C0';sh.getRange('B43:B60').format.font.color='#202020';
 return sh;
}
for(const s of snaps)createCompany(s);
summary.showGridLines=false;set(summary,'A1','Independent staged DCF | frozen validation scorecard');
set(summary,'A2','Mathematical outputs and economic interpretation are separate. No trading or market-price calibration.');
summary.getRange('A4:J4').values=[['Issuer','Base / share','Bear / share','Bull / share','Base WACC','Base terminal g','PV TV / EV','Quality','Math complete','Comparison allowed']];
for(let i=0;i<snaps.length;i++){
 const r=i+5,n=snaps[i].ticker;set(summary,`A${r}`,n);
 const refs=['B85','B115','B145','B77','B78','B86','B156','B157','B158'];
 for(let j=0;j<refs.length;j++)f(summary,`${'BCDEFGHIJ'[j]}${r}`,`='${n}'!${refs[j]}`);
}
summary.getRange('A12:B16').values=[['Policy boundary','Trigger'],['Terminal FCF or EV','<= 0, serious'],['Observed effective tax','>= 60%, caution'],['PV terminal / positive EV','>= 80%, caution'],['Bear <= Base <= Bull','Must hold; otherwise serious']];
summary.getRange('A18:B21').values=[['Limit','Explanation'],['Synthetic case','Hypothetical, not a real issuer'],['Scenario shifts','Validator convention, because written specification omits exact values'],['Economic credibility','Not established by arithmetic agreement alone']];
summary.getRange('A:A').format.columnWidth=31;summary.getRange('B:J').format.columnWidth=22;summary.getRange('B5:D9').setNumberFormat('$0.00;[Red]($0.00);–');summary.getRange('E5:G9').setNumberFormat(pct);
set(summary,'I11','1 = yes, 0 = no');
for(const r of [1,4,12,18]){summary.getRange(`A${r}:J${r}`).format.fill='#17365D';summary.getRange(`A${r}:J${r}`).format.font.color='#FFFFFF';summary.getRange(`A${r}:J${r}`).format.font.bold=true}
policy.showGridLines=false;set(policy,'A1','Policy, threshold tests and specification limits');
policy.getRange('A3:H3').values=[['Condition','Below boundary','At boundary','Above boundary','Trigger','Below flags','At flags','Above flags']];
const boundary=[
 ['Terminal FCF',-.01,0,.01],['Enterprise value',-.01,0,.01],['Observed tax',.599999,.6,.600001],['PV terminal / EV',.799999,.8,.800001]
];
for(let i=0;i<boundary.length;i++){
 const r=4+i,[name,a,b,c]=boundary[i];policy.getRange(`A${r}:D${r}`).values=[[name,a,b,c]];
 const op=i<2?'<=0':'>='+ (i===2?'60%':'80%');set(policy,`E${r}`,op);
 for(let j=0;j<3;j++){const col='BCD'[j];f(policy,`${'FGH'[j]}${r}`,i<2?`=${col}${r}<=0`:`=${col}${r}>=${i===2?'60%':'80%'}`)}
}
policy.getRange('A10:B16').values=[
 ['Written source','docs/model-specifications/dcf.md; docs/model-specifications/wacc-capm.md at c5ccbd41b6423c09cbc1851e8245139a0a3b7c72'],
 ['Growth fade interpretation','Years 3–5 use remaining excess fractions 2/3, 1/3, 0; weak growth unchanged. Exact interpolation is not stated numerically.'],
 ['Scenario shift ambiguity','Written spec does not give Bear/Bull numeric shifts. Validator uses growth ±2pp, margin ±1pp, WACC ∓1pp, terminal g ±0.5pp.'],
 ['Tax fallback','21% only when observed ratio unavailable. Extreme tax checks observed ratio only.'],
 ['Risk-free fallback','Static 4% offline written default; no live yield fetched.'],
 ['Economic credibility','Separate judgment; terminal dominance, perpetual losses and cyclical inputs may make mathematically complete prices unreliable.'],
 ['Comparison policy','All quality flags withhold market comparisons; diagnostics remain visible.']
];
set(policy,'A9','Flag cells: 1 = triggered, 0 = not triggered');
policy.getRange('A:A').format.columnWidth=36;policy.getRange('B:B').format.columnWidth=95;policy.getRange('C:H').format.columnWidth=19;
for(const r of [1,3,10]){policy.getRange(`A${r}:H${r}`).format.fill='#17365D';policy.getRange(`A${r}:H${r}`).format.font.color='#FFFFFF';policy.getRange(`A${r}:H${r}`).format.font.bold=true}
await wb.recalculate();
await fs.mkdir(outDir,{recursive:true});await fs.mkdir(previewDir,{recursive:true});
const frozen={sourceCommit:'c5ccbd41b6423c09cbc1851e8245139a0a3b7c72',created:'2026-09-13',scenarioConvention:'validator-only shifts: growth ±2pp, margin ±1pp, WACC ∓1pp, terminal growth ±0.5pp',companies:{}};
for(const s of snaps){
 const sh=wb.worksheets.getItem(s.ticker);const get=c=>sh.getRange(c).values[0][0];
 frozen.companies[s.ticker]={inputs:{growth:get('B45'),margin:get('B47'),tax:get('B49'),costDebt:get('B51'),wacc:get('B60')},base:{growth:sh.getRange('C65:G65').values[0],margin:sh.getRange('C66:G66').values[0],revenue:sh.getRange('C67:G67').values[0],fcf:sh.getRange('C73:G73').values[0],pvFcf:sh.getRange('C74:G74').values[0],terminalFcf:get('B79'),terminalValue:get('B80'),pvTerminal:get('B81'),pvAnnual:get('B82'),enterprise:get('B83'),equity:get('B84'),perShare:get('B85'),terminalShare:get('B86')},bear:get('B115'),bull:get('B145'),quality:get('B156'),flags:[151,152,153,154,155].map(r=>get(`B${r}`))};
}
await fs.writeFile(path.join(base,'staged_v3','frozen-results.json'),JSON.stringify(frozen,null,2)+'\n');
const xlsx=await SpreadsheetFile.exportXlsx(wb);const out=path.join(outDir,'staged_dcf_independent_validation.xlsx');await xlsx.save(out);
for(const name of ['Scorecard','Policy and limits',...snaps.map(s=>s.ticker)]){
 try{const preview=await wb.render({sheetName:name,range:name==='Scorecard'?'A1:J21':name==='Policy and limits'?'A1:H16':'A1:G22',scale:1,format:'png'});await fs.writeFile(path.join(previewDir,`${name.replaceAll(' ','_')}.png`),new Uint8Array(await preview.arrayBuffer()));}catch(e){console.error('render failed',name,e.message)}
}
for(const name of snaps.map(s=>s.ticker)){
 for(const [label,range] of [['forecast','A43:G86'],['scenarios','A94:G146'],['quality','A151:G160']]){
  try{const preview=await wb.render({sheetName:name,range,scale:1,format:'png'});await fs.writeFile(path.join(previewDir,`${name}_${label}.png`),new Uint8Array(await preview.arrayBuffer()));}catch(e){console.error('render failed',name,label,e.message)}
 }
}
console.log(JSON.stringify({out,sha256:crypto.createHash('sha256').update(await fs.readFile(out)).digest('hex'),frozen},null,2));
