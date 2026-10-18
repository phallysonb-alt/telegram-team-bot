"""Independent installer acceptance: fake companies/cloud, never Telegram polling."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts'))
import company_setup as setup


def token():
    return str(3456789) + ':' + ('mock' * 9)


class FakeCloud:
    def __init__(self, fail=None):
        self.env = {}; self.calls = []; self.exists = False; self.fail = fail

    def __call__(self, argv, *, input=None, **kw):
        self.calls.append((argv, input))
        if self.fail and self.fail in argv: raise setup.SetupError('模拟网络失败，可重试')
        if 'list' in argv:
            name = next((a[-1] for a, _ in self.calls if 'create' in a), '')
            return json.dumps([dict(name=name, uuid='a' * 8 + '-' + 'b' * 4 + '-' + 'c' * 4 + '-' + 'd' * 4 + '-' + 'e' * 12)] if self.exists else [])
        if 'create' in argv: self.exists = True
        if 'deploy' in argv:
            config = Path(argv[-1]).read_text(encoding='utf-8')
            name = config.split('"')[1]
            return 'Deployed https://' + name + '.example.workers.dev'
        return '[{"results":[]}]'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='company-installer-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'company'
        self.root.mkdir()
        setup.save_state(self.root, dict(schema=1, installation='a' * 24, steps={}, manifest={}))
        shutil.copyfile(SOURCE / '.env.example', self.root / '.env.example')
        (self.root / 'deploy').mkdir()
        shutil.copyfile(SOURCE / 'deploy/0000_miniapp_bridge.sql', self.root / 'deploy/0000_miniapp_bridge.sql')
        answers = iter(['4567890', '-1001234567890', '4', '2'])
        self.v = setup.configure(self.root, lambda _: next(answers), lambda _: token())
        self.transport_calls = []

    def transport(self, url, payload=None, headers=None):
        self.transport_calls.append((url.split('/')[-1], payload))
        if url.endswith('getMe'):
            result = dict(id=3456789, username='company_example_bot', is_bot=True, can_read_all_group_messages=True)
        elif url.endswith('getChat'):
            result = dict(id=-1001234567890, is_forum=True)
        elif url.endswith('getChatMember'):
            result = dict(status='administrator', can_pin_messages=True, can_invite_users=True)
        elif url.endswith('sendMessage'):
            result = dict(message_id=1, message_thread_id=payload['message_thread_id'], chat=dict(id=-1001234567890))
        elif url.endswith('getWebhookInfo'):
            result = dict(url='')
        elif url.endswith('/health'): return 200, dict(ok=True)
        elif url.endswith('/bridge/socket'): return 426, {}
        else: raise AssertionError('unexpected endpoint: ' + url.split('/')[-1])
        return 200, dict(ok=True, result=result)

    def test_prepare_fresh_resume_complete_manifest_no_company_files(self):
        target = Path(self.temp.name) / 'fresh'
        setup.prepare(SOURCE, target)
        self.assertTrue((target / 'miniapp/app.js').is_file())
        self.assertTrue((target / 'setup-company.cmd').is_file())
        self.assertFalse((target / '.env').exists())
        self.assertFalse((target / 'data').exists())
        self.assertFalse((target / '.git').exists())
        before = (target / setup.MARKER).read_bytes()
        # Resume after interrupted file copy, without touching configuration/data.
        (target / 'miniapp/app.js').unlink()
        (target / '.env').write_text('private company config', encoding='utf-8')
        setup.prepare(SOURCE, target)
        self.assertTrue((target / 'miniapp/app.js').is_file())
        self.assertEqual((target / '.env').read_text(), 'private company config')
        self.assertEqual((target / setup.MARKER).read_bytes(), before)
        (target / 'README.md').write_text('local edit', encoding='utf-8')
        with self.assertRaises(setup.SetupError): setup.prepare(SOURCE, target)
        self.assertEqual((target / 'README.md').read_text(), 'local edit')

    def test_prepare_rejects_live_source_overlap_and_existing_unknown_directory(self):
        for target in (SOURCE, SOURCE / 'new-company', SOURCE.parent):
            with self.assertRaises(setup.SetupError): setup.prepare(SOURCE, target)
        target = Path(self.temp.name) / 'unowned'; target.mkdir()
        (target / '.env').write_text('keep', encoding='utf-8')
        with self.assertRaises(setup.SetupError): setup.prepare(SOURCE, target)
        self.assertEqual((target / '.env').read_text(), 'keep')

    def test_independent_keys_safe_defaults_existing_config_not_overwritten(self):
        other = Path(self.temp.name) / 'another'; other.mkdir()
        setup.save_state(other, dict(schema=1, installation='b' * 24, steps={}, manifest={}))
        shutil.copyfile(SOURCE / '.env.example', other / '.env.example')
        answers = iter(['4567890', '-1001234567890', '4', '2'])
        values = setup.configure(other, lambda _: next(answers), lambda _: token())
        self.assertNotEqual(values['PROGRESS_BRIDGE_SECRET'], self.v['PROGRESS_BRIDGE_SECRET'])
        self.assertEqual(values['RUN_MODE'], 'trial')
        self.assertEqual(values['SCHEDULE_ENABLED'], 'false')
        self.assertEqual(values['AI_ENABLED'], 'false')
        before = (self.root / '.env').read_bytes()
        setup.configure(self.root, lambda _: self.fail('must not prompt'), lambda _: self.fail('must not ask token'))
        self.assertEqual(before, (self.root / '.env').read_bytes())

    def test_invalid_configs_fail_closed_without_echoing_values(self):
        for key, value in [('OWNER_USER_ID', '@someone'), ('REPORT_THREAD_ID', '0'),
                           ('REPORT_THREAD_ID', '2'), ('DATA_DIR', '../other-company'),
                           ('RUN_MODE', 'production'), ('SCHEDULE_ENABLED', 'true'),
                           ('TELEGRAM_BOT_TOKEN', 'bad-token')]:
            with self.subTest(key=key):
                v = dict(self.v, **{key: value})
                with self.assertRaises(setup.SetupError) as e: setup.validate_config(v)
                self.assertNotIn(token(), str(e.exception))

    def test_initialization_empty_preserves_records_on_rerun_and_isolation(self):
        setup.initialize(self.root, self.v)
        data = self.root / self.v['DATA_DIR']
        with contextlib.closing(sqlite3.connect(data / 'team.sqlite3', isolation_level=None)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 0)
            db.execute("INSERT INTO settings(key,value) VALUES ('installer-test','\"kept\"')")
        with contextlib.closing(sqlite3.connect(data / 'progress-acceptance.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pg_tasks').fetchone()[0], 0)
        setup.initialize(self.root, self.v)
        with contextlib.closing(sqlite3.connect(data / 'team.sqlite3', isolation_level=None)) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='installer-test'").fetchone()[0], '"kept"')
        self.assertEqual(setup.db_check(self.root, self.v), 0)

    def test_telegram_bot_group_owner_permissions_with_no_polling(self):
        self.assertEqual(setup.verify_telegram(self.v, self.transport)['id'], 3456789)
        self.assertEqual([m for m, _ in self.transport_calls], ['getMe', 'getChat', 'getChatMember', 'getChatMember', 'getWebhookInfo'])
        def forbidden(url, payload=None, headers=None):
            code, data = self.transport(url, payload, headers)
            if url.endswith('getChatMember'): data['result']['status'] = 'left'
            return code, data
        with self.assertRaises(setup.SetupError): setup.verify_telegram(self.v, forbidden)
        def wrong_bot(url, payload=None, headers=None):
            code, data = self.transport(url, payload, headers)
            if url.endswith('getMe'): data['result']['id'] += 1
            return code, data
        with self.assertRaises(setup.SetupError): setup.verify_telegram(self.v, wrong_bot)
        with self.assertRaisesRegex(setup.SetupError, '不能填写机器人ID'):
            setup.verify_telegram(dict(self.v, OWNER_USER_ID='3456789'), self.transport)
        def offline(*a, **kw): raise setup.SetupError('网络暂时不可用')
        with self.assertRaises(setup.SetupError): setup.verify_telegram(self.v, offline)

    def test_topic_partial_failure_resume_no_duplicate_probes(self):
        def fail_second(url, payload=None, headers=None):
            if payload and payload.get('message_thread_id') == 2: raise setup.SetupError('断网')
            return self.transport(url, payload, headers)
        with self.assertRaises(setup.SetupError): setup.verify_topics(self.root, self.v, fail_second)
        self.assertEqual(len(setup.state(self.root)['topic_receipts']), 1)
        setup.verify_topics(self.root, self.v, self.transport)
        self.assertEqual(len(setup.state(self.root)['topic_receipts']), 2)
        self.assertEqual([p['message_thread_id'] for m, p in self.transport_calls if m == 'sendMessage'], [4, 2])

    def ready(self):
        return [dict(item=k,status='通过',detail='模拟已核验') for k in ('隔离配置','数据库及人员','单实例','Mini App入口','话题路由','Telegram身份/群/权限','HTTPS与桥接认证','D1连接')]

    def test_full_wizard_normal_and_repeat_no_redeploy_or_repeat_topics(self):
        cloud=FakeCloud()
        with patch.object(setup,'verify_telegram',return_value=dict(username='company_example_bot')), \
             patch.object(setup,'Runner',return_value=cloud),patch.object(setup,'wrangler',return_value=['node','wrangler']), \
             patch.object(setup,'choose_account',return_value='f'*32), \
             patch.object(setup,'verify_topics',side_effect=lambda root,v: setup_verify(root,v,self.transport)), \
             patch.object(setup,'diagnose',return_value=self.ready()), \
             patch('builtins.input',side_effect=['work','y','']),patch('builtins.print'):
            self.assertEqual(setup.wizard(self.root),0)
        sent=len(self.transport_calls)
        with patch.object(setup,'verify_telegram',return_value=dict(username='company_example_bot')), \
             patch.object(setup,'deploy_cloud') as deploy,patch.object(setup,'initialize',wraps=setup.initialize), \
             patch.object(setup,'diagnose',return_value=self.ready()),patch('builtins.input',side_effect=['']),patch('builtins.print'):
            self.assertEqual(setup.wizard(self.root),0);deploy.assert_not_called()
        self.assertEqual(len(self.transport_calls),sent)

    def test_wizard_skip_then_resume_topics_without_start_prompt(self):
        s=setup.state(self.root);s.update(origin='https://example.workers.dev',bot_id='3456789');s['steps']['cloud']=True;setup.save_state(self.root,s)
        self.v.update(BOT_USERNAME='company_example_bot',PROGRESS_MINIAPP_ORIGIN=s['origin'],PROGRESS_MINIAPP_BRIDGE='true')
        setup.attach_miniapp(self.root,self.v,'work')
        with patch.object(setup,'verify_telegram',return_value=dict(username='company_example_bot')), \
             patch.object(setup,'diagnose') as diagnosis, patch('builtins.input',side_effect=['n']) as ask,patch('builtins.print') as output:
            self.assertEqual(setup.wizard(self.root),2);diagnosis.assert_not_called()
            self.assertEqual(ask.call_count,1)
            self.assertTrue(any('话题待验证' in str(c) for c in output.call_args_list))
        with patch.object(setup,'verify_telegram',return_value=dict(username='company_example_bot')), \
             patch.object(setup,'deploy_cloud') as deploy, \
             patch.object(setup,'verify_topics',side_effect=lambda root,v: setup_verify(root,v,self.transport)), \
             patch.object(setup,'diagnose',return_value=self.ready()),patch('builtins.input',side_effect=['y','']),patch('builtins.print'):
            self.assertEqual(setup.wizard(self.root),0);deploy.assert_not_called()
        self.assertTrue(setup.topics_verified(setup.state(self.root),self.v))

    def test_start_gate_rejects_incomplete_or_denied_checks(self):
        self.assertFalse(setup.ready_to_start([]))
        self.assertFalse(setup.ready_to_start([dict(item='本地桥接',status='失败')]))
        for item in self.ready():
            checks=self.ready()
            next(x for x in checks if x['item']==item['item'])['status']='待处理'
            self.assertFalse(setup.ready_to_start(checks))
        self.assertTrue(setup.ready_to_start(self.ready()+[dict(item='本地桥接',status='失败')]))

    def test_cloud_resume_no_duplicate_d1_secret_stdin_and_no_bootstrap(self):
        cloud = FakeCloud(fail='deploy')
        account = 'f' * 32
        with self.assertRaises(setup.SetupError): setup.deploy_cloud(self.root, self.v, account, cloud, ['node', 'wrangler'])
        self.assertTrue(setup.state(self.root).get('database_id'))
        self.assertFalse(setup.state(self.root)['steps'].get('cloud'))
        cloud.fail = None
        origin = setup.deploy_cloud(self.root, self.v, account, cloud, ['node', 'wrangler'])
        self.assertTrue(origin.endswith('.workers.dev'))
        self.assertEqual(len([a for a, _ in cloud.calls if 'create' in a]), 1)
        secret_calls = [(a, text) for a, text in cloud.calls if 'secret' in a]
        self.assertEqual(secret_calls[0][1].strip(), self.v['PROGRESS_BRIDGE_SECRET'])
        for args, _ in cloud.calls:
            self.assertNotIn(self.v['PROGRESS_BRIDGE_SECRET'], ' '.join(args))
            self.assertNotIn(token(), ' '.join(args))
            self.assertNotIn('bootstrap', ' '.join(args))
        state_raw = (self.root / setup.MARKER).read_text()
        self.assertNotIn(self.v['PROGRESS_BRIDGE_SECRET'], state_raw)
        self.assertNotIn(token(), state_raw)
        cfg = (self.root / 'deploy/wrangler.toml').read_text()
        self.assertIn('BOOTSTRAP_ALLOWED = "false"', cfg)
        self.assertNotIn(token(), cfg)
        with self.assertRaises(setup.SetupError): setup.deploy_cloud(self.root, self.v, 'a' * 32, cloud, ['node', 'wrangler'])

    def test_cloud_schema_retry_preserves_existing_jobs(self):
        cloud = FakeCloud()
        setup.deploy_cloud(self.root, self.v, 'f' * 32, cloud, ['node', 'wrangler'])
        sql = (self.root / 'deploy/company-init.sql').read_text()
        with contextlib.closing(sqlite3.connect(self.root / 'relay-test.sqlite3', isolation_level=None)) as db:
            db.executescript(sql)
            db.execute("INSERT INTO bridge_state VALUES ('test','preserved')")
            db.executescript(sql)
            self.assertEqual(db.execute("SELECT value FROM bridge_state WHERE key='test'").fetchone()[0], 'preserved')

    def test_cloud_auth_reuse_login_and_multi_account_selection(self):
        calls = []
        accounts = [dict(id='f' * 32, name='Company one')]
        def run(argv, **kwargs):
            calls.append(argv)
            return json.dumps(dict(loggedIn=True, accounts=accounts))
        self.assertEqual(setup.choose_account(self.root, run, ['node', 'wrangler']), 'f' * 32)
        self.assertFalse(any('login' in a for a in calls))
        accounts.append(dict(id='b' * 32, name='Company two'))
        with patch('builtins.print'):
            self.assertEqual(setup.choose_account(self.root, run, ['node', 'wrangler'], lambda _: '2'), 'b' * 32)
        marker = setup.state(self.root); marker['account'] = 'a' * 32; setup.save_state(self.root, marker)
        with self.assertRaises(setup.SetupError): setup.choose_account(self.root, run, ['node', 'wrangler'])
        marker.pop('account'); setup.save_state(self.root, marker)
        logged = False
        def need_login(argv, **kwargs):
            nonlocal logged
            if 'login' in argv: logged = True
            if not logged: raise setup.SetupError('not authenticated')
            return json.dumps(dict(loggedIn=True, accounts=accounts[:1]))
        with patch('builtins.print'):
            self.assertEqual(setup.choose_account(self.root, need_login, ['node', 'wrangler']), 'f' * 32)
        self.assertTrue(logged)

    def test_miniapp_direct_link_and_unknown_shortname(self):
        self.v.update(BOT_USERNAME='company_example_bot', PROGRESS_MINIAPP_ORIGIN='https://example.workers.dev')
        setup.attach_miniapp(self.root, self.v, 'work')
        self.assertEqual(setup.read_env(self.root)['PROGRESS_MINIAPP_LINK'], 'https://t.me/company_example_bot/work')
        with self.assertRaises(setup.SetupError): setup.attach_miniapp(self.root, self.v, '../private')

    def test_diagnostics_distinguish_https_auth_d1_bridge_and_duplicate_instances(self):
        setup.initialize(self.root, self.v)
        cloud = FakeCloud()
        setup.deploy_cloud(self.root, self.v, 'f' * 32, cloud, ['node', 'wrangler'])
        self.v['BOT_USERNAME'] = 'company_example_bot'
        setup.attach_miniapp(self.root, self.v, 'work')
        setup.verify_topics(self.root, self.v, self.transport)
        class HealthCloud:
            def __call__(self, *a, **kw):
                return json.dumps([dict(results=[dict(value=str(time.time() * 1000))])])
        with patch.object(setup, 'wrangler', return_value=['node', 'wrangler']):
            results = setup.diagnose(self.root, True, self.transport, lambda _: (1, 2), HealthCloud())
            self.assertTrue(all(r['status'] == '通过' for r in results), results)
            bad = setup.diagnose(self.root, True, self.transport, lambda _: (2, 3), lambda *a, **kw: '[{"results":[]}]')
            self.assertEqual({r['item'] for r in bad if r['status'] == '失败'}, {'单实例', '本地桥接'})
            def denied(url, payload=None, headers=None):
                if url.endswith('/bridge/socket'): return 403, {}
                return self.transport(url, payload, headers)
            denied_results = setup.diagnose(self.root, True, denied, lambda _: (0, 1), HealthCloud())
            self.assertIn('HTTPS与桥接认证', {r['item'] for r in denied_results if r['status'] == '失败'})

    def test_missing_wrangler_dependency_actionable_and_child_failure_redacted(self):
        with self.assertRaisesRegex(setup.SetupError, 'Node/Wrangler'): setup.wrangler(self.root, node='fake-node')
        runner = setup.Runner(self.root)
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, token(), token())):
            with self.assertRaises(setup.SetupError) as e: runner(['node', 'wrangler'])
        self.assertNotIn(token(), str(e.exception))

    def test_installer_lock_and_existing_single_instance_lock(self):
        with setup.install_lock(self.root):
            with self.assertRaises((setup.SetupError, BlockingIOError)):
                with setup.install_lock(self.root): pass
        from team_bot.maintenance import InstanceLock
        with InstanceLock(self.root / 'isolated-runtime'):
            with self.assertRaises(RuntimeError):
                with InstanceLock(self.root / 'isolated-runtime'): pass

    @unittest.skipUnless(os.name == 'nt', 'Windows bootstrap only')
    def test_powershell_parse_and_missing_python_node_detection(self):
        # Run the actual Find-Python function with a fake missing PATH; do not
        # install runtimes, invoke winget, or execute any installer main body.
        ps = SOURCE / 'scripts/setup-company.ps1'
        code = "$e=$null; $t=$null; $ast=[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$t,[ref]$e); if($e.Count){exit 3}; foreach($name in @('Find-Python','Find-Node')) { $f=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true); Invoke-Expression $f.Extent.Text }; function Get-Command { return $null }; function Test-Path { return $false }; if($null -ne (Find-Python)){exit 4}; if($null -ne (Find-Node)){exit 5}; 'missing-python-node-handled'"
        # Pass the path as a literal, not shell interpolation of user input.
        code = code.replace('$args[0]', "'" + str(ps).replace("'", "''") + "'")
        p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('missing-python-node-handled', p.stdout)


setup_verify=setup.verify_topics
if __name__ == '__main__': unittest.main()
