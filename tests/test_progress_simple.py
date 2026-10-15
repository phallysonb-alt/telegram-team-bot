import unittest,json,asyncio
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
import test_progress_rounds as fixtures
from team_bot.progress_ui import panel,team,menu,flush
from team_bot.progress_rounds import Progress
class SimpleTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.g=fixtures.ProgressTests();self.g.setUp()
  for x in ('p','s','c','u','v','r','t','z','ctx'):setattr(self,x,getattr(self.g,x))
 async def go(self,*args,**kwargs):return await self.g.go(*args,**kwargs)
 def tearDown(self):self.g.tearDown()
 def token(self):return json.loads(self.p.db.one('SELECT payload FROM pg_builders WHERE uid=?',(self.c.owner,))['payload'])['token']
 async def test_batch_add_four_toggle_atomic_publish_and_dedup(self):
  await self.go(1,self.c.owner,text='发布任务');token=self.token()
  await self.go(2,self.c.owner,text='任务主题：顺序演示\n步骤1：甲\n步骤2：乙\n步骤3：丙');self.assertEqual(len(self.p.tasks('2026-10-10')),2)
  await self.go(3,self.c.owner,f'pg:build:add:{token}');await self.go(4,self.c.owner,f'pg:build:four:{token}');await self.go(5,self.c.owner,f'pg:build:toggle:{token}')
  await self.go(6,self.c.owner,f'pg:build:publish:{token}');self.assertEqual(len(self.p.tasks('2026-10-10')),2)
  await self.go(7,self.c.owner,text='任务主题：自由演示\n步骤1：一\n步骤2：二\n步骤3：三\n步骤4：四');await self.go(8,self.c.owner,f'pg:build:publish:{token}')
  tasks=self.p.tasks('2026-10-10');self.assertEqual(len(tasks),4);self.assertEqual([t['kind'] for t in tasks[-2:]],['seq','free']);self.assertEqual(len(tasks[-1]['stages']),4)
  await self.go(8,self.c.owner,f'pg:build:publish:{token}');await self.go(9,self.c.owner,f'pg:build:publish:{token}');self.assertEqual(len(self.p.tasks('2026-10-10')),4)
  card=self.p.db.one("SELECT payload FROM pg_outbox WHERE key='update:8'");payload=json.loads(card['payload']);self.assertEqual(payload['chat'],self.c.chat);self.assertIn('今日任务｜2026-10-10',payload['text']);self.assertIn('步骤' if False else '4. 四',payload['text'])
  await self.go(10,self.c.owner,text='查询任务');rr=self.p.latest()['id'];self.assertEqual(len(self.p.round(rr)['tasks']),4)
  self.assertIsNone(self.p.feedback(rr,self.u));self.assertEqual([x['text'] for x in menu(self.p,self.c.owner)[1]['inline_keyboard'][0]],['发布任务','查询任务'])
 async def test_notes_are_separate_full_multiline_revision_empty_and_owner_guard(self):
  text,k=panel(self.p,self.r,self.u);self.assertIn('养号步骤',text);self.assertIn('资料整理',text);self.assertNotIn('轮',text);self.assertNotIn('v1',text)
  await self.go(1,self.u,f'pg:select:{self.r}:{self.u}:{self.t}:2:1');await self.go(2,self.u,f'pg:select:{self.r}:{self.u}:{self.z}:1:1');await self.go(3,self.u,f'pg:select:{self.r}:{self.u}:{self.z}:3:1')
  await self.go(4,self.u,f'pg:note:{self.r}:{self.u}:1');note='两个账户登录异常。\n等待处理，编号3与阶段数字无关。';await self.go(5,self.u,text=note)
  self.assertEqual(self.p.draft(self.r,self.u),{str(self.t):3,str(self.z):5});self.assertEqual(self.p.note(self.r,self.u),'');await self.go(6,self.u,f'pg:done:{self.r}:{self.u}:1');self.assertEqual(self.p.note(self.r,self.u),note)
  await self.go(7,self.v,f'pg:note:{self.r}:{self.u}:1');self.assertEqual(self.p.note(self.r,self.u),note)
  await self.go(8,self.u,f'pg:note:{self.r}:{self.u}:1');await self.go(9,self.u,text='修改备注\n第二行');await self.go(10,self.u,f'pg:done:{self.r}:{self.u}:1');self.assertEqual(self.p.feedback(self.r,self.u)['version'],2);self.assertEqual(self.p.draft(self.r,self.u)[str(self.z)],5)
  await self.go(11,self.u,f'pg:done:{self.r}:{self.u}:1');self.assertEqual(self.p.feedback(self.r,self.u)['version'],2)
  await self.go(12,self.u,f'pg:note:{self.r}:{self.u}:1');await self.go(13,self.u,text='清空备注');await self.go(14,self.u,f'pg:done:{self.r}:{self.u}:1');self.assertEqual(self.p.note(self.r,self.u),'')
  self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM daily_records')['n'],0)
 async def test_zero_unanswered_board_auto_creation_and_same_message_edit(self):
  await self.go(1,self.u,f'pg:done:{self.r}:{self.u}:1');self.g.f.clock('2026-10-10T14:00:03')
  with patch('team_bot.progress_ui.send_native',new=AsyncMock(return_value=SimpleNamespace(message_id=900))) as send:await flush(self.p,self.ctx.bot);send.assert_awaited_once()
  self.assertEqual(self.p.round(self.r)['board'],900);self.assertIn('○○○ 0/3',team(self.p,self.r)[0]);self.assertIn('未反馈',team(self.p,self.r)[0])
  note='多行问题\n'+('完整中文备注' * 150);self.p.edit_note(self.r,self.u,self.u,note);self.p.select(self.r,self.u,self.u,self.t,2,'s');self.p.submit(self.r,self.u,self.u,'new');self.g.f.clock('2026-10-10T14:00:10')
  with patch('team_bot.progress_ui.edit_native',new=AsyncMock(return_value=SimpleNamespace(message_id=900))) as edit:
   await flush(self.p,self.ctx.bot);self.assertEqual(edit.call_args.args[2],900)
   rich=edit.call_args.args[3];note_parts=rich['blocks'][-1]['blocks'];self.assertEqual(''.join(x['text'] for x in note_parts),note);self.assertIn('●●○ 2/3',str(rich))
  self.assertEqual(self.p.round(self.r)['board'],900);self.assertFalse(self.p.round(self.r)['pending'])
 async def test_many_tasks_selection_paging_notes_restart_new_round_isolation(self):
  for i in range(3):self.p.publish(self.c.owner,dict(day='2026-10-10',title='追加'+str(i),kind='free',stages=['一','二','三','四']))
  rr=self.p.new_round(self.c.owner,'2026-10-10');self.assertIn('下一页',str(panel(self.p,rr,self.u)[1]))
  await self.go(1,self.u,f'pg:select:{rr}:{self.u}:{self.t}:2:1');await self.go(2,self.u,f'pg:page:{rr}:{self.u}:2');await self.go(3,self.u,f'pg:note:{rr}:{self.u}:2');await self.go(4,self.u,text='跨页完整备注');await self.go(5,self.u,f'pg:done:{rr}:{self.u}:2')
  self.p.db.conn.close();self.p=Progress(self.s);self.s._progress=self.p;self.g.p=self.p;self.assertEqual(self.p.note(rr,self.u),'跨页完整备注');self.assertEqual(json.loads(self.p.feedback(rr,self.u)['payload'])[str(self.t)],3)
  newer=self.p.new_round(self.c.owner,'2026-10-10');self.assertIsNone(self.p.feedback(newer,self.u));self.assertEqual(self.p.note(newer,self.u,True),'')
  with self.assertRaisesRegex(ValueError,'旧轮'):self.p.edit_note(rr,self.u,self.u,'旧消息')
  await self.go(6,self.u,f'pg:done:{rr}:{self.u}:2');self.assertIsNone(self.p.feedback(newer,self.u))
 async def test_task_draft_and_note_input_do_not_capture_frozen_daily(self):
  self.g.f.approve(self.c.owner,'管理员');await self.go(1,self.c.owner,text='发布任务');await self.go(2,self.c.owner,text=self.g.f.raw());self.assertIsNotNone(self.s.record(self.c.owner,'2026-10-10'))
  await self.go(3,self.u,f'pg:note:{self.r}:{self.u}:1');await self.go(4,self.u,text=self.g.f.raw(note='日报备注'))
  self.assertIsNotNone(self.s.record(self.u,'2026-10-10'));self.assertEqual(self.p.note(self.r,self.u,True),'')
