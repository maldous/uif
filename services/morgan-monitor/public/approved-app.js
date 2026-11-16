import { renderApprovedGraph, isRuntimeProjection } from './lib/approved-graph.js';

const validTime = value => Number.isFinite(Date.parse(value));
const empty = () => ({ schemaVersion:1, slots:[], sources:[], summary:{ executionCoverageComplete:false }, conversationTopics:{ voice:[], chat:[], status:[] } });

/** All current-state claims are withheld when transport or source timing is unknown.
 * Observation timestamps are preserved, never replaced with request/check time. */
export function snapshotAtTime(input, now = Date.now(), reachable = true) {
  const snapshot = structuredClone(input);
  const generated = Date.parse(snapshot.generatedAt);
  const ageAt = (record, observed, threshold) => {
    const at = Date.parse(observed);
    if (!Number.isFinite(at) || !Number.isFinite(threshold) || threshold <= 0) return false;
    const recordedAge = Number.isFinite(record.ageSeconds) && Number.isFinite(generated)
      ? record.ageSeconds + Math.max(0, now - generated) / 1000 : 0;
    return at <= now + 30000 && Math.max((now - at) / 1000, recordedAge) < threshold;
  };
  snapshot.sources = (snapshot.sources || []).map(source => {
    const fresh = reachable && source.status === 'fresh' &&
      ageAt(source, source.lastObservedAt, source.staleAfterSeconds) &&
      validTime(source.lastSuccessAt) && now - Date.parse(source.lastSuccessAt) < source.staleAfterSeconds * 1000;
    return { ...source, status: fresh ? 'fresh' : source.status === 'fresh' ? (reachable ? 'stale' : 'unavailable') : source.status,
      executionCoverageComplete: fresh && source.executionCoverageComplete === true };
  });
  snapshot.slots = (snapshot.slots || []).map(slot => {
    const source = snapshot.sources.find(s => s.domain === slot.domain && s.aliases?.includes(slot.alias));
    const fresh = reachable && slot.availability === 'fresh' && source?.status === 'fresh' && ageAt(slot, slot.observedAt, source.staleAfterSeconds);
    return fresh ? slot : { ...slot, availability: slot.availability === 'fresh' ? (reachable ? 'stale' : 'unavailable') : slot.availability,
      recordedState: slot.recordedState ?? slot.displayState, displayState:null };
  });
  if (Array.isArray(snapshot.projectAssociations)) snapshot.projectAssociations = snapshot.projectAssociations.filter(binding => {
    const slot = snapshot.slots.find(slot => slot.id === binding.slotId);
    return reachable && slot?.availability === 'fresh' && slot.assignment === 'assigned' &&
      snapshot.sources.some(source => source.domain === slot.domain && source.aliases?.includes(slot.alias) && source.status === 'fresh');
  });
  if (Array.isArray(snapshot.projectActivities)) snapshot.projectActivities = snapshot.projectActivities.filter(activity => {
    const owners = snapshot.sources.filter(source => source.activityBindings?.some(binding => binding.project === activity.project && binding.kind === activity.kind));
    const source = owners.length === 1 ? owners[0] : null;
    return reachable && source?.status === 'fresh' && activity.verified === true &&
      activity.rosterGeneration === source.rosterGeneration && Date.parse(activity.observedAt) <= now &&
      ageAt(activity, activity.observedAt, source.staleAfterSeconds);
  });
  const allowance = snapshot.fooAllowance || {};
  const allowanceSource = snapshot.sources.find(s => s.capabilities?.includes('foo_allowance'));
  if (!reachable || !allowance.verified || allowance.availability !== 'fresh' || allowanceSource?.status !== 'fresh' ||
      !ageAt(allowance, allowance.observedAt, allowance.staleAfterSeconds)) {
    snapshot.fooAllowance = { ...allowance, verified:false, availability:'unavailable', state:'unavailable', canRunAgents:null };
  }
  snapshot.summary = { ...snapshot.summary, reportingSources: snapshot.sources.filter(source => source.status === 'fresh').length, executionCoverageComplete: reachable && snapshot.summary?.executionCoverageComplete === true &&
    snapshot.sources.length > 0 && snapshot.sources.filter(s => s.aliases?.length).every(s => s.executionCoverageComplete) &&
    snapshot.slots.every(s => s.availability === 'fresh') };
  return snapshot;
}

export function chooseMode(requested, snapshot, previous = 'full') {
  if (requested === 'full' || requested === 'compact') return requested;
  const allowance = snapshot.fooAllowance;
  if (allowance?.verified && allowance.availability === 'fresh') {
    if (allowance.state === 'exhausted' && allowance.canRunAgents === false) return 'compact';
    if (allowance.state === 'valid' && allowance.canRunAgents === true) return 'full';
  }
  return previous;
}
export function viewPreferences(search) {
  const params = new URLSearchParams(search), mode = params.get('mode') ?? params.get('layout');
  return { mode: ['full','compact','auto'].includes(mode) ? mode : ['o2','hexagon','focus'].includes(mode) ? 'full' : 'auto', theme: ['light','dark','system'].includes(params.get('theme')) ? params.get('theme') : 'system' };
}

export function isPublicProjection(value) {
  const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
  const list = x => x === undefined || (Array.isArray(x) && x.length <= 64 && x.every(v => typeof v === 'string' && v.length <= 300));
  const date = x => x === undefined || x === null || validTime(x);
  const availability = ['fresh','stale','outage','unavailable','unconfigured','error'];
  return object(value) && value.schemaVersion === 1 && validTime(value.generatedAt) &&
    Array.isArray(value.sources) && value.sources.length <= 16 && value.sources.every(source => object(source) &&
      ['cloud','foo'].includes(source.domain) && Array.isArray(source.aliases) && list(source.aliases) &&
      list(source.capabilities) && availability.includes(source.status) && date(source.lastObservedAt) && date(source.lastSuccessAt)) &&
    Array.isArray(value.slots) && value.slots.length <= 70 && value.slots.every(slot => object(slot) &&
      /^(cloud-[A-F]|foo-(?:[1-9]|[1-5][0-9]|6[0-4]))$/.test(slot.id) && slot.id === `${slot.domain}-${slot.alias}` &&
      availability.includes(slot.availability) && list(slot.topics) && list(slot.blockers) && date(slot.observedAt)) &&
    (value.fooAllowance === undefined || object(value.fooAllowance)) && isRuntimeProjection(value);
}

export function startMonitor() {
  const board = document.getElementById('board'), toggle = document.getElementById('theme-toggle');
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const smallViewport = window.matchMedia('(max-width: 600px), (max-height: 480px)');
  let preferences = viewPreferences(location.search), raw = empty(), snapshot = empty(), reachable = false;
  let automaticMode = 'full', activeController = null, requestGeneration = 0, pollTimer = null, expiryTimer = null, stopped = false, stateSignature = null;
  const theme = () => preferences.theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : preferences.theme;
  const signature = value => JSON.stringify([value.sources.map(s=>s.status),value.slots.map(s=>[s.availability,s.displayState]),value.fooAllowance?.verified,value.projectActivities?.map(a=>[a.project,a.state,a.observedAt])]);
  function draw() {
    snapshot = snapshotAtTime(raw, Date.now(), reachable);
    automaticMode = chooseMode('auto', snapshot, automaticMode);
    const mode = preferences.mode === 'full' || preferences.mode === 'compact' ? preferences.mode : smallViewport.matches ? 'compact' : automaticMode;
    const currentTheme = theme();
    const svg = renderApprovedGraph(snapshot, {mode,theme:currentTheme,now:Date.now(),interactive:false,footer:false,reducedMotion:reduced.matches});
    document.documentElement.dataset.theme = currentTheme;
    toggle.setAttribute('aria-label', currentTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    toggle.setAttribute('aria-pressed', String(currentTheme === 'dark'));
    board.innerHTML = svg;
    board.dataset.connection = reachable ? 'available' : 'unavailable';
    board.setAttribute('aria-description', !reachable ? 'Status unavailable. Current-state claims are withheld; retained observations keep their original capture time.' :
      snapshot.sources.some(source => source.observationMode === 'manual') ? 'Timestamped public snapshot. Manual observations do not provide continuous session coverage.' :
      snapshot.summary.executionCoverageComplete ? 'Fresh public observations with complete execution coverage.' : 'Public observations received. Execution coverage and project mapping remain incomplete.');
    stateSignature = signature(snapshot);
  }
  function expire() {
    if (stopped) return;
    const candidate = snapshotAtTime(raw, Date.now(), reachable);
    if (signature(candidate) !== stateSignature) draw();
    expiryTimer = setTimeout(expire, 1000);
  }
  async function poll() {
    if (stopped) return;
    clearTimeout(pollTimer); activeController?.abort(); pollTimer = setTimeout(poll, 5000);
    const generation = ++requestGeneration, controller = new AbortController(); activeController = controller;
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch('/api/status', {cache:'no-store',credentials:'omit',signal:controller.signal});
      if (!response.ok) throw Error('Public feed unavailable');
      const value = await response.json();
      if (stopped || generation !== requestGeneration) return;
      if (!isPublicProjection(value)) throw Error('Invalid public projection');
      const previous = raw;
      raw = value; reachable = true;
      try { draw(); } catch (error) { raw = previous; throw error; }
    } catch {
      if (stopped || generation !== requestGeneration) return;
      reachable = false; draw();
    } finally {
      clearTimeout(timeout);
      if (generation === requestGeneration) activeController = null;
    }
  }
  // A native button supplies Enter/Space activation without a second key handler.
  toggle.onclick = () => {
    preferences.theme = theme() === 'dark' ? 'light' : 'dark';
    const url = new URL(location.href); url.searchParams.set('theme',preferences.theme);
    history.replaceState(null,'',url); draw();
  };
  systemTheme.addEventListener('change',()=>{if(preferences.theme==='system')draw();});
  smallViewport.addEventListener('change',()=>draw()); reduced.addEventListener('change',()=>draw());
  window.addEventListener('popstate',()=>{preferences=viewPreferences(location.search);draw();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!stopped){draw();poll();}});
  window.addEventListener('pagehide',()=>{stopped=true;requestGeneration++;activeController?.abort();activeController=null;clearTimeout(pollTimer);clearTimeout(expiryTimer);reachable=false;draw();});
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;reachable=false;draw();poll();expire();}});
  draw(); poll(); expire();
}
if (typeof document !== 'undefined') startMonitor();
