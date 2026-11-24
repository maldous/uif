// Synthetic status fixtures are confined to tests. Production never imports this module.
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';
import { handleRequest } from '../../worker.mjs';
import { runtimeEvidence } from '../../public/lib/session-runtime.js';
export const NOW=Date.parse('2026-11-10T20:00:00.000Z');
export const AT=new Date(NOW).toISOString();
export const PROJECTS=['Cloud','UIF','ANQR','CIX','OM','CQ'];
const TOKEN='synthetic-test-only-graph-observability-token-0000';
class D1 {
  constructor(){this.db=new DatabaseSync(':memory:');for(const f of readdirSync(new URL('../../migrations/',import.meta.url)).sort())this.db.exec(readFileSync(new URL('../../migrations/'+f,import.meta.url),'utf8'));}
  prepare(sql){const st=this.db.prepare(sql);const bind=values=>({bind:(...args)=>bind(args),first:async()=>st.get(...values)??null,all:async()=>({results:st.all(...values)}),run:async()=>st.run(...values)});return bind([]);}
}
export async function runtimeSnapshot(states={},now=NOW,native=null) {
  const source={id:'graph_runtime',domain:'cloud',aliases:[...'ABCDEF'],capabilities:['session_runtime'],runtimeGeneration:4,
    projectBindings:PROJECTS.map((project,i)=>({alias:'ABCDEF'[i],project,kind:'durable'})),staleAfterSeconds:120,outageAfterSeconds:600,tokenEnv:'GRAPH_TEST_TOKEN'};
  const sources=[source];
  if(native)sources.push({id:'graph_native',domain:'cloud',aliases:[],capabilities:['coordinator_lifecycle'],rosterGeneration:1,activityBindings:native.map(a=>({project:a.project,kind:'native_worker'})),staleAfterSeconds:120,outageAfterSeconds:600,tokenEnv:'GRAPH_NATIVE_TEST_TOKEN'});
  const env={DB:new D1(),SOURCE_CONFIG_JSON:JSON.stringify({sources}),GRAPH_TEST_TOKEN:TOKEN,GRAPH_NATIVE_TEST_TOKEN:TOKEN+'-native'};
  const sessions=PROJECTS.flatMap((project,i)=>{
    if(!Object.hasOwn(states,project))return [];
    const state=states[project],runtime=state==='running'?{type:'active',activeFlags:[]}:state==='blocked'?{type:'active',activeFlags:['waitingOnUserInput']}:state==='idle'?{type:'idle'}:{type:'notLoaded'};
    const evidence=runtimeEvidence(runtime);
    return [{alias:'ABCDEF'[i],assignment:'assigned',proof:'session_runtime',observedAt:AT,state:evidence.state,runtime:evidence.runtime,topicKeys:[],blockerKeys:evidence.blockerKeys}];
  });
  const payload={schemaVersion:1,sourceId:source.id,eventId:'1',sequence:1,runtimeGeneration:4,observedAt:AT,health:'ok',coverage:{complete:false,observed:sessions.length,total:null},sessions};
  const res=await handleRequest(new Request('https://monitor.example/v1/ingest',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${TOKEN}`},body:JSON.stringify(payload)}),env,NOW);
  assert.equal(res.status,202,await res.clone().text());
  if(native) {
    const payload={schemaVersion:1,sourceId:'graph_native',eventId:'1',sequence:1,rosterGeneration:1,observedAt:AT,health:'ok',coverage:{complete:false,observed:0,total:null},sessions:[],
      activities:native.map(a=>({project:a.project,state:a.state,observedAt:AT,topicKeys:[],blockerKeys:a.state==='blocked'?['awaiting_input']:[]}))};
    const response=await handleRequest(new Request('https://monitor.example/v1/ingest',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${TOKEN}-native`},body:JSON.stringify(payload)}),env,NOW);
    assert.equal(response.status,202,await response.clone().text());
  }
  const response=await handleRequest(new Request('https://monitor.example/api/status'),env,now);
  assert.equal(response.status,200,await response.clone().text());
  const value=await response.json();env.DB.db.close();return value;
}
