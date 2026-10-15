import unittest,json,asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import test_daily_send
from team_bot.progress_ui import controller,flush,panel,team
from team_bot.progress_rounds import Progress
class ProgressTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.f=test_daily_send.SendTests();self.f.setUp();self.s=self.f.s;self.c=self.s.c;self.ctx=self.f.ctx;self.u=self.f.approve(456,'员工一','person1');self.v=self.f.approve(789,'员工二');self.f.fixture.members[self.c.owner]=('administrator',self.f.user(self.c.owner,'管理'));self.p=controller(self.s)
  self.c.bot_username='mock_company_bot'
  self.ctx.bot.edit_message_text=AsyncMock(side_effect=lambda **k:SimpleNamespace(message_id=k['message_id']));self.ctx.bot.send_message=AsyncMock(return_value=SimpleNamespace(message_id=501))
  self.t=self.p.publish(self.c.owner,dict(day='2026-10-10',title='养号步骤',kind='seq',stages=['账户整体异常检查','制定作品发布','指定评论点赞数量完成']))
  self.z=self.p.publish(self.c.owner,dict(day='2026-10-10',title='资料整理',kind='free',stages=['整理今日图片','更新工作名单','处理待办反馈','归档工作文件']));self.r=self.p.new_round(self.c.owner,'2026-10-10')
 def tearDown(self):
  if getattr(self.p,'_refresh_task',None):self.p._refresh_task.cancel()
  self.p.db.conn.close();self.f.tearDown()
 async def go(self,n,uid,data='',text='',chat=None):
  import test_acceptance as fixture
  from telegram import CallbackQuery
  u=fixture.TelegramAdapterTests.make_update(self.f.fixture,n,text,uid=uid,chat=chat or dict(id=uid,type='private'),thread=11,callback='test:newgroup:'+data if data else None)
  profile=self.f.fixture.members.get(uid)
  if profile:
   object.__setattr__(u.effective_message,'from_user',profile[1])
   if u.callback_query:object.__setattr__(u.callback_query,'from_user',profile[1])
  if u.callback_query:object.__setattr__(u.callback_query,'id','q'+str(n))
  with patch.object(CallbackQuery,'answer',new=AsyncMock()):await self.f.a.handle(u,self.ctx)
  return u
 def test_cumulative_free_zero_and_skip_guard(self):
  for t,v,k in [(self.t,2,'a'),(self.z,1,'b'),(self.z,3,'c')]:self.p.select(self.r,self.u,self.u,t,v,k)
  self.assertEqual(self.p.draft(self.r,self.u),{str(self.t):3,str(self.z):5});self.assertIsNone(self.p.feedback(self.r,self.u));self.p.submit(self.r,self.u,self.u,'d');self.p.submit(self.r,self.v,self.v,'zero')
  self.assertEqual(json.loads(self.p.feedback(self.r,self.v)['payload']),{str(self.t):0,str(self.z):0})
  self.p.db.run('UPDATE pg_drafts SET payload=? WHERE round=? AND uid=?',(json.dumps({str(self.t):4,str(self.z):0}),self.r,self.v))
  with self.assertRaisesRegex(ValueError,'不能跳过'):self.p.submit(self.r,self.v,self.v,'skip')
 async def test_handler_pagination_submit_idempotence_and_permissions(self):
  await self.go(1,self.u,f'pg:open:{self.r}');await self.go(2,self.u,f'pg:select:{self.r}:{self.u}:{self.t}:2:1');await self.go(3,self.u,f'pg:page:{self.r}:{self.u}:1')
  await self.go(4,self.u,f'pg:select:{self.r}:{self.u}:{self.z}:1:1');await self.go(5,self.u,f'pg:select:{self.r}:{self.u}:{self.z}:3:1');await self.go(6,self.u,f'pg:done:{self.r}:{self.u}:1');old=self.p.feedback(self.r,self.u)
  self.assertIsNotNone(old);self.assertEqual(json.loads(old['payload']),{str(self.t):3,str(self.z):5})
  await self.go(6,self.u,f'pg:done:{self.r}:{self.u}:1');await self.go(7,self.u,f'pg:done:{self.r}:{self.u}:1');self.assertEqual(old,self.p.feedback(self.r,self.u))
  await self.go(8,self.v,f'pg:select:{self.r}:{self.u}:{self.t}:3:1');self.assertEqual(old,self.p.feedback(self.r,self.u))
  await self.go(9,self.u,f'pg:select:{self.r}:{self.u}:{self.t}:3:1');await self.go(10,self.u,f'pg:done:{self.r}:{self.u}:1');self.assertEqual(self.p.feedback(self.r,self.u)['version'],1);self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM daily_records')['n'],0)
 def test_round_version_old_buttons_and_same_title(self):
  self.p.submit(self.r,self.u,self.u,'one');old=self.p.round(self.r)['tasks'][0];rr=self.p.new_round(self.c.owner,'2026-10-10',self.z);self.assertIsNone(self.p.feedback(rr,self.u))
  with self.assertRaisesRegex(ValueError,'旧轮'):self.p.submit(self.r,self.u,self.u,'late')
  
  with self.assertRaisesRegex(ValueError,'锁定'):self.p.publish(self.c.owner,dict(day='2026-10-10',title='新阶段',kind='seq',stages=['1','2','3']),self.t)
  self.p.publish(self.c.owner,dict(day='2026-10-10',title='新阶段',kind='seq',stages=['1','2','3']));self.assertEqual(self.p.round(self.r)['tasks'][0],old)
  more=self.p.publish(self.c.owner,dict(day='2026-10-10',title='资料整理',kind='seq',stages=['1','2','3']));self.assertNotEqual(more,self.z);self.p.close(self.c.owner,self.z);self.assertEqual(self.p.task(self.z)['closed'],1)
 async def test_ephemeral_fallback_and_reopen_entry(self):
  group=dict(id=self.c.chat,type='supergroup',is_forum=True)
  with patch('team_bot.progress_ui.official',new=AsyncMock(side_effect=ValueError('unsupported fixture'))):await self.go(1,self.u,f'pg:open:{self.r}',chat=group)
  self.assertEqual(self.p.db.one('SELECT chat,ephemeral FROM pg_panels'),dict(chat=self.u,ephemeral=0))
  await self.go(2,self.u,text='/start pg_'+str(self.r));self.assertEqual(self.p.db.one('SELECT COUNT(*) n FROM pg_panels')['n'],1)
  with patch('team_bot.progress_ui.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=600))):await self.go(3,self.c.owner,f'pg:team:{self.r}:1:1',chat=group)
  self.assertEqual(self.p.round(self.r)['board'],600)
 async def test_ephemeral_receiver_restart_and_failed_delivery_retry(self):
  group=dict(id=self.c.chat,type='supergroup',is_forum=True)
  with patch('team_bot.progress_ui.official',new=AsyncMock(return_value=dict(ephemeral_message_id=77))) as api:await self.go(1,self.u,f'pg:open:{self.r}',chat=group);self.assertEqual(api.call_args.args[2]['ephemeral_message_parameters']['receiver_user_id'],self.u)
  self.assertEqual(self.p.db.one('SELECT ephemeral FROM pg_panels')['ephemeral'],1);self.p.db.conn.close();self.p=Progress(self.s);self.s._progress=self.p;self.assertEqual(self.p.round(self.r)['roster'],[self.u,self.v])
  text,m=panel(self.p,self.r,self.u);self.p.queue('retry',dict(text=text,markup=m,chat=self.u,kind='panel',round=self.r,uid=self.u));self.ctx.bot.send_message.side_effect=RuntimeError('fixture');await flush(self.p,self.ctx.bot);self.assertEqual(self.p.db.one("SELECT state FROM pg_outbox WHERE key='retry'")['state'],'pending')
  self.f.clock('2026-10-10T14:01:00');self.ctx.bot.send_message.side_effect=None;await flush(self.p,self.ctx.bot);self.assertEqual(self.p.db.one("SELECT state FROM pg_outbox WHERE key='retry'")['state'],'sent')
 async def test_thirty_people_five_tasks_concurrent_entry(self):
  users=[self.f.approve(1000+i,'模拟同名','user'+str(i)) for i in range(30)]
  for i in range(3):self.p.publish(self.c.owner,dict(day='2026-10-10',title='模拟'+str(i),kind='free',stages=['1','2','3','4']))
  rr=self.p.new_round(self.c.owner,'2026-10-10',roster=users,simulated=True);tasks=self.p.round(rr)['tasks']
  async def submit(i,u):await self.go(100+i*2,u,f'pg:select:{rr}:{u}:{tasks[0]["id"]}:2:1');await self.go(101+i*2,u,f'pg:done:{rr}:{u}:1')
  await asyncio.gather(*(submit(i,u) for i,u in enumerate(users)));self.assertEqual(self.p.db.one('SELECT COUNT(*) n FROM pg_feedback WHERE round=?',(rr,))['n'],30)
  for page in range(1,5):self.assertIn('已反馈30人',team(self.p,rr,5,page)[0])
  self.assertTrue(all(json.loads(self.p.feedback(rr,u)['payload'])[str(tasks[0]['id'])]==3 for u in users))
 async def test_chinese_publish_query_and_daily_frozen(self):
  self.s.submit_daily(self.u,self.f.raw(),'existing');before=self.s.db.all('SELECT * FROM daily_records');history=self.s.db.all('SELECT * FROM history')
  await self.go(1,self.c.owner,text='发布任务')
  token=json.loads(self.p.db.one('SELECT payload FROM pg_builders')['payload'])['token']
  await self.go(2,self.c.owner,f'pg:build:toggle:{token}')
  await self.go(3,self.c.owner,text='任务主题：中文测试\n步骤1：一\n步骤2：二\n步骤3：三')
  await self.go(4,self.c.owner,f'pg:build:publish:{token}');await self.go(5,self.c.owner,text='查询任务')
  self.assertEqual(len(self.p.round(self.p.latest()['id'])['tasks']),3);self.assertEqual(before,self.s.db.all('SELECT * FROM daily_records'));self.assertEqual(history,self.s.db.all('SELECT * FROM history'))

 async def test_latest_board_retry_refresh_and_export_is_separate(self):
  from team_bot.progress_export import write_history
  from team_bot.config import ROOT
  self.p.db.run('UPDATE pg_rounds SET board=900 WHERE id=?',(self.r,))
  self.p.select(self.r,self.u,self.u,self.t,3,'pre');self.p.submit(self.r,self.u,self.u,'a');self.f.clock('2026-10-10T14:00:02')
  with patch('team_bot.progress_ui.edit_native',new=AsyncMock(side_effect=RuntimeError('fixture'))):await flush(self.p,self.ctx.bot)
  self.assertTrue(self.p.round(self.r)['pending']);self.p.submit(self.r,self.v,self.v,'c');self.f.clock('2026-10-10T14:01:00')
  with patch('team_bot.progress_ui.edit_native',new=AsyncMock(return_value=SimpleNamespace(message_id=900))) as edit:await flush(self.p,self.ctx.bot)
  self.assertEqual(edit.call_args.args[2],900);self.assertIn('3/3',str(edit.call_args.args[3]));self.assertFalse(self.p.round(self.r)['pending'])
  # Isolate export destination and ensure daily XLSX is not written by this path.
  import tempfile,zipfile
  with tempfile.TemporaryDirectory() as tmp,patch('team_bot.progress_export.ROOT',Path(tmp)):
   import shutil
   (Path(tmp)/'scripts').mkdir();shutil.copy2(ROOT/'scripts/progress_xlsx.mjs',Path(tmp)/'scripts/progress_xlsx.mjs');shutil.copytree(ROOT/'templates',Path(tmp)/'templates')
   daily=Path(tmp)/'exports/日报历史记录.xlsx';daily.parent.mkdir();daily.write_bytes(b'frozen daily')
   path=write_history(self.p,self.c.owner);self.assertEqual(path.name,'工作进度历史.xlsx');self.assertEqual(daily.read_bytes(),b'frozen daily')
   with zipfile.ZipFile(path) as z:self.assertIsNone(z.testzip());self.assertEqual(len([x for x in z.namelist() if x.startswith('xl/worksheets/sheet') and x.endswith('.xml')]),4)
 async def test_old_workbench_and_manager_topic_are_read_only_silent(self):
  before=self.s.db.all('SELECT * FROM wb_events');await self.go(1,self.u,'wb:add:2026-10-10:1:1');self.assertEqual(before,self.s.db.all('SELECT * FROM wb_events'))
  group=dict(id=self.c.chat,type='supergroup',is_forum=True)
  await self.f.handle(2,self.c.owner,'发布任务',chat=group,thread=self.c.threads['management'])
  self.assertEqual(len(self.p.tasks('2026-10-10')),2)

 def test_changed_version_reference_and_task_menu_paging(self):
  from team_bot.progress_ui import task_list
  self.p.select(self.r,self.u,self.u,self.t,3,'select');self.p.submit(self.r,self.u,self.u,'submit')
  
  with self.assertRaisesRegex(ValueError,'锁定'):self.p.publish(self.c.owner,dict(day='2026-10-10',title='新内容',kind='seq',stages=['新的1','新的2','新的3','新的4']),self.t)
  self.t=self.p.publish(self.c.owner,dict(day='2026-10-10',title='新内容',kind='seq',stages=['新的1','新的2','新的3','新的4']))
  rr=self.p.new_round(self.c.owner,'2026-10-10',self.t);text,_=panel(self.p,rr,self.u)
  self.assertNotIn('v1',text);self.assertIn('○ 1. 新的1',text);self.assertEqual(self.p.draft(rr,self.u),{str(self.t):0});self.assertIsNone(self.p.feedback(rr,self.u))
  for i in range(12):self.p.publish(self.c.owner,dict(day='2026-10-10',title='分页'+str(i),kind='free',stages=['1','2','3']))
  text,markup=task_list(self.p,self.c.owner,'2026-10-10',2,True);self.assertIn('第2/2页',text);self.assertIn('查询全部',str(markup))

 async def test_pending_task_form_does_not_intercept_daily_submission(self):
  self.f.approve(self.c.owner,'管理员')
  await self.go(1,self.c.owner,'pg:type:free')
  await self.go(2,self.c.owner,text=self.f.raw().replace('2026-10-10','日期：2026-10-10',1))
  self.assertIsNotNone(self.s.record(self.c.owner,'2026-10-10'))
  self.assertEqual(len(self.p.tasks('2026-10-10')),2);self.assertIsNotNone(self.p.db.one('SELECT uid FROM pg_inputs WHERE uid=?',(self.c.owner,)))

 def test_backup_restore_contains_independent_progress_ledger(self):
  import tempfile,copy,sqlite3,zipfile
  from team_bot.maintenance import backup,restore
  self.p.submit(self.r,self.u,self.u,'saved')
  archive=backup(self.s)
  with zipfile.ZipFile(archive) as z:self.assertIn('progress-acceptance.sqlite3',z.namelist());self.assertIsNone(z.testzip())
  with tempfile.TemporaryDirectory() as tmp:
   c=copy.copy(self.c);c.data=Path(tmp)/'restored';c.backups=Path(tmp)/'backups';restore(c,archive)
   conn=sqlite3.connect(c.data/'progress-acceptance.sqlite3')
   try:self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(conn.execute('SELECT payload FROM pg_feedback').fetchone()[0],self.p.feedback(self.r,self.u)['payload'])
   finally:conn.close()

 async def test_export_is_durable_background_job_not_update_blocker(self):
  with patch('team_bot.progress_export.write_export') as build:
   await self.go(1,self.c.owner,text='导出进度历史');build.assert_not_called()
  job=self.p.db.one("SELECT * FROM pg_outbox WHERE state='pending'");self.assertTrue(json.loads(job['payload'])['export'])
  import tempfile
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'progress.xlsx';path.write_bytes(b'fixture');self.ctx.bot.send_document=AsyncMock(return_value=SimpleNamespace(message_id=801))
   with patch('team_bot.progress_export.ensure_export',new=AsyncMock(return_value=True)) as build,patch('team_bot.progress_export.export_path',return_value=path):await flush(self.p,self.ctx.bot);build.assert_awaited_once()
   self.assertEqual(self.p.db.one('SELECT state FROM pg_outbox WHERE id=?',(job['id'],))['state'],'sent');self.ctx.bot.send_document.assert_awaited_once()

