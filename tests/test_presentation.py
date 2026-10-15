"""Layout tests: display changes must not rewrite persisted metrics or events."""
import json,unittest
from datetime import timedelta
from types import SimpleNamespace
import test_workbench as fixtures
from team_bot.workbench import Workbench
from team_bot.bot import Adapter
from team_bot.presentation import human_time,clean_blocks
from team_bot.scheduler import Scheduler

class LayoutModelTests(fixtures.DailyBase):
 def setUp(self):
  super().setUp();self.w=Workbench(self.s);self.u=self.emp();self.clock('2026-10-10T12:00:00');self.key=0
 click=fixtures.WorkbenchTests.click
 def test_human_times_china_today_and_history(self):
  self.clock('2026-10-11T13:05:00')
  self.assertEqual(human_time('2026-10-11T05:25:12.456+00:00',self.c),'13:25')
  self.assertEqual(human_time('2026-10-10T05:25:12.456+00:00',self.c),'10-10 13:25')
  self.assertEqual(human_time('2026-10-10T16:01:00Z',self.c),'00:01')
 def test_future_ended_unrecorded_and_zero_distinct(self):
  self.clock('2026-10-10T13:00:00');t=self.w.tasks(self.u,'2026-10-10')[0]
  self.assertEqual(self.w.display_amount(self.u,t,'2026-10-10',1),'未开始')
  self.assertEqual(self.w.display_amount(self.u,t,'2026-10-10',0),'未记录')
  self.click(action='zero');self.assertEqual(self.w.display_amount(self.u,t,'2026-10-10',0),'0份（明确0）')
  self.clock('2026-10-10T18:00:00');self.assertEqual(self.w.display_amount(self.u,t,'2026-10-10',1),'无记录')
 def test_task_pages_and_team_column_pages_preserve_values(self):
  self.click();before=self.s.db.all('select * from wb_events')
  for page in (1,2,3):
   text,ts,pages=self.w.personal(self.u,'2026-10-10',page)
   self.assertEqual(len(ts),1);self.assertEqual(pages,3);self.assertIn('我的工作',text)
  tasks,periods=self.w.config('2026-10-10');tasks.append(dict(tasks[0],id=4,name='模拟第四项'))
  self.w.configure(99999,tasks,periods,'仅模拟');self.clock('2026-10-11T13:00:00')
  text,rich,pages,r=self.w.team_view('2026-10-11',2)
  self.assertEqual(pages,2);self.assertEqual(len(rich['blocks'][2]['cells'][0]),4)
  self.assertEqual(self.s.db.all('select * from wb_events'),before)
 def test_team_stats_lines_all_roster_not_page_and_daily_status(self):
  for i in range(2,31):self.emp(i)
  text,rich,pages,r=self.w.team_view('2026-10-10',3,1)
  self.assertIn('当日任务状态 · 全员',text);self.assertIn('未记录 30人',text)
  stats=[b['text'] for b in rich['blocks'] if b['type']=='paragraph' and '未记录 30人' in b['text']]
  self.assertEqual(len(stats),3);self.assertEqual(pages,3)
 def test_rendering_does_not_revise_snapshot_or_record(self):
  self.submit(self.u,(5,2,3,4));self.click();self.clock('2026-10-10T15:00:00');self.w.capture()
  before={t:self.s.db.all('select * from '+t) for t in ['daily_records','wb_events','history','wb_snapshots']}
  self.w.team_view('2026-10-10',period=0);self.w.detail_view(self.u,'2026-10-10');self.w.personal(self.u,'2026-10-10')
  self.assertEqual(before,{t:self.s.db.all('select * from '+t) for t in before})
 def test_isolation_marker_once_and_nested_times(self):
  self.c.test_run='mock';text='【隔离测试，不计正式数据】\n时间：2026-10-10T12:00:00.000+08:00'
  Scheduler(self.s).reply('layout',self.u,None,text)
  row=self.s.db.one('select text from outbox');self.assertEqual(row['text'].count('隔离测试，不计正式数据'),1)
  self.assertNotIn('T12',row['text'])
  rich=clean_blocks({'blocks':[{'type':'paragraph','text':['2026-10-10T12:00:00.000+08:00']}]},self.c)
  self.assertEqual(rich['blocks'][0]['text'],['12:00'])
 def test_mock_display_once_and_formal_not_affected(self):
  self.c.test_run='mock';self.s.db.set('display_mock',True)
  Scheduler(self.s).reply('mock-layout',self.u,None,'【隔离测试，不计正式数据】\n模拟展示\n团队进度')
  text=self.s.db.one('select text from outbox')['text']
  self.assertEqual(text.count('模拟展示'),1);self.assertEqual(text.count('隔离测试，不计正式数据'),1)

class LayoutHandlerTests(fixtures.DailyBase,unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  super().setUp();self.u=self.emp();self.clock('2026-10-10T12:00:00');self.a=Adapter(self.s);self.ctx=SimpleNamespace(bot=SimpleNamespace(id=123));self.private={'id':self.u,'type':'private'}
 make_update=fixtures.WorkbenchUITests.make_update
 handle=fixtures.WorkbenchUITests.handle
 async def test_buttons_adjacent_to_single_task_and_help_group(self):
  await self.handle(1200,callback='wb:mine');row=self.s.db.one('select text,markup from outbox')
  self.assertIn('资料整理',row['text']);self.assertNotIn('记录更新',row['text'])
  markup=json.loads(row['markup'])['inline_keyboard'];self.assertEqual([b['text'] for b in markup[0]],['＋1','撤销','记为0'])
  self.assertEqual([b['text'] for b in markup[1]],['需要帮助','求助已解决'])
  await self.handle(1201,callback='wb:mine:2026-10-10:3')
  row=self.s.db.all('select markup from outbox')[-1];self.assertEqual(json.loads(row['markup'])['inline_keyboard'][0][0]['text'],'完成')
 async def test_details_native_tables_no_mutation_and_grouped_periods(self):
  await self.handle(1200,callback=f'wb:view:{self.u}:2026-10-10:1',uid=99999,chat={'id':99999,'type':'private'})
  rich=self.s.db.setting('rich:update:1200:0')['message'];self.assertEqual(sum(b['type']=='table' for b in rich['blocks']),2)
  self.assertNotIn('wb:add:',self.s.db.one('select markup from outbox')['markup'])
  group={'id':self.c.chat,'type':'supergroup','is_forum':True}
  await self.handle(1201,callback='wb:team:2026-10-10:1:all',uid=99999,chat=group)
  rows=json.loads(self.s.db.all('select markup from outbox')[-1]['markup'])['inline_keyboard']
  self.assertEqual([len(row) for row in rows if any('–' in b['text'] for b in row)],[2,2])
 async def test_daily_preview_short_labels_confirm_and_cancel(self):
  await self.handle(1200,text='日期：2026-10-10\n新增：1\n维聊：2\n有效：3\n精聊：4',chat=self.private,thread=44)
  rich=self.s.db.setting('rich:update:1200:0')['message'];table=next(b for b in rich['blocks'] if b['type']=='table')
  self.assertEqual([row[0]['text'] for row in table['cells'][1:]],['新增','维聊','有效','精聊'])
  self.assertEqual([b['text'] for b in json.loads(self.s.db.one('select markup from outbox')['markup'])['inline_keyboard'][0]],['确认提交','取消'])
 async def test_help_complete_notice_and_resolved_edits_same_notice(self):
  await self.handle(1200,callback='wb:mine');self.s.db.run("update wb_cards set message_id=50 where kind='mine'")
  await self.handle(1201,callback='wb:help:2026-10-10:1');notice=self.s.db.one("select * from outbox where key like 'wb-help:%'")
  for field in ['求助通知','时间：12:00','说明：','当前状态：需要帮助']:self.assertIn(field,notice['text'])
  self.assertIn('查看员工',notice['markup']);self.s.db.run("update outbox set state='sent',sent_id=77 where key=?",(notice['key'],))
  await self.handle(1202,callback='wb:resolved:2026-10-10:1');self.assertEqual(self.s.db.setting('edit:wb-help:1202:0'),77)

