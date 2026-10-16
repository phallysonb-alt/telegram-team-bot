"""Stage-one transport failures, using fake Telegram and temporary databases only."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_progress_miniapp as fixtures
from telegram import User
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
from team_bot.bridge_diagnostics import MemberCheckUnavailable, emit
from team_bot.miniapp_fast_bridge import BridgeHealth, http_delay, process
from team_bot.progress_miniapp import MiniAPI, request_timing


class StabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixtures.MiniAppTests(); self.f.setUp()
        self.p, self.s = self.f.p, self.f.s
        self.bot = SimpleNamespace(get_chat_member=AsyncMock())
        self.api = MiniAPI(self.p, self.bot)  # Real authorize/reconcile, no membership bypass.
        self.member = SimpleNamespace(user=User(self.f.u, '模拟员工', False), status='member')
        self.bot.get_chat_member.return_value = self.member

    def tearDown(self):
        self.f.tearDown()

    def job(self):
        return dict(id='isolated-job', path='/api/submit', claimed_at=123,
                    envelope=dict(initData=fixtures.signed(self.f.c.token, self.f.u), body=self.f.body()))

    async def test_lookup_unavailable_is_503_fail_closed_and_redacted(self):
        for error in (TimedOut('private-timeout'), NetworkError('private-network'),
                      Forbidden('private-forbidden'), BadRequest('private-bad'),
                      RetryAfter(1), TimeoutError('private-timeout'), RuntimeError('private-internal')):
            with self.subTest(kind=type(error).__name__):
                before = self.s.db.conn.total_changes
                self.api.limits.clear(); self.bot.get_chat_member.side_effect = error
                with self.assertLogs('team_bot.bridge', 'INFO') as log:
                    result = await process(self.api, self.job())
                self.assertEqual(result['status'], 503)
                self.assertEqual(result['body']['code'], 'membership_unavailable')
                self.assertTrue(result['body']['retryable'])
                self.assertNotIn('private-', str(result) + ''.join(log.output))
                self.assertEqual(self.s.db.conn.total_changes, before)
                self.assertIsNone(self.p.feedback(self.f.r, self.f.u))
                self.assertIsNone(request_timing.get())

    async def test_verified_nonmember_is_403_not_temporary_failure(self):
        self.bot.get_chat_member.return_value = SimpleNamespace(user=self.member.user, status='left')
        result = await process(self.api, self.job())
        self.assertEqual(result['status'], 403)
        self.assertIsNone(self.p.feedback(self.f.r, self.f.u))
        self.assertFalse(self.s.db.one('SELECT active FROM employees WHERE uid=?', (self.f.u,))['active'])

    async def test_verified_other_identity_is_403_without_save(self):
        self.bot.get_chat_member.return_value = SimpleNamespace(user=User(self.f.v, '同名', False), status='member')
        result = await process(self.api, self.job())
        self.assertEqual(result['status'], 403)
        self.assertIsNone(self.p.feedback(self.f.r, self.f.u))

    async def test_explicit_disable_and_nonadmin_remain_denied(self):
        self.s.change_employee(self.f.c.owner, self.f.u, '模拟管理员停用', active=0)
        self.s.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)', (self.f.u, 'disabled', '模拟停用', self.p.now()))
        self.assertEqual((await process(self.api, self.job()))['status'], 403)
        self.assertIsNone(self.p.feedback(self.f.r, self.f.u))

    async def test_unavailable_then_retry_duplicate_and_restart_save_once(self):
        from team_bot.progress_rounds import Progress
        job = self.job(); self.bot.get_chat_member.side_effect = TimedOut()
        self.assertEqual((await process(self.api, job))['status'], 503)
        self.bot.get_chat_member.side_effect = None
        first = await process(self.api, job); second = await process(self.api, job)
        self.assertEqual(first['status'], 200); self.assertEqual(first['body'], second['body'])
        self.p.db.conn.close(); self.p = Progress(self.s); self.f.p = self.p; self.f.fixture.p = self.p
        self.api = MiniAPI(self.p, self.bot)
        self.assertEqual((await process(self.api, job))['body'], first['body'])
        self.assertEqual(self.p.feedback(self.f.r, self.f.u)['version'], 1)
        self.assertEqual(self.p.db.one("SELECT COUNT(*) n FROM pg_audit WHERE entity='反馈'")['n'], 1)
        self.assertEqual(self.s.db.one('SELECT COUNT(*) n FROM daily_records')['n'], 0)

    async def test_metrics_failure_after_commit_does_not_report_failure_or_duplicate(self):
        original = self.p.db.set
        def set_value(key, value):
            if key.startswith('app:timing:'): raise OSError('private-metrics-storage')
            return original(key, value)
        with patch.object(self.p.db, 'set', side_effect=set_value), self.assertLogs('team_bot.bridge', 'INFO') as log:
            one = await process(self.api, self.job()); two = await process(self.api, self.job())
        self.assertEqual(one['status'], 200); self.assertEqual(two['body'], one['body'])
        self.assertEqual(self.p.feedback(self.f.r, self.f.u)['version'], 1)
        self.assertIn('timing_write_failed', ''.join(log.output))
        self.assertNotIn('private-', ''.join(log.output))

    async def test_cancel_is_not_converted_to_permission_or_success(self):
        self.bot.get_chat_member.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await process(self.api, self.job())
        self.assertIsNone(self.p.feedback(self.f.r, self.f.u))
        self.assertIsNone(request_timing.get())

    async def test_optional_local_gateway_uses_same_permission_classification(self):
        import http.client
        from team_bot.miniapp_server import start_server
        api = SimpleNamespace(dispatch=AsyncMock())
        server = start_server(api, asyncio.get_running_loop(), port=0)
        def request():
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                client.request('POST', '/api/summary', '{}', {'Content-Type': 'application/json'})
                reply = client.getresponse(); return reply.status, json.loads(reply.read())
            finally: client.close()
        try:
            for error, status in ((MemberCheckUnavailable(), 503), (PermissionError('禁止操作'), 403), (RuntimeError('private-storage'), 503)):
                api.dispatch.side_effect = error
                actual, body = await asyncio.to_thread(request)
                self.assertEqual(actual, status); self.assertNotIn('private-storage', str(body))
        finally:
            await asyncio.to_thread(server.shutdown); server.server_close()

    def test_diagnostics_reject_arbitrary_values(self):
        with self.assertLogs('team_bot.bridge', 'INFO') as log:
            emit('request_completed', endpoint='/api/summary', status=503, local_ms=1.234,
                 secret='private-credential', uid='private-identity', reason='private-exception',
                 mode='https://private.invalid', pending=float('inf'))
            emit('private-event', reason='network')
        self.assertEqual(len(log.output), 1)
        data = json.loads(log.output[0].split('bridge ', 1)[1])
        self.assertEqual(data, dict(event='request_completed', endpoint='/api/summary', status=503, local_ms=1.23))

    def test_mode_recovery_duration_is_bounded_and_no_payloads(self):
        with patch('team_bot.miniapp_fast_bridge.time.monotonic', side_effect=[10, 12]), self.assertLogs('team_bot.bridge', 'INFO') as log:
            health = BridgeHealth(); health.connected('WebSocket')
            health.disconnected('timeout'); health.disconnected('network')
            health.connected('HTTP兼容', 1); health.connected('HTTP兼容')
        events = [json.loads(x.split('bridge ', 1)[1]) for x in log.output]
        self.assertEqual(sum(x['event'] == 'disconnected' for x in events), 1)
        connected = [x for x in events if x['event'] == 'connected']
        self.assertEqual(connected[-1]['outage_ms'], 2000)
        self.assertEqual(connected[-1]['pending'], 1)

    def test_http_idle_delay_has_small_cap_and_busy_reset(self):
        delay = .05
        for _ in range(100):
            delay = http_delay(delay, False); self.assertLessEqual(delay, .5)
        self.assertEqual(delay, .5)
        self.assertEqual(http_delay(delay, True), .05)


if __name__ == '__main__': unittest.main()
