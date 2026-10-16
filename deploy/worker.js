/* Permanent HTTPS origin + short-lived request relay. Business DB remains on PC.
 * No bot token is deployed: Telegram Ed25519 verifies requests at the edge;
 * the existing bot independently checks HMAC, membership and business privileges.
 */
const TELEGRAM_KEY='e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d';
const encode=new TextEncoder();
const allowed=new Set(['/api/session','/api/history','/api/feedback','/api/summary','/api/tasks','/api/publish','/api/query','/api/submit','/api/metrics','/api/people','/api/people/invite','/api/people/approve','/api/people/reject']);
const assets=__ASSETS__;
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
// Advisory same-isolate wakeups only; D1 is still durable and cross-isolate fallback.
const activeBridges=new Set(),localResults=new Map();
let inlinePage;
async function page(){
 if(!inlinePage)inlinePage=(async()=>{
  const js=assets['/app.js'].body.replace(/<\/script/gi,'<\\/script'),css=assets['/style.css'].body;
  const hash=async text=>btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(text)))));
  const body=assets['/'].body.replace(/<link rel="stylesheet" href="\/style.css[^\"]*">/,'<style>'+css+'</style>').replace(/<script defer src="\/app.js[^\"]*"><\/script>/,'').replace('</body>','<script>'+js+'</script></body>');
  return {body,policy:`default-src 'self'; script-src 'self' https://telegram.org 'sha256-${await hash(js)}'; style-src 'self' 'sha256-${await hash(css)}'; connect-src 'self'; img-src 'self' data:; frame-ancestors https://web.telegram.org https://*.telegram.org`};
 })();return inlinePage;
}
function response(data,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',...(status===503?{'Retry-After':'1'}:{})}});}
// Wire pings stay frequent; durable heartbeat and cleanup are independent.
export const RELAY_TIMING=Object.freeze({heartbeatMs:10000,cleanupMs:60000,offlineMs:30000,pingMs:2000});
class AuthError extends Error{}
class InputError extends Error{}
class ServiceError extends Error{constructor(code,message){super(message);this.code=code;}}
function logEvent(event,fields={}){console.info(JSON.stringify({component:'miniapp_relay',event,...fields}));}
function fault(error){return error instanceof ServiceError?{status:503,code:error.code,message:error.message}:error instanceof AuthError?{status:403,code:'access_denied',message:error.message}:error instanceof InputError?{status:400,code:'invalid_request',message:error.message}:{status:503,code:'service_unavailable',message:'服务暂时不可用，填写内容已保留，请稍后重试'};}
function failed(error,path,started){const f=fault(error);logEvent('request_failed',{path:allowed.has(path)||['/bridge/socket','/bridge/pull','/bridge/complete','/bridge/bootstrap'].includes(path)?path:'other',status:f.status,reason:f.code,elapsed_ms:Math.round(performance.now()-started)});return response({error:f.message,code:f.code,retryable:f.status===503},f.status);}
function parseJSON(text){try{return JSON.parse(text);}catch{throw new InputError('请求格式无效，请重试');}}
const maintenance=new WeakMap();
export async function maintainBridge(db,now){
 let state=maintenance.get(db);if(!state){state={heartbeatDue:0,cleanupDue:0,heartbeat:null,cleanup:null};maintenance.set(db,state);}
 // Atomic conditional UPDATE is also the cross-isolate guard. Never REPLACE the
 // indexed key on every pull; other isolates may already have refreshed it.
 if(now>=state.heartbeatDue&&!state.heartbeat){state.heartbeat=(async()=>{const out=await db.prepare("INSERT INTO bridge_state(key,value) VALUES ('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE CAST(bridge_state.value AS INTEGER)<=?").bind(String(now),now-RELAY_TIMING.heartbeatMs).run();state.heartbeatDue=now+(out.meta?.changes?RELAY_TIMING.heartbeatMs:RELAY_TIMING.heartbeatMs/2);})().finally(()=>{state.heartbeat=null;});}
 if(now>=state.cleanupDue&&!state.cleanup){state.cleanup=(async()=>{await db.prepare('DELETE FROM bridge_jobs WHERE deadline<?').bind(now).run();state.cleanupDue=now+RELAY_TIMING.cleanupMs;})().finally(()=>{state.cleanup=null;});}
 await Promise.all([state.heartbeat,state.cleanup]);
}
export async function verify(raw,now=Date.now()/1000,keyHex=TELEGRAM_KEY,botId='0'){
 try{return await signedUser(raw,now,keyHex,botId);}catch(error){if(error instanceof SyntaxError||error?.name==='InvalidCharacterError')throw new AuthError('登录资料无效');throw error;}
}
async function signedUser(raw,now,keyHex,botId){
 if(typeof raw!=='string'||!raw||raw.length>16384)throw new AuthError('请从 Telegram 群内入口打开小程序');
 const pairs=[...new URLSearchParams(raw)],fields=Object.fromEntries(pairs);
 if(pairs.length!==Object.keys(fields).length)throw new AuthError('登录资料无效');
 const at=Number(fields.auth_date),user=JSON.parse(fields.user||'{}');
 if(!user||typeof user!=='object'||Array.isArray(user))throw new AuthError('登录资料无效');
 if(!Number.isInteger(at)||now-at>3600||at-now>30||!Number.isSafeInteger(user.id)||user.id<=0||user.is_bot)throw new AuthError('登录已过期，请重新打开小程序');
 const sig=fields.signature;if(typeof sig!=='string'||!sig||sig.length>128)throw new AuthError('当前客户端缺少 Telegram 签名，请更新客户端后重试');
 delete fields.hash;delete fields.signature;
 const message=botId+':WebAppData\n'+Object.keys(fields).sort().map(k=>k+'='+fields[k]).join('\n');
 const key=await crypto.subtle.importKey('raw',Uint8Array.from(keyHex.match(/../g),h=>parseInt(h,16)),{name:'Ed25519'},false,['verify']);
 const bytes=Uint8Array.from(atob(sig.replace(/-/g,'+').replace(/_/g,'/').padEnd(Math.ceil(sig.length/4)*4,'=')),c=>c.charCodeAt(0));
 if(bytes.length!==64)throw new AuthError('登录资料无效');
 if(!await crypto.subtle.verify('Ed25519',key,bytes,encode.encode(message)))throw new AuthError('Telegram 登录校验失败');
 return user.id;
}
async function bridgeAuthorized(request,db,env){
 const row=env.PROGRESS_BRIDGE_SECRET?{value:env.PROGRESS_BRIDGE_SECRET}:await db.prepare("SELECT value FROM bridge_state WHERE key='secret'").first();
 const supplied=request.headers.get('Authorization')||'';if(!row)return false;
 const a=new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(supplied)));
 const b=new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode('Bearer '+row.value)));
 return a.reduce((out,x,i)=>out|(x^b[i]),0)===0;
}
async function claim(db,now){
 const pending=await db.prepare("SELECT id FROM bridge_jobs WHERE (state='pending' OR state='claimed' AND lease<?) AND deadline>? LIMIT 1").bind(now,now).first();
 if(!pending)return [];
 const found=await db.prepare("UPDATE bridge_jobs SET state='claimed',lease=? WHERE id IN (SELECT id FROM bridge_jobs WHERE (state='pending' OR state='claimed' AND lease<?) AND deadline>? ORDER BY CASE WHEN path='/api/submit' THEN 0 WHEN path='/api/metrics' THEN 2 ELSE 1 END,created LIMIT 20) RETURNING id,path,payload").bind(now+10000,now,now).all();
 return found.results.map(r=>({id:r.id,path:r.path,envelope:JSON.parse(r.payload),claimed_at:now}));
}
async function complete(db,job,now){
 if(![200,400,403,503].includes(job.status))throw new InputError('请求状态无效');
 await db.prepare("UPDATE bridge_jobs SET state='done',status=?,result=?,payload='' WHERE id=? AND deadline>?").bind(job.status,JSON.stringify({_relay_body:job.body,_relay_timing:job.timing||{},completed_at:now}),job.id,now).run();
 localResults.get(job.id)?.({state:'done',status:job.status,result:JSON.stringify({_relay_body:job.body,_relay_timing:job.timing||{},completed_at:now})});
}
function bridgeSocket(db,ctx){
 const [client,server]=Object.values(new WebSocketPair());server.accept();let live=true;activeBridges.add(server);
 const close=()=>{live=false;activeBridges.delete(server);};
 logEvent('bridge_connected',{mode:'WebSocket'});
 server.addEventListener('close',event=>{close();logEvent('bridge_disconnected',{mode:'WebSocket',close_code:Number.isInteger(event.code)?event.code:0});});server.addEventListener('error',()=>{close();logEvent('bridge_disconnected',{mode:'WebSocket',reason:'socket_error'});});
 server.addEventListener('message',async event=>{try{const m=parseJSON(event.data);if(m.type!=='complete')return;await complete(db,m.job,Date.now());server.send(JSON.stringify({type:'ack',id:m.job.id}));}catch{close();logEvent('bridge_disconnected',{mode:'WebSocket',reason:'completion_failed'});try{server.close(1011,'retry');}catch{}}});
 const pump=(async()=>{let ping=0;while(live&&server.readyState===1){const now=Date.now();await maintainBridge(db,now);if(now-ping>=RELAY_TIMING.pingMs){server.send(JSON.stringify({type:'ping'}));ping=now;}const jobs=await claim(db,now);if(jobs.length)server.send(JSON.stringify({type:'jobs',jobs}));await wait(100);}})().catch(()=>{close();logEvent('bridge_disconnected',{mode:'WebSocket',reason:'storage_or_socket_error'});try{server.close(1011,'retry');}catch{}});
 ctx?.waitUntil(pump);return new Response(null,{status:101,webSocket:client});
}
const handler={async fetch(request,env,ctx){
 const requestStarted=performance.now();const url=new URL(request.url),path=url.pathname,db=env.DB;
 try{
  if(request.method==='GET'&&assets[path]){const inline=path==='/'?await page():null;return new Response(inline?inline.body:assets[path].body,{headers:{'Content-Type':assets[path].type,'Cache-Control':url.searchParams.has('v')?'public, max-age=86400, immutable':path==='/'?'public, max-age=60':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':inline?inline.policy:"default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors https://web.telegram.org https://*.telegram.org"}});}
  if(path==='/health')return response({ok:true});
  if(path==='/api/client'&&request.headers.get('Upgrade')?.toLowerCase()==='websocket'){
   const [client,server]=Object.values(new WebSocketPair());server.accept();let pending=0;
   server.addEventListener('message',async event=>{
    let id='',counted=false;try{
     if(typeof event.data!=='string'||event.data.length>131072)throw new InputError('内容过长');
     const input=parseJSON(event.data);if(!input||typeof input!=='object')throw new InputError('请求格式无效');id=input.id;
     if(typeof id!=='string'||id.length>40||!allowed.has(input.path)||pending>=4)throw new InputError('请求无效或操作过快');
     pending++;counted=true;
     // Same edge signature, age, rate and backend membership gates on every request.
     const result=await handler.fetch(new Request(url.origin+input.path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input.envelope)}),env,ctx);
     server.send(JSON.stringify({id,status:result.status,body:await result.json()}));
    }catch(error){const f=fault(error);try{server.send(JSON.stringify({id,status:f.status,body:{error:f.message,code:f.code,retryable:f.status===503}}));}catch{}}
    finally{if(counted)pending--;}
   });
   return new Response(null,{status:101,webSocket:client});
  }
  if(path==='/bridge/bootstrap'&&request.method==='POST'){
   // Enabled only during owner-private bootstrap; permanently one-time.
   if(env.BOOTSTRAP_ALLOWED!=='true')throw new AuthError('禁止访问');
   const key=Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join('');
   const result=await db.prepare("INSERT OR IGNORE INTO bridge_state(key,value) VALUES ('secret',?)").bind(key).run();
   if(!result.meta.changes)return response({error:'已初始化，使用本地已有凭据'},409);
   return response({key});
  }
  if(path.startsWith('/bridge/')){
   if(!await bridgeAuthorized(request,db,env))throw new AuthError('禁止访问');
   if(path==='/bridge/socket'){if(typeof WebSocketPair!=='function')return response({error:'socket unsupported'},501);if(request.headers.get('Upgrade')?.toLowerCase()!=='websocket')return response({error:'upgrade required'},426);return bridgeSocket(db,ctx);}
   const now=Date.now();
   if(path==='/bridge/pull'&&request.method==='POST'){
    await maintainBridge(db,now);
    return response({jobs:await claim(db,now)});
   }
   if(path==='/bridge/complete'&&request.method==='POST'){
    const body=parseJSON(await request.text());if(!Array.isArray(body?.jobs)||body.jobs.length>20)return response({error:'请求无效'},400);
    for(const job of body.jobs)await complete(db,job,now);
    return response({ok:true});
   }
   return response({error:'入口不存在'},404);
  }
  if(request.method!=='POST'||!allowed.has(path))return response({error:'入口不存在'},404);
  if(Number(request.headers.get('Content-Length')||0)>131072)return response({error:'内容过长'},400);
  const text=await request.text();if(text.length>131072)return response({error:'内容过长'},400);
  const envelope=parseJSON(text);if(!envelope||typeof envelope!=='object'||Array.isArray(envelope))throw new InputError('请求格式无效');const uid=await verify(envelope.initData,Date.now()/1000,TELEGRAM_KEY,env.PROGRESS_BOT_ID);
  const verifyMs=performance.now()-requestStarted;const now=Date.now(),heartbeat=await db.prepare("SELECT value FROM bridge_state WHERE key='heartbeat'").first();
  if(!heartbeat||!Number.isFinite(Number(heartbeat.value))||now-Number(heartbeat.value)>RELAY_TIMING.offlineMs){throw new ServiceError('backend_offline','电脑服务当前离线，填写内容已保留，请稍后重试');};
  const recent=await db.prepare('SELECT COUNT(*) n FROM bridge_jobs WHERE uid=? AND created>?').bind(uid,now-1000).first();if(recent.n>=12)return response({error:'操作过快，请稍候重试'},429);
  const id=crypto.randomUUID();await db.prepare("INSERT INTO bridge_jobs(id,path,payload,uid,created,deadline,state) VALUES (?,?,?,?,?,?,'pending')").bind(id,path,text,uid,now,now+60000).run();
  let stop=false;const fast=new Promise(resolve=>localResults.set(id,resolve));
  for(const socket of activeBridges){try{socket.send(JSON.stringify({type:'jobs',jobs:[{id,path,envelope,claimed_at:Date.now()}]}));break;}catch{activeBridges.delete(socket);}}
  const durable=(async()=>{for(let i=0;i<220&&!stop;i++){await wait(100);const row=await db.prepare('SELECT state,status,result FROM bridge_jobs WHERE id=?').bind(id).first();if(row?.state==='done')return row;}return null;})();
  let row;try{row=await Promise.race([fast,durable]);}finally{stop=true;localResults.delete(id);}
  if(row?.state==='done'){const remove=db.prepare('DELETE FROM bridge_jobs WHERE id=?').bind(id).run().catch(()=>logEvent('cleanup_retry',{reason:'storage_error'}));if(ctx?.waitUntil)ctx.waitUntil(remove);else await remove;const result=JSON.parse(row.result);const timing=result._relay_timing;timing.edge_verify_ms=verifyMs;timing.queue_ms=timing.claimed_at?timing.claimed_at-now:null;timing.result_ms=Date.now()-result.completed_at;timing.return_ms=timing.claimed_at?result.completed_at-timing.claimed_at-(timing.local_ms||0):null;timing.edge_ms=performance.now()-requestStarted;logEvent('request_completed',{path,status:row.status,elapsed_ms:Math.round(timing.edge_ms),local_ms:timing.local_ms||0,queue_ms:timing.queue_ms});return response({...result._relay_body,_timing:timing},row.status);}
  throw new ServiceError('relay_timeout','服务连接较慢，填写内容已保留，重试不会重复入账');
 }catch(error){return failed(error,path,requestStarted);}
}};
export default handler;
