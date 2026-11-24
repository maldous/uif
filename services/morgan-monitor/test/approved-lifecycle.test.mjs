// Offline DOM lifecycle model. Native browser keyboard/screenshot QA is separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {renderApprovedGraph,isRuntimeProjection} from '../public/lib/approved-graph.js';
const html=readFileSync(new URL('../public/approved-index.html',import.meta.url),'utf8');
const app=readFileSync(new URL('../public/approved-app.js',import.meta.url),'utf8').replace(/^import .*;\n/,'').replace(/^export /gm,'');
const tick=()=>new Promise(r=>setImmediate(r));
const captured='2026-11-10T05:11:55.369Z';
const sample=()=>({schemaVersion:1,generatedAt:captured,revision:'a'.repeat(64),summary:{executionCoverageComplete:false},sources:[{domain:'cloud',aliases:['A'],status:'fresh',lastObservedAt:captured,lastSuccessAt:captured,staleAfterSeconds:120,observationMode:'manual',capabilities:['coordinator_lifecycle']}],slots:[{id:'cloud-A',domain:'cloud',alias:'A',observedAt:captured,availability:'fresh',assignment:'assigned',displayState:null,recordedState:'unknown',proof:'coordinator_lifecycle',topics:['Engineering']}],conversationTopics:{voice:[],chat:[],status:[]}});
function harness({search='',small=false,dark=false}={}){
 let now=Date.parse(captured)+1000,next=1,fetchMode='ok',calls=0,data=sample();const timers=new Map(),elements=new Map(),windowEvents={},documentEvents={},signals=[],pending=[],medias=new Map();
 const element=()=>({innerHTML:'',dataset:{},attrs:{},setAttribute(k,v){this.attrs[k]=v;}});
 elements.set('board',element());elements.set('theme-toggle',element());
 const context={renderApprovedGraph,isRuntimeProjection,URLSearchParams,URL,AbortController,structuredClone,Math,Map,Date:class extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}},setTimeout(fn,ms){const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},location:{search,href:'http://localhost/'+search},history:{replaceState(_a,_b,url){context.location.search=new URL(url).search;context.location.href=String(url);}},window:{matchMedia:query=>{const media={matches:query.includes('max-width')?small:query.includes('color-scheme')?dark:false,addEventListener(_event,fn){this.change=fn;}};medias.set(query,media);return media;},addEventListener(k,fn){windowEvents[k]=fn;}},document:{hidden:false,documentElement:{dataset:{}},getElementById(id){assert(elements.has(id),'unexpected page control: '+id);return elements.get(id);},addEventListener(k,fn){documentEvents[k]=fn;}},fetch:async(url,options)=>{assert.equal(url,'/api/status');calls++;signals.push(options.signal);if(fetchMode==='error')throw Error('offline');if(fetchMode==='hold')await new Promise(r=>pending.push(r));const payload=structuredClone(data);return{ok:true,json:async()=>payload};}};
 vm.runInNewContext(app,context);
 return{elements,timers,medias,windowEvents,documentEvents,signals,context,calls:()=>calls,setMode:v=>fetchMode=v,setData:v=>data=v,advance:ms=>now+=ms,release:()=>pending.shift()?.(),timer:ms=>{const entry=[...timers].find(([,t])=>t.ms===ms);assert(entry,'expected timer '+ms);timers.delete(entry[0]);entry[1].fn();},nativeActivate:key=>{assert(['Enter',' '].includes(key));assert.match(html,/<button id="theme-toggle" type="button"/);assert(!documentEvents.keydown);elements.get('theme-toggle').onclick();}};
}
const graph=h=>h.elements.get('board').innerHTML;
const description=h=>graph(h).match(/<desc\b[^>]*>([\s\S]*?)<\/desc>/)?.[1];
test('page has exactly one icon-only native control and no visible chrome or cell hit targets',async()=>{
 const h=harness();await tick();
 assert.equal((html.match(/<button\b/g)||[]).length,1);assert(!/<(?:header|footer|nav|aside|select|output|h[1-6])\b/.test(html));assert(!/id="(?:connection|inspector|zoom|download|view-|theme")/.test(html));
 const button=html.match(/<button\b[^>]*>([\s\S]*?)<\/button>/)[1];assert(!button.replace(/<[^>]+>/g,'').trim());
 assert(!/ag-snapshot-footer|ag-cell-hit"|role="button"|tabindex=/.test(graph(h)));assert.match(description(h),/Manual snapshot/);assert.match(description(h),/Engineering/);assert.match(description(h),/2026-11-10T05:11:55.369Z/);
 const visible=graph(h).replace(/<(?:title|desc|style)\b[^>]*>[\s\S]*?<\/(?:title|desc|style)>/g,'');assert(!/Captured |Recorded observations|Observed 2026|Engineering|execution health unknown/.test(visible));
});
test('light/dark icon supports repeat mouse and native Enter/Space activation without double handlers',async()=>{
 const h=harness();await tick();const toggle=h.elements.get('theme-toggle');
 assert.match(graph(h),/data-theme="light"/);assert.equal(toggle.attrs['aria-label'],'Switch to dark theme');
 h.nativeActivate('Enter');assert.match(graph(h),/data-theme="dark"/);assert.equal(toggle.attrs['aria-pressed'],'true');assert.equal(toggle.attrs['aria-label'],'Switch to light theme');
 h.nativeActivate(' ');assert.match(graph(h),/data-theme="light"/);assert.equal(toggle.attrs['aria-pressed'],'false');
 toggle.onclick();toggle.onclick();assert.match(graph(h),/data-theme="light"/);assert.equal(new URL(h.context.location.href).searchParams.get('theme'),'light');
});
test('uses system theme initially and explicit theme/deep-linked layout override responsive defaults',async()=>{
 const dark=harness({dark:true});await tick();assert.match(graph(dark),/data-theme="dark"/);
 const small=harness({small:true});await tick();assert.match(graph(small),/data-layout="compact"/);
 const full=harness({search:'?mode=full&theme=light',small:true,dark:true});await tick();assert.match(graph(full),/data-layout="full"/);assert.match(graph(full),/data-theme="light"/);
 const compact=harness({search:'?layout=compact'});await tick();assert.match(graph(compact),/data-layout="compact"/);
 const legacy=harness({search:'?layout=o2',small:true});await tick();assert.match(graph(legacy),/data-layout="full"/);
 const media=small.medias.get('(max-width: 600px), (max-height: 480px)');media.matches=false;media.change();assert.match(graph(small),/data-layout="full"/);
});
test('polls API every five seconds and keeps original capture only in accessible metadata',async()=>{
 const h=harness();await tick();assert.equal(h.calls(),1);const original=description(h);h.timer(5000);await tick();assert.equal(h.calls(),2);assert.equal(description(h),original);assert(!graph(h).includes('Read spec'));
});
test('aborts overlapping fetch and ignores obsolete success after a newer failure',async()=>{
 const h=harness();await tick();h.setMode('hold');h.timer(5000);await tick();const held=h.signals.at(-1);h.setMode('error');h.documentEvents.visibilitychange();await tick();assert(held.aborted);assert.equal(h.elements.get('board').dataset.connection,'unavailable');
 h.release();await tick();assert.equal(h.elements.get('board').dataset.connection,'unavailable');assert.match(description(h),/Stale or unavailable evidence/);
});
test('pagehide cancels requests/timers and bfcache return rechecks instead of trusting prior health',async()=>{
 const h=harness();await tick();h.setMode('hold');h.timer(5000);await tick();h.windowEvents.pagehide();assert(h.signals.at(-1).aborted);assert.equal([...h.timers.values()].filter(t=>t.ms===5000||t.ms===1000).length,0);h.release();await tick();assert.equal(h.elements.get('board').dataset.connection,'unavailable');
 h.setMode('ok');h.windowEvents.pageshow({persisted:true});await tick();assert.equal(h.calls(),3);assert.equal(h.elements.get('board').dataset.connection,'available');
});
test('explicit full still overrides exhausted allowance without a visible mode selector',async()=>{
 const h=harness({search:'?mode=full'});const value=sample();value.sources.push({domain:'foo',aliases:[],capabilities:['foo_allowance'],status:'fresh',lastObservedAt:captured,lastSuccessAt:captured,staleAfterSeconds:120});value.fooAllowance={verified:true,availability:'fresh',state:'exhausted',canRunAgents:false,observedAt:captured,staleAfterSeconds:120};h.setData(value);await tick();h.timer(5000);await tick();assert.match(graph(h),/data-layout="full"/);
 h.context.location.search='?mode=auto';h.windowEvents.popstate();assert.match(graph(h),/data-layout="compact"/);
});
test('malformed nested feed preserves last-good observation provenance and withholds claims',async()=>{
 const h=harness();await tick();for(const malformed of [{...sample(),sources:[null]},{...sample(),slots:[null]},{...sample(),slots:[{...sample().slots[0],topics:'unsafe type'}]}]){
  h.setData({...malformed,generatedAt:'2026-11-10T06:00:00.000Z'});h.timer(5000);await tick();assert.equal(h.elements.get('board').dataset.connection,'unavailable');assert.match(description(h),/2026-11-10T05:11:55.369Z/);assert.match(description(h),/Engineering/);assert.match(description(h),/stale snapshot/);
 }
});
test('independent expiry updates accessible stale status even while no new API response arrives',async()=>{
 const h=harness();await tick();assert(!description(h).includes('Stale or unavailable evidence.'));h.advance(121000);h.timer(1000);assert.match(description(h),/Stale or unavailable evidence/);assert.match(description(h),/2026-11-10T05:11:55.369Z/);assert(!/ag-snapshot-footer|role="button"|tabindex=/.test(graph(h)));
});
