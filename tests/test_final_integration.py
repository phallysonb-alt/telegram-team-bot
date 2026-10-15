"""Independent synthetic identities, requests through actual handlers/API dispatch."""
import asyncio,json,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from telegram import Update
import test_progress_miniapp as mini_tests
import test_new_group as group_tests
from test_progress_miniapp import signed
from team_bot.member_registry import reconcile
from team_bot.progress_lock import mask
from team_bot.progress_history_v2 import data
from team_bot.progress_rounds import Progress

class LockTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.f=mini_tests.MiniAppTests();self.f.setUp();self.p=self.f.p;self.s=self.f.s;self.api=self.f.api;self.u=self.f.u;self.r=self.f.r;self.t=self.f.fixture.t;self.z=self.f.fixture.z
 def tearDown(self):self.f.tearDown()
 async def request(self,path,body,rid=None):
  return await self.api.dispatch('/api/'+path,dict(initData=signed(self.f.c.token,self.u),body=body))
 def body(self,rid,m1=0,m2=0,note='',key='lockedrequest001'):
  return dict(key=key,round=rid,masks={str(self.t):m1,str(self.z):m2},notes={str(self.t):note,str(self.z):''})
 async def test_first_submission_locked_exact_retry_and_changes_denied(self):
  body=self.body(self.r,1,5,'第一轮\n问题');a=await self.request('submit',body);self.assertEqual(await self.request('submit',body),a)
  same=dict(body,key='anotherduplicate');self.assertFalse((await self.request('submit',same))['changed'])
  for changed in [self.body(self.r,3,5,key='changedphase001'),self.body(self.r,1,5,'改问题',key='changednote001')]:
   with self.assertRaisesRegex(ValueError,'锁定'):await self.request('submit',changed)
  self.assertEqual(self.p.feedback(self.r,self.u)['version'],1);self.assertEqual(self.api.task_notes(self.r,self.u)[str(self.t)],'第一轮\n问题')
 async def test_webapp_user_missing_sdk_is_bot_uses_live_member_branch(self):
  from telegram import User
  self.api.membership=None;self.api.bot.get_chat_member=AsyncMock(return_value=SimpleNamespace(user=User(self.u,'模拟账号',False),status='member'))
  result=await self.request('session',{})
  self.assertFalse(result['admin']);self.api.bot.get_chat_member.assert_awaited_once()
 async def test_older_signed_profile_cannot_undo_recent_same_uid_rename(self):
  from telegram import User
  import time
  self.s.profile(User(self.u,'较新的账号名称',False,username='new_account_name'))
  self.api.membership=None;self.api.bot.get_chat_member=AsyncMock(return_value=SimpleNamespace(user=User(self.u,'API缓存旧名',False),status='member'))
  await self.api.dispatch('/api/session',dict(initData=signed(self.f.c.token,self.u,at=time.time()-60,name='旧签名名称'),body={}))
  self.assertEqual(self.s.db.one('SELECT name FROM tg_profiles WHERE uid=?',(self.u,))['name'],'较新的账号名称')
 async def test_three_rounds_monotonic_delta_and_problems_independent(self):
  await self.request('submit',self.body(self.r,1,5,'第一轮'))
  r2=self.p.new_round(self.f.c.owner,'2026-10-10');view=await self.request('feedback',dict(round=r2));self.assertEqual(view['locked'],{str(self.t):1,str(self.z):5});self.assertFalse(view['submitted']);self.assertEqual(view['notes'][str(self.t)],'')
  with self.assertRaisesRegex(ValueError,'锁定'):await self.request('submit',self.body(r2,0,5,key='decrease001'))
  await self.request('submit',self.body(r2,3,7,'第二轮',key='secondround01'))
  r3=self.p.new_round(self.f.c.owner,'2026-10-10');await self.request('submit',self.body(r3,7,15,'',key='thirdround01'))
  self.assertEqual(mask(self.p,self.p.task(self.t),self.u),7)
  self.assertEqual(self.api.task_notes(self.r,self.u)[str(self.t)],'第一轮');self.assertEqual(self.api.task_notes(r2,self.u)[str(self.t)],'第二轮')
  self.assertEqual(self.p.db.one('SELECT added FROM pg_feedback_delta WHERE round=? AND uid=? AND task=?',(r2,self.u,self.t))['added'],2)
  exported=data(self.p);self.assertEqual(len(exported['problems']),2);self.assertEqual(len(exported['summaries']),6)
 async def test_zero_increment_not_unanswered_and_same_name_separate_task(self):
  await self.request('submit',self.body(self.r,0,0,'尚未完成'))
  r2=self.p.new_round(self.f.c.owner,'2026-10-10');await self.request('submit',self.body(r2,0,0,'问题仍在',key='zeroroundtwo'))
  summary=self.api.summary(r2,self.f.c.owner);self.assertEqual(summary['received'],1);self.assertEqual(summary['expected'],2)
  new=self.p.publish(self.f.c.owner,self.p.definition('2026-10-10',self.p.task(self.t)['title'],'seq',['a','b','c']))
  self.assertNotEqual(new,self.t);self.assertEqual(mask(self.p,self.p.task(new),self.u),0)
 async def test_old_round_and_unauthorized_identity_rejected(self):
  self.p.new_round(self.f.c.owner,'2026-10-10')
  with self.assertRaisesRegex(ValueError,'旧轮'):await self.request('submit',self.body(self.r))
  body=self.body(self.p.latest()['id']);body['uid']=self.f.v
  with self.assertRaises(PermissionError):await self.request('submit',body)
 async def test_concurrent_same_person_only_one_saved_feedback(self):
  results=await asyncio.gather(self.request('submit',self.body(self.r,1,key='concurrentone')),self.request('submit',self.body(self.r,3,key='concurrenttwo')),return_exceptions=True)
  self.assertEqual(sum(isinstance(x,dict) for x in results),1);self.assertEqual(self.p.feedback(self.r,self.u)['version'],1)
 async def test_free_task_decrease_rolls_back_earlier_task_and_late_network_retry(self):
  b=self.body(self.r,1,5);result=await self.request('submit',b)
  next_round=self.p.new_round(self.f.c.owner,'2026-10-10')
  self.assertEqual(await self.request('submit',b),result)
  with self.assertRaisesRegex(ValueError,'锁定'):await self.request('submit',self.body(next_round,3,4,key='partial_rollback'))
  self.assertEqual(mask(self.p,self.p.task(self.t),self.u),1);self.assertIsNone(self.p.feedback(next_round,self.u))
 async def test_restart_and_baseline_last_value_without_fake_events(self):
  await self.request('submit',self.body(self.r,1,5))
  self.p.db.run('DELETE FROM pg_migrations');self.p.db.run('DELETE FROM pg_cumulative');old=self.p.db.all('SELECT * FROM pg_audit');notes=self.p.db.all('SELECT * FROM pg_task_notes')
  self.p.db.close();self.p=Progress(self.s);self.f.fixture.p=self.p
  self.assertEqual(mask(self.p,self.p.task(self.t),self.u),1);self.assertEqual(old,self.p.db.all('SELECT * FROM pg_audit'));self.assertEqual(notes,self.p.db.all('SELECT * FROM pg_task_notes'))
 async def test_frozen_daily_and_existing_query_roster_not_changed_by_new_employee(self):
  before=self.s.db.all('SELECT * FROM daily_records');roster=self.p.round(self.r)['roster'];self.f.fixture.f.approve(112233,'模拟新员工')
  await self.request('submit',self.body(self.r,1));self.assertEqual(self.p.round(self.r)['roster'],roster);self.assertEqual(self.s.db.all('SELECT * FROM daily_records'),before)
 async def test_excel_failed_atomic_writer_retries_and_revision_during_write(self):
  from team_bot.progress_export import ensure_export
  self.p.db.set('progress_excel_dirty',True)
  with patch('team_bot.progress_export.write_export',side_effect=PermissionError('模拟Excel占用')):
   self.assertFalse(await ensure_export(self.p));self.assertTrue(self.p.db.setting('progress_excel_dirty'))
  with patch('team_bot.progress_export.write_export') as writer:
   self.assertTrue(await ensure_export(self.p));self.assertFalse(self.p.db.setting('progress_excel_dirty'));writer.assert_called_once()
  self.p.db.set('progress_excel_dirty',True)
  def edited(*args):
   from team_bot.progress_lock import dirty
   dirty(self.p)
  # Mutate in event-loop before return, not on SQLite's worker thread.
  with patch('team_bot.progress_export.asyncio.to_thread',new=AsyncMock(side_effect=lambda *a:edited())):
   self.assertFalse(await ensure_export(self.p));self.assertTrue(self.p.db.setting('progress_excel_dirty'))

class AutomaticMembers(unittest.IsolatedAsyncioTestCase):
 def setUp(self):self.f=group_tests.NewGroupTests();self.f.setUp()
 def tearDown(self):self.f.tearDown()
 async def test_first_business_access_opens_once_same_numeric_identity(self):
  f=self.f;uid=557799;f.members[uid]=('member',f.user(uid,'新员工 😀','new_member'))
  await f.handle(901,uid,'/start');self.assertTrue(f.s.employee(uid)['approved']);self.assertFalse(f.s.admin(uid));old=f.s.db.all('SELECT * FROM history')
  await f.handle(902,uid,'/start');self.assertEqual(f.s.db.one('SELECT COUNT(*) n FROM employees')['n'],1);self.assertEqual(old,f.s.db.all('SELECT * FROM history'))
 async def test_admin_bot_excluded_and_membership_failure_closed(self):
  f=self.f;uid=668800;f.members[uid]=('administrator',f.user(uid))
  await reconcile(f.s,f.ctx.bot,uid);self.assertIsNone(f.s.db.one('SELECT * FROM employees WHERE uid=?',(uid,)))
  self.assertFalse(f.s.admin(uid))
  with self.assertRaises(PermissionError):await reconcile(f.s,f.ctx.bot,992211)
  self.assertIsNone(f.s.db.one('SELECT * FROM employees WHERE uid=?',(992211,)))
 async def test_duplicate_join_leave_rejoin_and_promotion_preserve_history(self):
  f=self.f;uid=556677;user=f.user(uid,'同名');f.members[uid]=('member',user)
  await reconcile(f.s,f.ctx.bot,uid,user);f.submit(uid);before=f.s.db.all('SELECT * FROM daily_records')
  f.members[uid]=('left',user);await reconcile(f.s,f.ctx.bot,uid);self.assertFalse(f.s.db.one('SELECT active FROM employees WHERE uid=?',(uid,))['active'])
  f.members[uid]=('member',user);await reconcile(f.s,f.ctx.bot,uid);self.assertTrue(f.s.employee(uid)['active']);await reconcile(f.s,f.ctx.bot,uid)
  self.assertEqual(f.s.db.one('SELECT COUNT(*) n FROM employees')['n'],1)
  f.members[uid]=('administrator',user);await reconcile(f.s,f.ctx.bot,uid);self.assertFalse(f.s.db.one('SELECT active FROM employees WHERE uid=?',(uid,))['active']);self.assertFalse(f.s.admin(uid));self.assertEqual(before,f.s.db.all('SELECT * FROM daily_records'))
 async def test_member_updates_actual_adapter_and_durable_network_retry(self):
  f=self.f;uid=774411;user=f.user(uid,'模拟新成员');f.members[uid]=('member',user)
  def update(n,status):
   return Update.de_json(dict(update_id=n,chat_member=dict(chat=dict(id=f.c.chat,type='supergroup'),from_user=dict(id=f.c.owner,first_name='模拟管理员',is_bot=False),date=1780000000,old_chat_member=dict(status='left',user=user.to_dict()),new_chat_member=dict(status=status,user=user.to_dict()))),f.ctx.bot)
  await f.a.handle(update(501,'member'),f.ctx);await f.a.handle(update(501,'member'),f.ctx)
  self.assertEqual(f.s.db.one('SELECT COUNT(*) n FROM employees')['n'],1)
  f.members[uid]=('left',user);await f.a.handle(update(502,'left'),f.ctx);self.assertFalse(f.s.db.one('SELECT active FROM employees')['active'])
  del f.members[uid];await f.a.handle(update(503,'member'),f.ctx);self.assertIn(str(uid),f.s.db.setting('member_reconcile_pending'))
  f.members[uid]=('member',user);f.s.db.set('member_reconcile_pending',{str(uid):dict(at=0)})
  from team_bot.member_registry import retry_pending
  await retry_pending(f.s,f.ctx.bot);self.assertEqual(f.s.db.setting('member_reconcile_pending'),{});self.assertTrue(f.s.employee(uid)['active'])
 async def test_disabled_business_admin_is_not_auto_restored(self):
  f=self.f;uid=f.approve(774433,'模拟停用管理员');f.s.db.run("UPDATE employees SET role='admin',active=0 WHERE uid=?",(uid,))
  with self.assertRaisesRegex(PermissionError,'停用'):await reconcile(f.s,f.ctx.bot,uid)
  self.assertEqual(f.s.db.one('SELECT active FROM employees')['active'],0)
