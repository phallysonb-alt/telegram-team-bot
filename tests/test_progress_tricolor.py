"""Signed isolated handler requests; no real Telegram employee impersonation."""
import json,unittest,asyncio
from test_progress_miniapp import MiniAppTests
from team_bot.progress_history_v2 import data

class TricolorTests(unittest.IsolatedAsyncioTestCase):
    tearDown=MiniAppTests.tearDown
    request=MiniAppTests.request
    def setUp(self):
        MiniAppTests.setUp(self)
        for t in (self.fixture.t,self.fixture.z):self.p.close(self.c.owner,t)
        self.t=self.p.publish(self.c.owner,dict(day='2026-10-10',title='同名模拟任务',kind='seq',stages=['一','二','三','四']))
        self.z=self.p.publish(self.c.owner,dict(day='2026-10-10',title='同名模拟任务',kind='free',stages=['甲','乙','丙','丁']))
        self.r=self.p.new_round(self.c.owner,'2026-10-10');self.keys=(str(self.t),str(self.z))
    async def send(self,rid,uid,a=0,b=0,note='',other=''):
        tasks=self.p.round(rid)['tasks'];values={str(t['id']):a if t['id']==self.t else b if t['id']==self.z else 1 for t in tasks}
        notes={str(t['id']):note if t['id']==self.t else other if t['id']==self.z else '其他模拟任务问题' for t in tasks}
        body=dict(key=f'tri_{rid}_{uid}',round=rid,masks=values,notes=notes)
        return await self.request('submit',uid,body)
    async def view(self,rid,pages=None):return await self.request('summary',self.c.owner,dict(round=rid,issue_pages=pages or {}))
    def next(self,task=None):return self.p.new_round(self.c.owner,'2026-10-10',task)

    async def test_three_round_colors_issues_and_return_to_first_second(self):
        k=str(self.t);await self.send(self.r,self.u,3,5,'第一轮\n完整原文');await self.send(self.r,self.v,15)
        one=await self.view(self.r);self.assertEqual(one['rows'][0]['stage_states'][k],['new','new','pending','pending'])
        self.assertEqual(one['issues'][k]['history']['count'],0);self.assertEqual(one['issues'][k]['current']['count'],1)
        r2=self.next();await self.send(r2,self.u,7,5,'第二轮\n新问题');two=await self.view(r2)
        self.assertEqual(two['rows'][0]['stage_states'][k],['history','history','new','pending'])
        self.assertEqual(two['rows'][1]['stage_states'][k],['history']*4);self.assertFalse(two['rows'][1]['submitted'])
        issue=two['issues'][k]['history']['records'][0];self.assertEqual((issue['uid'],issue['task'],issue['round'],issue['progress'],issue['text']),(self.u,self.t,self.r,2,'第一轮\n完整原文'))
        self.assertEqual(two['issues'][k]['current']['records'][0]['progress'],3)
        self.assertTrue(issue['query_at']);self.assertTrue(issue['submitted_at']);self.assertEqual(issue['round_number'],1)
        r3=self.next();three=await self.view(r3)
        self.assertEqual(three['received'],0);self.assertEqual(three['rows'][0]['stage_states'][k],['history']*3+['pending'])
        self.assertEqual(three['issues'][k]['history']['count'],2);self.assertEqual(three['issues'][k]['current']['count'],0)
        self.assertEqual((await self.view(r2))['rows'],two['rows']);self.assertEqual((await self.view(self.r))['issues'],one['issues'])

    async def test_zero_new_submission_with_new_problem_does_not_turn_blue(self):
        k=str(self.t);await self.send(self.r,self.u,3,note='旧问题');r2=self.next();await self.send(r2,self.u,3,note='本轮0新增，另一个问题')
        s=await self.view(r2);self.assertTrue(s['rows'][0]['submitted']);self.assertEqual(s['received'],1)
        self.assertEqual(s['rows'][0]['stage_states'][k],['history','history','pending','pending'])
        self.assertEqual(s['rows'][0]['added_masks'][k],0);self.assertEqual(s['issues'][k]['current']['count'],1)

    async def test_identical_title_tasks_free_stages_and_problems_are_isolated(self):
        await self.send(self.r,self.u,3,5,'顺序问题','自由问题');r2=self.next();s=await self.view(r2)
        self.assertEqual(s['rows'][0]['stage_states'][str(self.z)],['history','pending','history','pending'])
        self.assertEqual(s['issues'][str(self.t)]['history']['records'][0]['text'],'顺序问题')
        self.assertEqual(s['issues'][str(self.z)]['history']['records'][0]['text'],'自由问题')

    async def test_legacy_missing_delta_is_unknown_not_fabricated_blue(self):
        await self.send(self.r,self.u,3)
        self.p.db.run('DELETE FROM pg_feedback_delta WHERE round=?',(self.r,)) # only an isolated legacy-format fixture
        s=await self.view(self.r);k=str(self.t)
        self.assertEqual(s['rows'][0]['stage_states'][k],['unknown','unknown','pending','pending'])
        self.assertEqual(s['rows'][0]['uncertain_masks'][k],3);self.assertEqual(s['rows'][0]['added_masks'][k],0)
        self.assertEqual((await self.view(self.next()))['rows'][0]['stage_states'][k],['history','history','pending','pending'])

    async def test_invalid_delta_cannot_claim_new_stages(self):
        await self.send(self.r,self.u,3)
        self.p.db.run('UPDATE pg_feedback_delta SET added=15 WHERE round=? AND uid=? AND task=?',(self.r,self.u,self.t))
        s=await self.view(self.r);self.assertEqual(s['rows'][0]['stage_states'][str(self.t)],['unknown','unknown','pending','pending'])

    async def test_thirty_people_five_tasks_three_rounds_paged_history_complete(self):
        people=[self.u,self.v]+[self.fixture.f.approve(40000+i,'模拟人员'+str(i),'tri_mock_'+str(i)) for i in range(28)]
        for i in range(3):self.p.publish(self.c.owner,dict(day='2026-10-10',title='其他模拟任务'+str(i),kind='free',stages=['A','B','C']))
        r1=self.next()
        await asyncio.gather(*(self.send(r1,uid,3,5,'首轮人员'+str(uid)) for uid in people))
        r2=self.next();await asyncio.gather(*(self.send(r2,uid,7,5,'二轮人员'+str(uid)) for uid in people))
        r3=self.next();s=await self.view(r3);k=str(self.t)
        self.assertEqual((s['expected'],s['received'],len(s['tasks'])),(30,0,5))
        self.assertEqual([len(s['rows'][i:i+10]) for i in range(0,30,10)],[10,10,10])
        self.assertTrue(all(x['stage_states'][k]==['history']*3+['pending'] for x in s['rows']))
        self.assertEqual((s['issues'][k]['history']['count'],s['issues'][k]['history']['pages']),(60,12))
        seen=[]
        for page in range(1,13):
            await asyncio.sleep(.12) # honor the real 12 requests/second guard
            seen.extend((await self.view(r3,{k:{'history':page}}))['issues'][k]['history']['records'])
        self.assertEqual(len({x['id'] for x in seen}),60);self.assertEqual(len(seen),60)
        self.assertTrue(all(x['text'].startswith(('首轮','二轮')) for x in seen))

    async def test_long_original_duplicate_and_refresh_do_not_duplicate_history(self):
        raw=('<原文 & 中文😀>\n'*150)+'最终一行';await self.send(self.r,self.u,3,note=raw)
        before=self.p.db.all('SELECT * FROM pg_audit');await self.send(self.r,self.u,3,note=raw)
        r2=self.next();s=await self.view(r2);issue=s['issues'][str(self.t)]['history']['records'][0]
        self.assertEqual(issue['text'],raw);self.assertEqual(s['issues'][str(self.t)]['history']['count'],1)
        self.assertEqual((await self.view(r2))['issues'],s['issues']);self.assertEqual(self.api.task_notes(self.r,self.u)[str(self.t)],raw)
        self.assertEqual(len(before)+1,len(self.p.db.all('SELECT * FROM pg_audit'))) # only the next query was added

    async def test_departed_employee_problem_survives_frozen_previous_roster(self):
        await self.send(self.r,self.u,3,note='离开前的问题');self.s.db.run('UPDATE employees SET active=0 WHERE uid=?',(self.u,))
        r2=self.next();s=await self.view(r2)
        self.assertEqual(s['expected'],1);self.assertEqual(s['issues'][str(self.t)]['history']['records'][0]['uid'],self.u)
        self.assertEqual(self.p.round(self.r)['roster'],[self.u,self.v])

    async def test_legacy_global_note_requires_reliable_task_association(self):
        await self.send(self.r,self.u,3)
        self.p.db.run('DELETE FROM pg_task_notes WHERE round=?',(self.r,))
        self.p.db.run('INSERT INTO pg_notes VALUES (?,?,?,?,?)',(self.r,self.u,'旧版全局原文',1,self.p.now()))
        s=await self.view(self.r);self.assertEqual(s['issues'][str(self.t)]['current']['count'],0)
        self.assertEqual(s['issues'][str(self.t)]['unscoped_count'],1)
        rid=self.next(self.t);await self.send(rid,self.u,3)
        self.p.db.run('DELETE FROM pg_task_notes WHERE round=?',(rid,))
        self.p.db.run('INSERT INTO pg_notes VALUES (?,?,?,?,?)',(rid,self.u,'单任务旧原文',1,self.p.now()))
        self.assertEqual((await self.view(rid))['issues'][str(self.t)]['current']['records'][0]['text'],'单任务旧原文')

    async def test_prior_archive_saved_after_selected_query_is_not_backdated(self):
        await self.send(self.r,self.u,3,note='后来修改的旧文');rid=self.next()
        self.p.db.run("UPDATE pg_feedback SET at='2026-10-12T12:00:00+08:00' WHERE round=?",(self.r,))
        s=await self.view(rid);self.assertEqual(s['issues'][str(self.t)]['history']['count'],0)
        self.assertEqual(s['issues'][str(self.t)]['time_unknown_count'],1)
        self.assertEqual(s['rows'][0]['cumulative_masks'],{})

    async def test_projection_read_only_export_daily_audit_and_permissions(self):
        await self.send(self.r,self.u,3,5,'文本','独立文本');tables=['pg_feedback','pg_task_notes','pg_cumulative','pg_audit','pg_feedback_delta','pg_rounds','pg_roster_names']
        before={t:self.p.db.all('SELECT * FROM '+t) for t in tables};excel=data(self.p);daily=self.s.db.all('SELECT * FROM daily_records')
        for _ in range(3):await self.view(self.r)
        self.assertEqual(before,{t:self.p.db.all('SELECT * FROM '+t) for t in tables});self.assertEqual(data(self.p),excel);self.assertEqual(self.s.db.all('SELECT * FROM daily_records'),daily)
        with self.assertRaises(PermissionError):await self.request('summary',self.u,dict(round=self.r))
        for invalid in ({str(self.t):{'history':0}},{'999999':{'history':1}},{str(self.t):{'history':True}}):
            with self.assertRaises(ValueError):await self.view(self.r,invalid)
