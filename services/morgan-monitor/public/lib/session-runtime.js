const PUBLIC_TOPICS = Object.freeze({
  infrastructure: 'Infrastructure', monitoring: 'Monitoring', research: 'Research', planning: 'Planning', engineering: 'Engineering',
  review: 'Review', testing: 'Testing', delivery: 'Delivery', monitoring_coordination: 'Monitoring coordination', status_display: 'Status display',
  status_updates: 'Status updates', offmenu_sync: 'OffMenu sync', monitor_service_implementation: 'Monitor service implementation',
  compact_display: 'Compact display', layout_design: 'Layout design', worker_identity_mapping: 'Worker identity mapping',
  expressive_animation: 'Expressive animation', public_deployment: 'Public deployment', browser_verification: 'Browser verification',
  display_refinement: 'Display refinement', monitor_coordination: 'Monitor coordination', ledger_packet_preparation: 'Ledger preparation',
});
const PUBLIC_BLOCKERS = Object.freeze({ source_unavailable: 'Source unavailable', coverage_partial: 'Partial coverage',
  awaiting_approval: 'Awaiting approval', awaiting_input: 'Awaiting input', dependency_unavailable: 'Dependency unavailable',
  quota_unavailable: 'Capacity unavailable', execution_failed: 'Recorded execution failure' });
/** Shared strict status union. No turn history, private identifiers or text. */
export function runtimeEvidence(runtime) {
  const fail = () => { throw new Error('Invalid session runtime'); };
  if (!runtime || typeof runtime !== 'object' || Array.isArray(runtime)) fail();
  const active = runtime.type === 'active';
  const keys = active ? ['type', 'activeFlags'] : ['type'];
  if (Object.keys(runtime).length !== keys.length || keys.some(key => !Object.hasOwn(runtime, key)) ||
      !['active', 'idle', 'systemError', 'notLoaded'].includes(runtime.type)) fail();
  const allowed = ['waitingOnApproval', 'waitingOnUserInput'];
  if (active && (!Array.isArray(runtime.activeFlags) || runtime.activeFlags.length > 2 ||
      new Set(runtime.activeFlags).size !== runtime.activeFlags.length ||
      runtime.activeFlags.some(flag => !allowed.includes(flag)))) fail();
  const flags = active ? allowed.filter(flag => runtime.activeFlags.includes(flag)) : [];
  return { runtime: active ? { type: 'active', activeFlags: flags } : { type: runtime.type },
    state: active ? flags.length ? 'blocked' : 'running' : runtime.type === 'idle' ? 'idle' : 'unknown',
    blockerKeys: flags.map(flag => flag === 'waitingOnApproval' ? 'awaiting_approval' : 'awaiting_input') };
}

/** Runtime snapshots have one strict public contract on server and browser.
 * Includes separate config-owned native activities, which are not sessions.
 * Legacy snapshots without either new proof retain their existing validation.
 * Validate against capture time; current wall-clock expiry is a separate step.
 */
export function isRuntimeProjection(snapshot) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const own = (value, key) => Object.hasOwn(value, key);
  const array = value => Array.isArray(value) ? value : [];
  const runtimeSources = array(snapshot?.sources).filter(source => array(source?.capabilities).includes('session_runtime'));
  const activitySources = array(snapshot?.sources).filter(source => object(source) && own(source, 'activityBindings'));
  const activityRelevant = activitySources.length || own(snapshot ?? {}, 'projectActivities');
  const relevant = activityRelevant || runtimeSources.length || array(snapshot?.slots).some(slot => slot?.proof === 'session_runtime' || object(slot) && (own(slot, 'runtime') || own(slot, 'runtimeGeneration'))) ||
    array(snapshot?.sources).some(source => object(source) && own(source, 'runtimeGeneration')) || own(snapshot ?? {}, 'projectAssociations');
  if (!relevant) return true;
  // Historical process-association fixtures predate the runtime contract. Do not
  // reinterpret them unless a runtime source, slot or generation is present.
  if (!activityRelevant && !runtimeSources.length && !array(snapshot?.slots).some(slot => slot?.proof === 'session_runtime' || object(slot) && (own(slot, 'runtime') || own(slot, 'runtimeGeneration'))) &&
      !array(snapshot?.sources).some(source => object(source) && own(source, 'runtimeGeneration')) &&
      !array(snapshot?.projectAssociations).some(binding => object(binding) && own(binding, 'runtimeGeneration'))) return true;
  try {
    const fail = () => { throw Error('Invalid runtime projection'); };
    const exact = (value, required, optional = []) => {
      if (!object(value) || required.some(key => !own(value, key)) || Object.keys(value).some(key => ![...required, ...optional].includes(key))) fail();
    };
    const integer = (n, min, max) => Number.isSafeInteger(n) && n >= min && n <= max;
    const time = value => {
      if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/.test(value)) fail();
      const n = Date.parse(value);
      if (!Number.isFinite(n) || n < Date.UTC(2000, 0, 1) || new Date(n).toISOString().replace('.000Z', 'Z') !== value.replace('.000Z', 'Z')) fail();
      return n;
    };
    const generation = n => integer(n, 1, 1000000);
    const age = n => n === null || integer(n, 0, Number.MAX_SAFE_INTEGER);
    const states = ['running', 'idle', 'stopped', 'completed', 'failed', 'blocked', 'unknown'];
    const capabilities = ['registry', 'turn_history', 'process', 'service_process', 'curation', 'foo_allowance', 'coordinator_lifecycle', 'session_runtime'];
    const publicId = /^(cloud-[A-F]|foo-(?:[1-9]|[1-5][0-9]|6[0-4]))$/;
    const availability = ['fresh', 'stale', 'outage', 'unconfigured', 'unavailable', 'error'];
    const sourceRequired = ['alias', 'domain', 'aliases', 'capabilities', 'status', 'lastReceivedAt', 'lastSuccessAt', 'lastObservedAt',
      'ageSeconds', 'staleAfterSeconds', 'outageAfterSeconds', 'coverage', 'executionCoverageComplete', 'evidenceScope', 'serviceState'];
    const sourceOptional = ['rosterGeneration', 'observationMode', 'runtimeGeneration', 'activityBindings'];
    const slotRequired = ['id', 'domain', 'alias', 'assignment', 'displayState', 'availability', 'recordedState', 'proof', 'observedAt',
      'ageSeconds', 'topicKeys', 'blockerKeys', 'topics', 'blockers'];
    const slotOptional = ['rosterGeneration', 'environmentKey', 'runtimeGeneration', 'runtime'];
    exact(snapshot, ['schemaVersion', 'generatedAt', 'receiver', 'summary', 'sources', 'slots', 'conversationTopics', 'revision'], ['fooAllowance', 'projectAssociations', 'projectActivities']);
    if (snapshot.schemaVersion !== 1 || !/^[0-9a-f]{64}$/.test(snapshot.revision) || !Array.isArray(snapshot.sources) || snapshot.sources.length > 16 ||
        !Array.isArray(snapshot.slots) || snapshot.slots.length !== 70 || (!runtimeSources.length && !activitySources.length) ||
        (runtimeSources.length ? !Array.isArray(snapshot.projectAssociations) : own(snapshot, 'projectAssociations')) ||
        (own(snapshot, 'projectAssociations') && snapshot.projectAssociations.length > 70) ||
        (activitySources.length ? !Array.isArray(snapshot.projectActivities) : own(snapshot, 'projectActivities')) ||
        (own(snapshot, 'projectActivities') && snapshot.projectActivities.length > 6)) fail();
    const captured = time(snapshot.generatedAt);
    exact(snapshot.receiver, ['state', 'storage', 'outsideFoo']);
    if (snapshot.receiver.state !== 'ok' || snapshot.receiver.storage !== 'durable' || snapshot.receiver.outsideFoo !== true) fail();
    exact(snapshot.summary, ['state', 'configuredSources', 'reportingSources', 'executionCoverageComplete']);
    if ((runtimeSources.length ? snapshot.summary.executionCoverageComplete !== false : typeof snapshot.summary.executionCoverageComplete !== 'boolean') || snapshot.summary.configuredSources !== snapshot.sources.length ||
        snapshot.summary.reportingSources !== snapshot.sources.filter(s => s.status === 'fresh').length ||
        !['partial', 'error', 'stale', 'outage'].includes(snapshot.summary.state)) fail();
    const owners = new Map(), sourceIds = new Set(), slots = new Map(), activityOwners = new Map();
    for (const source of snapshot.sources) {
      exact(source, sourceRequired, sourceOptional);
      if (!/^source-(?:[1-9]|1[0-6])$/.test(source.alias) || sourceIds.has(source.alias) || !['cloud', 'foo'].includes(source.domain) ||
          !Array.isArray(source.aliases) || !Array.isArray(source.capabilities) || !availability.includes(source.status)) fail();
      if (source.capabilities.length > capabilities.length || new Set(source.capabilities).size !== source.capabilities.length ||
          source.capabilities.some(key => !capabilities.includes(key)) || !age(source.ageSeconds) ||
          !integer(source.staleAfterSeconds, 30, 3600) || !integer(source.outageAfterSeconds, source.staleAfterSeconds + 1, 86400) ||
          typeof source.executionCoverageComplete !== 'boolean' ||
          !['process_liveness', 'metadata_only', 'coordinator_lifecycle', 'session_runtime'].includes(source.evidenceScope) ||
          ![null, 'running', 'stopped', 'unknown'].includes(source.serviceState)) fail();
      for (const key of ['lastReceivedAt', 'lastSuccessAt', 'lastObservedAt']) if (source[key] !== null) time(source[key]);
      exact(source.coverage, ['complete', 'observed', 'total']);
      if (typeof source.coverage.complete !== 'boolean' || !integer(source.coverage.observed, 0, 1000000) ||
          !(source.coverage.total === null || integer(source.coverage.total, source.coverage.observed, 1000000))) fail();
      if (source.capabilities.includes('coordinator_lifecycle')) {
        if (source.domain !== 'cloud' || !generation(source.rosterGeneration) || source.observationMode !== 'manual' ||
            source.evidenceScope !== 'coordinator_lifecycle' || source.executionCoverageComplete !== false || source.capabilities.includes('process')) fail();
      } else if (own(source, 'rosterGeneration') || (!source.capabilities.includes('session_runtime') && own(source, 'observationMode'))) fail();
      if (own(source, 'activityBindings')) {
        if (!source.capabilities.includes('coordinator_lifecycle') || source.capabilities.length !== 1 || source.aliases.length !== 0 ||
            source.staleAfterSeconds !== 120 || source.outageAfterSeconds !== 600 || source.serviceState !== null ||
            !Array.isArray(source.activityBindings) || source.activityBindings.length < 1 || source.activityBindings.length > 6 ||
            source.coverage.complete !== false || source.coverage.observed !== 0 || source.coverage.total !== null) fail();
        for (const binding of source.activityBindings) {
          exact(binding, ['project', 'kind']);
          if (!['Cloud', 'UIF', 'ANQR', 'CIX', 'OM', 'CQ'].includes(binding.project) || binding.kind !== 'native_worker' || activityOwners.has(binding.project)) fail();
          activityOwners.set(binding.project, source);
        }
        for (const key of ['lastReceivedAt', 'lastSuccessAt', 'lastObservedAt']) if (source[key] !== null && time(source[key]) > captured) fail();
        if ((source.lastObservedAt === null) !== (source.lastSuccessAt === null)) fail();
        if (source.lastSuccessAt !== null && (source.lastReceivedAt === null || time(source.lastSuccessAt) > time(source.lastReceivedAt) ||
            time(source.lastObservedAt) > time(source.lastSuccessAt) ||
            source.ageSeconds !== Math.floor((captured - Math.min(time(source.lastObservedAt), time(source.lastSuccessAt))) / 1000))) fail();
        if (source.status === 'fresh' && (source.lastObservedAt === null || source.ageSeconds === null || source.ageSeconds >= 120)) fail();
      }
      sourceIds.add(source.alias);
      for (const alias of source.aliases) {
        const id = `${source.domain}-${alias}`;
        if (!publicId.test(id) || owners.has(id)) fail();
        owners.set(id, source);
      }
      if (!source.capabilities.includes('session_runtime')) {
        if (own(source, 'runtimeGeneration') || source.evidenceScope === 'session_runtime') fail();
        continue;
      }
      if (source.capabilities.length !== 1 || !generation(source.runtimeGeneration) || own(source, 'rosterGeneration') ||
          source.observationMode !== 'sampled' || source.evidenceScope !== 'session_runtime' || source.executionCoverageComplete !== false ||
          source.serviceState !== null || source.staleAfterSeconds !== 120 || source.outageAfterSeconds !== 600) fail();
      exact(source.coverage, ['complete', 'observed', 'total']);
      if (source.coverage.complete !== false || source.coverage.total !== null || !integer(source.coverage.observed, 0, source.aliases.length) ||
          !(source.ageSeconds === null || integer(source.ageSeconds, 0, Number.MAX_SAFE_INTEGER))) fail();
      for (const key of ['lastReceivedAt', 'lastSuccessAt', 'lastObservedAt']) if (source[key] !== null && time(source[key]) > captured) fail();
      if ((source.lastObservedAt === null) !== (source.lastSuccessAt === null)) fail();
      if (source.lastSuccessAt !== null && (source.lastReceivedAt === null || time(source.lastSuccessAt) > time(source.lastReceivedAt) ||
          time(source.lastObservedAt) > time(source.lastSuccessAt) ||
          source.ageSeconds !== Math.floor((captured - Math.min(time(source.lastObservedAt), time(source.lastSuccessAt))) / 1000))) fail();
      if (source.status === 'fresh' && (source.lastObservedAt === null || source.ageSeconds === null || source.ageSeconds >= 120)) fail();
    }
    for (const slot of snapshot.slots) {
      exact(slot, slotRequired, slotOptional);
      if (!publicId.test(slot.id) || slot.id !== `${slot.domain}-${slot.alias}` || slots.has(slot.id) || !availability.includes(slot.availability)) fail();
      for (const [keys, labels, vocabulary] of [[slot.topicKeys, slot.topics, PUBLIC_TOPICS], [slot.blockerKeys, slot.blockers, PUBLIC_BLOCKERS]]) {
        if (!Array.isArray(keys) || keys.length > 3 || new Set(keys).size !== keys.length ||
            keys.some(key => typeof key !== 'string' || !own(vocabulary, key)) ||
            JSON.stringify(labels) !== JSON.stringify(keys.map(key => vocabulary[key]))) fail();
      }
      if (!['assigned', 'unassigned', 'unavailable'].includes(slot.assignment) ||
          ![null, ...states.filter(state => state !== 'unknown')].includes(slot.displayState) ||
          ![null, ...states].includes(slot.recordedState) || ![null, 'registry', 'turn_history', 'process', 'coordinator_lifecycle', 'session_runtime'].includes(slot.proof) ||
          !age(slot.ageSeconds)) fail();
      if (slot.observedAt !== null) time(slot.observedAt);
      if (own(slot, 'environmentKey') && (slot.assignment !== 'assigned' || !['foo_uif', 'foo_tv', 'cloud_scratch'].includes(slot.environmentKey))) fail();
      if (own(slot, 'rosterGeneration')) {
        const owner = owners.get(slot.id);
        if (!owner?.capabilities.includes('coordinator_lifecycle') || slot.rosterGeneration !== owner.rosterGeneration ||
            ![null, 'coordinator_lifecycle', 'registry', 'turn_history'].includes(slot.proof)) fail();
      }
      if (slot.proof === 'coordinator_lifecycle' && (!own(slot, 'rosterGeneration') ||
          ![null, 'running', 'completed', 'blocked'].includes(slot.displayState) ||
          ![null, 'running', 'completed', 'blocked', 'unknown'].includes(slot.recordedState))) fail();
      slots.set(slot.id, slot);
      const owner = owners.get(slot.id), owned = owner?.capabilities.includes('session_runtime');
      if (!owned) {
        if (slot.proof === 'session_runtime' || own(slot, 'runtime') || own(slot, 'runtimeGeneration')) fail();
        continue;
      }
      if (slot.runtimeGeneration !== owner.runtimeGeneration || own(slot, 'rosterGeneration') || own(slot, 'environmentKey')) fail();
      if (slot.proof === null) {
        if (own(slot, 'runtime') || slot.assignment !== 'unavailable' || slot.observedAt !== null || slot.ageSeconds !== null ||
            slot.recordedState !== null || slot.displayState !== null ||
            ![slot.topicKeys, slot.blockerKeys, slot.topics, slot.blockers].every(value => Array.isArray(value) && value.length === 0)) fail();
        continue;
      }
      if (slot.proof !== 'session_runtime' || slot.assignment !== 'assigned') fail();
      const evidence = runtimeEvidence(slot.runtime), observed = time(slot.observedAt);
      if (owner.lastObservedAt === null || owner.lastSuccessAt === null || observed > captured || observed > time(owner.lastObservedAt) ||
          slot.ageSeconds !== Math.floor((captured - Math.min(observed, time(owner.lastSuccessAt))) / 1000) ||
          slot.recordedState !== evidence.state || JSON.stringify(slot.blockerKeys) !== JSON.stringify(evidence.blockerKeys) ||
          JSON.stringify(slot.blockers) !== JSON.stringify(evidence.blockerKeys.map(key => key === 'awaiting_approval' ? 'Awaiting approval' : 'Awaiting input'))) fail();
      if (!Array.isArray(slot.topicKeys) || !Array.isArray(slot.topics) || slot.topicKeys.length !== slot.topics.length ||
          slot.topicKeys.length > 3 || new Set(slot.topicKeys).size !== slot.topicKeys.length) fail();
      if (slot.availability === 'fresh' && (owner.status !== 'fresh' || slot.ageSeconds >= 120)) fail();
      if (slot.displayState !== (slot.availability === 'fresh' && evidence.state !== 'unknown' ? evidence.state : null)) fail();
    }
    for (const source of runtimeSources) if (source.coverage.observed !== source.aliases.filter(alias => slots.get(`${source.domain}-${alias}`)?.proof === 'session_runtime').length) fail();
    const boundSlots = new Set(), cells = new Set();
    for (const binding of snapshot.projectAssociations || []) {
      exact(binding, ['slotId', 'project', 'kind', 'verified', 'observedAt', 'runtimeGeneration'], ['index']);
      const slot = slots.get(binding.slotId), owner = owners.get(binding.slotId);
      if (!slot || !owner || binding.verified !== true || slot.proof !== 'session_runtime' || slot.assignment !== 'assigned' ||
          slot.availability !== 'fresh' || owner.status !== 'fresh' || binding.runtimeGeneration !== slot.runtimeGeneration ||
          binding.runtimeGeneration !== owner.runtimeGeneration || binding.observedAt !== slot.observedAt ||
          !['Cloud', 'UIF', 'ANQR', 'CIX', 'OM', 'CQ'].includes(binding.project) || !['durable', 'primary', 'overflow'].includes(binding.kind) ||
          (binding.kind === 'durable' ? own(binding, 'index') : !integer(binding.index, 1, 6))) fail();
      const cell = `${binding.project}-${binding.kind}-${binding.index ?? ''}`;
      if (boundSlots.has(binding.slotId) || cells.has(cell)) fail();
      boundSlots.add(binding.slotId); cells.add(cell);
    }
    const activeProjects = new Set();
    for (const activity of snapshot.projectActivities || []) {
      exact(activity, ['project', 'kind', 'verified', 'observedAt', 'rosterGeneration', 'state', 'topicKeys', 'topics', 'blockerKeys', 'blockers']);
      const owner = activityOwners.get(activity.project), observed = time(activity.observedAt);
      if (!owner || activeProjects.has(activity.project) || activity.kind !== 'native_worker' || activity.verified !== true ||
          activity.rosterGeneration !== owner.rosterGeneration || !['running', 'completed', 'blocked', 'unknown'].includes(activity.state) ||
          owner.status !== 'fresh' || owner.lastObservedAt === null || owner.lastSuccessAt === null || observed > captured ||
          observed > time(owner.lastObservedAt) || observed > time(owner.lastSuccessAt) ||
          captured - observed >= owner.staleAfterSeconds * 1000) fail();
      for (const [keys, labels, vocabulary] of [[activity.topicKeys, activity.topics, PUBLIC_TOPICS], [activity.blockerKeys, activity.blockers, PUBLIC_BLOCKERS]]) {
        if (!Array.isArray(keys) || keys.length > 3 || new Set(keys).size !== keys.length ||
            keys.some(key => typeof key !== 'string' || !own(vocabulary, key)) ||
            JSON.stringify(labels) !== JSON.stringify(keys.map(key => vocabulary[key]))) fail();
      }
      if ((activity.state === 'blocked') !== (activity.blockerKeys.length > 0)) fail();
      activeProjects.add(activity.project);
    }
    exact(snapshot.conversationTopics, ['voice', 'chat', 'availability', 'lastObservedAt'], ['status']);
    if (!availability.includes(snapshot.conversationTopics.availability)) fail();
    if (snapshot.conversationTopics.lastObservedAt !== null) time(snapshot.conversationTopics.lastObservedAt);
    for (const channel of ['voice', 'chat', ...(own(snapshot.conversationTopics, 'status') ? ['status'] : [])]) {
      const topics = snapshot.conversationTopics[channel];
      if (!Array.isArray(topics) || topics.length > (channel === 'status' ? 12 : 10)) fail();
      for (const topic of topics) {
        exact(topic, ['key', 'text', 'observedAt']);
        if (!own(PUBLIC_TOPICS, topic.key) || topic.text !== PUBLIC_TOPICS[topic.key]) fail();
        time(topic.observedAt);
      }
    }
    if (own(snapshot, 'fooAllowance')) {
      const a = snapshot.fooAllowance;
      exact(a, ['state', 'canRunAgents', 'verified', 'availability', 'observedAt', 'ageSeconds', 'staleAfterSeconds', 'outageAfterSeconds']);
      const configured = a.staleAfterSeconds !== null;
      const pair = (a.state === 'valid' && a.canRunAgents === true) || (a.state === 'exhausted' && a.canRunAgents === false) ||
        (a.state === 'unavailable' && a.canRunAgents === null);
      if (!pair || typeof a.verified !== 'boolean' || !availability.includes(a.availability) || !age(a.ageSeconds) ||
          (configured ? !integer(a.staleAfterSeconds, 30, 3600) || !integer(a.outageAfterSeconds, a.staleAfterSeconds + 1, 86400) : a.outageAfterSeconds !== null) ||
          (a.verified ? a.availability !== 'fresh' || a.state === 'unavailable' || !configured || a.observedAt === null ||
            time(a.observedAt) > captured || a.ageSeconds === null || a.ageSeconds >= a.staleAfterSeconds : a.state !== 'unavailable' || a.canRunAgents !== null)) fail();
      if (a.observedAt !== null) time(a.observedAt);
    }
    return true;
  } catch { return false; }
}
