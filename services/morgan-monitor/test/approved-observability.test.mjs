import test from 'node:test';
import assert from 'node:assert/strict';
import { graphLayout, renderApprovedGraph, railwayState } from '../public/lib/approved-graph.js';
import { APPROVED_GEOMETRY as G } from '../public/lib/approved-geometry.js';
import { snapshotAtTime } from '../public/approved-app.js';
import { runtimeSnapshot, NOW, AT, PROJECTS } from './fixtures/graph-observation-fixtures.mjs';
const rails=G.cells.filter(c=>c.kind==='railway');
const get=(layout,id)=>layout.cells.find(c=>c.id===id);

test('each railway pair exactly matches its two nearest large durable hexagons',()=>{
  for(const rail of rails) {
    const nearest=G.cells.filter(c=>c.kind==='durable').sort((a,b)=>Math.hypot(a.x-rail.x,a.y-rail.y)-Math.hypot(b.x-rail.x,b.y-rail.y)).slice(0,2).map(c=>c.project).sort();
    assert.deepEqual([...rail.projects].sort(),nearest,rail.id);
  }
});
test('every adjacent pair: healthy, blocked and unknown combinations use durable evidence only',async()=>{
  for(const rail of rails)for(const a of ['idle','blocked','unknown'])for(const b of ['running','blocked','unknown']) {
    const states={[rail.projects[0]]:a,[rail.projects[1]]:b};
    const snapshot=await runtimeSnapshot(states);
    for(const phase of [0,1]) {
      const l=graphLayout(snapshot,{phase}),actual=get(l,rail.id);
      const expected=[a,b].includes('blocked')?(Number(rail.id.split('-')[1])%2===phase?'blocked':'neutral'):[a,b].includes('unknown')?'neutral':'success';
      assert.equal(actual.status,expected,`${rail.id} ${a}/${b} phase ${phase}`);
      assert.deepEqual(actual.adjacent.map(x=>x.project),rail.projects);
      assert(actual.adjacent.every(x=>x.observedAt===AT));
    }
  }
});
test('all 64 blocked-neighbor patterns retain opposite-phase red pairs, including adjacent conflicts',()=>{
  for(let mask=0;mask<64;mask++) {
    const durables=Object.fromEntries(PROJECTS.map((p,i)=>[p,{state:mask&(1<<i)?'blocked':'healthy'}]));
    for(const project of PROJECTS.filter(p=>durables[p].state==='blocked')) {
      const adjacent=rails.filter(c=>c.projects.includes(project));
      for(const phase of [0,1]) {
        assert.equal(adjacent.filter(c=>railwayState(c.projects,durables,phase)==='blocked').length,1,`${mask} ${project} phase ${phase}`);
        assert(adjacent.every(c=>railwayState(c.projects,durables,phase,true)==='blocked'));
      }
    }
  }
});
test('unknown or missing neighbor never becomes green, and duplicate/expired bindings fail closed',async()=>{
  const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'idle'});
  assert.equal(get(graphLayout(snapshot),'railway-5').status,'success');
  for(const mutate of [s=>s.projectAssociations.pop(),s=>s.projectAssociations.push({...s.projectAssociations[0]}),
    s=>s.projectAssociations[0].runtimeGeneration++,s=>s.sources[0].status='error']) {
    const copy=structuredClone(snapshot);mutate(copy);
    assert.notEqual(get(graphLayout(copy),'railway-5').status,'success');
  }
  const expired=graphLayout(snapshot,{now:NOW+120000});
  assert(expired.cells.every(c=>c.status==='neutral'));assert.equal(expired.overall.percentage,null);
});
test('full solid lamp faces and explicit provenance render in all modes, themes, motion and phase options',async()=>{
  const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'idle',ANQR:'blocked',CIX:'blocked'});
  for(const mode of ['full','compact'])for(const theme of ['light','dark'])for(const reducedMotion of [false,true])for(const phase of [0,1]) {
    const svg=renderApprovedGraph(snapshot,{mode,theme,reducedMotion,phase,footer:false,interactive:false});
    assert(svg.includes('class="ag-lamp-face"'));assert(svg.match(/class="ag-lamp-face"[^>]+fill="#00C853"/));
    assert(svg.match(/class="ag-lamp-face"[^>]+fill="#F22946"/));
    assert(svg.includes('data-adjacent="Cloud UIF"'));assert(svg.includes('Shared railway lamp for adjacent durable tiles ANQR and CIX'));
    assert(svg.includes('>2/4 observed</text>'));assert(svg.includes('>partial</text>'));
    assert.equal(svg.includes('class="ag-travel ag-blocked-edge"'),!reducedMotion);
    assert(svg.includes('class="ag-pulse"'));assert(svg.includes('prefers-reduced-motion:reduce'));
    assert(!svg.includes('ag-snapshot-footer'));assert(!svg.includes('role="button"'));
    assert.equal((svg.match(/data-graph-cell=/g)||[]).length,mode==='full'?91:13);
  }
});
test('green pulses for idle and running; only running travels; red baseline never blinks away',async()=>{
  for(const state of ['idle','running','blocked']) {
    const snapshot=await runtimeSnapshot({Cloud:state}),svg=renderApprovedGraph(snapshot,{footer:false,interactive:false});
    assert.equal(svg.includes('class="ag-travel"'),state==='running');
    assert.equal(svg.includes('class="ag-pulse"'),state!=='blocked');
    assert.equal(svg.includes('class="ag-travel ag-blocked-edge"'),state==='blocked');
    if(state==='blocked')assert(svg.match(/class="ag-steady"[^>]+stroke="#F22946"[^>]+stroke-width="10.4"/));
    assert(svg.includes('ag-edge-strobe 1.6s steps(1,end)'));
  }
});
test('center is allocated healthy/known, partial observed health, never capacity or unqualified global health',async()=>{
  const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'idle',ANQR:'blocked',CIX:'unknown'});
  const l=graphLayout(snapshot),svg=renderApprovedGraph(snapshot,{footer:false,interactive:false});
  assert.equal(l.overall.healthy,2);assert.equal(l.overall.known,3);assert.equal(l.overall.allocated,4);
  assert.equal(l.overall.percentage,67);assert.equal(l.overall.unknown,1);assert.equal(l.overall.unknownAllocated,1);
  assert.equal(l.overall.complete,false);assert(svg.includes('>2/3 observed</text>'));assert(svg.includes('>partial</text>'));
  assert(svg.includes('1 assigned observations in the received sample have unknown or stale state'));
  assert(svg.includes('configured capacity are excluded from the denominator'));
  for(const states of [{},{Cloud:'unknown'}])assert.equal(graphLayout(await runtimeSnapshot(states)).overall.percentage,null);
  const allHealthy=renderApprovedGraph(await runtimeSnapshot({Cloud:'idle',UIF:'running'}),{footer:false});
  assert(allHealthy.includes('>100%</text>'));assert(allHealthy.includes('>2/2 observed</text>'));assert(allHealthy.includes('>partial</text>'));
});
test('unmapped assigned sessions stay separate from scoped health and disclose exclusion',async()=>{
  const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'idle',ANQR:'unknown'});
  snapshot.projectAssociations=snapshot.projectAssociations.filter(a=>a.project==='Cloud');
  const l=graphLayout(snapshot);
  assert.equal(l.overall.known,1);assert.equal(l.overall.percentage,100);assert.equal(l.overall.unmappedAllocated,2);
  assert.equal(l.overall.unknownAllocated,1);assert.equal(l.observations.length,2);
  assert(renderApprovedGraph(snapshot).includes('2 lack a current verified project association and are excluded'));
});
test('transport failure, stale clocks and future timestamps withhold current colors and percentage without rejuvenation',async()=>{
  const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'blocked'});
  for(const [now,reachable] of [[NOW,false],[NOW+120000,true]]) {
    const projected=snapshotAtTime(snapshot,now,reachable),layout=graphLayout(projected,{now});
    assert(layout.cells.every(c=>c.status==='neutral'));assert.equal(layout.overall.percentage,null);
    assert.equal(layout.overall.unknownAllocated,2);assert.equal(layout.capturedAt,AT);
    assert(projected.slots.filter(s=>s.proof==='session_runtime').every(s=>s.observedAt===AT));
  }
  const bad=structuredClone(snapshot);bad.slots.find(s=>s.proof==='session_runtime').observedAt=new Date(NOW+1000).toISOString();
  assert(graphLayout(bad).cells.every(c=>c.status==='neutral'));
  const current=graphLayout(snapshot,{now:NOW+119999});assert.equal(current.overall.percentage,50);
  assert.equal(current.capturedAt,AT);assert.equal(current.projectionAt,AT);
});


test('native work is a distinct in-tile marker, never session allocation, health, border or rail evidence',async()=>{
  const base=await runtimeSnapshot({Cloud:'idle',UIF:'running',CIX:'unknown'}),baseline=graphLayout(base);
  for(const state of ['running','completed','blocked','unknown']) {
    const snapshot=await runtimeSnapshot({Cloud:'idle',UIF:'running',CIX:'unknown'},NOW,[{project:'CIX',state}]);
    const l=graphLayout(snapshot);
    assert.deepEqual(l.overall,baseline.overall);assert.equal(get(l,'durable-CIX').status,'neutral');
    assert.equal(get(l,'durable-CIX').nativeActivity.state,state);
    assert.deepEqual(l.cells.filter(c=>c.kind==='railway').map(c=>c.status),baseline.cells.filter(c=>c.kind==='railway').map(c=>c.status));
    for(const mode of ['full','compact'])for(const theme of ['light','dark']) {
      const svg=renderApprovedGraph(snapshot,{mode,theme,interactive:false,footer:false});
      assert(svg.includes(`data-native-state="${state}"`));
      assert(svg.includes(`CIX observed native worker: ${state}; observed ${AT}`));
      assert(svg.includes('not project completion or all native work'));
      assert(svg.includes(state==='completed'?'>worker done</text>':state==='unknown'?'>native ?</text>':state==='blocked'?'>native blocked</text>':'>native active</text>'));
      assert(svg.includes('>2/2 observed</text>'));
    }
    for(const [clock,reachable] of [[NOW+120000,true],[NOW,false]]) {
      const clean=snapshotAtTime(snapshot,clock,reachable);
      assert.equal(get(graphLayout(clean,{now:clock}),'durable-CIX').nativeActivity,null);
      assert(!renderApprovedGraph(clean,{now:clock}).includes('class="ag-native-work"'));
    }
  }
});
test('native activity independently expires without disturbing newer valid session evidence',async()=>{
  const snapshot=await runtimeSnapshot({Cloud:'idle',UIF:'running'},NOW,[{project:'CIX',state:'completed'}]);
  snapshot.projectActivities[0].observedAt=new Date(NOW-119000).toISOString();
  const before=graphLayout(snapshot,{now:NOW}),after=graphLayout(snapshot,{now:NOW+1000});
  assert.equal(get(before,'durable-CIX').nativeActivity.state,'completed');
  assert.equal(get(after,'durable-CIX').nativeActivity,null);assert.equal(after.overall.percentage,100);
  assert.equal(get(after,'railway-5').status,'success');
});

test('known stopped/failed/blocked allocations count against health; empty and unknown capacity do not',()=>{
  const states=['running','idle','completed','blocked','failed','stopped'];
  const slots=states.map((state,i)=>({id:`cloud-${'ABCDEF'[i]}`,domain:'cloud',alias:'ABCDEF'[i],assignment:'assigned',availability:'fresh',proof:'process',observedAt:AT,displayState:state,recordedState:state}));
  const snapshot={generatedAt:AT,summary:{executionCoverageComplete:true},sources:[{domain:'cloud',aliases:[...'ABCDEF'],status:'fresh',lastObservedAt:AT,capabilities:['process'],staleAfterSeconds:120,executionCoverageComplete:true,coverage:{complete:true,observed:6,total:6}}],slots,projectAssociations:slots.map((slot,i)=>({slotId:slot.id,project:PROJECTS[i],kind:'durable',verified:true,observedAt:AT}))};
  const layout=graphLayout(snapshot);assert.equal(layout.overall.percentage,50);assert.equal(layout.overall.known,6);assert.equal(layout.overall.healthy,3);assert.equal(layout.overall.complete,true);
  slots[5].assignment='unassigned';slots[5].displayState=null;slots[5].recordedState=null;
  assert.equal(graphLayout(snapshot).overall.percentage,60);assert.equal(graphLayout(snapshot).overall.known,5);
  slots[4].displayState=null;slots[4].recordedState='unknown';
  assert.equal(graphLayout(snapshot).overall.percentage,75);assert.equal(graphLayout(snapshot).overall.unknown,1);assert.equal(graphLayout(snapshot).overall.complete,false);
});
