"""Authenticated persistent transport; no second Telegram polling instance."""
import asyncio,json,os,time
from .progress_miniapp import MiniAPI,request_timing
from .progress_ui import controller,flush
from .bridge_diagnostics import MemberCheckUnavailable,emit

async def process(api,job):
    started=time.perf_counter();received=api.p.now();timing={};token=request_timing.set(timing)
    try:
        body=await api.dispatch(job['path'],job['envelope']);status=200
        if job['path'] in ('/api/submit','/api/query','/api/publish') and getattr(api.p,'_delivery_event',None):api.p._delivery_event.set()
    except MemberCheckUnavailable as e:
        body=dict(error=str(e),code='membership_unavailable',retryable=True);status=503
        timing['telegram_ms']=round(e.elapsed_ms,2)
    except PermissionError as e:body=dict(error=str(e));status=403
    except (ValueError,KeyError,TypeError) as e:body=dict(error=str(e) if isinstance(e,ValueError) else '填写或入口无效，请检查后重试');status=400
    except Exception:body=dict(error='服务暂时不可用，填写内容已保留，请重试',code='service_unavailable',retryable=True);status=503
    finally:request_timing.reset(token)
    timing.update(local_ms=round((time.perf_counter()-started)*1000,2),claimed_at=job.get('claimed_at'))
    emit('request_completed',endpoint=job['path'],status=status,local_ms=timing['local_ms'],telegram_ms=timing.get('telegram_ms',0))
    try:api.p.db.set('app:timing:'+job['id'],dict(endpoint=job['path'],received=received,status=status,processing_seconds=timing['local_ms']/1000,telegram_ms=timing.get('telegram_ms'),code=body.get('code')))
    except Exception:emit('timing_write_failed',reason='storage_error')
    return dict(id=job['id'],status=status,body=body,timing=timing)

async def delivery(p,bot):
    p._delivery_event=asyncio.Event()
    while True:
        try:
            try:await asyncio.wait_for(p._delivery_event.wait(),timeout=1)
            except asyncio.TimeoutError:pass
            p._delivery_event.clear()
            await asyncio.sleep(.15) # One coalesced edit for a burst; never blocks save response.
            await flush(p,bot)
            if getattr(p,'_verification_api',None):await flush(p._verification_api.p,bot)
        except asyncio.CancelledError:raise
        except Exception:
            emit('delivery_retry',reason='service_error');await asyncio.sleep(1)

def http_delay(previous,jobs):
    # Busy polling unchanged; at idle add at most 0.5 seconds, never an unbounded
    # fixed delay before dispatch/commit or while a result needs acknowledgement.
    return .05 if jobs else min(.5,max(.1,previous*2))

class BridgeHealth:
    def __init__(self):self.mode='starting';self.outage=None;self.online=False
    def switch(self,mode):
        if mode!=self.mode:
            emit('mode_changed',previous_mode=self.mode,mode=mode);self.mode=mode
    def connected(self,mode,pending=0):
        changed=mode!=self.mode;self.switch(mode)
        if not self.online or changed:
            emit('connected',mode=mode,outage_ms=0 if self.outage is None else (time.monotonic()-self.outage)*1000,pending=pending)
        self.online=True;self.outage=None
    def disconnected(self,reason,status=None):
        if self.outage is None:
            self.outage=time.monotonic();emit('disconnected',mode=self.mode,reason=reason,status=status or 0)
        self.online=False

async def loop(app,service):
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus
    import httpx
    api=MiniAPI(controller(service),app.bot);p=api.p;url=os.environ['PROGRESS_MINIAPP_ORIGIN'].rstrip('/')
    from .progress_verification import enabled,isolated
    if enabled(p):p._verification_api=isolated(api)
    if not url.startswith('https://'):raise ValueError('必须使用固定HTTPS服务')
    headers={'Authorization':'Bearer '+os.environ['PROGRESS_BRIDGE_SECRET']}
    if os.environ.get('PROGRESS_SITES_PRIVATE','').lower()=='true' and os.environ.get('PROGRESS_SITES_AUTH'):headers['OAI-Sites-Authorization']=os.environ['PROGRESS_SITES_AUTH']
    from .progress_export import watch
    health=BridgeHealth();idle_delay=.05
    pending={};inflight={};limit=asyncio.Semaphore(16);send_lock=asyncio.Lock();deliver=asyncio.create_task(delivery(p,app.bot));excel=asyncio.create_task(watch(p))
    verify_excel=asyncio.create_task(watch(p._verification_api.p)) if hasattr(p,'_verification_api') else None
    async def run_job(job,ws):
        async with limit:result=await process(api,job)
        pending[job['id']]=result
        try:
            async with send_lock:await ws.send(json.dumps(dict(type='complete',job=result),ensure_ascii=False))
        except Exception:emit('result_retained',reason='socket_error',pending=len(pending)) # Retained until ack/durable retry.
        finally:inflight.pop(job['id'],None)
    try:
        async with httpx.AsyncClient(timeout=35,headers=headers,http2=bool(__import__("importlib.util",fromlist=["find_spec"]).find_spec("h2"))) as client:
            use_socket=True;socket_failures=0;socket_retry_at=0
            async def http_job(job):
                try:
                    async with limit:result=await process(api,job)
                    pending[job['id']]=result
                    return result
                finally:inflight.pop(job['id'],None)
            while True:
                try:
                    if use_socket:
                        health.switch('WebSocket')
                        async with connect(url.replace('https://','wss://',1)+'/bridge/socket',additional_headers=headers,open_timeout=12,ping_interval=10,ping_timeout=15,close_timeout=2,max_size=1048576) as ws:
                            for result in list(pending.values()):await ws.send(json.dumps(dict(type='complete',job=result),ensure_ascii=False))
                            async for raw in ws:
                                message=json.loads(raw);socket_failures=0
                                health.connected('WebSocket',len(pending))
                                service.db.set('miniapp_bridge',dict(state='已连接',mode='WebSocket',at=p.now(),jobs=len(inflight)))
                                if message.get('type')=='ack':pending.pop(message['id'],None)
                                for job in message.get('jobs',[]):
                                    if job['id'] in pending:
                                        await ws.send(json.dumps(dict(type='complete',job=pending[job['id']]),ensure_ascii=False));continue
                                    if job['id'] not in inflight:inflight[job['id']]=asyncio.create_task(run_job(job,ws))
                        health.disconnected('normal_close')
                        await asyncio.sleep(.25)
                    else:
                        health.switch('HTTP兼容')
                        # An interrupted socket result can be acknowledged over HTTP.
                        cached=list(pending.values())[:20]
                        if cached:
                            answer=await client.post(url+'/bridge/complete',json=dict(jobs=cached));answer.raise_for_status()
                            for result in cached:pending.pop(result['id'],None)
                        reply=await client.post(url+'/bridge/pull',json={});reply.raise_for_status();jobs=reply.json()['jobs']
                        jobs.sort(key=lambda j:0 if j['path']=='/api/submit' else 2 if j['path']=='/api/metrics' else 1)
                        tasks=[]
                        for job in jobs:
                            if job['id'] in pending or job['id'] in inflight:continue
                            task=asyncio.create_task(http_job(job));inflight[job['id']]=task;tasks.append(task)
                        for done in asyncio.as_completed(tasks):
                            result=await done
                            answer=await client.post(url+'/bridge/complete',json=dict(jobs=[result]));answer.raise_for_status()
                            pending.pop(result['id'],None)
                        health.connected('HTTP兼容',len(pending))
                        service.db.set('miniapp_bridge',dict(state='已连接',mode='HTTP兼容',at=p.now(),jobs=len(jobs)))
                        if time.monotonic()>=socket_retry_at:use_socket=True;socket_failures=0
                        idle_delay=http_delay(idle_delay,bool(jobs) or bool(pending));await asyncio.sleep(idle_delay)
                except asyncio.CancelledError:raise
                except InvalidStatus as e:
                    health.disconnected('http_error',e.response.status_code)
                    socket_failures+=1
                    if e.response.status_code in (400,403,404,405,426,501) or socket_failures>=2:use_socket=False;socket_retry_at=time.monotonic()+30
                    service.db.set('miniapp_bridge',dict(state='连接中断，自动重试',mode='HTTP兼容' if not use_socket else 'WebSocket',at=p.now()))
                    await asyncio.sleep(1)
                except Exception as error:
                    status=getattr(getattr(error,'response',None),'status_code',None)
                    health.disconnected('http_error' if status else 'timeout' if isinstance(error,(TimeoutError,httpx.TimeoutException)) else 'network',status)
                    socket_failures+=1
                    if socket_failures>=2:use_socket=False;socket_retry_at=time.monotonic()+30
                    service.db.set('miniapp_bridge',dict(state='连接中断，自动重试',at=p.now()))
                    await asyncio.sleep(1)
    finally:
        deliver.cancel();excel.cancel()
        if verify_excel:verify_excel.cancel()
        for task in inflight.values():task.cancel()
        await asyncio.gather(deliver,excel,*([verify_excel] if verify_excel else []),*inflight.values(),return_exceptions=True)
