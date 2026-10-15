import asyncio,hashlib,hmac,json,time,unittest,os
from urllib.parse import urlencode
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
import test_progress_rounds
from team_bot.progress_miniapp import MiniAPI,validate_init_data,query_card
from team_bot.progress_rounds import Progress
from team_bot.progress_ui import flush

def signed(token,uid,start='',at=None,name='模拟员工',username=''):
    data=dict(auth_date=str(int(time.time() if at is None else at)),user=json.dumps(dict(id=uid,first_name=name,username=username),ensure_ascii=False),start_param=start)
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    data['hash']=hmac.new(secret,'\n'.join(k+'='+v for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
    return urlencode(data)

class MiniAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture=test_progress_rounds.ProgressTests();self.fixture.setUp()
        self.p=self.fixture.p;self.s=self.fixture.s;self.c=self.fixture.c;self.u=self.fixture.u;self.v=self.fixture.v;self.r=self.fixture.r
        self.env=patch.dict(os.environ,{'PROGRESS_MINIAPP_LINK':'https://t.me/example_team_bot/work','PROGRESS_MINIAPP_ENABLED':'true'});self.env.start()
        self.membership=AsyncMock();self.api=MiniAPI(self.p,self.fixture.ctx.bot,self.membership)
    def tearDown(self):self.env.stop();self.fixture.tearDown()
    async def request(self,path,uid,body=None,start=''):
        return await self.api.dispatch('/api/'+path,dict(initData=signed(self.c.token,uid,start),body=body or {}))
    async def test_initial_session_bundles_admin_history_without_exposing_it_to_employee(self):
        result=await self.request('session',self.c.owner,start='summary_'+str(self.r))
        self.assertTrue(result['admin']);self.assertEqual(result['initial']['round'],self.r)
        self.assertIn(self.r,[r['id'] for r in result['initial']['rounds']])
        self.assertEqual(result['initial']['summary']['received'],0)
        employee=await self.request('session',self.u)
        self.assertFalse(employee['admin']);self.assertNotIn('initial',employee)
        publish=await self.request('session',self.c.owner,start='publish')
        self.assertNotIn('initial',publish)
    async def test_initial_session_empty_query_and_no_tasks_are_explicit(self):
        self.p.db.run('DELETE FROM pg_rounds')
        self.p.db.run('UPDATE pg_tasks SET closed=1')
        result=await self.request('session',self.c.owner)
        self.assertTrue(result['admin']);self.assertIsNone(result['initial']['round'])
        self.assertEqual(result['initial']['tasks'],0)
        self.assertEqual(result['initial']['rounds'],[])
    def body(self,key='request0001',rid=None,masks=None,notes=None):
        r=self.p.round(rid or self.r);keys=[str(t['id']) for t in r['tasks']]
        return dict(key=key,round=r['id'],masks=masks or {k:0 for k in keys},notes=notes or {k:'' for k in keys})
    def test_signature_time_numeric_identity_and_duplicate_fields(self):
        token='mock-token';raw=signed(token,456,'feedback_1',1000)
        self.assertEqual(validate_init_data(raw,token,1001)[0]['id'],456)
        for bad,now in [(raw.replace('feedback_1','feedback_2'),1001),(raw,5000),(raw,800),(raw+'&user=x',1001)]:
            with self.assertRaises((PermissionError,ValueError)):validate_init_data(bad,token,now)
        with self.assertRaises(PermissionError):validate_init_data(signed(token,True,at=1000),token,1001)
    async def test_membership_failure_and_disabled_employee(self):
        self.membership.side_effect=PermissionError('校验失败')
        with self.assertRaises(PermissionError):await self.request('feedback',self.u,dict(round=self.r))
        self.membership.side_effect=None;self.s.db.run('UPDATE employees SET active=0 WHERE uid=?',(self.u,))
        with self.assertRaises(PermissionError):await self.request('submit',self.u,self.body())
    async def test_backend_permissions_and_forged_identity(self):
        for path,body in [('summary',dict(round=self.r)),('publish',dict(key='publish001',tasks=[])),('history',{})]:
            with self.assertRaises(PermissionError):await self.request(path,self.u,body)
        body=self.body();body['uid']=self.v
        with self.assertRaises(PermissionError):await self.request('submit',self.u,body)
        self.assertIsNone(self.p.feedback(self.r,self.v))
    async def test_seq_free_multi_notes_one_submit_and_zero_unanswered(self):
        body=self.body(masks={str(self.fixture.t):3,str(self.fixture.z):5},notes={str(self.fixture.t):'第一项\n中文备注 😀',str(self.fixture.z):'第二项账户封控3个'})
        result=await self.request('submit',self.u,body);self.assertTrue(result['changed'])
        summary=await self.request('summary',self.c.owner,dict(round=self.r));self.assertEqual(summary['received'],1)
        row=next(x for x in summary['rows'] if x['submitted']);self.assertEqual(row['masks'][str(self.fixture.z)],5);self.assertEqual(row['notes'],body['notes'])
        self.assertEqual(sum(x['submitted'] for x in summary['rows']),1)
        await self.request('submit',self.v,self.body('zero000001'));self.assertEqual(json.loads(self.p.feedback(self.r,self.v)['payload'])[str(self.fixture.t)],0)
    async def test_illegal_sequence_or_missing_tasks_has_no_partial_save(self):
        body=self.body(masks={str(self.fixture.t):4,str(self.fixture.z):5})
        with self.assertRaises(ValueError):await self.request('submit',self.u,body)
        body=self.body();body['notes'].pop(str(self.fixture.z))
        with self.assertRaises(ValueError):await self.request('submit',self.u,body)
        self.assertIsNone(self.p.feedback(self.r,self.u))
    async def test_duplicate_response_retry_modification_audit_and_restart(self):
        b=self.body();a=await self.request('submit',self.u,b);self.assertEqual(await self.request('submit',self.u,b),a)
        b['key']='retry000002';await self.request('submit',self.u,b);self.assertEqual(self.p.feedback(self.r,self.u)['version'],1)
        b['key']='retry000003';b['notes'][str(self.fixture.z)]='修改\n完整备注'
        with self.assertRaisesRegex(ValueError,'锁定'):await self.request('submit',self.u,b)
        self.assertEqual(self.p.feedback(self.r,self.u)['version'],1)
        self.p.db.conn.close();self.p=Progress(self.s);self.fixture.p=self.p;self.api=MiniAPI(self.p,self.fixture.ctx.bot,self.membership)
        self.assertEqual(self.api.task_notes(self.r,self.u)[str(self.fixture.z)],'')
        with self.assertRaisesRegex(ValueError,'锁定'):await self.request('submit',self.u,b)
        self.assertEqual(self.p.db.one("SELECT COUNT(*) n FROM pg_audit WHERE entity='反馈'")['n'],1)
    async def test_new_query_prefill_not_submitted_and_old_entry_rejected(self):
        b=self.body(masks={str(self.fixture.t):3,str(self.fixture.z):5});await self.request('submit',self.u,b)
        newer=self.p.new_round(self.c.owner,'2026-10-10');f=await self.request('feedback',self.u,dict(round=newer))
        self.assertTrue(f['prefilled']);self.assertFalse(f['submitted']);self.assertEqual(f['masks'],b['masks'])
        retry=await self.request('submit',self.u,b);self.assertEqual(retry['message'],'已提交')
        with self.assertRaisesRegex(ValueError,'旧轮'):await self.request('submit',self.u,dict(b,key='fresh_old_query_request'))
        old=await self.request('summary',self.c.owner,dict(round=self.r));new=await self.request('summary',self.c.owner,dict(round=newer));self.assertEqual(old['received'],1);self.assertEqual(new['received'],0)
    async def test_changed_version_not_reinterpreted_and_historical_snapshot(self):
        await self.request('submit',self.u,self.body(masks={str(self.fixture.t):3,str(self.fixture.z):0}))
        with self.assertRaisesRegex(ValueError,'锁定'):self.p.publish(self.c.owner,dict(day='2026-10-10',title='新版任务',kind='seq',stages=['新一','新二','新三']),self.fixture.t)
        self.p.close(self.c.owner,self.fixture.t)
        independent=self.p.publish(self.c.owner,dict(day='2026-10-10',title='新版任务',kind='seq',stages=['新一','新二','新三']))
        new=self.p.new_round(self.c.owner,'2026-10-10');f=await self.request('feedback',self.u,dict(round=new));self.assertEqual(f['masks'][str(independent)],0)
        summary=await self.request('summary',self.c.owner,dict(round=self.r));self.assertNotEqual(summary['tasks'][0]['title'],'新版任务')
    async def test_new_tasks_do_not_claim_old_unrelated_data_was_prefilled(self):
        await self.request('submit',self.u,self.body())
        for t in (self.fixture.t,self.fixture.z):self.p.close(self.c.owner,t)
        self.p.publish(self.c.owner,dict(day='2026-10-10',title='独立新任务',kind='free',stages=['一','二','三']))
        rid=self.p.new_round(self.c.owner,'2026-10-10');f=await self.request('feedback',self.u,dict(round=rid))
        self.assertFalse(f['prefilled']);self.assertFalse(f['submitted']);self.assertTrue(all(value==0 for value in f['masks'].values()))
    async def test_batch_publish_atomic_idempotent_and_durable_group_outbox(self):
        body=dict(key='batch000001',day='2026-10-10',tasks=[dict(title='模拟新一',kind='seq',stages=['一','二','三']),dict(title='模拟新二',kind='free',stages=['一','二','三','四'])])
        a=await self.request('publish',self.c.owner,body);b=await self.request('publish',self.c.owner,body);self.assertEqual(a,b);self.assertEqual(len(a['tasks']),2)
        self.assertEqual(self.p.db.one("SELECT COUNT(*) n FROM pg_outbox WHERE key LIKE 'app:publish:%'")['n'],1)
        body['key']='badbatch001';body['tasks'][1]['stages']=[]
        with self.assertRaises(ValueError):await self.request('publish',self.c.owner,body)
        self.assertEqual(len(self.p.tasks('2026-10-10')),4)
    async def test_query_api_and_empty_day_and_single_task(self):
        one=await self.request('query',self.c.owner,dict(key='query000001',day='2026-10-10',task=self.fixture.z));self.assertEqual(len(self.p.round(one['round'])['tasks']),1)
        with self.assertRaises(ValueError):await self.request('query',self.c.owner,dict(key='empty000001',day='2026-10-11'))
        r=await self.request('query',self.c.owner,dict(key='query000002',day='2026-10-10'));self.assertEqual(len(self.p.round(r['round'])['tasks']),2)
    async def test_brief_group_card_edit_retry_no_team_table_or_extra_messages(self):
        self.p.db.set('query_card:'+str(self.r),777);await self.request('submit',self.u,self.body())
        bot=self.fixture.ctx.bot;bot.edit_message_text.side_effect=RuntimeError('mock network')
        await flush(self.p,bot);self.assertTrue(self.p.round(self.r)['pending']);self.assertEqual(bot.edit_message_text.call_args.kwargs['message_id'],777)
        self.fixture.f.clock('2026-10-10T14:01:00');bot.edit_message_text.side_effect=lambda **k:SimpleNamespace(message_id=k['message_id']);await flush(self.p,bot)
        self.assertFalse(self.p.round(self.r)['pending']);bot.send_message.assert_not_awaited()
        self.assertNotIn('table',str(bot.edit_message_text.call_args));self.assertIn('已反馈：1',bot.edit_message_text.call_args.kwargs['text']);self.assertEqual(bot.edit_message_text.call_args.kwargs['parse_mode'],'HTML')
    async def test_same_group_count_not_modified_is_edit_receipt_not_new_send(self):
        from telegram.error import BadRequest
        self.p.db.set('query_card:'+str(self.r),777);await self.request('submit',self.u,self.body())
        bot=self.fixture.ctx.bot;bot.edit_message_text.side_effect=BadRequest('Message is not modified')
        await flush(self.p,bot)
        receipt=json.loads(self.p.db.one("SELECT receipt FROM pg_outbox WHERE key LIKE 'appcard:%'")['receipt'])
        self.assertEqual(receipt['message'],777);self.assertEqual(receipt['method'],'editMessageText');self.assertEqual(receipt['result'],'not_modified');self.assertFalse(self.p.round(self.r)['pending']);bot.send_message.assert_not_awaited()
    async def test_thirty_mock_five_tasks_concurrent_api_and_daily_frozen(self):
        users=[self.fixture.f.approve(10000+i,'模拟同名'+str(i),'mock'+str(i)) for i in range(30)]
        for i in range(3):self.p.publish(self.c.owner,dict(day='2026-10-10',title='模拟'+str(i),kind='free',stages=['1','2','3','4']))
        rid=self.p.new_round(self.c.owner,'2026-10-10',roster=users,simulated=True)
        before=self.s.db.all('SELECT * FROM daily_records');exports=self.s.db.all("SELECT * FROM settings WHERE key LIKE 'daily_%'")
        await asyncio.gather(*(self.request('submit',u,self.body('person'+str(u),rid)) for u in users))
        self.assertEqual(self.p.db.one('SELECT COUNT(*) n FROM pg_feedback WHERE round=?',(rid,))['n'],30)
        result=await self.request('summary',self.c.owner,dict(round=rid));self.assertEqual(result['expected'],30);self.assertEqual(len(result['tasks']),5)
        self.assertEqual(before,self.s.db.all('SELECT * FROM daily_records'));self.assertEqual(exports,self.s.db.all("SELECT * FROM settings WHERE key LIKE 'daily_%'"))
    def test_group_link_not_private_webapp_button_and_long_unicode(self):
        text,markup=query_card(self.p,self.r);self.assertNotIn('web_app',str(markup));self.assertIn('?startapp=feedback_',str(markup));self.assertIn('已反馈：0',text)
    async def test_actual_bot_handler_new_query_and_retired_personal_button(self):
        group=dict(id=self.c.chat,type='supergroup',is_forum=True)
        await self.fixture.go(900,self.c.owner,text='查询任务',chat=group)
        latest=self.p.latest('2026-10-10')['id'];self.assertNotEqual(latest,self.r)
        args=self.fixture.ctx.bot.send_message.call_args.kwargs
        self.assertEqual(args['chat_id'],self.c.chat);self.assertIn('startapp=feedback_',str(args['reply_markup']));self.assertNotIn('web_app',str(args['reply_markup']))
        await self.fixture.go(901,self.u,f'pg:done:{latest}:{self.u}:1')
        self.assertIsNone(self.p.feedback(latest,self.u));self.assertIn('此入口已停用',self.fixture.ctx.bot.send_message.call_args.kwargs['text'])
        await self.fixture.go(900,self.c.owner,text='查询任务',chat=group);self.assertEqual(self.p.latest('2026-10-10')['id'],latest)
