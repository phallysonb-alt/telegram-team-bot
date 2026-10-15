import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from telegram import Bot
from telegram.error import RetryAfter
from team_bot.scheduler import Scheduler
from team_bot.bot import Adapter
from team_bot.rich import send_native, report_view, progress_view, training_view, RichAPIError
from test_acceptance import Base, report, DEMO_DAY
import test_acceptance as fixtures


class RichTests(Base, unittest.IsolatedAsyncioTestCase):
    make_update = fixtures.TelegramAdapterTests.make_update

    async def test_official_payload_general_no_paid_broadcast(self):
        requests=[]
        def handler(request):
            data=json.loads(request.content)
            requests.append(data)
            return httpx.Response(200,json=dict(ok=True,result=dict(message_id=21,date=1791590400,chat=dict(id=-100321,type='supergroup',title='模拟'),rich_message=data['rich_message'])))
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        content=dict(blocks=[dict(type='table',cells=[[dict(text='指标',is_header=True,align='left',valign='top')]])])
        with patch('team_bot.rich.httpx.AsyncClient',return_value=client):
            sent=await send_native(Bot('123:fake'),-100321,None,content)
        self.assertNotIn('message_thread_id',requests[0])
        self.assertIs(requests[0]['allow_paid_broadcast'],False)
        self.assertEqual(sent.api_kwargs['rich_message']['blocks'][0]['type'],'table')

    async def test_fallback_splits_once_preserves_thread_and_plain_button(self):
        sch=Scheduler(self.s)
        sch.reply('rich-demo',self.c.chat,22,'中'*8000,rich=dict(blocks=[dict(type='paragraph',text='测试')]))
        sent=[]
        async def send_message(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(message_id=len(sent))
        bot=SimpleNamespace(send_message=send_message)
        with patch('team_bot.rich.send_native',new=AsyncMock(side_effect=RichAPIError(400,'rich_message'))) as native:
            await sch.flush(bot)
            await sch.flush(bot)
            await sch.flush(bot)
        self.assertEqual(native.await_count,1)
        self.assertEqual(''.join(m['text'] for m in sent),'中'*8000)
        self.assertTrue(all(m['message_thread_id']==22 for m in sent))
        self.assertEqual(len(sent),3)
        self.assertEqual(sch.db.setting('rich_failure:rich-demo:0')['code'],400)
        self.assertIn('普通文字查看',str(sent[0]['reply_markup']))

    async def test_429_waits_without_plain_flood(self):
        sch=Scheduler(self.s)
        sch.reply('rate',self.c.chat,11,'测试',rich=dict(blocks=[]))
        bot=SimpleNamespace(send_message=AsyncMock())
        with patch('team_bot.rich.send_native',new=AsyncMock(side_effect=RetryAfter(31))):
            await sch.flush(bot)
        bot.send_message.assert_not_awaited()
        self.assertEqual(sch.db.one('SELECT state FROM outbox')['state'],'pending')
        self.assertIsNotNone(sch.db.setting('rich:rate:0'))

    def test_table_numbers_and_unknown_dedup_not_ai(self):
        uid=self.emp()
        d,_,_=self.s.draft(uid,report(DEMO_DAY,1),'sample')
        self.s.confirm(uid,d)
        r=self.s.summary(DEMO_DAY)
        r['ai_text']='精聊 999999'
        rich=report_view(self.s,r)
        rows=next(b for b in rich['blocks'] if b['type']=='table')['cells']
        self.assertEqual(rows[1][3]['text'],str(r['totals']['精聊']))
        self.assertEqual(rows[4][3]['text'],'未统计')
        self.assertNotIn('999999',json.dumps(rich,ensure_ascii=False))

    def test_thirty_person_pages_no_unset_zero(self):
        for i in range(30):
            self.emp(i+1)
        self.s.roster(DEMO_DAY)
        rich,plain=progress_view(self.s,DEMO_DAY,page=6)
        rows=next(b for b in rich['blocks'] if b['type']=='table')['cells']
        self.assertEqual(len(rows),16)
        self.assertIn('E030',plain)
        self.assertNotIn('E001',plain)
        self.assertEqual(rows[1][2]['text'],'未设置')
        self.assertEqual(rows[1][3]['text'],'待补')
        with self.assertRaises(ValueError): progress_view(self.s,DEMO_DAY,page=7)

    def test_training_quotes_sources_long_details(self):
        hits=[dict(name='样例',version=2,location='第1段',text='<table>不可信内容</table>'+'中'*600)]
        rich=training_view('本地答案',hits)
        source=rich['blocks'][2]
        self.assertEqual(source['type'],'details')
        self.assertEqual(source['blocks'][0]['type'],'blockquote')
        self.assertEqual(source['blocks'][0]['blocks'][0]['text'],hits[0]['text'])

    async def test_plain_snapshot_viewer_permissions_and_test_scope(self):
        self.emp()
        self.emp(2)
        a=Adapter(self.s)
        a.db.set('private_started:10001',True)
        a.db.set('display:sample',dict(text='私有培训资料',audience='employee',viewer=10001))
        with self.assertRaises(PermissionError):
            a.dispatch(self.make_update(1,uid=10002),'plain',['sample'],'')
        text,_,_,target=a.dispatch(self.make_update(2),'plain',['sample'],'')
        self.assertEqual((text,target),('私有培训资料',10001))
        self.c.test_run='scope'
        a.scheduler.reply('test',self.c.chat,11,'测试',rich=dict(blocks=[]))
        markup=json.loads(a.db.one("SELECT markup FROM outbox WHERE key='test:0'")['markup'])
        self.assertTrue(markup['inline_keyboard'][0][0]['callback_data'].startswith('test:scope:plain:'))
        self.assertIn('联调测试',a.db.setting('rich:test:0')['message']['blocks'][0]['text'])

    async def test_plain_commands_work_without_any_rich_display(self):
        self.emp()
        a=Adapter(self.s)
        a.db.set('private_started:10001',True)
        ctx=SimpleNamespace(bot=SimpleNamespace(id=123))
        await a.handle(self.make_update(1,'/progress_plain '+DEMO_DAY,thread=11),ctx)
        await a.handle(self.make_update(2,'/report_plain '+DEMO_DAY,thread=44),ctx)
        await a.handle(self.make_update(3,'/ask_plain 不存在资料',thread=22),ctx)
        self.assertTrue(a.db.one("SELECT id FROM outbox WHERE text LIKE '%工作进度总表%'"))
        self.assertTrue(a.db.one("SELECT id FROM outbox WHERE text LIKE '%工作日报汇总%'"))
        self.assertTrue(a.db.one("SELECT id FROM outbox WHERE text LIKE '%未找到对应资料%'"))
        self.assertEqual(a.db.all("SELECT key FROM settings WHERE key LIKE 'rich:%'"),[])

    async def test_views_keep_topic_binding_and_detail_permissions(self):
        self.emp()
        a=Adapter(self.s)
        with self.assertRaises(ValueError):
            a.dispatch(self.make_update(1,thread=22),'progress_view',[DEMO_DAY],'')
        with self.assertRaises(PermissionError):
            a.dispatch(self.make_update(2,thread=11),'progress_details',[DEMO_DAY,'1'],'')
