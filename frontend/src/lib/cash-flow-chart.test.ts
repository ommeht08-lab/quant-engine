import test from 'node:test';
import assert from 'node:assert/strict';
import {cashFlowBridge,chartDomain} from './cash-flow-chart.ts';
test('bridge uses reported signed cash flows including cash releases and negative FCF',()=>{
 for(const row of [{year:1,nopat:120,da:20,capex:40,change_in_nwc:10,fcf:90},{year:2,nopat:-20,da:5,capex:30,change_in_nwc:-10,fcf:-35}]) {
  const steps=cashFlowBridge(row); assert.equal(steps[3].end,row.fcf); assert.equal(steps[4].end,row.fcf); assert.equal(steps[2].value,-row.capex); assert.equal(steps[3].value,-row.change_in_nwc);
 }
});
test('missing or inconsistent financial components are never silently plotted',()=>{
 assert.throws(()=>cashFlowBridge({year:1,nopat:NaN,da:1,capex:1,change_in_nwc:1,fcf:1}));
 assert.throws(()=>cashFlowBridge({year:1,nopat:100,da:20,capex:20,change_in_nwc:10,fcf:95}));
});
test('plot domains include zero and signed data even for all-zero series',()=>{
 for(const values of [[0,0],[-20,-10],[20,10],[-10,20]]) {const d=chartDomain(values);assert.ok(d.min<=Math.min(0,...values));assert.ok(d.max>=Math.max(0,...values));assert.ok(d.max>d.min);}
});
