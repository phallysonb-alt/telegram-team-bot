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
function response(data,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'}});}
export async function verify(raw,now=Date.now()/1000,keyHex=TELEGRAM_KEY,botId='0'){
 if(typeof raw!=='string'||!raw||raw.length>16384)throw Error('请从 Telegram 群内入口打开小程序');
 const pairs=[...new URLSearchParams(raw)],fields=Object.fromEntries(pairs);
 if(pairs.length!==Object.keys(fields).length)throw Error('登录资料无效');
 const at=Number(fields.auth_date),user=JSON.parse(fields.user||'{}');
 if(!Number.isInteger(at)||now-at>3600||at-now>30||!Number.isSafeInteger(user.id)||user.id<=0||user.is_bot)throw Error('登录已过期，请重新打开小程序');
 const sig=fields.signature;if(!sig)throw Error('当前客户端缺少 Telegram 签名，请更新客户端后重试');
 delete fields.hash;delete fields.signature;
 const message=botId+':WebAppData\n'+Object.keys(fields).sort().map(k=>k+'='+fields[k]).join('\n');
 const key=await crypto.subtle.importKey('raw',Uint8Array.from(keyHex.match(/../g),h=>parseInt(h,16)),{name:'Ed25519'},false,['verify']);
 const bytes=Uint8Array.from(atob(sig.replace(/-/g,'+').replace(/_/g,'/').padEnd(Math.ceil(sig.length/4)*4,'=')),c=>c.charCodeAt(0));
 if(!await crypto.subtle.verify('Ed25519',key,bytes,encode.encode(message)))throw Error('Telegram 登录校验失败');
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
 if(![200,400,403,503].includes(job.status))throw Error('invalid status');
 await db.prepare("UPDATE bridge_jobs SET state='done',status=?,result=?,payload='' WHERE id=? AND deadline>?").bind(job.status,JSON.stringify({_relay_body:job.body,_relay_timing:job.timing||{},completed_at:now}),job.id,now).run();
 localResults.get(job.id)?.({state:'done',status:job.status,result:JSON.stringify({_relay_body:job.body,_relay_timing:job.timing||{},completed_at:now})});
}
function bridgeSocket(db,ctx){
 const [client,server]=Object.values(new WebSocketPair());server.accept();let live=true;activeBridges.add(server);
 server.addEventListener('close',()=>{live=false;activeBridges.delete(server);});server.addEventListener('error',()=>{live=false;activeBridges.delete(server);});
 server.addEventListener('message',async event=>{try{const m=JSON.parse(event.data);if(m.type!=='complete')return;await complete(db,m.job,Date.now());server.send(JSON.stringify({type:'ack',id:m.job.id}));}catch{server.close(1011,'retry');live=false;}});
 const pump=(async()=>{let heartbeat=0;while(live&&server.readyState===1){const now=Date.now();if(now-heartbeat>2000){await db.batch([db.prepare('DELETE FROM bridge_jobs WHERE deadline<?').bind(now),db.prepare("INSERT OR REPLACE INTO bridge_state VALUES ('heartbeat',?)").bind(String(now))]);server.send(JSON.stringify({type:'ping'}));heartbeat=now;}const jobs=await claim(db,now);if(jobs.length)server.send(JSON.stringify({type:'jobs',jobs}));await wait(100);}})().catch(()=>{live=false;try{server.close(1011,'retry');}catch{}});
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
     if(typeof event.data!=='string'||event.data.length>131072)throw Error('内容过长');
     const input=JSON.parse(event.data);id=input.id;
     if(typeof id!=='string'||id.length>40||!allowed.has(input.path)||pending>=4)throw Error('请求无效或操作过快');
     pending++;counted=true;
     // Same edge signature, age, rate and backend membership gates on every request.
     const result=await handler.fetch(new Request(url.origin+input.path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input.envelope)}),env,ctx);
     server.send(JSON.stringify({id,status:result.status,body:await result.json()}));
    }catch{try{server.send(JSON.stringify({id,status:400,body:{error:'连接请求失败，填写内容已保留，请重试'}}));}catch{}}
    finally{if(counted)pending--;}
   });
   return new Response(null,{status:101,webSocket:client});
  }
  if(path==='/bridge/bootstrap'&&request.method==='POST'){
   // Enabled only during owner-private bootstrap; permanently one-time.
   if(env.BOOTSTRAP_ALLOWED!=='true')return response({error:'禁止访问'},403);
   const key=Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join('');
   const result=await db.prepare("INSERT OR IGNORE INTO bridge_state(key,value) VALUES ('secret',?)").bind(key).run();
   if(!result.meta.changes)return response({error:'已初始化，使用本地已有凭据'},409);
   return response({key});
  }
  if(path.startsWith('/bridge/')){
   if(!await bridgeAuthorized(request,db,env))return response({error:'禁止访问'},403);
   if(path==='/bridge/socket'){if(typeof WebSocketPair!=='function')return response({error:'socket unsupported'},501);if(request.headers.get('Upgrade')?.toLowerCase()!=='websocket')return response({error:'upgrade required'},426);return bridgeSocket(db,ctx);}
   const now=Date.now();
   if(path==='/bridge/pull'&&request.method==='POST'){
    await db.batch([db.prepare('DELETE FROM bridge_jobs WHERE deadline<?').bind(now),db.prepare("INSERT OR REPLACE INTO bridge_state VALUES ('heartbeat',?)").bind(String(now))]);
    return response({jobs:await claim(db,now)});
   }
   if(path==='/bridge/complete'&&request.method==='POST'){
    const body=await request.json();if(!Array.isArray(body.jobs)||body.jobs.length>20)return response({error:'请求无效'},400);
    for(const job of body.jobs)await complete(db,job,now);
    return response({ok:true});
   }
   return response({error:'入口不存在'},404);
  }
  if(request.method!=='POST'||!allowed.has(path))return response({error:'入口不存在'},404);
  if(Number(request.headers.get('Content-Length')||0)>131072)return response({error:'内容过长'},400);
  const text=await request.text();if(text.length>131072)return response({error:'内容过长'},400);
  const envelope=JSON.parse(text),uid=await verify(envelope.initData,Date.now()/1000,TELEGRAM_KEY,env.PROGRESS_BOT_ID);
  const verifyMs=performance.now()-requestStarted;const now=Date.now(),heartbeat=await db.prepare("SELECT value FROM bridge_state WHERE key='heartbeat'").first();
  if(!heartbeat||now-Number(heartbeat.value)>15000)return response({error:'电脑服务当前离线，填写内容已保留，请稍后重试'},503);
  const recent=await db.prepare('SELECT COUNT(*) n FROM bridge_jobs WHERE uid=? AND created>?').bind(uid,now-1000).first();if(recent.n>=12)return response({error:'操作过快，请稍候重试'},429);
  const id=crypto.randomUUID();await db.prepare("INSERT INTO bridge_jobs(id,path,payload,uid,created,deadline,state) VALUES (?,?,?,?,?,?,'pending')").bind(id,path,text,uid,now,now+60000).run();
  let stop=false;const fast=new Promise(resolve=>localResults.set(id,resolve));
  for(const socket of activeBridges){try{socket.send(JSON.stringify({type:'jobs',jobs:[{id,path,envelope,claimed_at:Date.now()}]}));break;}catch{activeBridges.delete(socket);}}
  const durable=(async()=>{for(let i=0;i<220&&!stop;i++){await wait(100);const row=await db.prepare('SELECT state,status,result FROM bridge_jobs WHERE id=?').bind(id).first();if(row?.state==='done')return row;}return null;})();
  let row;try{row=await Promise.race([fast,durable]);}finally{stop=true;localResults.delete(id);}
  if(row?.state==='done'){const remove=db.prepare('DELETE FROM bridge_jobs WHERE id=?').bind(id).run();if(ctx?.waitUntil)ctx.waitUntil(remove);else await remove;const result=JSON.parse(row.result);const timing=result._relay_timing;timing.edge_verify_ms=verifyMs;timing.queue_ms=timing.claimed_at?timing.claimed_at-now:null;timing.result_ms=Date.now()-result.completed_at;timing.return_ms=timing.claimed_at?result.completed_at-timing.claimed_at-(timing.local_ms||0):null;timing.edge_ms=performance.now()-requestStarted;return response({...result._relay_body,_timing:timing},row.status);}
  return response({error:'服务连接较慢，填写内容已保留，重试不会重复入账'},503);
 }catch(error){return response({error:error.message&&/Telegram|登录|签名/.test(error.message)?error.message:'服务暂时不可用，请重试'},403);}
}};
export default handler;
