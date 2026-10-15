"""Synthetic callbacks via production Adapter.handle; no Telegram network or live data."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
import test_workbench as fixtures
from team_bot.workbench import Workbench
from team_bot.bot import Adapter

class HandlerAcceptance(fixtures.DailyBase,unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  super().setUp();self.u=self.emp();self.clock('2026-10-10T12:00:00');self.a=Adapter(self.s);self.ctx=SimpleNamespace(bot=SimpleNamespace(id=123));self.private={'id':self.u,'type':'private'}
 make_update=fixtures.WorkbenchUITests.make_update
 handle=fixtures.WorkbenchUITests.handle
 async def card(self,uid,n):
  await self.handle(n,callback='wb:mine',uid=uid,chat={'id':uid,'type':'private'})
  # Transport fixture: delivery assigned message_id=50, as in synthetic Update.
  # No event or progress table is populated directly.
  self.s.db.run("update wb_cards set message_id=50 where kind='mine' and viewer=?",(uid,))
 async def test_all_thirty_changes_through_handler(self):
  users=[self.u]+[self.emp(i) for i in range(2,31)]
  w=Workbench(self.s);n=1000
  for uid in users:
   n+=1;await self.card(uid,n)
   n+=1;await self.handle(n,callback='wb:add:2026-10-10:1:1',uid=uid,chat={'id':uid,'type':'private'})
   self.now+=timedelta(seconds=3)
   n+=1;await self.handle(n,callback='wb:complete:2026-10-10:3:1',uid=uid,chat={'id':uid,'type':'private'})
  self.assertEqual(w.summary('2026-10-10')['totals'],[30,0,30])
  self.assertEqual(self.s.db.one('select count(*) n from wb_events')['n'],60)
  before=self.s.db.one('select count(*) n from wb_events')['n']
  await self.handle(3000,callback='wb:add:2026-10-10:1:1',uid=users[1],chat={'id':users[0],'type':'private'})
  self.assertEqual(self.s.db.one('select count(*) n from wb_events')['n'],before)
 async def test_boundary_undo_period_repair_through_handler(self):
  await self.card(self.u,1000)
  self.clock('2026-10-10T13:59:59');await self.handle(1001,callback='wb:add:2026-10-10:1:1')
  self.clock('2026-10-10T14:00:00');await self.handle(1002,callback='wb:add:2026-10-10:1:1')
  # One-second second click is throttled, not entered into another period.
  w=Workbench(self.s);self.assertEqual(w.amount(self.u,1,'2026-10-10'),1)
  self.clock('2026-10-10T14:00:02');await self.handle(1003,callback='wb:add:2026-10-10:1:1')
  self.assertEqual([w.amount(self.u,1,'2026-10-10',i) for i in [0,1]],[1,1])
  self.clock('2026-10-10T15:00:00');await self.handle(1004,callback='wb:undo:2026-10-10:1:1')
  self.clock('2026-10-10T15:00:03');await self.handle(1005,callback='wb:undo:2026-10-10:1:1')
  self.assertEqual(w.amount(self.u,1,'2026-10-10'),0)
  self.assertEqual(w.amount(self.u,1,'2026-10-10',1),0)
  self.assertEqual(self.s.db.one('select revised from wb_snapshots order by version desc limit 1')['revised'],1)
  self.clock('2026-10-11T00:00:00');await self.handle(1006,callback='wb:add:2026-10-10:1:1')
  self.assertEqual(w.amount(self.u,1,'2026-10-10'),0)
 async def test_overshoot_zero_undo_duplicate_and_daily_unchanged_handler(self):
  self.submit(self.u,(5,2,3,4));before=dict(self.s.record(self.u,'2026-10-10'))
  await self.card(self.u,1000)
  await self.handle(1001,callback='wb:zero:2026-10-10:2:1')
  self.assertEqual(Workbench(self.s).state(self.u,Workbench(self.s).tasks(self.u,'2026-10-10')[1],'2026-10-10')['status'],'明确为0')
  for i in range(4):
   self.now+=timedelta(seconds=3);await self.handle(1002+i,callback='wb:add:2026-10-10:1:1')
  await self.handle(1005,callback='wb:add:2026-10-10:1:1')
  self.assertEqual(Workbench(self.s).amount(self.u,1,'2026-10-10'),4)
  self.now+=timedelta(seconds=3);await self.handle(1006,callback='wb:undo:2026-10-10:1:1')
  self.assertEqual(Workbench(self.s).amount(self.u,1,'2026-10-10'),3)
  self.assertEqual(self.s.record(self.u,'2026-10-10'),before)

if __name__=='__main__':unittest.main()
