"""Synthetic Telegram updates through the real handler; never employee impersonation."""
import unittest,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import test_daily_send
from team_bot.daily_history import report_pages

class QueryTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.base=test_daily_send.SendTests();self.base.setUp()
  for k in ('s','c','a','ctx','clock','handle','approve','user','raw'):setattr(self,k,getattr(self.base,k))
  self.base.fixture.members[self.c.owner]=('administrator',self.user(self.c.owner,'管理员'))
  self.group={'id':self.c.chat,'type':'supergroup','is_forum':True}
  self.ctx.bot.edit_message_text=AsyncMock(return_value=SimpleNamespace(message_id=600))
 def tearDown(self):self.base.tearDown()
 async def query(self,n,day='2026-10-10'):
  await self.handle(n,self.c.owner,'查看日报 '+day,chat=self.group)
 async def test_first_second_submit_do_not_create_summary_but_excel_dirty(self):
  u=self.approve(456);v=self.approve(789)
  for n,uid in [(1,u),(2,v)]:await self.handle(n,uid,self.raw(n=0),chat=self.group)
  self.assertEqual(self.s.summary('2026-10-10')['submitted'],2)
  self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM daily_publications')['n'],0)
  self.assertTrue(self.s.db.setting('daily_excel_pending'))
  self.ctx.bot.set_message_reaction.assert_awaited();self.ctx.bot.send_message.assert_not_awaited()
  with patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)),patch('team_bot.rich.send_native',new=AsyncMock()) as send:
   await self.a.scheduler.daily_refresh(self.ctx.bot);send.assert_not_awaited()
 async def test_admin_query_then_catchup_edits_latest_and_new_query_replies_bottom(self):
  u=self.approve(456);v=self.approve(789)
  await self.handle(1,u,self.raw(n=0,note='保留\n多行备注'),chat=self.group)
  native=AsyncMock(side_effect=[SimpleNamespace(message_id=600),SimpleNamespace(message_id=601)]);edit=AsyncMock(side_effect=lambda bot,chat,message_id,*args:SimpleNamespace(message_id=message_id))
  with patch('team_bot.rich.send_native',new=native),patch('team_bot.rich.edit_native',new=edit),patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await self.query(2)
   pub=self.s.db.one('SELECT * FROM daily_publications');self.assertEqual(pub['message_id'],600)
   text=self.s.db.one("SELECT text FROM outbox WHERE key='update:2:0'")['text'];self.assertIn('未提交',text);self.assertIn('多行备注',text)
   self.clock('2026-10-11T00:01:00');await self.handle(3,v,self.raw(n=3),chat=self.group)
   await self.a.scheduler.daily_refresh(self.ctx.bot)
   self.assertEqual(self.s.record(v,'2026-10-10')['timing'],'迟交');self.assertIsNone(self.s.record(v,'2026-10-11'))
   await self.query(4);self.assertEqual(native.await_count,2);self.assertGreaterEqual(edit.await_count,1)
   self.assertEqual(self.s.db.one('SELECT message_id FROM daily_publications')['message_id'],601)
   self.assertEqual(native.call_args.kwargs['reply_to'],50)
   await self.query(4);self.assertEqual(native.await_count,2)
   await self.handle(5,u,self.raw(n=4),chat=self.group);await self.a.scheduler.daily_refresh(self.ctx.bot)
   self.assertEqual(edit.call_args.args[2],601)
   await self.handle(6,self.c.owner,data='report_page:2026-10-10:1',chat=self.group)
   self.assertEqual(self.s.db.one('SELECT message_id FROM daily_publications')['message_id'],601)
   self.assertEqual(edit.call_args.args[2],50)
   self.assertEqual(self.s.summary('2026-10-10')['totals']['新增'],7)
 async def test_replacement_duplicate_actual_date_zero_and_identity_envelope(self):
  u=self.approve(456);v=self.approve(789);self.clock('2026-10-12T09:00:00')
  await self.handle(1,u,self.raw('2026-10-11',n=0,note='预约10k\n数字：999'),chat=self.group)
  before=self.s.record(u,'2026-10-11');self.assertIsNone(self.s.record(u,'2026-10-12'))
  await self.handle(2,u,self.raw('2026-10-11',n=0,note='预约10k\n数字：999'),chat=self.group)
  self.assertEqual(self.s.record(u,'2026-10-11'),before)
  await self.handle(3,u,self.raw('2026-10-11',n=4),chat=self.group)
  self.assertEqual(self.s.record(u,'2026-10-11')['version'],2);self.assertEqual(self.s.record(u,'2026-10-11')['timing'],'截止后修订')
  self.assertEqual(self.s.summary('2026-10-11')['totals']['新增'],4)
  self.assertTrue(any(json.loads(x['value'])['uid']==u for x in self.s.db.all("SELECT value FROM settings WHERE key LIKE 'daily_origin:%'")))
  self.assertIn('未提交','\n'.join(p['text'] for p in report_pages(self.s,self.s.summary('2026-10-11'))))
 async def test_rename_same_name_refreshes_old_day_without_merging_or_changing_records(self):
  u=self.approve(456,'同名','name_a');v=self.approve(789,'同名','name_b')
  await self.handle(1,u,self.raw(),chat=self.group,user=self.user(u,'同名','name_a'))
  old=self.s.record(u,'2026-10-10').copy()
  with patch('team_bot.rich.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=600))):await self.query(2)
  self.clock('2026-10-11T10:00:00');self.s.profile(self.user(u,'新名','name_a'))
  self.assertEqual(self.s.record(u,'2026-10-10'),old)
  self.assertTrue(self.s.db.setting('daily_excel_pending'))
  stored=json.loads(self.s.db.one("SELECT payload FROM daily_summaries ORDER BY version DESC LIMIT 1")['payload'])
  self.assertIn('新名 @name_a',[r['code'] for r in stored['rows']]);self.assertEqual(len(stored['rows']),2)
  self.assertEqual(self.s.db.one('SELECT state FROM daily_publications')['state'],'pending')
 async def test_edit_failure_retries_same_message_without_new_send(self):
  u=self.approve(456);await self.handle(1,u,self.raw(),chat=self.group)
  with patch('team_bot.rich.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=600))):await self.query(2)
  await self.handle(3,u,self.raw(n=5),chat=self.group)
  self.ctx.bot.edit_message_text.side_effect=RuntimeError('network fixture')
  with patch('team_bot.rich.edit_native',new=AsyncMock(side_effect=RuntimeError('network fixture'))),patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await self.a.scheduler.daily_refresh(self.ctx.bot)
  self.assertEqual(self.s.db.one('SELECT state FROM daily_publications')['state'],'pending')
  self.clock('2026-10-10T14:02:00');self.ctx.bot.edit_message_text.side_effect=None
  with patch('team_bot.rich.edit_native',new=AsyncMock(return_value=SimpleNamespace(message_id=600))),patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await self.a.scheduler.daily_refresh(self.ctx.bot)
  self.assertEqual(self.s.db.one('SELECT state FROM daily_publications')['state'],'sent')
  self.ctx.bot.send_message.assert_not_awaited()
 async def test_unrequested_legacy_publication_and_employee_query_are_silent_or_denied(self):
  u=self.approve(456)
  self.s.db.run("INSERT INTO daily_publications(day,version,chat,next_try,state) VALUES (?,?,?,?,'pending')",('2026-10-10',0,self.c.chat,self.c.now().isoformat()))
  await self.handle(1,u,self.raw(),chat=self.group)
  with patch('team_bot.rich.send_native',new=AsyncMock()) as native,patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await self.a.scheduler.daily_refresh(self.ctx.bot);native.assert_not_awaited()
  await self.handle(2,u,'查看日报 2026-10-10',chat=self.group)
  self.assertIn('管理员',self.s.db.one("SELECT text FROM outbox WHERE key='update:2:error:0'")['text'])

 async def test_restart_pending_fresh_query_recovers_latest_data(self):
  u=self.approve(456);await self.handle(1,u,self.raw(),chat=self.group)
  self.ctx.bot.send_message.side_effect=RuntimeError('network fixture')
  with patch('team_bot.rich.send_native',new=AsyncMock(side_effect=RuntimeError('network fixture'))):await self.query(2)
  await self.handle(3,u,self.raw(n=8),chat=self.group)
  self.assertEqual(self.s.db.one('SELECT state FROM daily_publications')['state'],'query_pending')
  self.assertEqual(self.s.db.one("SELECT state FROM outbox WHERE key='update:2:0'")['state'],'pending')
  from team_bot.scheduler import Scheduler
  from team_bot.daily_history import query_delivered
  restarted=Scheduler(self.s);self.clock('2026-10-10T14:02:00')
  self.ctx.bot.send_message.side_effect=None;self.ctx.bot.send_message.return_value=SimpleNamespace(message_id=650)
  with patch('team_bot.rich.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=650))),patch('team_bot.rich.edit_native',new=AsyncMock(return_value=SimpleNamespace(message_id=650))),patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await restarted.flush(self.ctx.bot)
  query_delivered(self.s)
  self.assertEqual(self.s.db.one('SELECT message_id FROM daily_publications')['message_id'],650)
  with patch('team_bot.rich.edit_native',new=AsyncMock(return_value=SimpleNamespace(message_id=650))) as edit,patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await restarted.daily_refresh(self.ctx.bot)
   self.assertEqual(self.s.db.one('SELECT state FROM daily_publications')['state'],'sent')
  self.assertEqual(self.s.record(u,'2026-10-10')['version'],2)

 def test_two_generations_numeric_business_dates_and_title_only_freeze(self):
  import zipfile,xml.etree.ElementTree as E
  from datetime import date,timedelta
  from team_bot.daily_history import write_excel,export_data
  u=self.approve(456);self.clock('2026-10-12T14:00:00')
  for day in ('2026-10-10','2026-10-11'):self.s.submit_daily(u,self.raw(day,n=0,note='完整备注\n不改变'),'synthetic')
  path=self.c.data/'exports/date-format.xlsx';data=export_data(self.s)
  ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
  for _ in range(2):
   write_excel(data,path)
   with zipfile.ZipFile(path) as z:
    styles=E.fromstring(z.read('xl/styles.xml'));formats={x.get('numFmtId'):x.get('formatCode') for x in styles.findall('s:numFmts/s:numFmt',ns)};xf=styles.findall('s:cellXfs/s:xf',ns)
    for num,freeze in [(1,'4'),(2,'2')]:
     sh=E.fromstring(z.read(f'xl/worksheets/sheet{num}.xml'))
     self.assertEqual(sh.find('.//s:pane',ns).get('ySplit'),freeze)
     dates=[]
     for c in sh.findall('.//s:c',ns):
      fmt=formats.get(xf[int(c.get('s','0'))].get('numFmtId'))
      if fmt=='yyyy-mm-dd':
       self.assertIn(c.get('t'),(None,'n'));v=c.find('s:v',ns)
       if v is not None:dates.append((date(1899,12,30)+timedelta(days=float(v.text))).isoformat())
     self.assertEqual(dates,['2026-10-10','2026-10-11'])
