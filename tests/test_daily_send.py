import unittest,json,asyncio,tempfile,zipfile
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from telegram.error import BadRequest
import test_new_group as fixture
from team_bot.daily import parse_daily,daily_view,METRICS
from team_bot.daily_history import historical,recent,report_pages,ensure_export,export_data,export_path,write_excel

class SendTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.fixture=fixture.NewGroupTests();self.fixture.setUp()
  for name in ('s','c','a','ctx','clock','handle','approve','user'):setattr(self,name,getattr(self.fixture,name))
  self.ctx.bot.get_chat=AsyncMock(return_value=SimpleNamespace(available_reactions=None))
  self.ctx.bot.set_message_reaction=AsyncMock(return_value=True)
  self.ctx.bot.send_message=AsyncMock(return_value=SimpleNamespace(message_id=500))
  self.a.scheduler.request_daily_refresh=lambda bot:None
 def tearDown(self):self.fixture.tearDown()
 def raw(self,day='2026-10-10',n=2,note=None):
  return day+f'\n新增：{n}\n维聊：2\n有效：1\n精聊：0'+('\n备注：'+note if note is not None else '')
 async def test_send_saves_reacts_no_preview_and_idempotent(self):
  u=self.approve(456)
  await self.handle(100,u,self.raw(note='封控3个，使用量5个。\n下一行：数字99'))
  p=json.loads(self.s.record(u,'2026-10-10')['payload']);self.assertEqual(p['新增'],2);self.assertIn('数字99',p['备注'])
  self.ctx.bot.set_message_reaction.assert_awaited_once();self.ctx.bot.send_message.assert_not_awaited()
  self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM daily_drafts')['n'],0)
  await self.handle(101,u,self.raw(note='封控3个，使用量5个。\n下一行：数字99'));self.assertEqual(self.s.record(u,'2026-10-10')['version'],1)
  await self.handle(102,u,self.raw(n=0));p=json.loads(self.s.record(u,'2026-10-10')['payload']);self.assertEqual((p['新增'],p['备注']),(0,''));self.assertEqual(self.s.record(u,'2026-10-10')['version'],2)
 async def test_invalid_identity_position_and_old_button_no_save(self):
  u=self.approve(456)
  for i,raw in enumerate([self.raw().replace('新增：2','新增：'),self.raw().replace('维聊：2','维聊：-1'),self.raw().replace('有效：1','有效：1.5')]):await self.handle(200+i,u,raw)
  self.assertIsNone(self.s.record(u,'2026-10-10'));self.ctx.bot.set_message_reaction.assert_not_awaited()
  d,_,_=self.s.draft(u,self.raw(),'old');await self.handle(204,u,data='daily_confirm:'+str(d));self.assertIsNone(self.s.record(u,'2026-10-10'))
  await self.handle(205,u,self.raw(),chat={'id':self.c.chat,'type':'supergroup','is_forum':True},thread=11);self.assertIsNone(self.s.record(u,'2026-10-10'))
  del self.fixture.members[u]
  await self.handle(206,u,self.raw(),chat={'id':self.c.chat,'type':'supergroup','is_forum':True},thread=44)
  self.assertEqual(self.s.db.one("SELECT chat FROM outbox WHERE key='update:206:identity-error:0'")['chat'],self.c.chat)
  self.assertIsNone(self.s.record(u,'2026-10-10'))
 async def test_reaction_unavailable_fallback_and_network_retry_not_resave(self):
  u=self.approve(456);self.ctx.bot.set_message_reaction.side_effect=BadRequest('REACTION_INVALID')
  await self.handle(300,u,self.raw());self.assertEqual(self.ctx.bot.send_message.call_args.kwargs['text'],'已记录')
  self.ctx.bot.set_message_reaction.side_effect=RuntimeError('network')
  await self.handle(301,u,self.raw());self.assertEqual(self.s.record(u,'2026-10-10')['version'],1)
  self.assertEqual(self.s.db.setting('daily_feedback:301')['state'],'pending')
  self.ctx.bot.set_message_reaction.side_effect=None
  from team_bot.daily_history import flush_feedback
  await flush_feedback(self.s,self.ctx.bot,['301']);self.assertEqual(self.s.db.setting('daily_feedback:301')['state'],'sent');self.assertEqual(self.s.record(u,'2026-10-10')['version'],1)
 def test_midnight_window_date_and_blank_vs_zero(self):
  u=self.approve(456);v=self.approve(789)
  self.clock('2026-10-10T23:00:00');self.s.submit_daily(u,self.raw(n=0),'a');self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'按时提交')
  self.clock('2026-10-11T00:00:00');self.s.submit_daily(u,self.raw(n=1),'b');self.assertEqual(self.s.record(u,'2026-10-10')['timing'],'截止后修订');self.s.submit_daily(v,self.raw(),'c');self.assertEqual(self.s.record(v,'2026-10-10')['timing'],'迟交')
  self.assertIsNone(self.s.record(u,'2026-10-11'))
 def test_recent_seven_unknown_roster_no_fake_zero(self):
  self.clock('2026-10-11T14:00:00');u=self.approve(456)
  self.s.submit_daily(u,self.raw(),'x');r=recent(self.s)
  self.assertEqual([x['day'] for x in r],['2026-10-10','2026-10-09','2026-10-08','2026-10-07','2026-10-06','2026-10-05','2026-10-04'])
  self.assertIsNone(r[0]['expected']);self.assertEqual(r[0]['status'],'未知');self.assertEqual(r[1]['status'],'未收到日报')
 def test_thirty_three_days_long_notes_pagination_export_projection(self):
  users=[self.approve(600+i,'模拟员工'+str(i)) for i in range(30)]
  for day in ('2026-10-08','2026-10-09','2026-10-10'):
   self.s.set_roster(self.c.owner,day,users,'模拟名单')
   for i,u in enumerate(users[:29]):self.s.submit_daily(u,self.raw(day,n=i,note=('完整备注：'+str(i)+'\n')*300 if i==0 else '模拟备注'),'simulated')
  data=export_data(self.s);self.assertEqual(len(data['days']),3);self.assertTrue(all(len(x['rows'])==30 for x in data['days']))
  pages=report_pages(self.s,self.s.summary('2026-10-10'));text='\n'.join(p['text'] for p in pages)
  self.assertIn('未提交',text);self.assertIn('—',text);self.assertNotIn('历史应交名单',text);self.assertGreater(len(pages),3)
  self.assertEqual(sum(p['text'].count('完整备注：0') for p in pages),300)
 async def test_excel_failure_keeps_old_pending_restart_and_fresh_export(self):
  self.s.db.set('daily_excel_path',str(self.c.data/'exports/test.xlsx'));path=export_path(self.s);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'previous')
  self.s.db.set('daily_excel_pending',True)
  with patch('team_bot.daily_history.write_excel',side_effect=PermissionError('locked')):self.assertFalse(await ensure_export(self.s,True))
  self.assertEqual(path.read_bytes(),b'previous');self.assertTrue(self.s.db.setting('daily_excel_pending'))
  with patch('team_bot.daily_history.write_excel',side_effect=lambda d,p:p.write_bytes(b'fresh')):self.assertTrue(await ensure_export(self.s,True))
  self.assertFalse(self.s.db.setting('daily_excel_pending'));self.assertEqual(self.s.db.setting('daily_excel_done'),0)
 async def test_chinese_export_permission_and_specific_query(self):
  u=self.approve(456)
  await self.handle(400,u,'导出历史报表');self.assertIn('管理员',self.s.db.one("SELECT text FROM outbox WHERE key='update:400:error:0'")['text'])
  self.fixture.members[self.c.owner]=('administrator',self.user(self.c.owner,'管理员'))
  with patch('team_bot.rich.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=600))):await self.handle(401,self.c.owner,'查看日报 2026-10-09')
  self.assertIn('2026-10-09',self.s.db.one("SELECT text FROM outbox WHERE key='update:401:0'")['text']);self.assertIsNone(self.s.record(u,'2026-10-09'))
 async def test_name_identity_rename_and_no_workbench_impact(self):
  u=self.approve(456,'同名');v=self.approve(789,'同名')
  await self.handle(450,u,self.raw(),user=self.user(u,'新名称😀'));await self.handle(451,v,self.raw(n=0))
  self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM daily_records')['n'],2);self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM wb_events')['n'],0)
  self.assertIn('新名称',self.s.display(u))
 def test_parser_note_colons_and_metrics_are_not_rescanned(self):
  day,p=parse_daily(self.raw(note='中文 : 说明\n新增：999\n空行\n\n末行'),self.c.now().date());self.assertEqual(p['新增'],2);self.assertIn('新增：999',p['备注'])

 async def test_feedback_background_and_priority_do_not_race(self):
  from team_bot.daily_history import queue_feedback,flush_feedback
  u=self.approve(456)
  update=await self.handle(500,u,self.raw());v=self.s.db.setting('daily_feedback:500');v['state']='pending';self.s.db.set('daily_feedback:500',v)
  self.ctx.bot.set_message_reaction.reset_mock()
  async def network(**kwargs):await asyncio.sleep(.03);return True
  self.ctx.bot.set_message_reaction.side_effect=network
  await asyncio.gather(flush_feedback(self.s,self.ctx.bot,['500']),flush_feedback(self.s,self.ctx.bot))
  self.ctx.bot.set_message_reaction.assert_awaited_once()

 async def test_actual_excel_atomic_locked_target_retry(self):
  import os,hashlib
  if os.name!='nt':self.skipTest('Windows file-sharing lock integration')
  self.s.db.set('daily_excel_path',str(self.c.data/'exports/atomic.xlsx'));self.s.db.set('daily_excel_pending',True)
  self.assertTrue(await ensure_export(self.s,True));path=export_path(self.s);before=hashlib.sha256(path.read_bytes()).hexdigest()
  import ctypes
  from ctypes import wintypes
  kernel=ctypes.WinDLL('kernel32',use_last_error=True)
  kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p];kernel.CreateFileW.restype=wintypes.HANDLE
  kernel.CloseHandle.argtypes=[wintypes.HANDLE]
  handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0,None)
  self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
  try:
   self.s.db.set('daily_excel_revision',1);self.s.db.set('daily_excel_pending',True)
   self.assertFalse(await ensure_export(self.s,True));self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
  finally:kernel.CloseHandle(handle)
  self.assertTrue(await ensure_export(self.s,True));self.assertEqual(self.s.db.setting('daily_excel_done'),1)
  with zipfile.ZipFile(path) as z:self.assertIsNone(z.testzip())

 def test_actual_xlsx_long_note_continuations(self):
  import xml.etree.ElementTree as E
  u=self.approve(456);note=('完整多行备注：账户封控3个。\n')*160
  self.s.submit_daily(u,self.raw(note=note),'synthetic')
  path=self.c.data/'exports/long-notes.xlsx';write_excel(export_data(self.s),path)
  n={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
  with zipfile.ZipFile(path) as z:
   sh=E.fromstring(z.read('xl/worksheets/sheet2.xml'));texts=[]
   for cell in sh.findall('.//s:c',n):
    if cell.get('r','').startswith('G') and int(cell.get('r')[1:])>=6:
     v=cell.find('s:v',n)
     if v is not None:texts.append(v.text or '')
     elif cell.find('s:is',n) is not None:texts.append(''.join(cell.find('s:is',n).itertext()))
   self.assertEqual(''.join(texts),note)
   self.assertTrue(all(float(row.get('ht',0))<=409.5 for row in sh.findall('.//s:row',n)))

if __name__=='__main__':unittest.main()
