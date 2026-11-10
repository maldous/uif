import { runtimeEvidence, isRuntimeProjection } from './session-runtime.js';
import { APPROVED_GEOMETRY as GEOMETRY } from './approved-geometry.js';
import { APPROVED_ASSETS as ASSETS, FONT_ADVANCES } from './approved-assets.js';

/**
 * Deterministic port of the reviewed railway-v36 geometry and appearance.
 * Geometry describes capacity, never current workers. Public slots do not imply
 * project identity. In particular cloud A/B are NOT Cloud/UIF assignments.
 *
 * Receiver-owned public projection contract for sampled session runtime:
 * projectAssociations: [{slotId, project:'Cloud'|'UIF'|'ANQR'|'CIX'|'OM'|'CQ',
 *   kind:'durable'|'primary'|'overflow', index:1..6 (not for durable),
 *   verified:true, observedAt:ISO timestamp, runtimeGeneration:positive integer}].
 * Only unique, current, matching-generation associations
 * with a known public slot are used. No ordinal, domain, topic or environmentKey
 * inference occurs. Health percentages cover only current, verified, allocated
 * observations with known state. Partial discovery is always visibly qualified.
 * Shared railway lamps represent the two adjacent durable cells, not capacity.
 */
export const escapeGraphXml = value => String(value ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
const esc = escapeGraphXml;
export { isRuntimeProjection };
const PROJECTS = Object.freeze([...GEOMETRY.projects]);
const HEALTHY = new Set(['running','idle','completed']);
const BLOCKED = new Set(['blocked','failed']);
const KNOWN = new Set([...HEALTHY,...BLOCKED,'stopped']);
const PUBLIC_ID = /^(cloud-[A-F]|foo-(?:[1-9]|[1-5][0-9]|6[0-4]))$/;
const time = value => typeof value === 'number' ? value : Date.parse(value);
const number = value => Number.isFinite(value) ? Number(value.toFixed(5)) : 0;
const pp = points => points.map(([x,y]) => `${number(x)},${number(y)}`).join(' ');
const path = rings => rings.map(points => 'M'+points.map(([x,y]) => `${number(x)},${number(y)}`).join('L')+'Z').join(' ');
const array = value => Array.isArray(value) ? value : [];
const text = (value,max=90) => String(value ?? '').replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,max);
const modeFor = options => (options.mode ?? options.layout) === 'compact' ? 'compact' : 'full';
const sourceFor = (snapshot,slot) => {
  const owners = array(snapshot.sources).filter(source => source.domain === slot?.domain && array(source.aliases).includes(slot?.alias));
  return owners.length === 1 ? owners[0] : null;
};
const cutoff = source => Number.isFinite(source?.staleAfterSeconds) && source.staleAfterSeconds > 0
  ? source.staleAfterSeconds * 1000 : 120000;
function currentTimestamp(value,now,maxAge) {
  const at = time(value);
  return Number.isFinite(now) && Number.isFinite(at) && at <= now && now-at < maxAge;
}
function publicSlot(slot) {
  return Boolean(slot && PUBLIC_ID.test(slot.id) && slot.id === `${slot.domain}-${slot.alias}`);
}
function freshEvidence(snapshot,slot,now) {
  const source = sourceFor(snapshot,slot);
  if (!source || source.status !== 'fresh' || slot.availability !== 'fresh' ||
      !currentTimestamp(slot.observedAt,now,cutoff(source)) ||
      !currentTimestamp(source.lastObservedAt,now,cutoff(source))) return false;
  if (slot.proof === 'session_runtime') {
    let evidence;
    try { evidence = runtimeEvidence(slot.runtime); } catch { return false; }
    return array(source.capabilities).length === 1 && source.capabilities[0] === 'session_runtime' &&
      source.evidenceScope === 'session_runtime' && source.observationMode === 'sampled' &&
      Number.isSafeInteger(slot.runtimeGeneration) && slot.runtimeGeneration > 0 &&
      slot.runtimeGeneration === source.runtimeGeneration && slot.recordedState === evidence.state &&
      slot.displayState === (evidence.state === 'unknown' ? null : evidence.state) &&
      JSON.stringify(slot.blockerKeys) === JSON.stringify(evidence.blockerKeys);
  }
  if (slot.proof === 'process') return array(source.capabilities).includes('process');
  return slot.proof === 'coordinator_lifecycle' && slot.domain === 'cloud' &&
    Number.isSafeInteger(slot.rosterGeneration) && slot.rosterGeneration > 0 &&
    slot.rosterGeneration === source.rosterGeneration &&
    array(source.capabilities).includes('coordinator_lifecycle');
}
function cellState(snapshot,slot,now) {
  if (!slot || slot.assignment !== 'assigned' || !freshEvidence(snapshot,slot,now)) return 'neutral';
  if (BLOCKED.has(slot.displayState)) return 'blocked';
  if (HEALTHY.has(slot.displayState)) return 'success';
  return 'neutral';
}
function knownProcess(snapshot,slot,now) {
  const source = sourceFor(snapshot,slot);
  return Boolean(slot && slot.proof === 'process' && freshEvidence(snapshot,slot,now) &&
    source.executionCoverageComplete === true && source.coverage?.complete === true &&
    Number.isSafeInteger(source.coverage?.total) && source.coverage.total >= 0 &&
    source.coverage.observed === source.coverage.total &&
    (slot.assignment === 'unassigned' || (slot.assignment === 'assigned' && KNOWN.has(slot.displayState))));
}
function associations(snapshot,now) {
  const slots = new Map();
  const duplicateSlots = new Set();
  for (const slot of array(snapshot.slots)) if (publicSlot(slot)) {
    if (slots.has(slot.id)) duplicateSlots.add(slot.id);
    slots.set(slot.id,slot);
  }
  for (const id of duplicateSlots) slots.delete(id);
  const candidates=[];
  if (!isRuntimeProjection(snapshot)) return {map:new Map(),slots};
  for (const binding of array(snapshot.projectAssociations)) {
    const slot=slots.get(binding?.slotId), source=sourceFor(snapshot,slot);
    if (!slot || !source || binding.verified !== true || !PROJECTS.includes(binding.project) ||
        !['durable','primary','overflow'].includes(binding.kind) ||
        !currentTimestamp(binding.observedAt,now,cutoff(source))) continue;
    if (slot.proof === 'session_runtime') {
      const allowed=['slotId','project','kind','index','verified','observedAt','runtimeGeneration'];
      if (Object.keys(binding).some(key=>!allowed.includes(key)) ||
          binding.runtimeGeneration !== slot.runtimeGeneration || binding.runtimeGeneration !== source.runtimeGeneration ||
          binding.observedAt !== slot.observedAt || slot.assignment !== 'assigned' || !freshEvidence(snapshot,slot,now) ||
          (binding.kind === 'durable' && Object.hasOwn(binding,'index'))) continue;
    } else if (Object.hasOwn(binding,'runtimeGeneration')) continue;
    const id=binding.kind === 'durable' ? `durable-${binding.project}` :
      Number.isInteger(binding.index) && binding.index>=1 && binding.index<=6
        ? `${binding.project}-${binding.kind}-${binding.index}` : null;
    if (id && GEOMETRY.cells.some(cell => cell.id === id)) candidates.push({id,slot});
  }
  const map=new Map();
  for (const candidate of candidates) {
    if (candidates.filter(other => other.id===candidate.id).length===1 &&
        candidates.filter(other => other.slot.id===candidate.slot.id).length===1)
      map.set(candidate.id,candidate.slot);
  }
  return {map,slots};
}
function nativeActivities(snapshot,now) {
  const activities=new Map();
  for (const activity of array(snapshot.projectActivities)) {
    const owners=array(snapshot.sources).filter(source=>array(source.activityBindings).some(binding=>
      binding.project===activity.project && binding.kind===activity.kind));
    if (owners.length!==1 || activities.has(activity.project) || activity.verified!==true ||
        activity.kind!=='native_worker' || !PROJECTS.includes(activity.project) ||
        !['running','completed','blocked','unknown'].includes(activity.state)) continue;
    const source=owners[0];
    if (source.status!=='fresh' || source.rosterGeneration!==activity.rosterGeneration ||
        !currentTimestamp(source.lastObservedAt,now,cutoff(source)) ||
        !currentTimestamp(activity.observedAt,now,cutoff(source))) continue;
    activities.set(activity.project,activity);
  }
  return activities;
}
function health(snapshot,cells,bindings,now,coverageComplete=false) {
  const assigned=cells.map(cell=>bindings.get(cell.id)).filter(slot=>slot?.assignment==='assigned');
  const known=assigned.filter(slot=>freshEvidence(snapshot,slot,now) && KNOWN.has(slot.displayState));
  const healthy=known.filter(slot=>HEALTHY.has(slot.displayState)).length;
  const blocked=known.some(slot=>BLOCKED.has(slot.displayState));
  const complete=coverageComplete && known.length===assigned.length;
  return {complete,percentage:known.length?Math.round(100*healthy/known.length):null,
    allocated:assigned.length,known:known.length,healthy,unknown:assigned.length-known.length,
    state:blocked?'blocked':known.length && healthy===known.length?'healthy':'unknown'};
}
// One checkerboard phase for the even ring ensures opposite-phase lamps on each
// side of EVERY blocked durable tile, including two adjacent blocked neighbors.
// The off phase stays neutral: it must not falsely assert a healthy mixed pair.
export function railwayState(pair,durables,phase=0,reducedMotion=false) {
  const rail=GEOMETRY.cells.find(cell=>cell.kind==='railway' && cell.projects.length===pair.length &&
    cell.projects.every(project=>pair.includes(project)));
  if (!rail) return 'neutral';
  if (pair.some(project=>durables[project]?.state==='blocked'))
    return reducedMotion || (Number(rail.id.split('-')[1])%2 === (phase===1?1:0)) ? 'blocked' : 'neutral';
  return pair.every(project=>durables[project]?.state==='healthy') ? 'success' : 'neutral';
}
const measuredWidth=(value,size)=>[...String(value)].reduce((sum,c)=>sum+(FONT_ADVANCES[c]??.62)*size,0);
function wrap(value,maxWidth,size) {
  const words=text(value,300).split(/\s+/),rows=[];let line='';
  for (const word of words) {
    if(line && measuredWidth(line+' '+word,size)>maxWidth){rows.push(line);line='';}
    for(const char of word){
      if(line && measuredWidth(line+char,size)>maxWidth){rows.push(line);line='';}
      line+=char;
    }
    line+=' ';
  }
  if(line.trim())rows.push(line.trim());
  return rows;
}
function fitText(value,maxWidth,size) {
  let result=text(value,90);
  if(measuredWidth(result,size)<=maxWidth)return result;
  while(result && measuredWidth(result+'…',size)>maxWidth)result=[...result].slice(0,-1).join('');
  return result+'…';
}
function observationRows(snapshot,slots,mapped,now) {
  return [...slots.values()].filter(slot=>slot.assignment==='assigned' && !mapped.has(slot.id) && slot.observedAt &&
    ['registry','turn_history','coordinator_lifecycle','process','session_runtime'].includes(slot.proof)).map(slot=>{
      const source=sourceFor(snapshot,slot),fresh=slot.availability==='fresh' && source?.status==='fresh' &&
        currentTimestamp(slot.observedAt,now,cutoff(source)) && currentTimestamp(source?.lastObservedAt,now,cutoff(source));
      const subject=array(slot.topics).map(value=>text(value,60)).filter(Boolean).join(' · ') || 'Subject not recorded';
      return {slotId:slot.id,alias:slot.alias,domain:slot.domain,subject,observedAt:text(slot.observedAt,40),
        manual:source?.observationMode==='manual' || slot.proof==='coordinator_lifecycle',stale:!fresh,
        recordedState:KNOWN.has(slot.recordedState)?slot.recordedState:'unknown',status:'neutral'};
    });
}
/** Returns the same screen geometry used by the SVG, plus safe inspection data. */
export function graphLayout(snapshot={},options={}) {
  snapshot=snapshot ?? {};
  if (!isRuntimeProjection(snapshot)) snapshot={};
  const mode=modeFor(options), now=time(options.now ?? snapshot.generatedAt),
    {map:bindings,slots}=associations(snapshot,now),activities=nativeActivities(snapshot,now),
    workerCells=GEOMETRY.cells.filter(cell=>['primary','overflow','durable'].includes(cell.kind)),
    mappedIds=new Set([...bindings.values()].map(slot=>slot.id)),
    allocated=[...slots.values()].filter(slot=>slot.assignment==='assigned'),
    coverageComplete=snapshot.summary?.executionCoverageComplete===true && array(snapshot.sources).length>0 &&
      array(snapshot.sources).every(source=>source.executionCoverageComplete===true && array(source.aliases).length>0 &&
        array(source.aliases).every(alias=>knownProcess(snapshot,slots.get(`${source.domain}-${alias}`),now))) &&
      allocated.every(slot=>mappedIds.has(slot.id)),
    projects=Object.fromEntries(PROJECTS.map(project=>[project,
      health(snapshot,workerCells.filter(cell=>cell.project===project),bindings,now,coverageComplete)])),
    summaries=Object.fromEntries(PROJECTS.map(project=>[project,
      health(snapshot,workerCells.filter(cell=>cell.project===project && cell.kind!=='durable'),bindings,now,coverageComplete)])),
    durables=Object.fromEntries(PROJECTS.map(project=>[project,
      health(snapshot,[GEOMETRY.cells.find(cell=>cell.id===`durable-${project}`)],bindings,now,coverageComplete)])),
    overall={...health(snapshot,workerCells,bindings,now,coverageComplete),
      unknownAllocated:allocated.filter(slot=>!freshEvidence(snapshot,slot,now) || !KNOWN.has(slot.displayState)).length,
      unmappedAllocated:allocated.filter(slot=>!mappedIds.has(slot.id)).length},
    bounds=mode==='compact'?GEOMETRY.compactBounds:GEOMETRY.bounds,pad=mode==='compact'?18:48,
    [minx,miny,maxx,maxy]=bounds,width=maxx-minx+2*pad,graphHeight=maxy-miny+2*pad,
    tr=([x,y])=>[x-minx+pad,y-miny+pad],
    cells=GEOMETRY.cells.filter(cell=>mode==='full'||['durable','overall','railway'].includes(cell.kind)).map(cell=>{
      const slot=bindings.get(cell.id),[x,y]=tr([cell.x,cell.y]);
      return {...cell,x,y,vertices:cell.vertices.map(tr),slotId:slot?.id??null,
        nativeActivity:cell.kind==='durable'?activities.get(cell.project)??null:null,
        observedAt:slot?.observedAt??null,
        currentState:slot?.assignment==='assigned' && freshEvidence(snapshot,slot,now) && KNOWN.has(slot.displayState)?slot.displayState:'unknown',
        adjacent:cell.kind==='railway'?cell.projects.map(project=>({project,state:durables[project].state,
          slotId:bindings.get(`durable-${project}`)?.id??null,
          observedAt:bindings.get(`durable-${project}`)?.observedAt??null})):null,
        status:cell.kind==='railway'?railwayState(cell.projects,durables,options.phase===1?1:0,options.reducedMotion===true):
          cellState(snapshot,slot,now),health:cell.kind==='summary'?summaries[cell.project]:cell.kind==='overall'?overall:null};
    }),
    groups=GEOMETRY.groups.filter(group=>mode==='full'||['durable','center'].includes(group.id))
      .map(group=>({...group,center:tr(group.center),rings:group.rings.map(ring=>ring.map(tr))})),
    observations=observationRows(snapshot,slots,new Set([...bindings.values()].map(slot=>slot.id)),now),
    manualSources=array(snapshot.sources).filter(source=>source.observationMode==='manual'),
    manual=manualSources.length>0,
    captureCandidates=(manual?manualSources:array(snapshot.sources)).map(source=>source.lastObservedAt).filter(value=>Number.isFinite(time(value))).sort((a,b)=>time(a)-time(b)),
    capturedAt=text(manual?captureCandidates[0]:captureCandidates.at(-1),40)||'unavailable',
    projectionAt=text(snapshot.generatedAt,40)||'unavailable',
    expired=array(snapshot.sources).some(source=>['stale','outage','error','unavailable'].includes(source.status) ||
      (source.status==='fresh' && !currentTimestamp(source.lastObservedAt,now,cutoff(source))));
  const footerSize=mode==='compact'?9:10,footerWidth=width-2*(mode==='compact'?12:48);
  const footer=[`Captured ${capturedAt}`,
    `${manual?'Manual snapshot':array(snapshot.sources).some(source=>array(source.capabilities).includes('session_runtime'))?'Sampled session runtime':'Status snapshot'} · ${expired?'stale or unavailable evidence':'dated observations'} · ${overall.complete?'complete verified coverage':'partial coverage; allocated observations only'}`,
    `Configured topology clockwise from top: Cloud, UIF, ANQR, CIX, OM, CQ. ${bindings.size?'Only verified project associations are shown.':'Project associations unverified; empty cells are unobserved.'}`];
  footer.push(healthDescription(overall,'Observed allocated health'));
  if (array(snapshot.sources).some(source=>array(source.capabilities).includes('session_runtime'))) footer.push('Session runtime is a sampled observation, not OS process liveness or complete host discovery.');
  if (observations.length) {
    footer.push('Recorded observations without verified project association:');
    for (const observation of observations) {
      footer.push(`${observation.domain==='cloud'?'Cloud':'Foo'} ${observation.alias}: ${observation.subject} · ${observation.manual?'manual ':''}${observation.stale?'stale snapshot':'snapshot'} · recorded ${observation.recordedState}`);
      footer.push(`Observed ${observation.observedAt}`);
    }
  }
  const footerLines=footer.flatMap(line=>wrap(line,footerWidth,footerSize)),height=options.footer===false?graphHeight:graphHeight+14+footerLines.length*13+10;
  return {mode,width,height,graphHeight,cells,groups,projects,durables,summaries,overall,observations,capturedAt,projectionAt,manual,expired,footerLines};
}
const ICON_KEYS=Object.freeze({engineering:'tools',testing:'test',research:'search',review:'document',
  planning:'compass',delivery:'package',monitoring:'chart',monitoring_coordination:'chart',infrastructure:'server',
  display_refinement:'tools',monitor_service_implementation:'tools',browser_verification:'test',
  public_deployment:'package',ledger_packet_preparation:'document',status_updates:'note'});
function iconFor(slot) {
  return array(slot?.topicKeys).map(key=>ICON_KEYS[key]).find(Boolean)||'document';
}
const image=(asset,x,y)=>`<image x="${number(x-14)}" y="${number(y)}" width="28" height="28" href="${ASSETS[asset]}" aria-hidden="true"/>`;
function label(value,x,y,size,ink,weight=400,extra='') {
  return `<text x="${number(x)}" y="${number(y)}" text-anchor="middle" dominant-baseline="central" font-family="MorganRoboto,Roboto,sans-serif" font-size="${size}" font-weight="${weight}" fill="${ink}" ${extra}>${esc(value)}</text>`;
}
const percentage=health=>health.percentage===null?'?':`${health.percentage}%`;
function healthDescription(health,scope) {
  return `${scope}: ${health.percentage===null?'unknown':percentage(health)}. `+
    `${health.healthy} healthy of ${health.known} fresh, verified, allocated observations with known state. `+
    `${health.complete?'Complete verified discovery. ':'Partial coverage; not global execution health. '}`+
    `${health.unknown} mapped allocated observations have unknown or stale state. `+
    (Object.hasOwn(health,'unknownAllocated')?`${health.unknownAllocated} assigned observations in the received sample have unknown or stale state; ${health.unmappedAllocated} lack a current verified project association and are excluded. `:'')+
    'Running, idle and completed count as healthy; blocked, failed and stopped do not. Unknown states and configured capacity are excluded from the denominator. Empty capacity cells do not establish allocation.';
}
function nativeDescription(project,activity) {
  return `${project} observed native worker: ${activity.state}; observed ${activity.observedAt}. `+
    'Partial native coverage: this worker only, not project completion or all native work. '+
    'Separate native task observation; not durable session runtime and excluded from railway lamps and session health.';
}
function cellDescription(cell) {
  if (cell.kind==='overall') return healthDescription(cell.health,'Observed allocated health');
  if (cell.kind==='summary') return healthDescription(cell.health,`${cell.project} observed allocated health`);
  if (cell.kind==='railway') return `Shared railway lamp for adjacent durable tiles ${cell.projects.join(' and ')}. `+
    cell.adjacent.map(neighbor=>`${neighbor.project}: ${neighbor.state==='blocked'?'verified blocker':neighbor.state==='healthy'?'healthy':'unknown'}${neighbor.observedAt?`; observed ${neighbor.observedAt}`:''}. `).join('')+
    (cell.adjacent.some(neighbor=>neighbor.state==='blocked')?'Blocked warning: opposite-phase red lamps; steady red with reduced motion.':cell.status==='success'?'Both adjacent durable tiles are healthy.':'Healthy pair not verified.');
  return `${cell.project} ${cell.kind}${cell.index?' '+cell.index:''}. ${cell.slotId?'Verified project association.':'Configured capacity; association unverified.'} `+
    `${cell.status==='neutral'?(cell.currentState==='stopped'?'Stopped':'Health unknown'):cell.status==='success'?'Healthy':'Verified blocker'}. State ${cell.currentState}.`+
    (cell.observedAt?` Observed ${cell.observedAt}.`:'')+
    (cell.nativeActivity?' '+nativeDescription(cell.project,cell.nativeActivity):'');
}
/** Pure SVG renderer. options.now defaults to captured time for immutable replay.
 * width/height set output size, never alter geometry or the top-Cloud orientation.
 * interactive adds keyboard-focusable cell targets. Default true.
 * footer:false omits visible metadata and its viewport space for the minimal page;
 * accessible descriptions retain capture, freshness, and unmapped observations.
 * reducedMotion makes rail alerts steady and disables all motion.
 * phase seeds synchronized shared-rail phases (0 or 1).
 */
export function renderApprovedGraph(snapshot={},options={}) {
  snapshot=snapshot??{};
  if (!isRuntimeProjection(snapshot)) snapshot={};
  const layout=graphLayout(snapshot,options),{width:W,height:H,graphHeight,cells,groups}=layout,
    dark=options.theme==='dark',theme=dark?'dark':'light',ink=dark?'#FFFFFF':'#1B2B40',bg=dark?'#141C2A':'#F0F2F6',
    neutral=dark?'#B9C5D5':'#FFFFFF',fill=dark?'#17212E':'#FFFFFF',GREEN='#00C853',RED='#F22946',
    id='ag-'+(layout.mode==='compact'?'c':'f')+'-'+theme,
    ref=name=>`${id}-${name}`,
    tilepath=path(cells.map(cell=>cell.vertices)),
    platepath=path(groups.flatMap(group=>group.rings)),
    groupRings=groups.flatMap(group=>group.rings.filter((ring,index)=>group.renderStroke[index]!==false)),
    grouppath=path(groupRings),
    outputWidth=Number.isFinite(options.width)&&options.width>0?options.width:W,
    outputHeight=Number.isFinite(options.height)&&options.height>0?options.height:H,
    phase=options.phase===1?1:0,reduced=options.reducedMotion===true,
    slots=new Map(array(snapshot.slots).filter(publicSlot).map(slot=>[slot.id,slot]));
  const svg=[`<svg xmlns="http://www.w3.org/2000/svg" class="approved-graph" role="img" aria-labelledby="${ref('title')} ${ref('description')}" data-layout="${layout.mode}" data-theme="${theme}" data-revision="${esc(/^[0-9a-f]{64}$/.test(snapshot.revision)?snapshot.revision:'')}" data-geometry="railway-v36" data-motion="${reduced?'reduced':'enabled'}" stroke-miterlimit="2" viewBox="0 0 ${number(W)} ${number(H)}" width="${number(outputWidth)}" height="${number(outputHeight)}" preserveAspectRatio="xMidYMid meet">`,
    `<title id="${ref('title')}">Morgan monitoring topology</title><desc id="${ref('description')}">Six configured project groups clockwise from Cloud. Percentages marked ? are unknown, never zero by default. ${esc(healthDescription(layout.overall,'Observed allocated health'))} Idle has a green pulse without a traveling edge; only verified running has green edge travel. Blocked tiles retain red borders with a traveling red warning edge. Neutral cells do not assert running, healthy, empty or stopped workers. Captured ${esc(layout.capturedAt)}. ${layout.manual?'Manual snapshot. ':''}${layout.expired?'Stale or unavailable evidence. ':''}${cells.filter(cell=>cell.slotId&&slots.get(cell.slotId)?.proof==='session_runtime').map(cell=>{const slot=slots.get(cell.slotId);return esc(`${cell.project} ${cell.kind}${cell.index?' '+cell.index:''}: sampled session ${slot.recordedState}; observed ${slot.observedAt}. `);}).join('')}${cells.filter(cell=>cell.nativeActivity).map(cell=>esc(nativeDescription(cell.project,cell.nativeActivity))).join(' ')} ${cells.filter(cell=>cell.kind==='railway').map(cell=>esc(cellDescription(cell))).join(' ')} ${options.footer===false?'Project associations remain unverified where no binding exists. '+esc(layout.footerLines.join('. ')):'Recorded subjects without a verified project association are listed separately.'}</desc>`,
    `<defs><style>@font-face{font-family:MorganRoboto;src:url(${ASSETS.fontRegular}) format('truetype');font-weight:400}@font-face{font-family:MorganRoboto;src:url(${ASSETS.fontSemibold}) format('truetype');font-weight:600}
.approved-graph .ag-pulse{animation:ag-pulse 2.4s ease-in-out infinite}.approved-graph .ag-travel{animation:ag-travel 2.4s linear infinite}.approved-graph .ag-blocked-edge{animation:ag-travel 2.4s linear infinite,ag-edge-strobe 1.6s steps(1,end) infinite}.approved-graph .ag-rail-0{animation:ag-rail0 1.6s steps(1,end) infinite}.approved-graph .ag-rail-1{animation:ag-rail1 1.6s steps(1,end) infinite}
@keyframes ag-pulse{0%,100%{opacity:1}50%{opacity:.42}}@keyframes ag-edge-strobe{0%,100%{opacity:1}50%{opacity:.35}}@keyframes ag-travel{to{stroke-dashoffset:-600}}@keyframes ag-rail0{0%,49.999%{opacity:1}50%,100%{opacity:0}}@keyframes ag-rail1{0%,49.999%{opacity:0}50%,100%{opacity:1}}
.approved-graph .ag-cell-hit{cursor:pointer;outline:none}.approved-graph .ag-cell-hit:focus polygon{stroke:#60A5FA;stroke-width:2;fill:#60A5FA;fill-opacity:.10}.approved-graph .ag-reduced-rail{display:none}
.approved-graph[data-motion=\"reduced\"] .ag-pulse,.approved-graph[data-motion=\"reduced\"] .ag-travel,.approved-graph[data-motion=\"reduced\"] .ag-rail-0,.approved-graph[data-motion=\"reduced\"] .ag-rail-1{animation:none}.approved-graph[data-motion=\"reduced\"] .ag-travel{display:none}
@media(prefers-reduced-motion:reduce){.approved-graph .ag-pulse,.approved-graph .ag-travel,.approved-graph .ag-rail-0,.approved-graph .ag-rail-1{animation:none}.approved-graph .ag-travel,.approved-graph .ag-animated-rail{display:none}.approved-graph .ag-reduced-rail{display:inline}}
</style>`];
  for (const [name,blur,dy,col,alpha] of [
    ['tileShadow',5,5,dark?'#000000':'#253A53',dark?.85:.42],
    ['tileAmbient',9,0,dark?'#C9DCF5':'#FFFFFF',dark?.95:.3],
    ['groupShadow',3,3,dark?'#DCEAFF':'#253A53',dark?.8:.45],
    ['groupRelief',7,5,dark?'#D1E3FF':'#253A53',dark?.95:.42],
    ['statusGreen',4.5,0,GREEN,.85],['statusRed',4.5,0,RED,.85]]) {
    svg.push(`<filter id="${ref(name)}" filterUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><feGaussianBlur in="SourceAlpha" stdDeviation="${blur}"/><feOffset dy="${dy}" result="offset"/><feFlood flood-color="${col}" flood-opacity="${alpha}"/><feComposite in2="offset" operator="in"/></filter>`);
  }
  svg.push(`<linearGradient id="${ref('gloss')}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="white" stop-opacity=".14"/><stop offset=".45" stop-color="white" stop-opacity="0"/></linearGradient>`);
  cells.forEach((cell,index)=>svg.push(`<clipPath id="${ref('tile'+index)}"><polygon points="${pp(cell.vertices)}"/></clipPath>`));
  svg.push(`</defs><rect width="${W}" height="${H}" fill="${bg}"/>`,
    `<path d="${platepath}" fill="white" fill-rule="evenodd" filter="url(#${ref('groupRelief')})"/><path d="${platepath}" fill="${dark?'#1B2636':'#EEF2F6'}" fill-rule="evenodd"/>`,
    `<path d="${grouppath}" fill="none" stroke="${ink}" stroke-width="2.4" filter="url(#${ref('groupShadow')})"/>`);
  for (const fx of ['tileShadow','tileAmbient']) svg.push(`<path d="${tilepath}" fill="white" filter="url(#${ref(fx)})"/>`);
  for (const [status,fx] of [['success','statusGreen'],['blocked','statusRed']]) {
    const rings=cells.filter(cell=>cell.kind!=='railway'&&cell.status===status).map(cell=>cell.vertices);
    if(rings.length) svg.push(`<path d="${path(rings)}" fill="white" filter="url(#${ref(fx)})"/>`);
  }
  const lamp=(cell,index,status,classes='')=>{
    const color=status==='success'?GREEN:status==='blocked'?RED:neutral;
    return `<g class="${classes}" data-rail-state="${status}">${status!=='neutral'?`<polygon points="${pp(cell.vertices)}" fill="white" filter="url(#${ref(status==='success'?'statusGreen':'statusRed')})"/>`:''}<polygon class="ag-lamp-face" points="${pp(cell.vertices)}" fill="${status==='neutral'?fill:color}"/><polygon points="${pp(cell.vertices)}" fill="url(#${ref('gloss')})"/><polygon points="${pp(cell.vertices)}" fill="none" stroke="${color}" stroke-width="${2*(status==='neutral'?1.3:5.2)}" stroke-linejoin="miter" clip-path="url(#${ref('tile'+index)})"/></g>`;
  };
  cells.forEach((cell,index)=>{
    const poly=pp(cell.vertices),status=cell.status,color=status==='success'?GREEN:status==='blocked'?RED:neutral;
    svg.push(`<g data-graph-cell="${esc(cell.id)}" data-kind="${cell.kind}" data-status="${status}"${cell.project?` data-project="${cell.project}"`:''}${cell.kind==='railway'?` data-adjacent="${cell.projects.join(' ')}"`:''}><title>${esc(cellDescription(cell))}</title>`);
    if(cell.kind==='railway') {
      const a=railwayState(cell.projects,layout.durables,phase,reduced),b=railwayState(cell.projects,layout.durables,1-phase,reduced);
      if(a===b || reduced) svg.push(lamp(cell,index,a));
      else svg.push(lamp(cell,index,a,'ag-animated-rail ag-rail-0'),lamp(cell,index,b,'ag-animated-rail ag-rail-1'),lamp(cell,index,railwayState(cell.projects,layout.durables,phase,true),'ag-reduced-rail'));
    } else {
      svg.push(`<polygon points="${poly}" fill="${fill}"/>`);
      if(status!=='neutral') svg.push(`<polygon points="${poly}" fill="${color}" opacity="${4/255}"/>`);
      svg.push(`<polygon points="${poly}" fill="url(#${ref('gloss')})"/><polygon class="ag-steady" points="${poly}" fill="none" stroke="${color}" stroke-width="${2*(status==='blocked'?5.2:1.3)}" stroke-linejoin="miter" clip-path="url(#${ref('tile'+index)})"/>`);
      if(status==='success') svg.push(`<polygon class="ag-pulse" points="${poly}" fill="none" stroke="${color}" stroke-width="10.4" stroke-linejoin="miter" clip-path="url(#${ref('tile'+index)})"/>`);
      if(!reduced && status==='blocked') svg.push(`<polygon class="ag-travel ag-blocked-edge" pathLength="600" points="${poly}" fill="none" stroke="#FF9EAE" stroke-width="6" stroke-dasharray="70 530" stroke-linejoin="miter" clip-path="url(#${ref('tile'+index)})"/>`);
      if(!reduced && status==='success' && cell.currentState==='running') svg.push(`<polygon class="ag-travel" pathLength="600" points="${poly}" fill="none" stroke="#AAFFD0" stroke-width="6" stroke-dasharray="70 530" stroke-linejoin="miter" clip-path="url(#${ref('tile'+index)})"/>`);
    }
    const ratio=Math.max(0,cell.r-7)/cell.r,roof=cell.vertices.filter(([,y])=>y<=cell.y+1e-7).map(([x,y])=>[cell.x+(x-cell.x)*ratio,cell.y+(y-cell.y)*ratio]).sort((a,b)=>a[0]-b[0]);
    svg.push(`<polyline points="${pp(roof)}" fill="none" stroke="white" stroke-opacity="${dark?.55:.8}" stroke-width="1.2"/>`);
    if(options.selected===cell.id) svg.push(`<polygon points="${poly}" fill="none" stroke="#60A5FA" stroke-width="2"/>`);
    svg.push('</g>');
  });
  svg.push(`<path d="${grouppath}" fill="none" stroke="${ink}" stroke-width="2.4" stroke-linejoin="miter"/>`);
  for (const cell of cells) {
    const {x,y,r}=cell;
    if(cell.kind==='summary'||cell.kind==='overall') {
      const h=cell.health,col=h.percentage===null||h.allocated===0?ink:h.percentage===100?GREEN:RED;
      svg.push(label(percentage(h),x,y-(cell.kind==='summary'?7:8),cell.kind==='summary'?23:Math.min(30,r*.65),col,600,'class="ag-percentage"'));
      if(cell.kind==='overall') {
        svg.push(label(`${h.healthy}/${h.known} observed`,x,y+15,8.5,ink,400,'class="ag-scope"'));
        if(!h.complete) svg.push(label('partial',x,y+26,8,ink,600,'class="ag-coverage"'));
      }
      if(cell.kind==='summary' && !h.complete && h.known) svg.push(label('observed · partial',x,y+34,8,ink,400,'class="ag-scope"'));
      if(cell.kind==='summary') svg.push(label(cell.project==='Cloud'?'cloud':cell.project,x,y+20,12,ink,600));
    } else if(cell.kind==='durable') {
      if(['Cloud','UIF','CIX'].includes(cell.project)) svg.push(label(cell.project==='Cloud'?'cloud':cell.project,x,y,cell.project==='Cloud'?18:23,ink,600));
      else {
        const asset=cell.project==='ANQR'?'brandANQR':`brand${cell.project}${dark?'Dark':'Light'}`;
        svg.push(image(asset,x,y-28*.8),label(cell.project,x,y+20,10,dark?'#F1F5FA':'#233348'));
      }
      if(cell.nativeActivity) {
        const activity=cell.nativeActivity,activityLabel={running:'native active',completed:'worker done',blocked:'native blocked',unknown:'native ?'}[activity.state];
        const badgeInk=activity.state==='blocked'?(dark?'#FFD486':'#795000'):(dark?'#BDCEE5':'#344D70');
        svg.push(`<g class="ag-native-work" data-native-state="${activity.state}"><title>${esc(nativeDescription(cell.project,activity))}</title><rect x="${number(x-36)}" y="${number(y+30)}" width="72" height="14" rx="7" fill="${dark?'#26384F':'#E7EDF5'}"/>`,
          label(activityLabel,x,y+37,8.5,badgeInk,600),'</g>');
      }
    } else if(['primary','overflow'].includes(cell.kind) && cell.slotId) {
      const slot=slots.get(cell.slotId);
      if(slot?.assignment==='assigned') {
        svg.push(image(iconFor(slot),x,y-(r<40?14:22.4)));
        if(cell.kind==='primary') {
          const subject=fitText(array(slot.topics)[0]||'Recorded assignment',1.35*r,10);
          svg.push(label(subject,x,y+20,10,dark?'#F1F5FA':'#233348'));
        }
      }
    }
  }
  if(options.interactive!==false) cells.forEach((cell,index)=>svg.push(`<g class="ag-cell-hit"${options.interactive===false?'':' tabindex="0" role="button"'} data-cell-id="${esc(cell.id)}" aria-label="${esc(cellDescription(cell))}"><title>${esc(cellDescription(cell))}</title><polygon points="${pp(cell.vertices)}" fill="#000000" fill-opacity="0" pointer-events="all" clip-path="url(#${ref('tile'+index)})"/></g>`));
  if(options.footer!==false) {
  svg.push(`<g class="ag-snapshot-footer" fill="${ink}" font-family="MorganRoboto,Roboto,sans-serif" font-size="${layout.mode==='compact'?9:10}">`);
  layout.footerLines.forEach((line,index)=>svg.push(`<text x="${layout.mode==='compact'?12:48}" y="${number(graphHeight+14+index*13)}">${esc(line)}</text>`));
  svg.push('</g>');
  }
  svg.push('</svg>');
  return svg.join('');
}
