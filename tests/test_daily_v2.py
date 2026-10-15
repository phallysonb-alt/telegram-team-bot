import json,unittest,asyncio
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from telegram import CallbackQuery
from telegram.error import BadRequest
from test_acceptance import Base,report,DEMO_DAY
import test_acceptance as fixtures
from team_bot.bot import Adapter
from team_bot.daily import METRICS,parse_daily,daily_view
from team_bot.checkin import Checkins
from team_bot.scheduler import Scheduler

def raw(day='2026-10-10',values=(1,2,3,4)):
    return '日期：'+day+'\n'+'\n'.join(k+'：'+str(v) for k,v in zip(METRICS,values))

class DailyBase(Base):
    def setUp(self):
        super().setUp();self.c.report_version=2;self.c.ai_enabled=False
        self.c.times={'open':'23:00','deadline':'00:00','summary':'00:05'};self.c.offsets={'open':0,'deadline':1,'summary':1};self.c.validate()
    def clock(self,t):self.now=datetime.fromisoformat(t).replace(tzinfo=self.c.zone)
    def submit(self,uid,values=(1,2,3,4)):
        d,_,_=self.s.draft(uid,raw(values=values),'local');return self.s.confirm(uid,d)

class DailyTests(DailyBase):
    def test_four_order_blank_zero_and_no_business_comparison(self):
        day,p=parse_daily(raw(values=(100,0,999,1)),self.now.date());self.assertEqual(tuple(k for k in p if k!='备注'),METRICS)
        self.assertEqual(p['有效'],999)
        with self.assertRaisesRegex(ValueError,'尚未填写'):parse_daily(raw(values=('',0,1,2)),self.now.date())
        for values in [(-1,2,3,4),('1.5',2,3,4)]:
            with self.assertRaises(ValueError):parse_daily(raw(values=values),self.now.date())
        with self.assertRaises(ValueError):parse_daily(raw()+'\n员工编号：E002',self.now.date())

    def test_same_day_replaces_history_and_preserves_legacy(self):
        u=self.emp();self.c.report_version=1;d,_,_=self.s.draft(u,report(DEMO_DAY,1),'legacy');self.s.confirm(u,d);old=self.s.record(u,DEMO_DAY);self.c.report_version=2
        self.submit(u);self.submit(u,(5,6,7,8));r=self.s.record(u,'2026-10-10')
        self.assertEqual(r['version'],2);self.assertEqual(json.loads(r['payload'])['新增'],5)
        self.assertEqual(self.s.db.one('select * from records')['payload'],old['payload'])
        self.assertIn('上半场',self.s.legacy_view(DEMO_DAY)[1]);self.assertNotIn('引流',self.s.render_summary(self.s.summary('2026-10-10')))
        self.assertEqual(self.s.db.one("select count(*) n from history where entity='新版日报'")['n'],2)

    def test_confirmation_time_boundaries_not_preview_time(self):
        u=self.emp();self.clock('2026-10-10T22:59:59');d,_,_=self.s.draft(u,raw(),'x');self.clock('2026-10-10T23:00:00');self.s.confirm(u,d)
        self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'按时提交')
        self.clock('2026-10-10T23:59:59');self.submit(u);self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'按时提交')
        self.clock('2026-10-11T00:00:00');self.submit(u,(2,2,3,4));self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'截止后修订')
        self.assertEqual(self.c.business_date().isoformat(),'2026-10-11')

    def test_early_late_missing_and_partial(self):
        u=self.emp();v=self.emp(2)
        with self.assertRaises(ValueError):self.submit(u,('',0,3,4))
        self.submit(u,(0,0,3,4));self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'提前提交')
        self.clock('2026-10-11T00:01');self.submit(v);self.assertEqual(self.s.record(v,'2026-10-10')['timing'],'迟交')
        r=self.s.summary('2026-10-10');self.assertEqual(r['submitted'],2);self.assertEqual(r['missing_fields']['新增'],0);self.assertEqual(r['totals']['新增'],1)

    def test_thirty_people_isolation_order_and_totals(self):
        users=[self.emp(i) for i in range(1,31)]
        for i,u in enumerate(users[:29],1):self.submit(u,(i,i*2,i*3,i*4))
        r=self.s.summary('2026-10-10');self.assertEqual(r['totals'],dict(zip(METRICS,(435,870,1305,1740))))
        self.assertEqual(r['missing_reports'],1);self.assertEqual(r['rows'][0]['code'],'E001');self.assertEqual(r['rows'][-1]['code'],'E030')
        self.assertEqual(daily_view(self.s,r,2)['blocks'][2]['cells'][0][0]['text'],'员工名称')
        d,_,_=self.s.draft(users[0],raw(),'x')
        with self.assertRaises(PermissionError):self.s.confirm(users[1],d)
        self.s.leave(99999,users[-1],'2026-10-10',1,'local leave');self.assertEqual(self.s.summary('2026-10-10')['missing_reports'],0)

    def test_revoke_and_correct_update_published_versions(self):
        u=self.emp();self.submit(u);sch=Scheduler(self.s);sch.publish('2026-10-10','manual')
        self.s.revoke(u,u,'2026-10-10','撤销');self.assertEqual(self.s.summary('2026-10-10')['missing_reports'],1)
        self.clock('2026-10-11T00:10');self.s.correct(99999,u,'2026-10-10',raw(values=(9,8,7,6)),'更正')
        self.assertEqual(self.s.summary('2026-10-10')['totals']['新增'],9)
        self.assertEqual(self.s.db.one('select count(*) n from daily_summaries')['n'],3)

    def test_export_labels_and_legacy_separation(self):
        import zipfile
        u=self.emp();self.submit(u);p=self.s.range_export(99999,'2026-10-10','2026-10-10')
        with zipfile.ZipFile(p) as z:
            header=z.read('新版四项.csv').decode('utf-8-sig').splitlines()[0]
            self.assertEqual(header.split(',')[2:6],list(METRICS));self.assertIn('旧版原字段.json',z.namelist())

class CheckTests(DailyBase):
    def setUp(self):super().setUp();self.u=self.emp();self.ck=Checkins(self.s);self.task=self.ck.tasks()[0]['id'];self.clock('2026-10-10T15:00')
    def click(self,action='add',key=None):
        key=key or str(self.now);result=self.ck.change(self.u,self.task,'2026-10-10',action,key);self.now+=timedelta(seconds=3);return result
    def test_duplicate_callback_cooldown_over_target_and_undo(self):
        self.click(key='one');self.ck.change(self.u,self.task,'2026-10-10','add','one');self.assertEqual(self.ck.amount(self.u,self.task,'2026-10-10'),1)
        self.ck.change(self.u,self.task,'2026-10-10','add','two')
        with self.assertRaises(ValueError):self.ck.change(self.u,self.task,'2026-10-10','add','fast')
        self.now+=timedelta(seconds=3);self.click();self.click();text,_=self.ck.card(self.u,self.task,'2026-10-10');self.assertIn('4/3',text);self.assertIn('已完成',text)
        self.click('undo');self.assertEqual(self.ck.amount(self.u,self.task,'2026-10-10'),3)
        self.assertEqual(self.s.db.one('select count(*) n from check_events')['n'],5)
    def test_undo_zero_and_inactive_hours(self):
        with self.assertRaises(ValueError):self.click('undo')
        self.clock('2026-10-10T18:00')
        with self.assertRaises(ValueError):self.click()
    def test_midnight_never_previous_day(self):
        self.clock('2026-10-10T23:59:57');self.click();self.clock('2026-10-11T00:00')
        with self.assertRaises(ValueError):self.ck.change(self.u,self.task,'2026-10-10','add','old')
        with self.assertRaises(ValueError):self.ck.change(self.u,self.task,'2026-10-11','add','night')
        self.clock('2026-10-11T12:00');self.ck.change(self.u,self.task,'2026-10-11','add','new')
        self.assertEqual(self.ck.amount(self.u,self.task,'2026-10-11'),1);self.assertEqual(self.ck.amount(self.u,self.task,'2026-10-10'),1)
    def test_reports_and_checks_independent_thirty_people(self):
        users=[self.u]+[self.emp(i) for i in range(2,31)]
        for u in users:self.ck.change(u,self.task,'2026-10-10','add',str(u))
        self.assertEqual(self.s.db.one('select count(*) n from daily_records')['n'],0)
        self.submit(self.u,(50,2,3,4));self.assertEqual(self.ck.amount(self.u,self.task,'2026-10-10'),1)
        self.now+=timedelta(seconds=3);self.click('undo');self.assertEqual(self.s.summary('2026-10-10')['totals']['新增'],50)
        self.assertEqual(self.ck.amount(users[-1],self.task,'2026-10-10'),1)
    def test_config_applicability_and_period_details(self):
        other=self.emp(2);self.ck.configure(99999,self.task,'模拟新名称',4,[self.u],['16:00','21:00'],'调整')
        with self.assertRaises(PermissionError):self.ck.change(other,self.task,'2026-10-10','add','other')
        self.click();self.clock('2026-10-10T20:00');self.click()
        view=self.ck.inspect(99999,'2026-10-10',self.task,detail_uid=self.u)
        self.assertIn('12:00–16:00净变化1',view);self.assertIn('21:00净变化1',view);self.assertIn('打卡明细',view)

class ScheduleTests(DailyBase,unittest.IsolatedAsyncioTestCase):
    def bot(self):
        return SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=101)),edit_message_text=AsyncMock(return_value=SimpleNamespace(message_id=101)))
    async def test_midnight_due_day_restart_repeated_and_disabled(self):
        self.emp();self.clock('2026-10-11T00:04:59');sch=Scheduler(self.s);self.c.workdays=tuple(range(7))
        sch.plan(self.now);self.assertEqual(self.s.db.one('select count(*) n from daily_publications')['n'],0)
        sch.confirm(99999,self.c.timezone,','.join(self.c.times.values()));self.s.db.set('daily_scan_start','2026-10-10')
        self.clock('2026-10-11T00:05');sch.plan(self.now);Scheduler(self.s).plan(self.now)
        self.assertEqual(self.s.db.one('select count(*) n from daily_publications')['n'],1);self.assertEqual(self.s.db.one('select day from daily_publications')['day'],'2026-10-10')
        self.clock('2026-10-13T12:00');Scheduler(self.s).plan(self.now);self.assertEqual(self.s.db.one('select count(*) n from daily_publications')['n'],3)
        self.s.db.set('schedule_confirmed',None);self.clock('2026-10-14T12:00');sch.plan(self.now);self.assertEqual(self.s.db.one('select count(*) n from daily_publications')['n'],3)
    async def test_post_deadline_late_revise_edit_retry_no_new_message(self):
        u=self.emp();sch=Scheduler(self.s);sch.publish('2026-10-10','x');bot=self.bot()
        with patch('team_bot.rich.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=101))):await sch.flush(bot)
        self.clock('2026-10-11T00:10');self.submit(u);self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'迟交')
        with patch('team_bot.rich.edit_native',new=AsyncMock(side_effect=RuntimeError('mock failure'))):
            bot.edit_message_text.side_effect=RuntimeError('mock failure');await sch.flush(bot)
        pub=self.s.db.one('select * from daily_publications');self.assertEqual(pub['state'],'pending');self.assertEqual(pub['message_id'],101)
        self.now+=timedelta(minutes=1);self.submit(u,(5,6,7,8));self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'截止后修订')
        with patch('team_bot.rich.edit_native',new=AsyncMock(return_value=SimpleNamespace(message_id=101))):await Scheduler(self.s).flush(bot)
        pub=self.s.db.one('select * from daily_publications');self.assertEqual(pub['state'],'sent');self.assertEqual(pub['version'],self.s.db.one('SELECT MAX(version) v FROM daily_summaries WHERE day=?',('2026-10-10',))['v']);self.assertEqual(pub['message_id'],101);bot.send_message.assert_not_called()
    async def test_native_failure_plain_fallback_and_management_queue_block(self):
        self.emp();sch=Scheduler(self.s);sch.publish('2026-10-10','x');sch.reply('retired',self.c.chat,33,'old management');bot=self.bot()
        with patch('team_bot.rich.send_native',new=AsyncMock(side_effect=RuntimeError('mock unsupported'))):await sch.flush(bot)
        self.assertEqual(bot.send_message.await_count,1);self.assertEqual(self.s.db.one("select state from outbox where key='retired:0'")['state'],'cancelled')

class AdapterV2Tests(DailyBase,unittest.IsolatedAsyncioTestCase):
    make_update=fixtures.TelegramAdapterTests.make_update
    async def test_isolation_run_blocks_formal_registration_and_training(self):
        u=self.emp();self.s.db.set('isolation_only_run',True)
        self.s.db.set('live_test',dict(run='local-only',path=str(self.c.data/'live-tests/local-only'),participants=[u],allowed=[u]))
        a=Adapter(self.s);ctx=SimpleNamespace(bot=SimpleNamespace(id=123))
        await a.handle(self.make_update(90,'/register E002',uid=10002),ctx)
        await a.handle(self.make_update(91,'/materials',uid=u,thread=22),ctx)
        self.assertIsNone(self.s.db.one('select uid from employees where uid=10002'))
        self.assertEqual(self.s.db.all('select * from outbox'),[])
    async def test_management_silent_even_callback_and_admin_group_denied(self):
        self.emp();a=Adapter(self.s);ctx=SimpleNamespace(bot=SimpleNamespace(id=123))
        with patch.object(CallbackQuery,'answer',new=AsyncMock()) as answer:
            await a.handle(self.make_update(1,'/task_new',thread=33,uid=99999),ctx)
            await a.handle(self.make_update(2,thread=33,callback='task_update:1'),ctx)
            answer.assert_not_called()
        self.assertEqual(self.s.db.all('select * from outbox'),[])
        await a.handle(self.make_update(3,'/approve 10001',thread=44,uid=99999),ctx)
        self.assertIn('管理员私聊',self.s.db.one('select text from outbox')['text'])
    async def test_old_confirm_buttons_never_touch_new_draft(self):
        u=self.emp();d,_,_=self.s.draft(u,raw(),'local');a=Adapter(self.s);ctx=SimpleNamespace(bot=SimpleNamespace(id=123))
        with patch.object(CallbackQuery,'answer',new=AsyncMock()):await a.handle(self.make_update(1,callback='confirm:1'),ctx)
        self.assertEqual(self.s.db.one('select state from daily_drafts')['state'],'draft')
    async def test_retired_check_buttons_preserve_old_history(self):
        u=self.emp();self.clock('2026-10-10T15:00');ck=Checkins(self.s);task=ck.tasks()[0]['id'];ck.change(u,task,'2026-10-10','add','old-history');a=Adapter(self.s)
        ctx=SimpleNamespace(bot=SimpleNamespace(id=123));private={'id':u,'type':'private'}
        with patch.object(CallbackQuery,'answer',new=AsyncMock()):
            await a.handle(self.make_update(1,uid=u,chat=private,callback=f'ck:add:{u}:{task}:2026-10-10'),ctx)
        self.assertEqual(ck.amount(u,task,'2026-10-10'),1)
        self.assertEqual(ck.amount(u,task,'2026-10-10'),1) # retired callback must never mutate history
