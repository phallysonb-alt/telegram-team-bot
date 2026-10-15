"""Export entry regression through Adapter.handle, using independent fixture databases."""
import json, unittest, zipfile, io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET
import test_progress_rounds as fixtures
from team_bot.progress_ui import flush

class ExportNamesTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.f=fixtures.ProgressTests();self.f.setUp();self.s=self.f.s;self.p=self.f.p;self.c=self.f.c
  self.sent=[]
  async def capture(**kw):
   self.sent.append(dict(filename=kw.get('filename') or Path(kw['document'].name).name,
                         bytes=kw['document'].read(),caption=kw.get('caption',''),chat=kw['chat_id']))
   return SimpleNamespace(message_id=800+len(self.sent))
  self.f.ctx.bot.send_document=AsyncMock(side_effect=capture)
  self.daily=self.c.data/'exports/日报历史记录.xlsx';self.daily.parent.mkdir(exist_ok=True)
  self.progress=self.c.data/'exports/工作进度历史.xlsx'
  self.daily.write_bytes(b'daily independent');self.progress.write_bytes(b'progress independent')
  self.p.db.set('progress_excel_path',str(self.progress))
 def tearDown(self):self.f.tearDown()
 async def daily_send(self,n,text='',data=''):
  with patch('team_bot.daily_history.ensure_export',new=AsyncMock(return_value=True)):
   await self.f.go(n,self.c.owner,text=text,data=data)
 async def progress_send(self,n,text='',data='',mini=True):
  with patch('team_bot.progress_miniapp.enabled',return_value=mini):
   await self.f.go(n,self.c.owner,text=text,data=data)
  with patch('team_bot.progress_export.ensure_export',new=AsyncMock(return_value=True)):
   await flush(self.p,self.f.ctx.bot)
 async def test_daily_chinese_aliases_button_and_internal_command(self):
  for n,text in enumerate(('导出日报表','导出历史报表','导出日报历史','/daily_export'),100):
   await self.daily_send(n,text=text)
  await self.daily_send(110,data='daily_export')
  self.assertEqual(len(self.sent),5)
  for x in self.sent:
   self.assertEqual(x['filename'],'日报历史记录.xlsx');self.assertEqual(x['bytes'],b'daily independent')
   self.assertIn('导出日报表',x['caption']);self.assertEqual(x['chat'],self.c.owner)
 async def test_progress_chinese_aliases_and_button_in_both_paths(self):
  n=200
  for mini in (True,False):
   for text in ('导出跟进表','导出进度历史','导出工作进度历史'):
    await self.progress_send(n,text=text,mini=mini);n+=1
   await self.progress_send(n,data='pg:export',mini=mini);n+=1
  self.assertEqual(len(self.sent),8)
  for x in self.sent:
   self.assertEqual(x['filename'],'工作进度历史.xlsx');self.assertEqual(x['bytes'],b'progress independent')
   self.assertIn('导出跟进表',x['caption'])
 async def test_menu_and_help_names_callbacks_unchanged(self):
  await self.f.go(300,self.c.owner,text='/menu')
  row=self.s.db.one("SELECT * FROM outbox WHERE key='update:300:0'")
  buttons=[b for r in json.loads(row['markup'])['inline_keyboard'] for b in r]
  self.assertIn({'text':'导出日报表','callback_data':'test:newgroup:daily_export'},buttons)
  self.assertIn({'text':'导出跟进表','callback_data':'test:newgroup:pg:export'},buttons)
  await self.f.go(301,self.c.owner,text='/help')
  text=self.s.db.one("SELECT text FROM outbox WHERE key='update:301:0'")['text']
  for label in ('导出日报表','导出跟进表'):self.assertIn(label,text)
  for old in ('导出历史报表','导出进度历史'):self.assertNotIn(old,text)
 async def test_employee_denied_for_both_labels_and_buttons(self):
  for n,(text,data) in enumerate((('导出日报表',''),('','daily_export'),('导出跟进表',''),('','pg:export')),400):
   with patch('team_bot.progress_miniapp.enabled',return_value=True):await self.f.go(n,self.f.u,text=text,data=data)
  await flush(self.p,self.f.ctx.bot)
  self.assertEqual(self.sent,[])
 async def test_duplicate_update_does_not_resend_or_cross_files(self):
  await self.daily_send(500,text='导出日报表');await self.daily_send(500,text='导出日报表')
  await self.progress_send(501,text='导出跟进表');await self.progress_send(501,text='导出跟进表')
  self.assertEqual([x['filename'] for x in self.sent],['日报历史记录.xlsx','工作进度历史.xlsx'])
  self.assertEqual(self.daily.read_bytes(),b'daily independent');self.assertEqual(self.progress.read_bytes(),b'progress independent')
 async def test_failed_send_retries_original_file(self):
  capture=self.f.ctx.bot.send_document.side_effect
  self.f.ctx.bot.send_document.side_effect=RuntimeError('network fixture')
  await self.progress_send(600,text='导出跟进表')
  self.assertEqual(self.p.db.one("SELECT state FROM pg_outbox WHERE key='app:update:600'")['state'],'pending')
  self.f.ctx.bot.send_document.side_effect=capture
  self.f.f.clock('2026-10-10T14:00:10')
  with patch('team_bot.progress_export.ensure_export',new=AsyncMock(return_value=True)):await flush(self.p,self.f.ctx.bot)
  self.assertEqual(len(self.sent),1);self.assertEqual(self.sent[0]['bytes'],b'progress independent')
 async def test_real_generators_remain_two_and_four_sheets(self):
  from team_bot.daily_history import ensure_export
  from team_bot.progress_export import ensure_export as ensure_progress
  self.s.submit_daily(self.f.u,'2026-10-10\n新增：0\n维聊：2\n有效：1\n精聊：0\n备注：完整备注。','mock:only')
  self.assertTrue(await ensure_export(self.s,force=True))
  daily=self.daily.read_bytes()
  self.p.db.set('progress_excel_dirty',True)
  self.assertTrue(await ensure_progress(self.p));self.assertEqual(self.daily.read_bytes(),daily)
  def names(raw):
   with zipfile.ZipFile(io.BytesIO(raw)) as z:
    self.assertIsNone(z.testzip())
    return [s.attrib['name'] for s in ET.fromstring(z.read('xl/workbook.xml')).findall('{*}sheets/{*}sheet')]
  self.assertEqual(names(daily),['每日汇总','员工明细'])
  self.assertEqual(names(self.progress.read_bytes()),['查询汇总','员工进度历史','当前问题历史','任务定义'])
  await self.daily_send(700,text='导出日报表');await self.progress_send(701,text='导出跟进表')
  self.assertEqual(self.sent[0]['bytes'],daily);self.assertEqual(self.sent[1]['bytes'],self.progress.read_bytes())

if __name__=='__main__':unittest.main()
