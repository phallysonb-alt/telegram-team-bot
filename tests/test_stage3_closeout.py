"""Review closeout: fake cloud clocks and real disposable Windows processes.

No company configuration, tokens, business database or scheduler are accessed.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import patch
import test_company_setup as installer_fixture
import test_stage3_process as process_fixture
from team_bot.maintenance import InstanceLock
from team_bot.process_control import ProcessRef, stop_capability

setup = installer_fixture.setup
launcher = process_fixture.launcher


class ReadinessTests(unittest.TestCase):
    def wait(self, probe, limit=12):
        now = [0]
        def sleep(n): now[0] += n
        result = setup.wait_for_bridge(Path('unused'), limit, 3, probe,
                                       clock=lambda: now[0], sleep=sleep)
        return result, now[0]

    def test_slow_start_bridge_after_three_seconds_is_success(self):
        remaining = []
        def probe(left):
            remaining.append(left)
            return 1, len(remaining) >= 3
        result, elapsed = self.wait(probe)
        self.assertEqual(elapsed, 6)
        self.assertTrue(all(x['status'] == '通过' for x in result))
        self.assertEqual(remaining, [12, 9, 6])

    def test_running_but_bridge_delayed_is_pending_not_failed_install(self):
        result, elapsed = self.wait(lambda _: (1, False))
        self.assertEqual(elapsed, 12)
        self.assertEqual([x['status'] for x in result], ['通过', '待处理'])
        self.assertIn('不要重复安装', result[-1]['detail'])

    def test_real_start_failure_not_reported_as_pending_bridge(self):
        result, elapsed = self.wait(lambda _: (0, False))
        self.assertEqual(result[0]['status'], '失败')
        self.assertEqual(elapsed, 0)
        result, _ = self.wait(lambda _: (2, True))
        self.assertEqual(result[0]['status'], '失败')

    def test_probe_network_failure_does_not_claim_process_stopped_or_ready(self):
        def fail(_): raise setup.SetupError('模拟探测超时')
        result, elapsed = self.wait(fail)
        self.assertEqual(elapsed, 12)
        self.assertEqual([x['status'] for x in result], ['待处理', '待处理'])

    def test_post_start_d1_probe_uses_remaining_timeout_and_read_only_query(self):
        with patch.object(setup, 'processes', return_value=(1, 1)), \
             patch.object(setup, 'state', return_value=dict(database_name='isolated')), \
             patch.object(setup, 'wrangler', return_value=['node', 'wrangler']), \
             patch.object(setup, 'Runner') as runner:
            runner.return_value.return_value = json.dumps([dict(success=True, results=[dict(value=str(time.time()*1000))])])
            result = setup.wait_for_bridge(Path('unused'), timeout=4)
            args, kw = runner.return_value.call_args
            self.assertLessEqual(kw['timeout'], 4)
            self.assertIn('SELECT value FROM bridge_state', args[0][-1])
            self.assertTrue(all(x['status'] == '通过' for x in result))


class WizardReadinessTests(unittest.TestCase):
    setUp = installer_fixture.InstallerTests.setUp
    ready = installer_fixture.InstallerTests.ready
    transport = installer_fixture.InstallerTests.transport

    def prepare_bound(self):
        s = setup.state(self.root)
        s.update(origin='https://example.workers.dev', bot_id='3456789')
        s['steps']['cloud'] = True
        setup.save_state(self.root, s)
        self.v.update(BOT_USERNAME='company_example_bot', PROGRESS_MINIAPP_ORIGIN=s['origin'], PROGRESS_MINIAPP_BRIDGE='true')
        setup.attach_miniapp(self.root, self.v, 'work')
        setup.verify_topics(self.root, self.v, self.transport)

    def test_complete_wizard_waits_for_readiness_and_pending_resume_does_not_start_twice(self):
        self.prepare_bound()
        for count, status, expected in [(0, '待处理', 2), (1, '通过', 0)]:
            with self.subTest(count=count), \
                 patch.object(setup, 'verify_telegram', return_value=dict(username='company_example_bot')), \
                 patch.object(setup, 'diagnose', return_value=self.ready()), \
                 patch.object(setup, 'processes', return_value=(count, count)), \
                 patch.object(setup, 'wait_for_bridge', return_value=[dict(item='本地桥接', status=status, detail='模拟')]) as wait, \
                 patch.object(setup.subprocess, 'run') as run, \
                 patch('builtins.input', side_effect=['START','n']), patch('builtins.print'):
                self.assertEqual(setup.wizard(self.root), expected)
                self.assertEqual(run.call_count, 1 if count == 0 else 0)
                wait.assert_called_once_with(self.root)

    def test_start_command_failure_is_sanitized_not_reported_as_success(self):
        self.prepare_bound()
        with patch.object(setup, 'verify_telegram', return_value=dict(username='company_example_bot')), \
             patch.object(setup, 'diagnose', return_value=self.ready()), \
             patch.object(setup, 'processes', return_value=(0, 0)), \
             patch.object(setup.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['private-argv'])), \
             patch.object(setup, 'wait_for_bridge') as wait, \
             patch('builtins.input', return_value='START'), patch('builtins.print'):
            with self.assertRaisesRegex(setup.SetupError, '启动失败') as caught: setup.wizard(self.root)
            self.assertNotIn('private-argv', str(caught.exception)); wait.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows only')
class ProcessRaceTests(unittest.TestCase):
    setUp = process_fixture.Stage3ProcessTests.setUp
    cleanup_service = process_fixture.Stage3ProcessTests.cleanup_service
    invoke = process_fixture.Stage3ProcessTests.invoke
    start = process_fixture.Stage3ProcessTests.start

    def test_cim_timeout_sanitized_and_records_preserved(self):
        pid = self.data/'process.pid'; pid.write_text('99999999')
        with patch.object(launcher.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['private-command'], 25)):
            with self.assertRaisesRegex(RuntimeError, 'CIM.*超时') as caught:
                launcher.stop_bot(self.data, self.root)
            self.assertNotIn('private-command', str(caught.exception))
        self.assertEqual(pid.read_text(), '99999999')
        self.assertFalse((self.data/'autostart.stop').exists())

    def test_snapshot_exit_race_safe_but_permission_failure_not_swallowed(self):
        row = dict(ProcessId=99999999, Birth=1, ExecutablePath=str(self.root/'.venv/Scripts/pythonw.exe'),
                   ParentProcessId=1, CommandLine='pythonw -m team_bot run')
        with ExitStack() as stack, patch.object(launcher, 'process_type', return_value=lambda *a, **kw: (_ for _ in ()).throw(ProcessLookupError())):
            self.assertIsNone(launcher.checked_ref(row, stack))
        (self.data/'process.pid').write_text('99999999')
        with patch.object(launcher, 'snapshot', return_value=[row]):
            self.assertIn('已停止', launcher.stop_bot(self.data, self.root))
        self.assertFalse((self.data/'process.pid').exists())
        with ExitStack() as stack, patch.object(launcher, 'process_type', return_value=lambda *a, **kw: (_ for _ in ()).throw(PermissionError())):
            with self.assertRaisesRegex(RuntimeError, '权限或系统故障'): launcher.checked_ref(row, stack)

    def test_unknown_instance_lock_retains_stale_record(self):
        (self.data/'process.pid').write_text('99999999')
        with InstanceLock(self.data):
            with self.assertRaisesRegex(RuntimeError, '实例锁占用'): launcher.stop_bot(self.data, self.root)
            with self.assertRaisesRegex(RuntimeError, '实例锁占用'): launcher.upgrade_control(self.data, self.root)
        self.assertEqual((self.data/'process.pid').read_text(), '99999999')
        self.assertFalse((self.data/'upgrade-hold.json').exists())

    def test_reused_parent_pid_does_not_adopt_older_foreign_child_or_cycle(self):
        rootrow=dict(ProcessId=101,ParentProcessId=102,Birth=1000,ExecutablePath=str(self.root/'.venv/Scripts/pythonw.exe'),CommandLine='pythonw -m team_bot run')
        child=dict(ProcessId=102,ParentProcessId=101,Birth=2000,ExecutablePath=str(Path(getattr(process_fixture.sys,'_base_executable',process_fixture.sys.executable)).with_name('pythonw.exe')),CommandLine='pythonw -m team_bot run')
        foreign=dict(child,ProcessId=103,Birth=500)  # Predates the reused parent.
        chain,unknown=launcher.project_tree(rootrow,[rootrow,child,foreign],self.root)
        self.assertEqual([r['ProcessId'] for r in chain],[101,102]);self.assertFalse(unknown)

    def test_capability_is_bound_to_kernel_identity_not_numeric_pid(self):
        pid = self.start()
        cap = self.data/'process.stop-capability.json'
        original = cap.read_bytes(); value = json.loads(original); value['birth'] -= 1
        cap.write_text(json.dumps(value))
        refused = self.invoke('stop', timeout=0, force=True)
        self.assertNotEqual(refused.returncode, 0)
        with ProcessRef(pid) as ref: self.assertTrue(ref.alive())
        cap.write_bytes(original)
        self.assertEqual(self.invoke('stop').returncode, 0)
        self.assertFalse(cap.exists())

    def test_legacy_watcher_transition_hold_blocks_old_start_without_recovery(self):
        new_source = self.module.read_text(encoding='utf-8')
        # Old live code has no listener/capability even if its source is replaced.
        self.module.write_text(new_source.replace(',stop_capability(data,me)', '').replace(
            "while not stop_requested(data/'stop-request.json',me.pid,me.birth):time.sleep(.05)",
            "while True:time.sleep(.05)"), encoding='utf-8')
        pid = self.start()
        watcher = None
        try:
            refused = self.invoke('stop', force=True, timeout=0)
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((self.data/'stop-request.json').exists())
            self.assertEqual(self.invoke('upgrade-hold').returncode, 0)
            (self.data/'durable-pending.json').write_text('{"pending":["legacy-event"]}')
            # Actual PowerShell watcher, intentionally using the OLD call form.
            watch = self.root/'scripts/login-watch.ps1'
            watch.write_text("while (-not (Test-Path 'watcher-exit')) {\n"
                " if (Test-Path 'attempt-old-start') { try { & \"$PSScriptRoot/start.ps1\" } catch {} ; 'attempted' | Set-Content 'watcher-attempted'; Remove-Item 'attempt-old-start' }\n"
                " Start-Sleep -Milliseconds 100\n}\n", encoding='utf-8-sig')
            watcher = subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(watch)],
                cwd=self.root,env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**launcher.no_console_options())
            # Wait for watcher snapshot visibility before the guarded stop.
            deadline=time.monotonic()+10
            while not any(launcher.is_watcher(r,self.root) for r in launcher.snapshot()) and time.monotonic()<deadline: time.sleep(.1)
            self.assertNotEqual(self.invoke('stop',timeout=0,force=True,legacy_upgrade=True).returncode,0)
            (self.root/'attempt-old-start').touch()
            deadline=time.monotonic()+15
            while not (self.root/'watcher-attempted').exists() and time.monotonic()<deadline:time.sleep(.1)
            self.assertTrue((self.root/'watcher-attempted').exists())
            with ProcessRef(pid) as ref:self.assertTrue(ref.alive())
            (self.root/'watcher-exit').touch();watcher.wait(timeout=10)
            self.assertEqual(self.invoke('stop',timeout=0,force=True,legacy_upgrade=True).returncode,0)
            self.assertNotEqual(self.invoke('start',resume=True).returncode,0)  # hold survives
            self.assertEqual(self.invoke('upgrade-release').returncode,0)
            self.module.write_text(new_source,encoding='utf-8')
            self.start()
            self.assertEqual(json.loads((self.data/'durable-pending.json').read_text())['pending'],['legacy-event'])
            self.assertEqual(self.invoke('stop').returncode,0)
            # No -Recovery flag: the legacy form must not undo manual stop.
            self.assertNotEqual(self.invoke('start').returncode,0)
            self.assertEqual(self.invoke('start',resume=True).returncode,0)
        finally:
            if watcher and watcher.poll() is None:
                (self.root/'watcher-exit').touch();watcher.wait(timeout=10)
            # Only this disposable process may be explicitly forced in cleanup.
            if (self.data/'upgrade-hold.json').exists():
                self.invoke('stop',timeout=0,force=True,legacy_upgrade=True)
                self.invoke('upgrade-release')


if __name__ == '__main__': unittest.main()
