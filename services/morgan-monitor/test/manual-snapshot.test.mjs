import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {renderSvg,scanDirections,executionComplete} from '../public/lib/render.js';
import {CLIENT_RENDER_SOURCE} from '../client-render-source.mjs';
import {handleRequest,loadConfig,validateEvent} from '../worker.mjs';
const at='2026-11-10T05:00:00.000Z';
const fixture=(availability='fresh')=>({schemaVersion:1,generatedAt:'2026-11-10T05:01:00.000Z',revision:'a'.repeat(64),summary:{executionCoverageComplete:false},
 sources:[{domain:'cloud',aliases:['A','B','C','D','E','F'],observationMode:'manual',lastObservedAt:at,status:availability,executionCoverageComplete:false}],
 slots:['A','B'].map((alias,i)=>({id:`cloud-${alias}`,domain:'cloud',alias,assignment:'assigned',availability,proof:'coordinator_lifecycle',rosterGeneration:3,recordedState:'unknown',displayState:null,observedAt:at,topicKeys:[i?'research':'engineering'],topics:[i?'Research':'Engineering'],blockerKeys:[]})),conversationTopics:{voice:[],chat:[],status:[]}});
test('manual unknown observations show neutral snapshot subjects, never active claims',()=>{
 const snapshot=fixture(),svg=renderSvg(snapshot,{layout:'compact'});
 assert(svg.includes('Engineering'));assert(svg.includes('Research'));assert.equal((svg.match(/class="snapshot-label"/g)||[]).length,2);
 assert(svg.includes('Snapshot 2026-11-10 05:00:00 UTC'));assert(svg.includes('Execution unknown'));assert(svg.includes('Roster 3'));
 assert(!svg.includes('cell-content working'));assert(!svg.includes('cell-content blocked'));assert(!svg.includes('class="blocked-marker'));
 assert(!executionComplete(snapshot));assert(scanDirections(snapshot,{layout:'compact'}).every(d=>!d.target && !d.clear && !d.empty));
});
test('stale and lost-source snapshots retain labels only with explicit older attribution',()=>{
 for(const state of ['stale','outage','error','unavailable']){
  const svg=renderSvg(fixture(state),{layout:'compact'});assert.equal((svg.match(/Older snapshot/g)||[]).length,2);assert(svg.includes('05:00:00 UTC'));assert(!svg.includes('cell-content working'));assert(!svg.includes('cell-content blocked'));
 }
});
test('unobserved capacity does not receive a subject, state or snapshot badge',()=>{
 const s=fixture();s.slots.push({id:'cloud-C',domain:'cloud',alias:'C',assignment:'unavailable',availability:'unavailable',proof:null});
 const svg=renderSvg(s,{layout:'compact'});const cell=svg.match(/data-content-cell="cloud-C"[^>]*>([\s\S]*?)<\/g>/)?.[1];assert(cell);assert(!cell.includes('snapshot-label'));assert(!cell.includes('task-title'));
});
test('snapshot subjects still escape markup and do not change renderer geometry',()=>{
 const s=fixture();s.slots[0].topics=['<script>alert(1)</script>'];const svg=renderSvg(s,{layout:'compact'});assert(!svg.includes('<script>'));assert(svg.includes('&lt;script&gt;'));assert.equal((svg.match(/<polygon /g)||[]).length,10);
});
test('browser renderer route is byte-identical, no-store and read-only',async()=>{
 assert.equal(CLIENT_RENDER_SOURCE,readFileSync(new URL('../public/lib/render.js',import.meta.url),'utf8'));
 const r=await handleRequest(new Request('https://morgan.aldous.info/lib/render.js'),{});assert.equal(r.status,200);assert.equal(await r.text(),CLIENT_RENDER_SOURCE);assert.equal(r.headers.get('cache-control'),'no-store, max-age=0');
 assert.equal((await handleRequest(new Request('https://morgan.aldous.info/lib/render.js',{method:'POST'}),{})).status,405);
 assert.equal(await (await handleRequest(new Request('https://morgan.aldous.info/lib/render.js',{method:'HEAD'}),{})).text(),'');
});
test('deployment-published source has no HTTP credential and cannot authenticate ingestion',async()=>{
 const source={id:'durable_snapshot',domain:'cloud',aliases:[...'ABCDEF'],capabilities:['coordinator_lifecycle'],rosterGeneration:3,staleAfterSeconds:120,outageAfterSeconds:600,tokenEnv:'MANUAL_SNAPSHOT_INGEST_DISABLED'};
 const env={SOURCE_CONFIG_JSON:JSON.stringify({sources:[source]}),DB:{}};const config=await loadConfig(env);
 const event={schemaVersion:1,sourceId:source.id,eventId:'1',sequence:1,rosterGeneration:3,observedAt:at,health:'ok',coverage:{complete:false,observed:2,total:null},sessions:fixture().slots.map(({alias,observedAt,topicKeys})=>({alias,assignment:'assigned',observedAt,state:'unknown',proof:'coordinator_lifecycle',topicKeys,blockerKeys:[]}))};
 assert.equal(validateEvent(event,config[0],Date.parse(at)),event);
 const r=await handleRequest(new Request('https://morgan.aldous.info/v1/ingest',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer synthetic-invalid-credential-000000000000'},body:JSON.stringify(event)}),env,Date.parse(at));assert.equal(r.status,401);
});
