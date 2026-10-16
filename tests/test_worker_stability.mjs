// Actual Worker handlers + in-memory SQLite; no Cloudflare or Telegram traffic.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';

const source=(await readFile(new URL('../deploy/worker.js',import.meta.url),'utf8')).replace('__ASSETS__','{}');
const worker=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const ddl=await readFile(new URL('../deploy/0000_miniapp_bridge.sql',import.meta.url),'utf8');
class DB {
 constructor(){this.sql=new DatabaseSync(':memory:');this.sql.exec(ddl);this.writes=0;this.cleanups=0;this.calls=0;this.fail=false;this.onInsert=null;}
 prepare(sql){
  this.calls++; if(this.fail)throw Error('private-database-credential');
  const db=this;let args=[];
  return {bind(...a){args=a;return this;},async run(){
   if(db.fail)throw Error('private-database-credential');
   const result=db.sql.prepare(sql).run(...args);db.writes+=Number(result.changes);
   if(sql.startsWith('DELETE FROM bridge_jobs WHERE deadline'))db.cleanups++;
   if(sql.startsWith('INSERT INTO bridge_jobs'))db.onInsert?.(args[0]);
   return {meta:{changes:Number(result.changes)}};
  },async first(){return db.sql.prepare(sql).get(...args)||null;},async all(){return {results:db.sql.prepare(sql).all(...args)};}};
 }
 close(){this.sql.close();}
}
const env=db=>({DB:db,PROGRESS_BRIDGE_SECRET:'mock-only-bridge',PROGRESS_BOT_ID:'123456789',BOOTSTRAP_ALLOWED:'false'});
const bridge=(path,body={},auth=true)=>new Request('https://example.test/bridge/'+path,{method:'POST',headers:auth?{Authorization:'Bearer mock-only-bridge'}:{},body:JSON.stringify(body)});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function signedRequest(path,db,body={}){
 // Transport tests isolate the cryptographic primitive; real Ed25519/tamper/age
 // checks remain in test_miniapp_edge.mjs. Production exposes no auth bypass.
 const raw=new URLSearchParams({auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:1234}),signature:Buffer.alloc(64).toString('base64url')}).toString();
 const original=crypto.subtle.verify;
 crypto.subtle.verify=async()=>true;
 try{return await worker.default.fetch(new Request('https://example.test/api/'+path,{method:'POST',body:JSON.stringify({initData:raw,body})}),env(db));}
 finally{crypto.subtle.verify=original;}
}

test('heartbeat throttles a virtual day and protects against different isolates',async()=>{
 const db=new DB();
 try{
  for(let t=100000;t<100000+86400000;t+=2000)await worker.maintainBridge(db,t);
  assert.equal(db.writes,8640); // Old 2-second heartbeat: 43,200 logical mutations/day.
  const before=db.writes;
  for(let i=0;i<30;i++)await worker.maintainBridge({prepare:sql=>db.prepare(sql)},100000+86400000-2000);
  assert.equal(db.writes,before); // Atomic SQL guard, not just the JS WeakMap.
  assert.equal(worker.RELAY_TIMING.offlineMs,30000);assert.equal(worker.RELAY_TIMING.pingMs,2000);
 }finally{db.close();}
});

test('concurrent idle HTTP handlers coalesce heartbeat/cleanup; wrong auth does not write',async()=>{
 const db=new DB();const now=Date.now;Date.now=()=>100000;
 try{
  const replies=await Promise.all(Array.from({length:100},()=>worker.default.fetch(bridge('pull'),env(db))));
  assert(replies.every(r=>r.status===200));assert.equal(db.writes,1);assert.equal(db.cleanups,1);
  const calls=db.calls;
  assert.equal((await worker.default.fetch(bridge('pull',{},false),env(db))).status,403);
  assert.equal(db.calls,calls);assert.equal(db.writes,1);
  Date.now=()=>110000;await worker.default.fetch(bridge('pull'),env(db));assert.equal(db.writes,2);assert.equal(db.cleanups,1);
  Date.now=()=>160000;await worker.default.fetch(bridge('pull'),env(db));assert.equal(db.cleanups,2);
 }finally{Date.now=now;db.close();}
});

test('heartbeat retries after storage failure and does not remove live jobs',async()=>{
 const db=new DB();
 try{
  db.sql.prepare("INSERT INTO bridge_jobs VALUES (?,?,?,?,?,?,'pending',NULL,NULL,NULL)").run('live','/api/submit','{}',1,90000,200000);
  db.sql.prepare("INSERT INTO bridge_jobs VALUES (?,?,?,?,?,?,'pending',NULL,NULL,NULL)").run('expired','/api/submit','{}',1,0,10);
  db.fail=true;await assert.rejects(worker.maintainBridge(db,100000));db.fail=false;
  await worker.maintainBridge(db,100000);
  assert.equal(db.sql.prepare("SELECT COUNT(*) n FROM bridge_jobs WHERE id='live'").get().n,1);
  assert.equal(db.sql.prepare("SELECT COUNT(*) n FROM bridge_jobs WHERE id='expired'").get().n,0);
  assert.equal(db.sql.prepare("SELECT value FROM bridge_state WHERE key='heartbeat'").get().value,'100000');
 }finally{db.close();}
});

test('auth failures 403, malformed input 400, D1 failures 503, all redacted',async()=>{
 const db=new DB();const logs=[];const original=console.info;console.info=x=>logs.push(x);
 try{
  const bad=await worker.default.fetch(new Request('https://example.test/api/submit',{method:'POST',body:JSON.stringify({initData:''})}),env(db));
  assert.equal(bad.status,403);assert.equal(db.calls,0);
  const malformed=await worker.default.fetch(new Request('https://example.test/api/submit',{method:'POST',body:'{'}),env(db));
  assert.equal(malformed.status,400);
  db.fail=true;
  const broken=await worker.default.fetch(bridge('pull'),env(db));
  assert.equal(broken.status,503);assert.equal(broken.headers.get('Retry-After'),'1');
  assert.equal((await broken.json()).code,'service_unavailable');
  const noRuntimeSecret={DB:db,BOOTSTRAP_ALLOWED:'false'};
  const authStoreBroken=await worker.default.fetch(bridge('pull'),noRuntimeSecret);
  assert.equal(authStoreBroken.status,503); // A secret-store failure is not denial.
  assert(!logs.join('').includes('private-database'));assert(!logs.join('').includes('mock-only-bridge'));
 }finally{console.info=original;db.close();}
});

test('offline threshold allows throttled heartbeat, then correctly reports 503',async()=>{
 const db=new DB();const now=Date.now;
 try{
  Date.now=()=>100000;await worker.maintainBridge(db,100000);
  Date.now=()=>130001;
  const offline=await signedRequest('summary',db);assert.equal(offline.status,503);
  assert.equal((await offline.json()).code,'backend_offline');
  assert.equal(db.sql.prepare('SELECT COUNT(*) n FROM bridge_jobs').get().n,0);
  Date.now=()=>130002;await worker.maintainBridge(db,130002);
  assert.equal((await worker.default.fetch(bridge('pull'),env(db))).status,200);
 }finally{Date.now=now;db.close();}
});

test('handler persists and returns local 200/403/503 without changing business status',async()=>{
 const db=new DB();
 try{
  await worker.maintainBridge(db,Date.now());
  for(const status of [200,403,503]){
   let completion;
   db.onInsert=id=>{completion=(async()=>{
    const pulled=await worker.default.fetch(bridge('pull'),env(db));
    const {jobs}=await pulled.json();assert.equal(jobs[0].id,id);
    const job={id,status,body:status===200?{message:'已提交'}:{error:status===403?'禁止操作':'暂不可用',retryable:status===503},timing:{local_ms:3,claimed_at:Date.now()}};
    assert.equal((await worker.default.fetch(bridge('complete',{jobs:[job]}),env(db))).status,200);
   })();};
   const reply=await signedRequest('submit',db);await completion;
   assert.equal(reply.status,status);const body=await reply.json();assert.equal(body._timing.local_ms,3);
   assert.equal(db.sql.prepare('SELECT COUNT(*) n FROM bridge_jobs').get().n,0);
   await pause(1);
  }
 }finally{db.close();}
});

test('WebSocket keeps frequent wire pings while throttling durable writes',async()=>{
 const db=new DB(),nativeResponse=globalThis.Response,nativePair=globalThis.WebSocketPair,nativeNow=Date.now;
 let clock=100000,pump;const packets=[];
 class Socket {
  readyState=1;listeners={};accept(){}
  addEventListener(name,fn){this.listeners[name]=fn;}
  close(code=1000){this.readyState=3;this.listeners.close?.({code});}
  send(raw){const p=JSON.parse(raw);packets.push(p);if(p.type==='ping'&&packets.filter(x=>x.type==='ping').length===3)this.close();}
 }
 globalThis.WebSocketPair=class {constructor(){this[0]=new Socket();this[1]=new Socket();}};
 globalThis.Response=function(body,init){return init?.status===101?{status:101,webSocket:init.webSocket}:new nativeResponse(body,init);};
 Date.now=()=>{const t=clock;clock+=2000;return t;};
 try{
  const r=await worker.default.fetch(new Request('https://example.test/bridge/socket',{headers:{Authorization:'Bearer mock-only-bridge',Upgrade:'websocket'}}),env(db),{waitUntil(p){pump=p;}});
  assert.equal(r.status,101);await pump;
  assert.equal(packets.filter(x=>x.type==='ping').length,3);assert.equal(db.writes,1);
  assert.equal(db.cleanups,1);
 }finally{globalThis.Response=nativeResponse;globalThis.WebSocketPair=nativePair;Date.now=nativeNow;db.close();}
});

test('browser WebSocket returns classified 400/403/503 without bypassing edge authentication',async()=>{
 const db=new DB(),nativeResponse=globalThis.Response,nativePair=globalThis.WebSocketPair,nativeVerify=crypto.subtle.verify;
 let server;const packets=[];
 class Socket {
  listeners={};accept(){}addEventListener(name,fn){this.listeners[name]=fn;}
  send(raw){packets.push(JSON.parse(raw));}
 }
 globalThis.WebSocketPair=class {constructor(){this[0]=new Socket();this[1]=server=new Socket();}};
 globalThis.Response=function(body,init){return init?.status===101?{status:101,webSocket:init.webSocket}:new nativeResponse(body,init);};
 try{
  const r=await worker.default.fetch(new Request('https://example.test/api/client',{headers:{Upgrade:'websocket'}}),env(db));assert.equal(r.status,101);
  await server.listeners.message({data:'{'});assert.equal(packets.at(-1).status,400);
  await server.listeners.message({data:JSON.stringify({id:'a',path:'/api/summary',envelope:{initData:''}})});
  assert.equal(packets.at(-1).status,403);assert.equal(db.calls,0);
  crypto.subtle.verify=async()=>true;db.fail=true;
  const raw=new URLSearchParams({auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:1234}),signature:Buffer.alloc(64).toString('base64url')}).toString();
  await server.listeners.message({data:JSON.stringify({id:'b',path:'/api/summary',envelope:{initData:raw}})});
  assert.equal(packets.at(-1).status,503);assert.equal(packets.at(-1).body.retryable,true);
  assert(!JSON.stringify(packets).includes('private-database'));
 }finally{globalThis.Response=nativeResponse;globalThis.WebSocketPair=nativePair;crypto.subtle.verify=nativeVerify;db.close();}
});
