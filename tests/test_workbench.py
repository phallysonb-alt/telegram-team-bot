import unittest,json,asyncio
from datetime import timedelta,datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from telegram import CallbackQuery
from test_daily_v2 import DailyBase
import test_acceptance as fixtures
from team_bot.workbench import Workbench
from team_bot.bot import Adapter
from team_bot.scheduler import Scheduler

class WorkbenchTests(DailyBase):
 def setUp(self):super().setUp();self.w=Workbench(self.s);self.u=self.emp();self.clock('2026-10-10T12:00:00');self.key=0
 def click(self,task=1,action='add',uid=None):
  self.key+=1;result=self.w.change(uid or self.u,uid or self.u,task,'2026-10-10',action,str(self.key));self.now+=timedelta(seconds=3);return result
 def test_three_tasks_units_zero_and_overshoot(self):
  ts=self.w.tasks(self.u,'2026-10-10');self.assertEqual([t['name'] for t in ts],['资料整理','记录更新','待办检查']);self.assertEqual(self.w.state(self.u,ts[0],'2026-10-10')['status'],'未记录')
  self.click(action='zero');self.assertEqual(self.w.state(self.u,ts[0],'2026-10-10')['status'],'明确为0')
  for _ in range(4):self.click()
  self.assertEqual(self.w.amount(self.u,1,'2026-10-10'),4);self.assertIn('4/3份',self.w.personal(self.u,'2026-10-10')[0]);self.click(action='undo');self.assertEqual(self.w.amount(self.u,1,'2026-10-10'),3)
 def test_complete_undo_no_duplicate_completion(self):
  self.click(3,'complete')
  with self.assertRaises(ValueError):self.click(3,'complete')
  self.click(3,'undo');self.assertEqual(self.w.amount(self.u,3,'2026-10-10'),0)
  with self.assertRaises(ValueError):self.click(3,'undo')
 def test_callback_repeat_and_cooldown(self):
  self.w.change(self.u,self.u,1,'2026-10-10','add','same');self.assertIn('忽略',self.w.change(self.u,self.u,1,'2026-10-10','add','same'))
  with self.assertRaises(ValueError):self.w.change(self.u,self.u,1,'2026-10-10','add','another')
  self.assertEqual(self.w.amount(self.u,1,'2026-10-10'),1)
 def test_thirty_employees_permissions_and_pagination(self):
  users=[self.u]+[self.emp(i) for i in range(2,31)]
  for i,u in enumerate(users):
   for _ in range(i%4):self.click(uid=u)
   if u!=self.u:
    with self.assertRaises(PermissionError):self.w.change(self.u,u,1,'2026-10-10','add','bad'+str(u))
  r=self.w.summary('2026-10-10');self.assertEqual(len(r['rows']),30);self.assertEqual(r['totals'][0],sum(i%4 for i in range(30)));self.assertEqual(self.w.team_view('2026-10-10',3)[2],3)
 def test_current_vs_period_boundary_and_end_exclusive(self):
  for stamp in ['12:00:00','13:00:00','14:00:00']:
   self.clock('2026-10-10T'+stamp);self.click()
  self.assertEqual(self.w.amount(self.u,1,'2026-10-10'),3);self.assertEqual(self.w.amount(self.u,1,'2026-10-10',0),2);self.assertEqual(self.w.amount(self.u,1,'2026-10-10',1),1)
  self.clock('2026-10-10T17:00:00');self.click();self.assertEqual(self.s.db.one('select outside from wb_events order by id desc limit 1')['outside'],1)
  self.clock('2026-10-10T19:00:00');self.click();self.assertEqual(self.w.amount(self.u,1,'2026-10-10',2),1)
  self.clock('2026-10-10T21:00:00');self.click();self.assertEqual(self.w.amount(self.u,1,'2026-10-10',3),1)
 def test_undo_revises_original_period_and_keeps_real_time(self):
  self.click();self.clock('2026-10-10T14:00:00');self.w.capture();self.assertEqual(self.s.db.one('select version from wb_snapshots')['version'],1)
  self.clock('2026-10-10T15:00:00');self.click(action='undo');self.assertEqual(self.w.amount(self.u,1,'2026-10-10',0),0);self.assertEqual(self.w.amount(self.u,1,'2026-10-10',1),0)
  event=self.s.db.one('select * from wb_events order by id desc limit 1');self.assertEqual(event['at'][11:16],'15:00');self.assertEqual(event['period'],0);self.assertIsNotNone(event['undo_of']);self.assertEqual(self.s.db.one('select version,revised from wb_snapshots order by version desc limit 1'),dict(version=2,revised=1))
 def test_snapshot_unchanged_by_other_period_and_restart_catchup(self):
  self.click();self.clock('2026-10-10T14:00:00');self.w.capture();self.click();self.assertEqual(self.s.db.one('select count(*) n from wb_snapshots')['n'],1)
  self.clock('2026-10-11T01:00:00');Workbench(self.s).capture();self.assertEqual(self.s.db.one('select count(*) n from wb_snapshots')['n'],4);self.w.capture();self.assertEqual(self.s.db.one('select count(*) n from wb_snapshots')['n'],4)
 def test_midnight_new_day_outside_and_old_card_denied(self):
  self.clock('2026-10-10T23:59:59');self.click();self.clock('2026-10-11T00:00:00')
  with self.assertRaises(PermissionError):self.w.change(self.u,self.u,1,'2026-10-10','add','old')
  self.w.change(self.u,self.u,1,'2026-10-11','add','new');self.assertEqual(self.w.amount(self.u,1,'2026-10-10',3),1);self.assertEqual(self.w.amount(self.u,1,'2026-10-11'),1);self.assertEqual(self.s.db.one('select outside from wb_events order by id desc limit 1')['outside'],1)
 def test_configuration_frozen_history_and_applicability(self):
  other=self.emp(2);self.click();tasks,periods=self.w.config('2026-10-10');tasks[0].update(name='模拟新任务',target=9,unit='件',uids=[self.u],order=9);self.w.configure(99999,tasks,periods,'模拟修改')
  self.assertEqual(self.w.tasks(self.u,'2026-10-10')[0]['target'],3);self.clock('2026-10-11T12:00:00');self.assertEqual(self.w.tasks(self.u,'2026-10-11')[-1]['name'],'模拟新任务');self.assertEqual(len(self.w.tasks(other,'2026-10-11')),2)
  with self.assertRaises(PermissionError):self.w.change(other,other,1,'2026-10-11','add','excluded')
 def test_admin_correction_requires_reason_and_audit(self):
  self.click()
  with self.assertRaises(ValueError):self.w.change(99999,self.u,1,'2026-10-10','correct','bad',4,'',0)
  self.w.change(99999,self.u,1,'2026-10-10','correct','fixed',4,'模拟核对',0);self.assertEqual(self.w.amount(self.u,1,'2026-10-10',0),4);self.assertEqual(self.s.db.one('select reason from wb_events order by id desc limit 1')['reason'],'模拟核对')
 def test_daily_and_legacy_untouched(self):
  self.submit(self.u,(5,2,3,4));old=dict(self.s.record(self.u,'2026-10-10'));self.s.db.run('insert into check_events(uid,task,day,server_time,delta,callback) values (?,?,?,?,?,?)',(self.u,1,'2026-10-09',self.now.isoformat(),7,'legacy'));self.click();self.click(3,'complete');self.assertEqual(self.s.record(self.u,'2026-10-10'),old);self.assertEqual(self.s.db.one('select delta from check_events')['delta'],7)
 def test_correction_cannot_make_period_negative(self):
  self.click();self.clock('2026-10-10T15:00:00')
  with self.assertRaises(ValueError):self.w.change(99999,self.u,1,'2026-10-10','correct','wrong-period',0,'模拟更正',1)
  self.w.change(99999,self.u,1,'2026-10-10','correct','right-period',0,'模拟更正',0);self.assertEqual(self.w.amount(self.u,1,'2026-10-10',0),0)
 def test_help_and_active_roster(self):
  self.assertTrue(self.w.help(self.u,'2026-10-10','需要帮助'));self.assertFalse(self.w.help(self.u,'2026-10-10','需要帮助'));self.assertEqual(self.w.summary('2026-10-10')['rows'][0]['help'],'需要帮助');self.s.db.run('update employees set active=0 where uid=?',(self.u,));self.assertEqual(len(self.w.summary('2026-10-10')['rows']),0)

 def test_first_late_undo_freezes_original_snapshot_before_revision(self):
  self.click();self.clock('2026-10-10T15:00:00');self.click(action='undo');versions=self.s.db.all('select version,payload,revised from wb_snapshots where period=0 order by version');self.assertEqual(len(versions),2);self.assertEqual(json.loads(versions[0]['payload'])['totals'][0],1);self.assertEqual(json.loads(versions[1]['payload'])['totals'][0],0);self.assertEqual(versions[1]['revised'],1)
  rich=self.w.team_view('2026-10-10',period=0)[1];self.assertTrue(any('修订' in b.get('text','') for b in rich['blocks']))
 def test_rest_time_undo_actual_time_separate_from_original_period(self):
  self.click();self.clock('2026-10-10T18:00:00');self.click(action='undo');event=self.s.db.one('select period,operation_period,outside from wb_events order by id desc limit 1');self.assertEqual(event,dict(period=0,operation_period=None,outside=1));self.assertEqual(self.w.amount(self.u,1,'2026-10-10',-1),0)
 def test_period_configuration_and_invalid_overlap(self):
  tasks,periods=self.w.config('2026-10-10')
  with self.assertRaises(ValueError):self.w.configure(99999,tasks,[['12:00','15:00'],['14:00','17:00']],'模拟')
  self.w.configure(99999,tasks,[['12:00','15:00'],['15:00','17:00']],'模拟时段');self.clock('2026-10-11T14:00:00');self.w.change(self.u,self.u,1,'2026-10-11','add','modified');self.assertEqual(self.w.amount(self.u,1,'2026-10-11',0),1);self.assertEqual(self.w.ensure('2026-10-10')[1][0],['12:00','14:00'])
 def test_quantity_outside_visible_not_dropped(self):
  self.clock('2026-10-10T18:00:00');self.click();self.assertEqual(self.w.amount(self.u,1,'2026-10-10'),1);self.assertEqual(self.w.amount(self.u,1,'2026-10-10',-1),1);self.assertIn('1份',self.w.team_view('2026-10-10',period=-1)[0])

class WorkbenchUITests(DailyBase,unittest.IsolatedAsyncioTestCase):
 make_update=fixtures.TelegramAdapterTests.make_update
 def setUp(self):super().setUp();self.u=self.emp();self.clock('2026-10-10T12:00:00');self.a=Adapter(self.s);self.ctx=SimpleNamespace(bot=SimpleNamespace(id=123));self.private={'id':self.u,'type':'private'}
 async def handle(self,n,callback='',text='',uid=None,chat=None,thread=11):
  update=self.make_update(n,text,uid=uid or self.u,chat=chat or self.private,thread=thread,callback=callback or None)
  if update.callback_query:object.__setattr__(update.callback_query,'id','wb-query-'+str(n))
  with patch.object(CallbackQuery,'answer',new=AsyncMock()):await self.a.handle(update,self.ctx)
 async def test_menu_workbench_and_personal_edit(self):
  await self.handle(1,text='/menu');self.assertIn('我的工作',self.s.db.one('select markup from outbox')['markup']);await self.handle(2,callback='wb:mine');self.s.db.run("update wb_cards set message_id=50 where kind='mine'")
  await self.handle(3,callback='wb:add:2026-10-10:1:1');self.assertEqual(self.s.db.setting('edit:update:3:0'),50);self.assertEqual(Workbench(self.s).amount(self.u,1,'2026-10-10'),1)
 async def test_stale_card_and_visitor_cannot_operate(self):
  other=self.emp(2);await self.handle(1,callback='wb:mine');self.s.db.run("update wb_cards set message_id=50 where kind='mine'");await self.handle(2,callback='wb:add:2026-10-10:1:1',uid=other,chat=self.private);self.assertEqual(self.s.db.all('select * from wb_events'),[])
 async def test_admin_readonly_detail_and_private_config(self):
  self.s.db.set('wb_input:99999',None);adminchat={'id':99999,'type':'private'};await self.handle(1,callback=f'wb:view:{self.u}:2026-10-10:1',uid=99999,chat=adminchat);markup=self.s.db.one('select markup from outbox')['markup'];self.assertNotIn('wb:add:',markup);self.assertIn('correct:',markup)
  await self.handle(2,callback='wb:config',uid=99999,chat=adminchat);self.assertIn('修改',self.s.db.all('select markup from outbox')[-1]['markup'])
 async def test_management_is_silent_and_retired_buttons_disabled(self):
  group={'id':self.c.chat,'type':'supergroup','is_forum':True};await self.handle(1,callback='wb:mine',chat=group,thread=33);await self.handle(2,text='/task_new',chat=group,thread=33);self.assertEqual(self.s.db.all('select * from outbox'),[])
  await self.handle(3,callback=f'ck:add:{self.u}:1:2026-10-10');self.assertEqual(self.s.db.all('select * from check_events'),[]);self.assertIn('旧打卡',self.s.db.one('select text from outbox')['text'])
 async def test_merged_board_flush_edits_one_message_and_retry(self):
  w=Workbench(self.s);w.ensure('2026-10-10');self.s.db.run('insert into wb_boards(day,message_id) values (?,?)',('2026-10-10',90));self.s.db.run('insert into wb_cards values (?,?,?,?,?)',('team',0,'2026-10-10',self.c.chat,90));self.s.db.set('wb_board_markup:2026-10-10',{'inline_keyboard':[]})
  for i in range(3):w.change(self.u,self.u,1,'2026-10-10','add',str(i));self.now+=timedelta(seconds=3)
  bot=SimpleNamespace(edit_message_text=AsyncMock(return_value=SimpleNamespace(message_id=90)))
  with patch('team_bot.rich.edit_native',new=AsyncMock(side_effect=RuntimeError('mock fallback'))):await Scheduler(self.s).flush(bot)
  self.assertEqual(bot.edit_message_text.await_count,1);self.assertEqual(self.s.db.one("select count(*) n from outbox where key like 'wb-refresh:%'")['n'],1);self.assertEqual(self.s.db.one('select message_id from wb_boards')['message_id'],90)
 async def test_config_button_form_next_day(self):
  private={'id':99999,'type':'private'};await self.handle(1,callback='wb:edit:1',uid=99999,chat=private)
  await self.handle(2,text='名称：模拟归档\n单位：件\n目标：9\n排序：2\n类型：数量型\n员工：全部\n原因：演示配置',uid=99999,chat=private)
  self.assertEqual(Workbench(self.s).tasks(self.u,'2026-10-10')[0]['name'],'资料整理');self.clock('2026-10-11T12:00');self.assertIn('模拟归档',[t['name'] for t in Workbench(self.s).tasks(self.u,'2026-10-11')])

 async def test_board_edit_failure_persisted_then_retry_same_message(self):
  w=Workbench(self.s);w.ensure('2026-10-10');self.s.db.run('insert into wb_boards(day,message_id) values (?,?)',('2026-10-10',90));w.change(self.u,self.u,1,'2026-10-10','add','save-first');self.now+=timedelta(seconds=4)
  bot=SimpleNamespace(edit_message_text=AsyncMock(side_effect=RuntimeError('mock transport failure')))
  with patch('team_bot.rich.edit_native',new=AsyncMock(side_effect=RuntimeError('mock transport failure'))):await Scheduler(self.s).flush(bot)
  row=self.s.db.one('select * from outbox');self.assertEqual(row['state'],'pending');self.assertEqual(w.amount(self.u,1,'2026-10-10'),1);self.now+=timedelta(seconds=61);bot.edit_message_text=AsyncMock(return_value=SimpleNamespace(message_id=90))
  with patch('team_bot.rich.edit_native',new=AsyncMock(side_effect=RuntimeError('mock fallback'))):await Scheduler(self.s).flush(bot)
  self.assertEqual(self.s.db.one('select sent_id from outbox')['sent_id'],90);self.assertEqual(bot.edit_message_text.call_args.kwargs['message_id'],90)
 async def test_thirty_people_team_pages_and_employee_navigation(self):
  for i in range(2,31):self.emp(i)
  group={'id':self.c.chat,'type':'supergroup','is_forum':True};await self.handle(1,callback='wb:team:2026-10-10:3:all',uid=99999,chat=group)
  row=self.s.db.one('select * from outbox');self.assertIn('第3/3页',row['text']);self.assertIn('E030',row['markup']);self.assertNotIn('E001',row['markup'])
  for line in json.loads(row['markup'])['inline_keyboard']:
   for b in line:
    if 'callback_data' in b:self.assertLessEqual(len(('test:eab267fe5a4b:'+b['callback_data']).encode()),64)
 async def test_daily_form_priority_without_workbench_state(self):
  self.c.owner=self.u;self.s.db.set('wb_input:'+str(self.u),dict(kind='task',task=1));group={'id':self.c.chat,'type':'supergroup','is_forum':True};await self.handle(1,text='日期：2026-10-10\n新增：1\n维聊：2\n有效：3\n精聊：4',chat=self.private,thread=44);self.assertEqual(self.s.db.one('select count(*) n from daily_drafts')['n'],1);self.assertEqual(self.s.db.one('select count(*) n from wb_events')['n'],0)

 async def test_completion_and_undo_buttons_edit_same_personal_message(self):
  await self.handle(1,callback='wb:mine');self.s.db.run("update wb_cards set message_id=50 where kind='mine'");await self.handle(2,callback='wb:complete:2026-10-10:3:1');self.now+=timedelta(seconds=3);await self.handle(3,callback='wb:undo:2026-10-10:3:1')
  self.assertEqual(Workbench(self.s).amount(self.u,3,'2026-10-10'),0);self.assertEqual(self.s.db.setting('edit:update:2:0'),50);self.assertEqual(self.s.db.setting('edit:update:3:0'),50);self.assertEqual(self.s.db.one('select count(*) n from wb_events')['n'],2)

 async def test_timeline_page_independent_from_task_page(self):
  w=Workbench(self.s)
  for i in range(12):w.change(self.u,self.u,1,'2026-10-10','add','timeline-'+str(i));self.now+=timedelta(seconds=3)
  await self.handle(1,callback=f'wb:timeline:{self.u}:2026-10-10:2',uid=99999,chat={'id':99999,'type':'private'})
  row=self.s.db.one('select text,markup from outbox');self.assertIn('时间线第2/2页',row['text']);self.assertIn('上一页',row['markup']);self.assertNotIn('页码超出',row['text'])
