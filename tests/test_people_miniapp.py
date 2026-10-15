import unittest,json,asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from telegram import User
from test_progress_miniapp import MiniAppTests

class PeopleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.b=MiniAppTests();self.b.setUp();self.s=self.b.s;self.p=self.b.p;self.uid=887766
        self.person=User(self.uid,'新业务员 中文😀',False,username='mock_new_person')
    def tearDown(self):self.b.tearDown()
    def apply(self,status='member'):
        self.s.apply_identity(self.person)
        self.b.fixture.f.fixture.members[self.uid]=(status,self.person)
    async def req(self,path,body=None,uid=None):return await self.b.request(path,self.b.c.owner if uid is None else uid,body)
    async def test_one_approval_opens_both_modules_and_is_idempotent(self):
        before=await self.req('people');self.apply();result=await self.req('people/approve',{'target':self.uid})
        self.assertEqual(result['people']['count'],before['count']+1);self.assertTrue(self.s.employee(self.uid)['approved'])
        # Both modules go through their real processing entry, using a synthetic update.
        await self.b.fixture.f.handle(990002,self.uid,'2026-10-10\n新增：0\n维聊：0\n有效：0\n精聊：0',user=self.person)
        self.assertEqual(json.loads(self.s.record(self.uid,'2026-10-10')['payload'])['新增'],0)
        rid=self.p.new_round(self.b.c.owner,'2026-10-10');self.assertIn(self.uid,self.p.round(rid)['roster'])
        await self.b.request('feedback',self.uid,{'round':rid})
        audits=self.s.db.one('SELECT COUNT(*) n FROM history')['n'];again=await self.req('people/approve',{'target':self.uid})
        self.assertEqual(again['people']['count'],result['people']['count']);self.assertEqual(audits,self.s.db.one('SELECT COUNT(*) n FROM history')['n']);self.assertFalse(self.s.admin(self.uid))
    async def test_history_snapshots_and_daily_records_are_not_rewritten(self):
        self.s.roster('2026-10-09');old=self.s.db.one('SELECT * FROM attendance WHERE day=?',('2026-10-09',));round_old=self.p.round(self.b.r)
        daily=self.s.db.all('SELECT * FROM daily_records');self.apply();await self.req('people/approve',{'target':self.uid})
        self.assertEqual(old,self.s.db.one('SELECT * FROM attendance WHERE day=?',('2026-10-09',)));self.assertEqual(round_old,self.p.round(self.b.r));self.assertEqual(daily,self.s.db.all('SELECT * FROM daily_records'))
    async def test_regular_employee_cannot_manage_or_forge_admin(self):
        self.apply()
        for endpoint in ('people','people/invite','people/approve','people/reject'):
            with self.assertRaises(PermissionError):await self.req(endpoint,{'target':self.uid},uid=self.b.u)
        self.assertFalse(self.s.db.one('SELECT approved FROM employees WHERE uid=?',(self.uid,))['approved'])
    async def test_join_failure_remains_pending_and_retry_completes(self):
        self.apply('left');self.s.db.run("UPDATE onboarding SET state='pending_join' WHERE uid=?",(self.uid,))
        self.b.api.bot.approve_chat_join_request=AsyncMock(side_effect=RuntimeError('simulation failure'))
        with self.assertRaises(ValueError):await self.req('people/approve',{'target':self.uid})
        self.assertFalse(self.s.db.one('SELECT approved FROM employees WHERE uid=?',(self.uid,))['approved']);self.assertEqual(self.s.db.one('SELECT state FROM onboarding WHERE uid=?',(self.uid,))['state'],'pending_join')
        async def join(*args):self.b.fixture.f.fixture.members[self.uid]=('member',self.person)
        self.b.api.bot.approve_chat_join_request=AsyncMock(side_effect=join)
        result=await self.req('people/approve',{'target':self.uid});self.assertTrue(result['ok']);self.assertEqual(self.s.db.one('SELECT state FROM onboarding WHERE uid=?',(self.uid,))['state'],'approved')
    async def test_group_admin_is_business_employee_and_management_only_excluded(self):
        self.apply('administrator');before=(await self.req('people'))['count'];await self.req('people/approve',{'target':self.uid})
        self.assertFalse(self.s.admin(self.uid));self.assertEqual((await self.req('people'))['count'],before+1)
        self.s.change_employee(self.b.c.owner,self.uid,'模拟仅有管理角色',role='admin',required=0)
        self.assertEqual((await self.req('people'))['count'],before);rid=self.p.new_round(self.b.c.owner,'2026-10-10');self.assertNotIn(self.uid,self.p.round(rid)['roster'])
    async def test_pending_join_event_uses_existing_registry(self):
        from team_bot.onboarding import preflight
        from team_bot.bot import Adapter
        join=SimpleNamespace(chat=SimpleNamespace(id=self.b.c.chat),from_user=self.person)
        update=SimpleNamespace(update_id=990001,chat_join_request=join,effective_chat=join.chat)
        await preflight(Adapter(self.s),update,self.b.fixture.ctx)
        self.assertEqual(self.s.db.one('SELECT state FROM onboarding WHERE uid=?',(self.uid,))['state'],'pending_join');self.assertFalse(self.s.db.one('SELECT approved FROM employees WHERE uid=?',(self.uid,))['approved'])
        before=self.s.db.one('SELECT COUNT(*) n FROM history')['n'];await preflight(Adapter(self.s),update,self.b.fixture.ctx)
        self.assertEqual(before,self.s.db.one('SELECT COUNT(*) n FROM history')['n'])
    async def test_join_accepted_but_membership_lags_remains_pending(self):
        self.apply('left');self.s.db.run("UPDATE onboarding SET state='pending_join' WHERE uid=?",(self.uid,))
        self.b.api.bot.approve_chat_join_request=AsyncMock()
        with self.assertRaises(ValueError):await self.req('people/approve',{'target':self.uid})
        self.assertFalse(self.s.db.one('SELECT approved FROM employees WHERE uid=?',(self.uid,))['approved']);self.assertEqual(self.s.db.one('SELECT state FROM onboarding WHERE uid=?',(self.uid,))['state'],'pending_join')
        self.b.fixture.f.fixture.members[self.uid]=('member',self.person)
        await self.req('people/approve',{'target':self.uid});self.b.api.bot.approve_chat_join_request.assert_awaited_once()
    async def test_missing_username_remains_empty_display_identity(self):
        self.person=User(self.uid,'无用户名业务员',False);self.apply()
        row=next(x for x in (await self.req('people'))['rows'] if x['target']==self.uid)
        self.assertEqual(row['username'],'');self.assertEqual(row['name'],'无用户名业务员')
    async def test_concurrent_approval_is_one_business_approval(self):
        self.apply();gate=asyncio.Event();calls=0
        async def member(chat,uid):
            nonlocal calls
            calls+=1
            if calls==2:gate.set()
            await gate.wait()
            return SimpleNamespace(status='member',user=self.person)
        self.b.api.bot.get_chat_member=AsyncMock(side_effect=member)
        before=self.s.db.one('SELECT COUNT(*) n FROM history')['n']
        await asyncio.gather(self.req('people/approve',{'target':self.uid}),self.req('people/approve',{'target':self.uid}))
        self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM history')['n'],before+1)
    async def test_decline_join_failure_remains_pending_then_retry_rejects(self):
        self.apply('left');self.s.db.run("UPDATE onboarding SET state='pending_join' WHERE uid=?",(self.uid,))
        self.b.api.bot.decline_chat_join_request=AsyncMock(side_effect=RuntimeError('mock failure'))
        with self.assertRaises(ValueError):await self.req('people/reject',{'target':self.uid})
        self.assertEqual(self.s.db.one('SELECT state FROM onboarding WHERE uid=?',(self.uid,))['state'],'pending_join')
        self.b.api.bot.decline_chat_join_request.side_effect=None
        result=await self.req('people/reject',{'target':self.uid});self.assertTrue(result['ok'])
        self.assertEqual(self.s.db.one('SELECT state FROM onboarding WHERE uid=?',(self.uid,))['state'],'rejected')
