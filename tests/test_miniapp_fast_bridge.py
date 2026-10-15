import asyncio,json,os,unittest,time
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from test_progress_miniapp import MiniAppTests,signed
from team_bot.miniapp_fast_bridge import process,loop
from team_bot.progress_miniapp import request_timing

class FastBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture=MiniAppTests();self.fixture.setUp();self.api=self.fixture.api
        self.p=self.fixture.p;self.s=self.fixture.s
    def tearDown(self):self.fixture.tearDown()
    def job(self):
        return dict(id='mock-transport-job',path='/api/submit',claimed_at=123,
            envelope=dict(initData=signed(self.fixture.c.token,self.fixture.u),body=self.fixture.body()))
    async def test_saved_response_does_not_wait_for_group_delivery(self):
        self.p._delivery_event=asyncio.Event()
        with patch('team_bot.miniapp_fast_bridge.flush',new=AsyncMock()) as delivery:
            result=await process(self.api,self.job())
            self.assertEqual(result['status'],200);self.assertTrue(self.p._delivery_event.is_set())
            self.assertIsNotNone(self.p.feedback(self.fixture.r,self.fixture.u))
            delivery.assert_not_awaited();self.assertIsNone(request_timing.get())
    async def test_permission_failure_does_not_save_or_report_success(self):
        job=self.job();job['envelope']['body']['uid']=self.fixture.v
        result=await process(self.api,job)
        self.assertEqual(result['status'],403);self.assertNotIn('saved',result['body'])
        self.assertIsNone(self.p.feedback(self.fixture.r,self.fixture.u))
    async def test_internal_failure_is_redacted_and_retry_retains_request(self):
        with patch.object(self.api,'dispatch',new=AsyncMock(side_effect=RuntimeError('mock-private-value'))):
            result=await process(self.api,self.job())
        self.assertEqual(result['status'],503);self.assertNotIn('mock-private-value',str(result))
        self.assertEqual((await process(self.api,self.job()))['status'],200)
        self.assertEqual(self.p.feedback(self.fixture.r,self.fixture.u)['version'],1)
    async def test_socket_duplicate_inflight_and_lost_ack_reconnect(self):
        job=self.job();completed=asyncio.Event();delivered=asyncio.Event();sends=[]
        class Socket:
            def __init__(self,number):self.number=number
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def send(self,raw):
                packet=json.loads(raw);sends.append(packet)
                if packet.get('type')=='complete':
                    completed.set()
                    if self.number==1:raise ConnectionError('mock lost ack')
                    delivered.set()
            def __aiter__(self):return self.stream()
            async def stream(self):
                if self.number==1:
                    yield json.dumps(dict(type='jobs',jobs=[job,job]))
                    await completed.wait();raise ConnectionError('mock reconnect')
                yield json.dumps(dict(type='ack',id=job['id']))
                await asyncio.Event().wait()
        count=0
        def connect(*args,**kwargs):
            nonlocal count
            count+=1;return Socket(count)
        class Client:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        app=SimpleNamespace(bot=self.api.bot)
        with patch.dict(os.environ,PROGRESS_MINIAPP_ORIGIN='https://mock.invalid',PROGRESS_BRIDGE_SECRET='mock-only'),patch('websockets.asyncio.client.connect',new=connect),patch('httpx.AsyncClient',return_value=Client()),patch('team_bot.miniapp_fast_bridge.MiniAPI',return_value=self.api),patch('team_bot.miniapp_fast_bridge.controller',return_value=self.p),patch('team_bot.miniapp_fast_bridge.flush',new=AsyncMock()),patch.object(self.api,'dispatch',wraps=self.api.dispatch) as dispatch:
            runner=asyncio.create_task(loop(app,self.s))
            try:await asyncio.wait_for(delivered.wait(),5)
            finally:runner.cancel();await asyncio.gather(runner,return_exceptions=True)
            self.assertEqual(dispatch.await_count,1)
        self.assertEqual(count,2);self.assertEqual(self.p.feedback(self.fixture.r,self.fixture.u)['version'],1)
        packets=[x['job'] for x in sends if x.get('type')=='complete']
        self.assertEqual(len(packets),2);self.assertEqual(packets[0],packets[1])
    async def test_http_fallback_probes_socket_again_without_stopping_bot(self):
        connected=asyncio.Event();clock=[0];attempts=[0];pulls=[]
        class Socket:
            async def __aenter__(self):
                if attempts[0]<3:raise ConnectionError('mock temporary outage')
                return self
            async def __aexit__(self,*args):return False
            async def send(self,*args):pass
            def __aiter__(self):return self.stream()
            async def stream(self):
                yield json.dumps(dict(type='ping'))
                connected.set();await asyncio.Event().wait()
        def connect(*args,**kwargs):attempts[0]+=1;return Socket()
        class Client:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def post(self,path,**kwargs):
                pulls.append(path);clock[0]=31
                return SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(jobs=[]))
        with patch.dict(os.environ,PROGRESS_MINIAPP_ORIGIN='https://mock.invalid',PROGRESS_BRIDGE_SECRET='mock-only'),patch('websockets.asyncio.client.connect',new=connect),patch('httpx.AsyncClient',return_value=Client()),patch('team_bot.miniapp_fast_bridge.MiniAPI',return_value=self.api),patch('team_bot.miniapp_fast_bridge.controller',return_value=self.p),patch('team_bot.miniapp_fast_bridge.flush',new=AsyncMock()),patch('team_bot.miniapp_fast_bridge.time',new=SimpleNamespace(monotonic=lambda:clock[0],perf_counter=time.perf_counter)):
            runner=asyncio.create_task(loop(SimpleNamespace(bot=self.api.bot),self.s))
            try:await asyncio.wait_for(connected.wait(),6)
            finally:runner.cancel();await asyncio.gather(runner,return_exceptions=True)
        self.assertEqual(attempts[0],3);self.assertTrue(pulls)
        self.assertEqual(self.s.db.setting('miniapp_bridge')['mode'],'WebSocket')
