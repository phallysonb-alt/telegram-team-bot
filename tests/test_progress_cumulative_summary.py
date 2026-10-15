"""Isolated signed API requests; no real employee submissions or Telegram calls."""
import json,unittest
from team_bot.progress_rounds import Progress
from team_bot.progress_miniapp import MiniAPI
from test_progress_miniapp import MiniAppTests

class CumulativeSummaryTests(unittest.IsolatedAsyncioTestCase):
    setUp=MiniAppTests.setUp
    tearDown=MiniAppTests.tearDown
    request=MiniAppTests.request
    body=MiniAppTests.body

    async def send(self,uid,rid,seq,free=0,note=''):
        return await self.request('submit',uid,self.body('round_%s_user_%s'%(rid,uid),rid,{str(self.fixture.t):seq,str(self.fixture.z):free},{str(self.fixture.t):note,str(self.fixture.z):''}))

    async def view(self,rid):return await self.request('summary',self.c.owner,dict(round=rid))

    async def test_three_rounds_current_counts_cumulative_and_historical_snapshots(self):
        await self.send(self.u,self.r,3,note='第一轮当前问题\n完整保存')
        await self.send(self.v,self.r,7)
        first=await self.view(self.r)
        second=self.p.new_round(self.c.owner,'2026-10-10')
        partial=await self.view(second)
        self.assertEqual(partial['received'],0)
        self.assertEqual(partial['rows'][0]['cumulative_masks'][str(self.fixture.t)],3)
        self.assertEqual(partial['rows'][1]['cumulative_masks'][str(self.fixture.t)],7)
        self.assertEqual(partial['cumulative_completed'][str(self.fixture.t)],1)
        self.assertEqual(partial['rows'][0]['notes'],{})
        await self.send(self.u,second,7)
        two=await self.view(second)
        self.assertEqual(two['received'],1);self.assertEqual(two['cumulative_completed'][str(self.fixture.t)],2)
        self.assertEqual(two['rows'][0]['added_masks'][str(self.fixture.t)],4)
        self.assertEqual(two['rows'][1]['added_masks'],{})
        self.assertFalse(two['rows'][1]['submitted']);self.assertEqual(two['rows'][1]['masks'],{})
        third=self.p.new_round(self.c.owner,'2026-10-10');three=await self.view(third)
        self.assertEqual(three['received'],0);self.assertEqual(three['expected'],2)
        self.assertEqual(three['cumulative_completed'][str(self.fixture.t)],2)
        historical=await self.view(self.r)
        # Current signed account names may update between requests; historical
        # progress/feedback must remain identical independently of that rename.
        progress=lambda rows:[{k:v for k,v in row.items() if k not in ('name','username')} for row in rows]
        self.assertEqual(progress(historical['rows']),progress(first['rows']));self.assertEqual(historical['rows'][0]['cumulative_masks'][str(self.fixture.t)],3)
        self.assertEqual(self.api.task_notes(self.r,self.u)[str(self.fixture.t)],'第一轮当前问题\n完整保存')

    async def test_zero_added_completed_submission_counts_current_feedback(self):
        await self.send(self.u,self.r,7)
        newer=self.p.new_round(self.c.owner,'2026-10-10')
        await self.send(self.u,newer,7)
        result=await self.view(newer)
        self.assertEqual(result['received'],1);self.assertEqual(result['cumulative_completed'][str(self.fixture.t)],1)
        self.assertEqual(self.p.db.one('SELECT added FROM pg_feedback_delta WHERE round=? AND uid=? AND task=?',(newer,self.u,self.fixture.t))['added'],0)
        saved=self.p.feedback(newer,self.u)
        await self.send(self.u,newer,7)
        self.assertEqual(saved,self.p.feedback(newer,self.u))

    async def test_explicit_zero_is_record_not_unanswered(self):
        await self.send(self.u,self.r,0)
        newer=self.p.new_round(self.c.owner,'2026-10-10');result=await self.view(newer)
        self.assertEqual(result['rows'][0]['cumulative_masks'],{str(self.fixture.t):0,str(self.fixture.z):0})
        self.assertEqual(result['rows'][1]['cumulative_masks'],{})
        self.assertEqual(result['received'],0)

    async def test_task_identity_and_version_are_isolated(self):
        await self.send(self.u,self.r,7,5)
        same=self.p.publish(self.c.owner,dict(day='2026-10-10',title='养号步骤',kind='seq',stages=['账户整体异常检查','制定作品发布','指定评论点赞数量完成']))
        newer=self.p.new_round(self.c.owner,'2026-10-10');result=await self.view(newer)
        self.assertNotIn(str(same),result['rows'][0]['cumulative_masks'])
        self.assertEqual(result['cumulative_completed'][str(same)],0)
        self.assertEqual(result['rows'][0]['cumulative_masks'][str(self.fixture.z)],5)
        unused=self.p.publish(self.c.owner,dict(day='2026-10-10',title='未用版本任务',kind='free',stages=['一','二','三']))
        old=self.p.new_round(self.c.owner,'2026-10-10',unused)
        self.p.publish(self.c.owner,dict(day='2026-10-10',title='调整未用版本',kind='seq',stages=['甲','乙','丙']),unused)
        updated=self.p.new_round(self.c.owner,'2026-10-10',unused)
        self.assertEqual((await self.view(old))['tasks'][0]['version'],1)
        self.assertEqual((await self.view(updated))['tasks'][0]['version'],2)
        self.assertEqual((await self.view(updated))['rows'][0]['cumulative_masks'],{})

    async def test_thirty_people_multiround_frozen_roster_and_pagination(self):
        people=[self.u,self.v]+[self.fixture.f.approve(20000+i,'模拟员工%02d'%i,'mock_%02d'%i) for i in range(28)]
        first=self.p.new_round(self.c.owner,'2026-10-10',roster=people,simulated=True)
        for i,uid in enumerate(people):await self.send(uid,first,7 if i%2==0 else 3,5 if i%3==0 else 0)
        newer=self.p.new_round(self.c.owner,'2026-10-10',roster=people,simulated=True)
        result=await self.view(newer)
        self.assertEqual(result['received'],0);self.assertEqual(result['expected'],30)
        self.assertEqual(result['cumulative_completed'][str(self.fixture.t)],15)
        self.assertEqual([len(result['rows'][i:i+10]) for i in range(0,30,10)],[10,10,10])
        self.assertEqual((await self.view(first))['expected'],30)

    async def test_new_employee_not_added_to_old_round_roster(self):
        new_uid=self.fixture.f.approve(88888,'后来新增员工','new_person')
        self.assertEqual((await self.view(self.r))['expected'],2)
        latest=self.p.new_round(self.c.owner,'2026-10-10')
        self.assertIn(new_uid,self.p.round(latest)['roster']);self.assertNotIn(new_uid,self.p.round(self.r)['roster'])

    async def test_summary_is_read_only_preserves_audit_history_and_daily(self):
        await self.send(self.u,self.r,3,5,'旧问题\n保留')
        tables=('pg_feedback','pg_task_notes','pg_cumulative','pg_feedback_delta','pg_audit','pg_roster_names')
        before={t:self.p.db.all('SELECT * FROM '+t) for t in tables}
        daily=self.s.db.all('SELECT * FROM daily_records');export=self.p.export(self.c.owner)
        for _ in range(3):await self.view(self.r)
        self.assertEqual(before,{t:self.p.db.all('SELECT * FROM '+t) for t in tables})
        self.assertEqual(daily,self.s.db.all('SELECT * FROM daily_records'))
        self.assertEqual(export,self.p.export(self.c.owner))

    async def test_restart_permissions_and_late_callbacks_remain_unchanged(self):
        await self.send(self.u,self.r,3);newer=self.p.new_round(self.c.owner,'2026-10-10')
        self.p.db.conn.close();self.p=Progress(self.s);self.fixture.p=self.p;self.api=MiniAPI(self.p,self.fixture.ctx.bot,self.membership)
        self.assertEqual((await self.view(newer))['rows'][0]['cumulative_masks'][str(self.fixture.t)],3)
        with self.assertRaises(PermissionError):await self.request('summary',self.u,dict(round=newer))
        with self.assertRaisesRegex(ValueError,'旧轮'):await self.request('submit',self.u,self.body('late_old_round',self.r))
        with self.assertRaises(PermissionError):await self.request('submit',self.u,dict(self.body('fake_identity',newer),uid=self.v))

if __name__=='__main__':unittest.main()
