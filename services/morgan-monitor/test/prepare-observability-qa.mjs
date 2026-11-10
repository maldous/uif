// Local-only visual QA; generated files are explicitly synthetic test fixtures.
import {writeFileSync,mkdirSync} from 'node:fs';
import {renderApprovedGraph} from '../public/lib/approved-graph.js';
import {runtimeSnapshot,NOW} from './fixtures/graph-observation-fixtures.mjs';
const output=new URL('../../graph-qa/',import.meta.url);mkdirSync(output,{recursive:true});
const snapshot=await runtimeSnapshot({Cloud:'running',UIF:'idle',ANQR:'blocked',CIX:'blocked',OM:'idle',CQ:'unknown'});
for(const mode of ['full','compact'])for(const theme of ['light','dark'])for(const reducedMotion of [false,true]) {
  const name=`synthetic-${mode}-${theme}${reducedMotion?'-reduced':''}.html`;
  const svg=renderApprovedGraph(snapshot,{mode,theme,reducedMotion,now:NOW,footer:false,interactive:false});
  const body=`<!doctype html><html><head><meta charset="utf-8"><title>SYNTHETIC TEST ONLY · ${mode} ${theme}</title><style>html,body{margin:0;width:100%;height:100%;background:${theme==='dark'?'#141C2A':'#F0F2F6'}}svg{width:100%;height:100%;display:block}#qa{position:fixed;top:0;left:0;color:${theme==='dark'?'white':'black'};font:12px sans-serif;background:${theme==='dark'?'#141C2A':'#F0F2F6'}}</style></head><body>${svg}<div id="qa">Synthetic test only. <button id="p0">Phase 0</button><button id="p1">Phase 1</button><button id="play">Play</button></div><script>function freeze(ms){for(const animation of document.getAnimations()){animation.pause();animation.currentTime=ms}}document.getElementById('p0').onclick=()=>freeze(100);document.getElementById('p1').onclick=()=>freeze(900);document.getElementById('play').onclick=()=>{for(const a of document.getAnimations())a.play()};</script></body></html>`;
  writeFileSync(new URL(name,output),body);
}
console.log('Prepared eight synthetic test-only local pages.');
