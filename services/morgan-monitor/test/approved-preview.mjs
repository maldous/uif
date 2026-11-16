// Local QA only: replay the explicitly synthetic legacy regression fixture.
import {createServer} from 'node:http';
import {readFileSync} from 'node:fs';
import {handleRequest} from '../worker.mjs';
import {renderApprovedGraph} from '../public/lib/approved-graph.js';
const capture=JSON.parse(readFileSync(new URL('./fixtures/synthetic-legacy-status.json',import.meta.url),'utf8'));
const server=createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://localhost:4177');
  let response;
  if(url.pathname==='/api/status')response=new Response(JSON.stringify(capture),{headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  else if(url.pathname==='/render.svg')response=new Response(renderApprovedGraph(capture,{layout:url.searchParams.get('layout')||'full',theme:url.searchParams.get('theme')||'light'}),{headers:{'Content-Type':'image/svg+xml'}});
  else response=await handleRequest(new Request(url,{method:req.method}),{});
  res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch{res.writeHead(500);res.end('Local preview unavailable');}
});
server.listen(4177,'127.0.0.1',()=>console.log('Approved graph local QA: http://localhost:4177 (SYNTHETIC regression data, not live status)'));
