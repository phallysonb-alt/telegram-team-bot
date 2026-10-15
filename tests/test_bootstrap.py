import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from telegram import CallbackQuery
from team_bot.bot import Adapter
from team_bot.scheduler import Scheduler
from test_acceptance import Base, report, DEMO_DAY
import test_acceptance as fixtures


class ShiftConfigTests(Base):
    def test_midnight_stays_start_day_until_next_shift(self):
        for clock in ('2026-10-10T19:00', '2026-10-10T23:59', '2026-10-11T00:00', '2026-10-11T00:01'):
            now = datetime.fromisoformat(clock).replace(tzinfo=self.c.zone)
            self.assertEqual(self.c.business_date(now).isoformat(), '2026-10-10')
        now = datetime.fromisoformat('2026-10-11T12:00').replace(tzinfo=self.c.zone)
        self.assertEqual(self.c.business_date(now).isoformat(), '2026-10-11')

    def test_cross_day_deadline_and_unconfirmed_schedule(self):
        self.c.times.update(open='19:00', deadline='00:00', summary='00:15')
        self.c.offsets.update(deadline=1, summary=1)
        self.c.validate()
        self.assertEqual(self.c.event_time(datetime.fromisoformat(DEMO_DAY).date(), 'deadline').isoformat(), '2026-10-10T00:00:00+08:00')
        self.c.report_times_confirmed = False
        sch = Scheduler(self.s)
        with self.assertRaises(ValueError):
            sch.confirm(99999, self.c.timezone, ','.join(self.c.times.values()))
        self.assertFalse(sch.schedule_enabled())

    def test_unconfirmed_window_does_not_block_or_claim_late(self):
        uid = self.emp()
        self.c.report_times_confirmed = False
        self.now = self.now.replace(hour=15)
        d, _, _ = self.s.draft(uid, report(self.now.date().isoformat(), 1), 'test')
        receipt = self.s.confirm(uid, d)
        self.assertIn('暂不判定迟交', receipt)
        self.assertEqual(self.s.record(uid, self.now.date().isoformat())['late'], -1)

    def test_presets_do_not_invent_ids_or_admins(self):
        self.s.preset_personnel()
        self.assertEqual(len(self.s.db.all('SELECT * FROM personnel_slots')), 40)
        self.assertEqual(self.s.db.all('SELECT * FROM employees'), [])
        self.s.register(12345, 'G001')
        row = self.s.db.one('SELECT * FROM employees WHERE uid=12345')
        self.assertEqual((row['name'], row['approved'], row['required']), ('G001', 0, 0))
        self.assertFalse(self.s.admin(12345))
        self.s.approve(99999, 12345)
        self.assertFalse(self.s.admin(12345))


class BootstrapAndIsolationTests(Base, unittest.IsolatedAsyncioTestCase):
    make_update = fixtures.TelegramAdapterTests.make_update

    async def test_general_route_preserves_absent_thread_and_unbound_is_distinct(self):
        self.emp()
        self.c.threads['report'] = 'general'
        a = Adapter(self.s)
        update = self.make_update(91, '/whereami', thread=None)
        a.dispatch(update, 'whereami', [], '')
        a.route(update, 'report')
        a.scheduler.enqueue('general-test', 'report', '测试')
        row = self.s.db.one("SELECT * FROM outbox WHERE key='general-test:0'")
        self.assertIsNone(row['thread'])
        obs = self.s.db.setting(f'whereami:10001:{self.c.chat}:absent')
        self.assertIsNone(obs['thread'])
        self.assertFalse(obs['thread_field_present'])
        self.c.threads['report'] = 0
        with self.assertRaises(ValueError):
            a.scheduler.enqueue('unbound-test', 'report', '不得发送')

    async def test_bootstrap_collects_ids_but_first_user_gets_no_access(self):
        self.c.owner = 0
        self.c.chat = 0
        self.c.report_times_confirmed = False
        a = Adapter(self.s)
        ctx = SimpleNamespace(bot=SimpleNamespace(id=123))
        private = dict(id=10001, type='private', first_name='匿名')
        await a.handle(self.make_update(1, '/whereami', chat=private), ctx)
        await a.handle(self.make_update(2, '/register Y001', chat=private), ctx)
        group = dict(id=-100321, type='supergroup', title='测试群', is_forum=True)
        await a.handle(self.make_update(3, '/whereami@csg6gbot', chat=group, thread=44), ctx)
        self.assertEqual(self.s.db.all('SELECT * FROM employees'), [])
        self.assertFalse(self.s.admin(10001))
        self.assertEqual(self.c.owner, 0)
        obs = json.loads(self.s.db.one("SELECT value FROM settings WHERE key='whereami:10001:-100321:44'")['value'])
        self.assertEqual((obs['uid'], obs['chat'], obs['thread']), (10001, -100321, 44))
        self.assertFalse(Scheduler(self.s).schedule_enabled())

    async def test_test_registration_approval_progress_and_report_are_isolated(self):
        self.s.preset_personnel()
        a = Adapter(self.s)
        ctx = SimpleNamespace(bot=SimpleNamespace(id=123))
        uid = self.c.owner
        await a.handle(self.make_update(1, '/test_begin', uid=uid), ctx)
        await a.handle(self.make_update(2, '/register Y001', uid=uid), ctx)
        await a.handle(self.make_update(3, f'/approve {uid}', uid=uid), ctx)
        child = list(a.test_adapters.values())[0]
        await a.handle(self.make_update(4, f'日期：{DEMO_DAY}\n阶段：上半场\n精聊新增：100\n引流新增：2\n维聊新增：3', uid=uid, thread=11), ctx)
        raw = report(DEMO_DAY, 1).replace('员工编号：E001', '员工编号：Y001')
        await a.handle(self.make_update(5, raw, uid=uid), ctx)
        markup = json.loads(child.db.one("SELECT markup FROM outbox WHERE markup IS NOT NULL AND text LIKE '%请核对%'")['markup'])
        data = markup['inline_keyboard'][0][0]['callback_data']
        self.assertTrue(data.startswith('test:'))
        with patch.object(CallbackQuery, 'answer', new=AsyncMock()):
            await a.handle(self.make_update(6, callback=data, uid=uid), ctx)
        self.assertEqual(child.s.summary(DEMO_DAY)['totals']['精聊'], 2)
        self.assertEqual(self.s.db.all('SELECT * FROM records'), [])
        self.assertEqual(self.s.db.all('SELECT * FROM employees'), [])
        self.assertTrue(all(r['text'].startswith('【联调测试') for r in child.db.all('SELECT * FROM outbox')))
        await a.handle(self.make_update(7, '/test_end', uid=uid), ctx)
        # 测试 update 退出后重复投递不得写入正式数据。
        await a.handle(self.make_update(5, raw, uid=uid), ctx)
        self.assertEqual(self.s.db.all('SELECT * FROM records'), [])
        self.assertEqual(self.s.db.all('SELECT * FROM drafts'), [])
        with patch.object(CallbackQuery, 'answer', new=AsyncMock()) as answer:
            await a.handle(self.make_update(8, callback=data, uid=uid), ctx)
            self.assertTrue(answer.called)
        child.db.close()

    async def test_test_join_requires_explicit_admin_allow(self):
        a = Adapter(self.s)
        ctx = SimpleNamespace(bot=SimpleNamespace(id=123))
        await a.handle(self.make_update(1, '/test_begin', uid=99999), ctx)
        await a.handle(self.make_update(2, '/test_join', uid=10001), ctx)
        self.assertNotIn(10001, self.s.db.setting('live_test')['participants'])
        await a.handle(self.make_update(3, '/test_allow 10001', uid=99999), ctx)
        await a.handle(self.make_update(4, '/test_join', uid=10001), ctx)
        self.assertIn(10001, self.s.db.setting('live_test')['participants'])
        self.assertFalse(self.s.admin(10001))
        for child in a.test_adapters.values():
            child.db.close()
