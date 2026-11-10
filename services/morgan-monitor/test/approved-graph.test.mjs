import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {FONT_ADVANCES} from '../public/lib/approved-assets.js';
import {graphLayout,renderApprovedGraph,railwayState} from '../public/lib/approved-graph.js';
import {APPROVED_GEOMETRY as G} from '../public/lib/approved-geometry.js';
const at='2026-11-10T05:00:00.000Z',generatedAt='2026-11-10T05:01:00.000Z';
function manual(status='fresh') {
  return {schemaVersion:1,generatedAt,revision:'a'.repeat(64),summary:{executionCoverageComplete:false},
    sources:[{domain:'cloud',aliases:[...'ABCDEF'],observationMode:'manual',lastObservedAt:at,status,
      capabilities:['coordinator_lifecycle'],rosterGeneration:3,staleAfterSeconds:120,executionCoverageComplete:false}],
    slots:['A','B'].map((alias,i)=>({id:`cloud-${alias}`,domain:'cloud',alias,assignment:'assigned',availability:status,
      proof:'coordinator_lifecycle',rosterGeneration:3,recordedState:'unknown',displayState:null,observedAt:at,
      topics:[i?'Research':'Engineering'],topicKeys:[i?'research':'engineering'],environmentKey:i?'foo_uif':'cloud_scratch'}))};
}
function projectFixture(project='Cloud',state='unassigned') {
  const slots=Array.from({length:12},(_,i)=>({id:`foo-${i+1}`,domain:'foo',alias:String(i+1),
    assignment:state==='unassigned'?'unassigned':'assigned',availability:'fresh',proof:'process',
    observedAt:at,displayState:state==='unassigned'?null:state,recordedState:state==='unassigned'?null:state,
    topicKeys:[],topics:[]}));
  slots.push({...slots[0],id:'cloud-F',domain:'cloud',alias:'F'});
  return {generatedAt,sources:[{domain:'foo',aliases:slots.slice(0,12).map(s=>s.alias),status:'fresh',lastObservedAt:at,
    staleAfterSeconds:120,capabilities:['process'],executionCoverageComplete:true,coverage:{complete:true,observed:12,total:12}},
  {domain:'cloud',aliases:['F'],status:'fresh',lastObservedAt:at,staleAfterSeconds:120,capabilities:['process'],
    executionCoverageComplete:true,coverage:{complete:true,observed:1,total:1}}],slots,
  projectAssociations:slots.map((slot,i)=>({slotId:slot.id,project,kind:i===12?'durable':i<6?'primary':'overflow',
    ...(i===12?{}:{index:i%6+1}),verified:true,observedAt:at}))};
}
function statuses(svg){return [...svg.matchAll(/data-status="([^"]+)"/g)].map(m=>m[1]);}
test('exact approved topology: six groups of six primary and six overflow, six durable and six shared signals',()=>{
  assert.equal(G.cells.length,91);assert.equal(G.gap,14);assert.equal(G.stroke,2.4);
  assert.deepEqual(G.projects,['Cloud','UIF','ANQR','CIX','OM','CQ']);
  for(const project of G.projects){
    for(const kind of ['primary','overflow']) assert.equal(G.cells.filter(c=>c.project===project&&c.kind===kind).length,6);
    assert.equal(G.cells.filter(c=>c.project===project&&c.kind==='summary').length,1);
    assert.equal(G.cells.filter(c=>c.project===project&&c.kind==='durable').length,1);
  }
  assert.equal(G.cells.filter(c=>c.kind==='railway').length,6);
  assert.equal(G.cells.filter(c=>c.kind==='overall').length,1);
  assert(!G.cells.some(c=>c.kind==='history'));
});
test('geometry cell vertices are exact regular approved hexagons; world radii unchanged',()=>{
  for(const cell of G.cells){
    assert.equal(cell.vertices.length,6);
    for(const [x,y]of cell.vertices) assert(Math.abs(Math.hypot(x-cell.x,y-cell.y)-cell.r)<1e-9);
    if(cell.kind==='primary'||cell.kind==='summary'||cell.kind==='durable')assert.equal(cell.r,60);
    if(cell.kind==='overflow'||cell.kind==='railway')assert.equal(cell.r,25.95854811567262);
  }
  assert.deepEqual(G.bounds,[-549.8815965698052,-572.8537180435968,549.8815965698052,572.8537180435967]);
});
test('Cloud remains top and project order clockwise in every view and requested dimension',()=>{
  for(const mode of ['full','compact'])for(const theme of ['light','dark']){
    const l=graphLayout({}, {mode,theme,width:1600,height:800}),o=l.cells.find(c=>c.kind==='overall');
    const durable=l.cells.filter(c=>c.kind==='durable').sort((a,b)=>
      ((Math.atan2(a.y-o.y,a.x-o.x)+Math.PI/2+2*Math.PI)%(2*Math.PI))-
      ((Math.atan2(b.y-o.y,b.x-o.x)+Math.PI/2+2*Math.PI)%(2*Math.PI)));
    assert.deepEqual(durable.map(c=>c.project),G.projects);
    assert.equal(durable[0].y,Math.min(...durable.map(c=>c.y)));
  }
});
test('compact contains only center, durable cells and shared railway signals',()=>{
  const full=graphLayout({}, {mode:'full'}),compact=graphLayout({}, {mode:'compact'});
  assert.equal(full.cells.length,91);assert.equal(compact.cells.length,13);assert.equal(compact.groups.length,2);
  assert(compact.cells.every(c=>['durable','overall','railway'].includes(c.kind)));
  for(const c of compact.cells){const f=full.cells.find(v=>v.id===c.id);assert.equal(c.r,f.r);}
});
test('unknown manual A/B preserve neutral recorded subjects without any project mapping',()=>{
  const s=manual(),l=graphLayout(s),svg=renderApprovedGraph(s);
  assert.equal(l.observations.length,2);assert.equal(l.observations[0].subject,'Engineering');
  assert.equal(l.observations[1].subject,'Research');assert(l.cells.every(c=>c.slotId===null));
  assert(l.cells.every(c=>c.status==='neutral'));assert(l.overall.percentage===null);
  assert(Object.values(l.summaries).every(p=>p.percentage===null));
  assert(svg.includes('Cloud A: Engineering'));assert(svg.includes('Cloud B: Research'));
  assert(svg.includes('Recorded observations without verified project association'));
  assert(svg.includes('Captured '+at));assert(svg.includes('Observed '+at));assert(svg.includes('Manual snapshot'));
  assert(!svg.match(/>\d+%<\/text>/));assert(!svg.includes('class="ag-alarm"'));assert(!svg.includes('class="ag-travel"'));
  assert(statuses(svg).every(status=>status==='neutral'));
});
test('unobserved local positions have no subject, task icon or invented activity',()=>{
  const svg=renderApprovedGraph(manual());
  assert.equal((svg.match(/<image /g)||[]).length,3,'only ANQR, OM and CQ brand images');
  for(const sample of ['Fix layout','Run tests','Draft update','Review change','Prepare build'])assert(!svg.includes(sample));
  assert(!svg.match(/>\d+%<\/text>/));
});
test('stale, unknown and lost-source snapshots are neutral, retaining dated subjects',()=>{
  for(const state of ['stale','outage','error','unavailable']) {
    const svg=renderApprovedGraph(manual(state),{mode:'compact',theme:'dark'});
    assert(svg.includes('stale snapshot'));assert(svg.includes('stale or unavailable evidence'));
    assert(svg.includes('Engineering'));assert(statuses(svg).every(status=>status==='neutral'));
  }
  const svg=renderApprovedGraph(manual(),{now:'2026-11-10T05:03:00.000Z'});
  assert(svg.includes('stale snapshot'));assert(statuses(svg).every(status=>status==='neutral'));
});
test('falsely fresh unknown state never colors or creates a percentage',()=>{
  const s=projectFixture('Cloud','unknown');const l=graphLayout(s);
  assert(l.cells.every(c=>c.status==='neutral'));assert.equal(l.projects.Cloud.percentage,null);
  assert.equal(l.summaries.Cloud.percentage,null);
});
test('verified empty capacity does not invent allocated health or green rails',()=>{
  const s=projectFixture(),l=graphLayout(s);
  assert.equal(l.summaries.Cloud.percentage,null);assert.equal(l.projects.Cloud.state,'unknown');
  assert.equal(l.overall.percentage,null);assert.equal(l.overall.known,0);
  assert(l.cells.filter(c=>c.kind==='railway').every(c=>c.status==='neutral'));
  assert(!renderApprovedGraph(s).includes('>0%</text>'));
});
test('known allocated observations have an explicitly partial percentage without full capacity coverage',()=>{
  const s=projectFixture('Cloud','running');let l=graphLayout(s);
  assert.equal(l.projects.Cloud.percentage,100);assert.equal(l.projects.Cloud.state,'healthy');
  s.sources[0].executionCoverageComplete=false;l=graphLayout(s);
  assert.equal(l.projects.Cloud.state,'healthy');assert.equal(l.summaries.Cloud.percentage,100);
  assert.equal(l.overall.percentage,100);assert.equal(l.overall.complete,false);
  const svg=renderApprovedGraph(s);assert(svg.includes('>13/13 observed</text>'));assert(svg.includes('>partial</text>'));
  assert(svg.includes('not global execution health'));
});
test('missing, duplicate, unverified and expired bindings cannot imply project membership',()=>{
  for(const change of [s=>delete s.projectAssociations,s=>s.projectAssociations.forEach(a=>a.verified=false),
    s=>s.projectAssociations.forEach(a=>a.observedAt='2026-11-09T00:00:00Z')]){
    const s=projectFixture('Cloud','blocked');change(s);const l=graphLayout(s);
    assert(l.cells.every(c=>c.slotId===null&&c.status==='neutral'));assert.equal(l.projects.Cloud.state,'unknown');
  }
  const s=projectFixture('Cloud','blocked');s.projectAssociations.push({...s.projectAssociations[0]});
  assert.equal(graphLayout(s).cells.find(c=>c.id==='Cloud-primary-1').slotId,null);
});
test('source and slot expiration neutralizes formerly verified mapped running or blocked observations',()=>{
  for(const state of ['running','blocked']) {
    const s=projectFixture('Cloud',state),l=graphLayout(s,{now:'2026-11-10T05:03:00.000Z'});
    assert(l.cells.every(c=>c.status==='neutral'));assert.equal(l.projects.Cloud.percentage,null);
    assert.equal(l.projects.Cloud.state,'unknown');
  }
});
test('only adjacent durable states drive railway lamps, never local worker aggregates',()=>{
  const s=projectFixture('CIX','blocked');s.projectAssociations=s.projectAssociations.slice(0,1);
  let l=graphLayout(s);
  assert.equal(l.projects.CIX.state,'blocked');assert.equal(l.projects.CIX.percentage,0);
  assert(l.cells.filter(c=>c.kind==='railway').every(c=>c.status==='neutral'));
  s.projectAssociations[0].kind='durable';delete s.projectAssociations[0].index;
  const a=graphLayout(s,{phase:0}),b=graphLayout(s,{phase:1});
  const railsA=a.cells.filter(c=>c.kind==='railway'&&c.status==='blocked'),railsB=b.cells.filter(c=>c.kind==='railway'&&c.status==='blocked');
  assert.equal(railsA.length,1);assert.equal(railsB.length,1);assert.notEqual(railsA[0].id,railsB[0].id);
  assert.equal(graphLayout(s,{reducedMotion:true}).cells.filter(c=>c.kind==='railway'&&c.status==='blocked').length,2);
  const svg=renderApprovedGraph(s);assert(svg.includes('ag-animated-rail ag-rail-0'));assert(svg.includes('ag-animated-rail ag-rail-1'));
  assert(svg.includes('prefers-reduced-motion:reduce'));
});
test('red rail override and neutral gaps respect adjacent evidence',()=>{
  const health={Cloud:{state:'blocked'},UIF:{state:'healthy'}};
  assert.equal(railwayState(['Cloud','UIF'],health,1),'blocked');
  assert.equal(railwayState(['Cloud','UIF'],health,0),'neutral');
  assert.equal(railwayState(['Cloud','UIF'],health,0,true),'blocked');
});
test('XML text escaped; arbitrary IDs, URLs, fonts and raw markup never become attributes',()=>{
  const s=manual();s.slots[0].topics=['<script>alert("x")</script>&'];
  s.slots[0].rawText='PRIVATE SECRET';s.revision='" onload="evil';
  s.slots.push({...s.slots[0],id:'private-sensitive-id',alias:'private-sensitive-id'});
  const svg=renderApprovedGraph(s,{selected:'" onclick="evil'});
  assert(!svg.includes('<script>'));assert(svg.includes('&lt;script&gt;'));
  assert(!svg.includes('PRIVATE SECRET'));assert(!svg.includes('private-sensitive-id'));assert(!svg.includes('onload='));
  assert(!svg.includes('onclick='));assert(!/href="https?:/.test(svg));
  for(const m of svg.matchAll(/href="([^"]+)"/g))assert(m[1].startsWith('data:image/png;base64,'));
});
test('light and dark preserve same geometry and constant 28-world-unit icons and relief',()=>{
  for(const mode of ['full','compact'])for(const theme of ['light','dark']){
    const svg=renderApprovedGraph(manual(),{mode,theme,width:1024,height:768});
    assert(svg.includes(`data-layout="${mode}" data-theme="${theme}"`));
    assert(svg.includes('width="1024" height="768"'));assert(svg.includes('stdDeviation="9"'));
    assert(svg.includes('stdDeviation="7"'));assert(svg.includes('font-family:MorganRoboto'));
    for(const m of svg.matchAll(/<image [^>]+width="([^"]+)" height="([^"]+)"/g))assert.deepEqual(m.slice(1),['28','28']);
    assert.equal(statuses(svg).length,mode==='full'?91:13);
    assert(svg.includes(theme==='dark'?'#141C2A':'#F0F2F6'));
  }
});
test('rendering is deterministic and does not modify snapshot or reviewed geometry',()=>{
  const s=manual(),before=JSON.stringify(s),g=JSON.stringify(G);
  assert.equal(renderApprovedGraph(s),renderApprovedGraph(s));assert.equal(JSON.stringify(s),before);assert.equal(JSON.stringify(G),g);
  assert(renderApprovedGraph(null).includes('Captured unavailable'));
});
test('runtime sources contain no filesystem or fixture reads, no sample state, no hidden data requests',()=>{
  for(const name of ['approved-graph.js','approved-geometry.js','approved-assets.js']){
    const source=readFileSync(new URL('../public/lib/'+name,import.meta.url),'utf8');
    assert(!/\bfetch\s*\(|node:fs|readFile|fixtures\.json|sample_status|synthetic_status/.test(source),name);
  }
});

test('manual capture is fixed while receiver projection timestamp advances',()=>{
  const s=manual();s.generatedAt='2026-11-10T10:12:30.111Z';
  const l=graphLayout(s);assert.equal(l.capturedAt,at);assert.equal(l.projectionAt,s.generatedAt);
  assert(renderApprovedGraph(s).includes('Captured '+at));
  s.sources.push({...s.sources[0],lastObservedAt:'2026-11-10T04:59:59.111Z'});
  assert.equal(graphLayout(s).capturedAt,'2026-11-10T04:59:59.111Z');
});

test('all reviewed geometry coordinates and outlines retain the canonical v36 digest',()=>{
  const canonical=JSON.stringify({cells:G.cells,groups:G.groups,bounds:G.bounds,compactBounds:G.compactBounds});
  assert.equal(createHash('sha256').update(canonical).digest('hex'),'4eb2b8fde6ab0870fb59206039ef7251d26b0a274d1c3e84ac3d9f7f1c8cc974');
});
test('footer wrapping fits measured Roboto widths even for long unbroken public text',()=>{
  const s=manual();s.slots[0].topics=['W'.repeat(200)];
  for(const mode of ['full','compact']){
    const l=graphLayout(s,{mode}),size=mode==='compact'?9:10,max=l.width-2*(mode==='compact'?12:48);
    for(const line of l.footerLines){
      const width=[...line].reduce((sum,c)=>sum+(FONT_ADVANCES[c]??.62)*size,0);
      assert(width<=max+1e-8);
    }
  }
});
