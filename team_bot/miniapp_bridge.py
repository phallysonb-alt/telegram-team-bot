"""Outbound authenticated relay; no second polling/getUpdates and no DB migration."""
import asyncio,os
import httpx

async def loop(app,service):
    from .progress_ui import controller
    from .progress_miniapp import MiniAPI
    api=MiniAPI(controller(service),app.bot)
    url=os.environ['PROGRESS_MINIAPP_ORIGIN'].rstrip('/')
    if not url.startswith('https://'):raise ValueError('Mini App 必须使用固定 HTTPS 地址')
    headers={'Authorization':'Bearer '+os.environ['PROGRESS_BRIDGE_SECRET']}
    if os.environ.get('PROGRESS_SITES_AUTH'):headers['OAI-Sites-Authorization']='Bearer '+os.environ['PROGRESS_SITES_AUTH']
    async with httpx.AsyncClient(timeout=45,headers=headers) as client:
        from .miniapp_fast_bridge import process as process_job
        async def process(job):return await process_job(api,job)
        while True:
            try:
                response=await client.post(url+'/bridge/pull',json={});response.raise_for_status();jobs=response.json()['jobs']
                if jobs:
                    results=await asyncio.gather(*(process(job) for job in jobs))
                    response=await client.post(url+'/bridge/complete',json=dict(jobs=results));response.raise_for_status()
                service.db.set('miniapp_bridge',dict(state='已连接',at=api.p.now(),jobs=len(jobs)))
                await asyncio.sleep(0.8)
            except asyncio.CancelledError:raise
            except Exception:
                service.db.set('miniapp_bridge',dict(state='连接中断，自动重试',at=api.p.now()))
                await asyncio.sleep(3)

async def start(app,service):
    if os.environ.get('PROGRESS_MINIAPP_BRIDGE','').lower()=='true':
        from .miniapp_fast_bridge import loop as fast_loop
        app.bot_data['miniapp_bridge_task']=asyncio.create_task(fast_loop(app,service))

async def stop(app):
    task=app.bot_data.pop('miniapp_bridge_task',None)
    if task:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
