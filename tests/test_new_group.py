"""New-group identity tests. Synthetic updates through Adapter.handle, no Telegram network."""
import unittest,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from telegram import User,CallbackQuery
from test_daily_v2 import DailyBase,raw
import test_acceptance as fixtures
from team_bot.bot import Adapter
from team_bot.workbench import Workbench
from team_bot.scheduler import Scheduler

class NewGroupTests(DailyBase,unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  super().setUp();self.c.identity_mode='telegram';self.c.membership_required=True;self.c.internal_chat=-100999;self.c.run_mode='trial';self.c.test_run='newgroup';self.c.schedule_allowed=False
  # A trial fixture must never project its synthetic data into the live export path.
  self.s.db.set('daily_excel_path',str(self.c.data/'exports'/'日报历史记录.xlsx'))
  self.clock('2026-10-10T13:59:59');self.a=Adapter(self.s);self.members={self.c.owner:('creator',self.user(self.c.owner,'模拟所有者'))};self.ctx=SimpleNamespace(bot=SimpleNamespace(id=123,get_chat_member=AsyncMock(side_effect=self.member)))
 def member(self,chat,uid):
  if uid not in self.members:raise RuntimeError('network-fixture')
  status,user=self.members[uid];return SimpleNamespace(status=status,user=user,is_member=status=='restricted')
 def user(self,uid,name='同名员工',username=None):return User(uid,name,False,username=username)
 async def handle(self,n,uid,text='',data='',chat=None,thread=44,user=None):
  u=fixtures.TelegramAdapterTests.make_update(self,n,text,uid=uid,chat=chat or {'id':uid,'type':'private'},thread=thread,callback='test:newgroup:'+data if data else None)
  if user:object.__setattr__(u,'_effective_user',user)
  with patch.object(CallbackQuery,'answer',new=AsyncMock()):await self.a.handle(u,self.ctx)
  return u
 def approve(self,uid,name='测试用户',username=None):
  u=self.user(uid,name,username);self.members[uid]=('member',u);self.s.open_identity(self.c.owner,u);return uid
 async def test_start_apply_buttons_approve_no_code_no_autoadmin(self):
  uid=456;self.members[uid]=('administrator',self.user(uid,'中文 员工😀 <&>'))
  await self.handle(1,uid,'/start');self.assertIn('申请开通',self.s.db.one('SELECT markup FROM outbox')['markup']);self.assertIsNone(self.s.db.one('SELECT * FROM employees'))
  await self.handle(2,uid,data='apply');self.assertFalse(self.s.admin(uid));self.assertIsNone(self.s.db.one('SELECT * FROM employees WHERE uid=?',(uid,)))
  self.members[uid]=('member',self.user(uid,'中文 员工😀 <&>'));await self.handle(3,uid,'/start');self.assertEqual(self.s.employee(uid)['role'],'employee');self.assertFalse(self.s.admin(uid))
  self.assertEqual(self.s.db.one('SELECT state FROM onboarding')['state'],'approved')
 def test_bulk_approval_updates_existing_today_roster(self):
  self.approve(456);self.assertEqual(self.s.summary('2026-10-10')['expected'],1)
  self.approve(789);self.assertEqual(self.s.summary('2026-10-10')['expected'],2)
 async def test_same_name_rename_namesnapshot_and_history_retained(self):
  x=self.approve(456,'重复😀','one');y=self.approve(789,'重复😀','two');self.assertNotEqual(self.s.display(x),self.s.display(y))
  self.submit(x);self.s.profile(self.user(x,'改名 中文 <&>'));self.assertEqual(json.loads(self.s.record(x,'2026-10-10')['payload'])['新增'],1)
  self.assertIn('改名',self.s.summary('2026-10-10')['rows'][0]['code']);snap=self.s.db.one('SELECT target_name FROM audit_names WHERE target_name LIKE ?',('重复%',));self.assertIsNotNone(snap)
  self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM employees')['n'],2)
 async def test_member_left_disabled_and_transient_failure_fail_closed(self):
  uid=self.approve(456);await self.handle(1,uid,text=raw());self.members[uid]=('left',self.user(uid));await self.handle(2,uid,data='daily_confirm:1');self.assertEqual(self.s.record(uid,'2026-10-10')['version'],1);self.assertEqual(self.s.db.one('SELECT active FROM employees')['active'],0)
  other=self.approve(789);del self.members[other];await self.handle(3,other,text=raw());self.assertIsNone(self.s.db.one('SELECT * FROM daily_drafts WHERE uid=?',(other,)));self.assertEqual(self.s.db.one('SELECT active FROM employees WHERE uid=?',(other,))['active'],1)
 async def test_disable_admin_buttons_and_reject_unauthorized(self):
  uid=self.approve(456);await self.handle(1,uid,data='people:disable:456');self.assertEqual(self.s.employee(uid)['active'],1)
  await self.handle(2,self.c.owner,data='people:disable:456');self.assertEqual(self.s.db.one('SELECT active FROM employees')['active'],0)
  await self.handle(3,uid,data='wb:mine');self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM wb_events')['n'],0)
 async def test_new_routes_internal_silent_and_original_card_ownership(self):
  uid=self.approve(456);old={'id':self.c.internal_chat,'type':'supergroup','is_forum':True}
  await self.handle(1,uid,'/template',chat=old);self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM outbox')['n'],0)
  await self.handle(2,uid,'/template',chat={'id':self.c.chat,'type':'supergroup','is_forum':True},thread=11);self.assertIn('对应话题',self.s.db.one('SELECT text FROM outbox')['text'])
  await self.handle(3,uid,'/template',chat={'id':self.c.chat,'type':'supergroup','is_forum':True},thread=44);self.assertIn('验收试用',self.s.db.one("SELECT text FROM outbox WHERE key='update:3:0'")['text'])
  await self.handle(4,uid,data='wb:mine');self.s.db.run("UPDATE wb_cards SET message_id=50 WHERE kind='mine'");other=self.approve(789)
  await self.handle(5,other,data='wb:add:2026-10-10:1:1',chat={'id':uid,'type':'private'});self.assertEqual(Workbench(self.s).amount(uid,1,'2026-10-10'),0)
 async def test_name_exports_formula_and_unique_ids(self):
  uid=self.approve(456,'=危险名称 😀');self.submit(uid);dest=self.s.range_export(self.c.owner,'2026-10-10','2026-10-10')
  import zipfile
  with zipfile.ZipFile(dest) as z:self.assertIn("'=危险",z.read('新版四项.csv').decode('utf-8-sig'))
 async def test_mock_transport_and_old_PH_outbox_are_never_sent(self):
  bot=SimpleNamespace(send_message=AsyncMock())
  scheduler=Scheduler(self.s);self.s.db.set('display_mock',True)
  scheduler.reply('mock-user',123456,None,'模拟消息');await scheduler.flush(bot)
  self.assertEqual(self.s.db.one("SELECT state FROM outbox WHERE key='mock-user:0'")['state'],'fixture_only');bot.send_message.assert_not_called()
  self.s.db.set('display_mock',False);scheduler.reply('old-route',self.c.internal_chat,11,'旧群待发');await scheduler.flush(bot)
  self.assertEqual(self.s.db.one("SELECT state FROM outbox WHERE key='old-route:0'")['state'],'paused_old_route');bot.send_message.assert_not_called()
 async def test_admin_plain_view_in_group_without_employee_identity(self):
  self.s.db.set('private_started:'+str(self.c.owner),True)
  self.s.db.set('display:fixture',dict(text='原报表',audience='admin',viewer=None))
  await self.handle(1,self.c.owner,data='plain:fixture',chat={'id':self.c.chat,'type':'supergroup','is_forum':True},thread=44)
  row=self.s.db.one("SELECT * FROM outbox WHERE key='update:1:0'")
  self.assertEqual(row['chat'],self.c.owner);self.assertIn('原报表',row['text'])
  uid=self.approve(456);await self.handle(2,uid,data='plain:fixture',chat={'id':self.c.chat,'type':'supergroup','is_forum':True},thread=44)
  self.assertNotIn('原报表',self.s.db.one("SELECT text FROM outbox WHERE key LIKE 'update:2:%' ORDER BY key LIMIT 1")['text'])
 async def test_retired_work_buttons_do_not_write_or_touch_daily(self):
  uid=self.approve(456,'演示中文😀');before=self.s.db.all('SELECT * FROM daily_records')
  await self.handle(1,uid,data='wb:mine');await self.handle(2,uid,data='wb:add:2026-10-10:1:1')
  self.assertEqual(Workbench(self.s).amount(uid,1,'2026-10-10'),0)
  self.assertEqual(before,self.s.db.all('SELECT * FROM daily_records'))
  self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM wb_events')['n'],0)
 async def test_retired_help_buttons_leave_old_history_intact(self):
  uid=self.approve(456);w=Workbench(self.s);w.help(uid,'2026-10-10','需要帮助')
  await self.handle(1,uid,data='wb:adminhelp:456:2026-10-10');self.assertEqual(self.s.db.one('SELECT state FROM wb_help')['state'],'需要帮助')
  history=self.s.db.all("SELECT * FROM history WHERE entity='工作台求助'")
  await self.handle(2,self.c.owner,data='wb:adminhelp:456:2026-10-10');self.assertEqual(self.s.db.one('SELECT state FROM wb_help')['state'],'需要帮助')
  self.assertEqual(history,self.s.db.all("SELECT * FROM history WHERE entity='工作台求助'"))
 def test_employee_export_uses_names_and_formal_admin_menu_hides_employee_actions(self):
  uid=self.approve(456,'中文 空格 😀');path=self.s.export_employees(self.c.owner);data=path.read_text(encoding='utf-8-sig')
  self.assertIn('中文 空格 😀',data);self.assertNotIn('员工编号',data)
  u=fixtures.TelegramAdapterTests.make_update(self,99,'/start',uid=self.c.owner,chat={'id':self.c.owner,'type':'private'})
  _,markup,_,_=self.a.dispatch(u,'start',[],'/start');labels=json.dumps(markup,ensure_ascii=False)
  self.assertIn('员工管理',labels);self.assertNotIn('我的工作',labels)
 async def test_private_deep_links_and_no_code_legacy_reassignment(self):
  uid=self.approve(456);code=self.s.employee(uid)['code'];await self.handle(1,uid,'/start work')
  self.assertIsNone(self.s.db.one("SELECT * FROM wb_cards WHERE kind='mine'"));self.assertEqual(self.s.employee(uid)['code'],code)
  await self.handle(2,uid,'/start daily');self.assertIn('新增',self.s.db.one("SELECT text FROM outbox WHERE key='update:2:0'")['text'])
 def test_trial_scheduler_cannot_enable_and_long_names_safe(self):
  self.assertFalse(Scheduler(self.s).schedule_enabled());uid=self.approve(456,'中文😀 <&> 空格 '*30);self.assertLess(len(self.s.display(uid,True)),30);self.assertNotIn('\n',self.s.display(uid))
 def test_formal_schedule_requires_only_two_enabled_topics(self):
  self.c.run_mode='formal';self.c.test_run='';self.c.schedule_allowed=True
  for name in ('training','management'):self.c.threads[name]=0;self.s.db.set('thread:'+name,0)
  scheduler=Scheduler(self.s);scheduler.confirm(self.c.owner,'Asia/Shanghai','23:00,00:00,00:05');self.assertTrue(scheduler.schedule_enabled())

if __name__=='__main__':unittest.main()
