import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync,readdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { handleRequest } from '../worker.mjs';
import { APPROVED_STATIC } from '../approved-static-source.mjs';
import { renderApprovedGraph,graphLayout } from '../public/lib/approved-graph.js';
import { snapshotAtTime,chooseMode,viewPreferences } from '../public/approved-app.js';
class D1 {
  constructor(){this.db=new DatabaseSync(':memory:');for(const f of readdirSync(new URL('../migrations/',import.meta.url)).sort())this.db.exec(readFileSync(new URL('../migrations/'+f,import.meta.url),'utf8'));}
  prepare(sql){const statement=this.db.prepare(sql);const bound=values=>({bind:(...args)=>bound(args),first:async()=>statement.get(...values)??null,all:async()=>({results:statement.all(...values)}),run:async()=>statement.run(...values)});return bound([]);}
}
const now=Date.parse('2026-11-10T05:12:00.000Z');
const captured='2026-11-10T05:11:55.369Z';
const sample=()=>({schemaVersion:1,generatedAt:captured,slots:[{id:'cloud-A',alias:'A',domain:'cloud',assignment:'assigned',availability:'fresh',displayState:'running',observedAt:captured,proof:'process',topics:['Engineering']}],sources:[{domain:'cloud',aliases:['A'],capabilities:['process'],status:'fresh',lastObservedAt:captured,lastSuccessAt:captured,staleAfterSeconds:120,executionCoverageComplete:true}],summary:{executionCoverageComplete:true}});
const get=(path,env={})=>handleRequest(new Request('https://morgan.aldous.info'+path),env,now);
test('reviewed dashboard assets are byte-identical, read-only and no-store without replacing unrelated assets',async()=>{
 const paths={'/':'public/approved-index.html','/index.html':'public/approved-index.html','/approved-index.html':'public/approved-index.html','/approved-app.js':'public/approved-app.js','/approved-styles.css':'public/approved-styles.css','/lib/session-runtime.js':'public/lib/session-runtime.js','/lib/approved-graph.js':'public/lib/approved-graph.js','/lib/approved-geometry.js':'public/lib/approved-geometry.js','/lib/approved-assets.js':'public/lib/approved-assets.js'};
 assert.deepEqual(Object.keys(APPROVED_STATIC).sort(),Object.keys(paths).sort());
 for(const [path,file] of Object.entries(paths)){
  const r=await get(path);assert.equal(r.status,200,path);assert.equal(await r.text(),readFileSync(new URL('../'+file,import.meta.url),'utf8'));
  assert.equal(r.headers.get('cache-control'),'no-store, max-age=0');assert.match(r.headers.get('content-security-policy'),/font-src 'self' data:/);assert.match(r.headers.get('content-security-policy'),/connect-src 'self';/);
  assert.equal((await handleRequest(new Request('https://morgan.aldous.info'+path,{method:'POST'}),{})).status,405);
  assert.equal(await(await handleRequest(new Request('https://morgan.aldous.info'+path,{method:'HEAD'}),{})).text(),'');
 }
 const r=await get('/unrelated-logo.svg',{ASSETS:{fetch:async()=>new Response('existing asset')}});assert.equal(await r.text(),'existing asset');
 assert.equal((await get('/lib/approved-graph.js/secret')).status,503);
 assert(!APPROVED_STATIC['/'].body.includes('/app.js"'));assert(!APPROVED_STATIC['/approved-app.js'].body.includes('fixtures.json'));
});
test('full, compact, auto, themes and legacy aliases all use the actual approved server renderer',async()=>{
 const env={DB:new D1(),SOURCE_CONFIG_JSON:JSON.stringify({sources:[]})};
 const snapshot=await(await get('/api/status',env)).json();
 for(const [layout,expected] of [['full','full'],['compact','compact'],['auto','full'],['o2','full'],['hexagon','full'],['focus','full']])for(const theme of ['light','dark']){
  const r=await get(`/render.svg?revision=${snapshot.revision}&layout=${layout}&theme=${theme}&width=1200&height=800`,env);assert.equal(r.status,200);
  const body=await r.text();assert.match(body,/data-geometry="railway-v36"/);assert.equal(r.headers.get('x-morgan-rendered-layout'),expected);assert.equal(r.headers.get('x-morgan-theme'),theme);
  assert.equal(body,renderApprovedGraph(snapshot,{mode:expected,theme,width:1200,height:800,now:Date.parse(snapshot.generatedAt)}));
 }
 for(const options of ['theme=purple','theme=system','layout=wrong','layout=full&layout=compact','width=1','height=NaN','mode=compact','file=/etc/passwd','selected=Cloud-primary-7','theme=dark&extra=1'])assert.equal((await get('/render.svg?'+options,env)).status,422,options);
 for(const selected of ['Cloud-primary-1','summary-CQ','railway-6','overall'])assert.equal((await get('/render.svg?selected='+selected,env)).status,200);
 const rpc=await handleRequest(new Request('https://morgan.aldous.info/mcp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'render_status',arguments:{revision:snapshot.revision,layout:'full',theme:'dark'}}})}),env,now);
 const result=(await rpc.json()).result;assert.equal(result.isError,false);assert.equal(result.structuredContent.renderedLayout,'full');assert.equal(result.structuredContent.theme,'dark');assert.equal(result.content[0].text,await(await get(`/render.svg?revision=${snapshot.revision}&layout=full&theme=dark`,env)).text());
});
test('current claims expire independently of successful polling and are withheld on transport failure',()=>{
 const raw=sample(),fresh=snapshotAtTime(raw,now,true);assert.equal(fresh.slots[0].displayState,'running');
 for(const [clock,reachable] of [[now,false],[now+120000,true]]){const stale=snapshotAtTime(raw,clock,reachable);assert.equal(stale.slots[0].displayState,null);assert.equal(stale.summary.executionCoverageComplete,false);assert.equal(stale.slots[0].observedAt,captured);assert.equal(stale.generatedAt,captured);}
 assert.equal(raw.slots[0].displayState,'running');
 const noTiming=sample();delete noTiming.sources[0].lastSuccessAt;assert.equal(snapshotAtTime(noTiming,now).slots[0].displayState,null);
});
test('explicit full is never overridden by exhaustion and automatic mode restores only on verified valid allowance',()=>{
 const exhausted={fooAllowance:{verified:true,availability:'fresh',state:'exhausted',canRunAgents:false}};
 const valid={fooAllowance:{verified:true,availability:'fresh',state:'valid',canRunAgents:true}};
 assert.equal(chooseMode('full',exhausted),'full');assert.equal(chooseMode('compact',valid),'compact');assert.equal(chooseMode('auto',exhausted),'compact');assert.equal(chooseMode('auto',{},'compact'),'compact');assert.equal(chooseMode('auto',valid,'compact'),'full');
 assert.deepEqual(viewPreferences('?mode=compact&theme=dark'),{mode:'compact',theme:'dark'});assert.deepEqual(viewPreferences('?layout=full&theme=light'),{mode:'full',theme:'light'});assert.deepEqual(viewPreferences('?mode=arbitrary&theme=unknown'),{mode:'auto',theme:'system'});
});
test('synthetic legacy API data renders without invented project assignments or health',()=>{
 const raw=JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json',import.meta.url),'utf8'));
 const clean=snapshotAtTime(raw,Date.parse('2026-11-10T16:00:00.000Z'),true);
 for(const mode of ['full','compact'])for(const theme of ['light','dark']){
  const model=graphLayout(clean,{mode,theme});assert.equal(model.cells.length,mode==='full'?91:13);assert(model.cells.every(c=>!c.slotId));assert.equal(model.overall.percentage,null);
  const svg=renderApprovedGraph(clean,{mode,theme});assert(svg.includes('Engineering'));assert(svg.includes('Research'));assert(!/\b(?:Read spec|Run tests|Draft update|Prepare build|71%|64%)\b/.test(svg));
 }
});
test('page-only footer option removes visible metadata and its space without changing graph geometry or API defaults',()=>{
 const raw=JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json',import.meta.url),'utf8'));
 for(const mode of ['full','compact'])for(const theme of ['light','dark']){
  const options={mode,theme},normal=graphLayout(raw,options),page=graphLayout(raw,{...options,footer:false});
  assert.deepEqual(page.cells,normal.cells);assert.deepEqual(page.groups,normal.groups);assert.equal(page.width,normal.width);assert.equal(page.height,page.graphHeight);assert(page.height<normal.height);
  const svg=renderApprovedGraph(raw,{...options,footer:false,interactive:false});assert(!svg.includes('class="ag-snapshot-footer"'));assert(!svg.includes('data-cell-id='));assert(!svg.includes('role="button"'));assert(!svg.includes('tabindex='));assert.match(svg,/<desc[^>]*>[^<]*Engineering[^<]*Research/);
  assert(renderApprovedGraph(raw,options).includes('class="ag-snapshot-footer"'));
 }
});
