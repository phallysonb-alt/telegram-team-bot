"""Real historical files on Windows; only the polling business entry is a dummy.

No Telegram credentials, real task modification or company database access.
The fixture embeds exact Git fc90f735 bytes, usable in source ZIPs without Git.
"""
from contextlib import ExitStack
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
import legacy_lifecycle_fixture as legacy
import test_stage3_process as fixtures

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bundle_install',ROOT/'scripts/lifecycle_bundle.py')
bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
control=bundle.controller(ROOT)


class ManifestTests(unittest.TestCase):
    def test_deleted_manifest_cannot_bypass_completed_install_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'data/lifecycle-upgrade';folder.mkdir(parents=True)
            (folder/'complete.json').write_text('{"manifest":"missing"}')
            with self.assertRaisesRegex(RuntimeError,'不完整或版本不一致'):control.verify_controls(root)

    def test_reviewed_release_group_and_exact_historical_fixture(self):
        control.verify_controls(ROOT,required=True)
        self.assertIn('Stop-Process',legacy.FILES['scripts/stop.ps1'])
        self.assertNotIn('%*',legacy.FILES['stop.cmd'])
        self.assertIsNone(legacy.FILES['team_bot/process_control.py'])
        for name,digest in legacy.SHA256.items():
            self.assertEqual(hashlib.sha256(legacy.FILES[name].encode()).hexdigest(),digest)
            if (ROOT/'.git').exists() and shutil.which('git'):
                blob=subprocess.run(['git','-C',str(ROOT),'cat-file','blob',legacy.REVISION+':'+name],capture_output=True)
                self.assertEqual(blob.returncode,0)
                self.assertEqual(blob.stdout,legacy.FILES[name].encode())

    def test_enabled_task_or_read_failure_is_not_treated_as_paused(self):
        with patch.object(bundle.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'private task details')):
            with self.assertRaisesRegex(RuntimeError,'仍启用.*无法核实') as caught:bundle.paused_tasks(control,Path('isolated'))
            self.assertNotIn('private task details',str(caught.exception))


@unittest.skipUnless(os.name=='nt','Windows transition tests')
class HistoricalTransitionTests(unittest.TestCase):
    invoke=fixtures.Stage3ProcessTests.invoke

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lifecycle historical ');self.addCleanup(self.temp.cleanup)
        self.parent=Path(self.temp.name);self.root=self.parent/'old company';self.data=self.root/'fixture data'
        for folder in ('scripts','team_bot','.venv/Scripts','fixture data'):(self.root/folder).mkdir(parents=True,exist_ok=True)
        for name in ('python.exe','pythonw.exe'):shutil.copyfile(ROOT/'.venv/Scripts'/name,self.root/'.venv/Scripts'/name)
        shutil.copyfile(ROOT/'.venv/pyvenv.cfg',self.root/'.venv/pyvenv.cfg')
        for name,raw in legacy.FILES.items():
            if raw is not None:(self.root/name).write_bytes(raw.encode('utf-8'))
        (self.root/'team_bot/__init__.py').write_text('')
        (self.root/'team_bot/__main__.py').write_text("raise RuntimeError('offline harness not active')\n")
        # Inject the offline CLI externally: actual old/new control files and
        # the complete copied new public source remain byte-for-byte unchanged.
        harness=self.parent/'offline harness';harness.mkdir()
        (harness/'sitecustomize.py').write_text('''import importlib.abc,importlib.util,sys
CODE="""import os,sys,time
from pathlib import Path
from team_bot.maintenance import InstanceLock
data=Path('fixture data')
if sys.argv[1]=='paths':print(data.resolve());raise SystemExit(0)
if os.environ.get('DUMMY_PHASE')=='crash':raise SystemExit(1)
with InstanceLock(data),InstanceLock(Path('data/bot-global-lock')):
 with (data/'runs.txt').open('a') as log:log.write(str(os.getpid())+chr(10))
 (data/'ready').write_text(str(os.getpid()))
 if os.environ.get('DUMMY_PHASE')=='new':
  from team_bot.process_control import ProcessRef,stop_capability,stop_requested
  with ProcessRef(os.getpid()) as me,stop_capability(data,me):
   while not stop_requested(data/'stop-request.json',me.pid,me.birth):time.sleep(.05)
 else:
  while not (data/'exit-old').exists():time.sleep(.05)
"""
class Loader(importlib.abc.Loader):
 def get_code(self,fullname):return compile(CODE,'offline-business-dummy','exec')
 def create_module(self,spec):return None
 def exec_module(self,module):exec(self.get_code(module.__name__),module.__dict__)
class Finder(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='team_bot.__main__':return importlib.util.spec_from_loader(fullname,Loader())
sys.meta_path.insert(0,Finder())
''',encoding='utf-8')
        sites=[p for p in sys.path if p and Path(p).name=='site-packages']
        self.env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(harness),*sites]),PYTHONUTF8='1',DUMMY_PHASE='old')
        self.backup=self.parent/'control backup';self.monitor=None
        self.database=self.data/'team.sqlite3'
        with sqlite3.connect(self.database) as db:db.execute('CREATE TABLE protected(value TEXT)');db.execute("INSERT INTO protected VALUES ('simulated-history-do-not-replace')")
        db.close()
        self.original_database=self.database.read_bytes()
        self.addCleanup(self.cleanup)

    def ps(self,name,*args):
        return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(self.root/'scripts'/name),*args],
            cwd=self.root,env=self.env,capture_output=True,timeout=30,**control.no_console_options())

    def cmd(self,name,*args):
        self.assertIn(name,('start.cmd','stop.cmd'))
        return subprocess.run([os.environ.get('COMSPEC','cmd.exe'),'/d','/c',name,*args],
            cwd=self.root,env=self.env,input=b'\r\n',capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)

    def cleanup(self):
        if self.monitor and self.monitor.poll() is None:
            (self.data/'autostart.stop').touch();self.monitor.wait(timeout=45)
        (self.data/'exit-old').touch()
        rows=control.snapshot();bots=[r for r in rows if control.is_bot(r,self.root)]
        with ExitStack() as stack:
            for b in bots:
                chain,unknown=control.project_tree(b,rows,self.root)
                if unknown:raise RuntimeError('Unknown dummy child; cleanup refused')
                refs=control.checked_refs(chain,stack,True)
                control.request_stop(self.data,refs)
                if not control.drain(refs,5):
                    for r in reversed(refs):r.kill()
                    self.assertTrue(control.drain(refs,5))

    def start_old(self):
        p=self.ps('start.ps1')
        if p.returncode:
            detail=subprocess.run([str(self.root/'.venv/Scripts/pythonw.exe'),'-m','team_bot','paths'],cwd=self.root,env=self.env,capture_output=True,timeout=10,**control.no_console_options())
            self.fail(p.stderr.decode(errors='replace')+detail.stderr.decode(errors='replace'))
        return int((self.data/'process.pid').read_text())

    def current_count(self):return len([r for r in control.snapshot() if control.is_bot(r,self.root)])

    def install(self):return bundle.install(self.root,self.data,self.backup)

    def switch_full_source(self):
        # Whitelisted source only; never copy source .env, data, exports or Git.
        sys.path.insert(0,str(ROOT/'scripts'))
        from repo_guard import policy,permitted
        p=policy(ROOT)
        names=set(p['files'])|set(p['templates'])
        for folder in p['source_roots']:
            for directory,children,files in os.walk(ROOT/folder):
                children[:]=[n for n in children if n not in ('node_modules','__pycache__','.git','data','exports')]
                names.update((Path(directory)/name).relative_to(ROOT).as_posix() for name in files)
        for name in names:
            src=ROOT/name
            if permitted(name,p) and src.is_file():
                dest=self.root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
        self.assertEqual((self.root/'team_bot/__main__.py').read_bytes(),(ROOT/'team_bot/__main__.py').read_bytes())

    def test_actual_old_watcher_and_files_to_new_group_authorized_stop_and_full_source(self):
        pid=self.start_old();old_main=(self.root/'team_bot/__main__.py').read_bytes()
        self.monitor=subprocess.Popen([str(self.root/'.venv/Scripts/pythonw.exe'),str(self.root/'scripts/windows_background.py'),'monitor'],
            cwd=self.root,env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**control.no_console_options())
        deadline=time.monotonic()+10
        while not any(control.is_watcher(r,self.root) for r in control.snapshot()) and time.monotonic()<deadline:time.sleep(.1)
        time.sleep(2)  # Let the unchanged historical watcher enter its 30s loop.
        before=(self.root/'scripts/stop.ps1').read_bytes()
        with self.assertRaisesRegex(RuntimeError,'旧监控'):self.install()
        self.assertEqual((self.root/'scripts/stop.ps1').read_bytes(),before)
        (self.data/'autostart.stop').touch();self.monitor.wait(timeout=45)
        self.install()
        self.assertEqual((self.root/'team_bot/__main__.py').read_bytes(),old_main)
        with fixtures.ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        self.assertFalse((self.data/'process.stop-capability.json').exists())
        self.assertIn('%*',(self.root/'stop.cmd').read_text())
        self.assertNotEqual(self.cmd('stop.cmd','-Force','-LegacyUpgrade','-TimeoutSeconds','0').returncode,0)
        with fixtures.ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        self.assertEqual(self.invoke('upgrade-hold').returncode,0)
        # Old call form, new installed script: no -Recovery cannot bypass hold.
        self.assertNotEqual(self.ps('start.ps1').returncode,0);self.assertEqual(self.current_count(),1)
        for args in [(),('-Force','-TimeoutSeconds','0'),('-LegacyUpgrade','-TimeoutSeconds','0')]:
            result=self.cmd('stop.cmd',*args);self.assertNotEqual(result.returncode,0,result.stdout)
            with fixtures.ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        unrelated=subprocess.Popen([str(Path(getattr(sys,'_base_executable',sys.executable)).with_name('pythonw.exe')),'-c','import time;time.sleep(180)'],**control.no_console_options())
        try:
            # Explicit authorization is represented ONLY in this offline fixture.
            result=self.cmd('stop.cmd','-Force','-LegacyUpgrade','-TimeoutSeconds','0')
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertTrue(control.runtime_free(self.data));self.assertTrue(control.runtime_free(self.root/'data/bot-global-lock'))
            self.assertIsNone(unrelated.poll())
            self.switch_full_source();self.env['DUMMY_PHASE']='new'
            self.assertEqual(self.invoke('upgrade-release').returncode,0)
            started=self.cmd('start.cmd');self.assertEqual(started.returncode,0,started.stderr)
            self.assertEqual(self.current_count(),1)
            self.assertTrue((self.data/'process.stop-capability.json').exists())
            self.assertEqual(self.database.read_bytes(),self.original_database)
            self.assertEqual(len((self.data/'runs.txt').read_text().splitlines()),2)
            self.assertEqual(self.cmd('stop.cmd').returncode,0)
        finally:unrelated.terminate();unrelated.wait(timeout=10)

    def test_interrupted_control_install_resume_and_failed_new_start_rollback(self):
        pid=self.start_old();replace=bundle.atomic_bytes
        def interrupted(path,raw):
            if path==self.root/'scripts/common.ps1':raise OSError('simulated interruption')
            replace(path,raw)
        with patch.object(bundle,'atomic_bytes',side_effect=interrupted):
            with self.assertRaises(OSError):self.install()
        self.assertTrue((self.root/'data/lifecycle-upgrade/pending.json').exists())
        self.assertNotEqual(self.cmd('stop.cmd','-Force').returncode,0)
        self.assertNotEqual(self.ps('stop.ps1','-Force').returncode,0)
        with fixtures.ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        observed=bundle.quiescent(control,self.root,self.data)
        with patch.object(bundle,'quiescent',side_effect=[observed,[]]):
            with self.assertRaisesRegex(RuntimeError,'进程身份变化'):self.install()
        self.assertTrue((self.root/'data/lifecycle-upgrade/pending.json').exists())
        self.install();self.assertEqual(self.invoke('upgrade-hold').returncode,0)
        # A later partial/mismatched stop.ps1 must not bypass the group check.
        saved=(self.root/'scripts/stop.ps1').read_bytes()
        (self.root/'scripts/stop.ps1').write_bytes(saved+b'\r\n# mixed version\r\n')
        self.assertNotEqual(self.cmd('stop.cmd','-Force','-LegacyUpgrade','-TimeoutSeconds','0').returncode,0)
        (self.root/'scripts/stop.ps1').write_bytes(saved)
        self.assertEqual(self.cmd('stop.cmd','-Force','-LegacyUpgrade','-TimeoutSeconds','0').returncode,0)
        self.switch_full_source();self.assertEqual(self.invoke('upgrade-release').returncode,0)
        self.env['DUMMY_PHASE']='crash';self.assertNotEqual(self.cmd('start.cmd').returncode,0)
        self.assertEqual(self.current_count(),0);self.assertTrue(control.runtime_free(self.data))
        self.assertEqual(self.invoke('upgrade-hold').returncode,0)
        # Roll back the business release, retain the verified safe control group.
        (self.root/'team_bot/__main__.py').write_text("raise RuntimeError('offline old business fixture')\n")
        control.verify_controls(self.root,required=True)
        self.env['DUMMY_PHASE']='old';self.assertEqual(self.invoke('upgrade-release').returncode,0)
        self.assertEqual(self.cmd('start.cmd').returncode,0);self.assertEqual(self.current_count(),1)
        self.assertEqual(self.database.read_bytes(),self.original_database)
        checkpoint=json.loads((self.backup/'checkpoint.json').read_text())
        self.assertEqual(checkpoint['old']['scripts/stop.ps1'],hashlib.sha256(legacy.FILES['scripts/stop.ps1'].encode().replace(b'\r\n',b'\n')).hexdigest())
        self.assertEqual((self.backup/'stop.cmd').read_bytes(),legacy.FILES['stop.cmd'].encode())


if __name__=='__main__':unittest.main()
