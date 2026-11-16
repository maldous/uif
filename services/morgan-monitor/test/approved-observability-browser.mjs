// Optional real-browser verification; all fixture status is synthetic and test-only.
import assert from 'node:assert/strict';
import { writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright');
import { renderApprovedGraph } from '../public/lib/approved-graph.js';
import { runtimeSnapshot, NOW, AT } from './fixtures/graph-observation-fixtures.mjs';
const output=new URL('../../graph-qa/',import.meta.url);mkdirSync(output,{recursive:true});
const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'idle',ANQR:'blocked',CIX:'blocked',OM:'idle',CQ:'unknown'});
const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1180,height:757}});
const errors=[];page.on('pageerror',err=>errors.push(String(err)));
const report=[];
function html(options){return '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;background:'+(options.theme==='dark'?'#141C2A':'#F0F2F6')+'}svg{width:100%;height:100%;display:block}</style></head><body>'+renderApprovedGraph(snapshot,{...options,now:NOW,footer:false,interactive:false})+'</body></html>';}
const scope=()=>page.evaluate(()=>[...document.querySelectorAll('text.ag-scope,text.ag-coverage')].map(n=>n.textContent));
const freeze=ms=>page.evaluate(ms=>{for(const a of document.getAnimations()){a.pause();a.currentTime=ms;}},ms);
const rail=()=>page.evaluate(()=>Object.fromEntries([...document.querySelectorAll('[data-kind="railway"]')].map(cell=>[cell.dataset.graphCell,[...cell.querySelectorAll('[data-rail-state]')].filter(g=>getComputedStyle(g).display!=='none' && Number(getComputedStyle(g).opacity)>.5).map(g=>({state:g.dataset.railState,face:getComputedStyle(g.querySelector('.ag-lamp-face')).fill}))])));
for(const mode of ['full','compact'])for(const theme of ['light','dark']) {
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.setContent(html({mode,theme}));await page.evaluate(()=>document.fonts.ready);
  assert.deepEqual(await scope(),['3/5 observed','partial']);
  await freeze(100);const phase0=await rail();await freeze(900);const phase1=await rail();
  assert.equal(phase0['railway-5'][0].face,'rgb(0, 200, 83)');
  for(const id of ['railway-1','railway-2','railway-6']) {
    assert.notEqual(phase0[id][0].state,phase1[id][0].state);
    for(const phase of [phase0,phase1])if(phase[id][0].state==='blocked')assert.equal(phase[id][0].face,'rgb(242, 41, 70)');
  }
  for(const phase of [phase0,phase1]) {
    assert.equal([phase['railway-1'][0].state,phase['railway-6'][0].state].filter(s=>s==='blocked').length,1);
    assert.equal([phase['railway-1'][0].state,phase['railway-2'][0].state].filter(s=>s==='blocked').length,1);
  }
  const visual=await page.evaluate(()=>({idleTravel:document.querySelector('[data-graph-cell="durable-UIF"] .ag-travel')!==null,
    idlePulse:getComputedStyle(document.querySelector('[data-graph-cell="durable-UIF"] .ag-pulse')).animationName,
    runningTravel:getComputedStyle(document.querySelector('[data-graph-cell="durable-Cloud"] .ag-travel')).animationName,
    redBaseline:getComputedStyle(document.querySelector('[data-graph-cell="durable-CIX"] .ag-steady')).stroke,
    alertDuration:getComputedStyle(document.querySelector('.ag-blocked-edge')).animationDuration}));
  assert.equal(visual.idleTravel,false);assert.equal(visual.idlePulse,'ag-pulse');assert.equal(visual.runningTravel,'ag-travel');
  assert.equal(visual.redBaseline,'rgb(242, 41, 70)');assert.equal(visual.alertDuration,'2.4s, 1.6s');
  await page.screenshot({path:new URL(`synthetic-${mode}-${theme}.png`,output).pathname});
  await page.emulateMedia({reducedMotion:'reduce'});
  const reduced=await rail();
  for(const id of ['railway-1','railway-2','railway-6'])assert.equal(reduced[id][0].state,'blocked');
  assert(await page.evaluate(()=>[...document.querySelectorAll('.ag-travel')].every(n=>getComputedStyle(n).display==='none')));
  assert(await page.evaluate(()=>[...document.querySelectorAll('.ag-pulse,.ag-rail-0,.ag-rail-1')].every(n=>getComputedStyle(n).animationName==='none')));
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.setContent(html({mode,theme,reducedMotion:true}));
  assert(await page.evaluate(()=>[...document.querySelectorAll('.ag-pulse')].every(n=>getComputedStyle(n).animationName==='none')));
  assert.equal(await page.locator('polygon.ag-travel').count(),0);
  for(const id of ['railway-1','railway-2','railway-6'])assert.equal((await rail())[id][0].state,'blocked');
  report.push({mode,theme,phase0,phase1,visual,reducedMotion:'system and explicit passed'});
}
assert.deepEqual(errors,[]);
writeFileSync(new URL('browser-report.json',output),JSON.stringify({fixture:'Synthetic test-only mixed runtime states; not a live-status capture.',at:AT,checks:report},null,2)+'\n');
await browser.close();console.log('Browser QA passed: both modes/themes, solid lamps, 2 blocked neighbors, phase parity, idle/running, explicit/system reduced motion.');
