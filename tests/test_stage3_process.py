"""Real isolated Windows processes; no real bot token or Telegram network.

The PTB fixture uses its actual polling/application lifecycle with an in-memory
fake BaseRequest, so stop_running must drain an active handler before exiting.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from team_bot.process_control import ProcessRef,stop_requested,stop_capability

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stage3_launcher',ROOT/'scripts/windows_background.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


@unittest.skipUnless(os.name=='nt','Windows process tests')
class Stage3ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='stage3 模拟进程 ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'fixture data';self.data.mkdir()
        for folder in ('scripts','team_bot','.venv/Scripts'):(self.root/folder).mkdir(parents=True)
        for name in ('windows_background.py','common.ps1','start.ps1','stop.ps1','login-watch.ps1','lifecycle-control.json'):
            shutil.copyfile(ROOT/'scripts'/name,self.root/'scripts'/name)
        for name in ('start.cmd','stop.cmd'):shutil.copyfile(ROOT/name,self.root/name)
        for name in ('python.exe','pythonw.exe'):
            shutil.copyfile(ROOT/'.venv/Scripts'/name,self.root/'.venv/Scripts'/name)
        shutil.copyfile(ROOT/'.venv/pyvenv.cfg',self.root/'.venv/pyvenv.cfg')
        # PTB comes from this test runner's installed dependencies, not Codex.
        sites=[str(p) for p in sys.path if p and Path(p).name=='site-packages']
        self.env=dict(os.environ,PYTHONPATH=os.pathsep.join(sites),PYTHONUTF8='1')
        (self.root/'team_bot/__init__.py').write_text('')
        for name in ('process_control.py','maintenance.py'):
            shutil.copyfile(ROOT/'team_bot'/name,self.root/'team_bot'/name)
        self.module=self.root/'team_bot/__main__.py'
        self.module.write_text('''import asyncio,json,os,sys,time
from pathlib import Path
from team_bot.maintenance import InstanceLock
from team_bot.process_control import ProcessRef,stop_requested,stop_capability
data=Path('fixture data')
if sys.argv[1]=='paths':print(data.resolve());raise SystemExit(0)
with InstanceLock(data),InstanceLock(Path('data/bot-global-lock')):
 (data/'started').write_text(str(os.getpid()))
 with ProcessRef(os.getpid()) as me,stop_capability(data,me):
  while not stop_requested(data/'stop-request.json',me.pid,me.birth):time.sleep(.05)
 (data/'drained').write_text('complete')
''',encoding='utf-8')
        self.addCleanup(self.cleanup_service)

    def invoke(self,action,timeout=6,force=False,recovery=False,resume=False,legacy_upgrade=False):
        args=[str(self.root/'.venv/Scripts/pythonw.exe'),str(self.root/'scripts/windows_background.py'),action,'--data-path',str(self.data),'--timeout',str(timeout)]
        if force:args.append('--force')
        if recovery:args.append('--recovery')
        if resume:args.append('--resume')
        if legacy_upgrade:args.append('--legacy-upgrade')
        return subprocess.run(args,cwd=self.root,env=self.env,capture_output=True,timeout=45,**launcher.no_console_options())

    def cleanup_service(self):
        result=self.invoke('stop',timeout=2,force=True)
        if result.returncode and (self.data/'process.identity.json').exists():
            # Restore only a deliberately corrupted fixture identity, after
            # looking up our own isolated root; never act on the live project.
            rows=launcher.snapshot();bots=[r for r in rows if launcher.is_bot(r,self.root)]
            if len(bots)==1:
                with ProcessRef(bots[0]['ProcessId']) as ref:
                    (self.data/'process.identity.json').write_text(json.dumps(ref.identity()))
                self.invoke('stop',timeout=1,force=True)

    def start(self):
        p=self.invoke('start',resume=True)
        self.assertEqual(p.returncode,0,p.stderr.decode('utf-8',errors='replace'))
        return int((self.data/'process.pid').read_text())

    def test_simultaneous_start_one_success_pid_never_replaced_by_failed_attempt(self):
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.invoke('start'),range(2)))
        self.assertEqual(sorted(p.returncode for p in results),[0,1])
        pid=int((self.data/'process.pid').read_text())
        with ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        self.assertEqual(json.loads((self.data/'process.identity.json').read_text())['pid'],pid)
        self.assertEqual(len([r for r in launcher.snapshot() if launcher.is_bot(r,self.root)]),1)

    def test_stale_foreign_pid_does_not_kill_foreign_process(self):
        base=Path(getattr(sys,'_base_executable',sys.executable)).with_name('pythonw.exe')
        p=subprocess.Popen([str(base),'-c','import time;time.sleep(30)'],**launcher.no_console_options())
        try:
            (self.data/'process.pid').write_text(str(p.pid))
            result=self.invoke('stop',timeout=0,force=True)
            self.assertEqual(result.returncode,0);self.assertIsNone(p.poll())
            self.assertFalse((self.data/'process.pid').exists())
        finally:
            p.terminate();p.wait(timeout=10)
        # The same stale numeric file can be safely replaced once no process owns it.
        self.start()

    def test_timeout_preserves_pid_wrong_birth_refused_then_graceful_stop(self):
        self.module.write_text(self.module.read_text(encoding='utf-8').replace("while not stop_requested(data/'stop-request.json',me.pid,me.birth):time.sleep(.05)","while not (data/'allow-stop').exists():time.sleep(.05)"),encoding='utf-8')
        pid=self.start();identity=json.loads((self.data/'process.identity.json').read_text())
        failed=self.invoke('stop',timeout=.2)
        self.assertNotEqual(failed.returncode,0)
        self.assertEqual((self.data/'process.pid').read_text(),str(pid))
        self.assertTrue((self.data/'autostart.stop').exists())
        with ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        wrong=dict(identity,birth=identity['birth']-100)
        (self.data/'process.identity.json').write_text(json.dumps(wrong))
        self.assertNotEqual(self.invoke('stop',timeout=0,force=True).returncode,0)
        with ProcessRef(pid) as ref:self.assertTrue(ref.alive())
        (self.data/'process.identity.json').write_text(json.dumps(identity))
        (self.data/'allow-stop').touch()
        self.assertEqual(self.invoke('stop').returncode,0)
        self.assertFalse((self.data/'process.pid').exists())
        (self.data/'allow-stop').unlink()
        forced=self.start()
        self.assertEqual(self.invoke('stop',timeout=0,force=True).returncode,0)
        with self.assertRaises(ProcessLookupError):ProcessRef(forced)

    def test_failed_start_preserves_previous_record_and_recovery_honors_manual_stop(self):
        self.module.write_text("import sys;raise SystemExit(1)\n")
        (self.data/'process.pid').write_text('99999999')
        failed=self.invoke('start');self.assertNotEqual(failed.returncode,0)
        self.assertEqual((self.data/'process.pid').read_text(),'99999999')
        (self.data/'autostart.stop').touch()
        self.assertNotEqual(self.invoke('start',recovery=True).returncode,0)
        self.assertTrue((self.data/'autostart.stop').exists())

    def test_real_pid_publication_failure_does_not_orphan_interpreter(self):
        (self.data/'process.pid').write_text('99999999')
        replace=os.replace
        def fail_pid(src,dest):
            if Path(dest)==self.data/'process.pid':raise PermissionError('simulated PID publication failure')
            return replace(src,dest)
        with patch.object(launcher.os,'replace',side_effect=fail_pid):
            with self.assertRaises(PermissionError):launcher.start_bot(self.data,self.root)
        self.assertEqual((self.data/'process.pid').read_text(),'99999999')
        self.assertTrue((self.data/'drained').exists())
        self.assertFalse(any(launcher.is_bot(r,self.root) for r in launcher.snapshot()))

    def test_abnormal_exit_recovery_keeps_durable_files_and_new_process_identity(self):
        first=self.start();first_identity=(self.data/'process.identity.json').read_text()
        (self.data/'durable-pending.json').write_text('{"pending":["simulated-event-1"]}')
        rows=launcher.snapshot();row=next(r for r in rows if r['ProcessId']==first)
        chain,unknown=launcher.project_tree(row,rows,self.root);self.assertFalse(unknown)
        with ExitStack() as stack:
            refs=[launcher.checked_ref(r,stack,True) for r in chain]
            for ref in reversed(refs):ref.kill()
            self.assertTrue(launcher.drain(refs,5))
        self.assertEqual(self.invoke('start',recovery=True).returncode,0)
        self.assertNotEqual((self.data/'process.identity.json').read_text(),first_identity)
        self.assertEqual(json.loads((self.data/'durable-pending.json').read_text())['pending'],['simulated-event-1'])

    def test_stale_stop_request_does_not_match_same_pid_different_birth(self):
        path=self.data/'stop-request.json'
        path.write_text(json.dumps(dict(targets=[dict(pid=1234,birth=100)])))
        self.assertFalse(stop_requested(path,1234,101))
        self.assertTrue(stop_requested(path,1234,100))

    def test_real_ptb_stop_drains_inflight_handler_without_network(self):
        self.module.write_text('''import asyncio,json,sys
from pathlib import Path
from telegram import Update
from telegram.ext import Application,TypeHandler
from telegram.request import BaseRequest
from team_bot.maintenance import InstanceLock
from team_bot.process_control import watch_stop
data=Path('fixture data')
if sys.argv[1]=='paths':print(data.resolve());raise SystemExit(0)
class FakeRequest(BaseRequest):
 @property
 def read_timeout(self):return 1
 async def initialize(self):pass
 async def shutdown(self):pass
 async def do_request(self,url,method,request_data=None,**kwargs):
  if url.endswith('/getMe'):result=dict(id=12345,is_bot=True,first_name='Simulated',username='simulated_bot')
  elif url.endswith('/getUpdates'):
   await asyncio.sleep(.1)
   if not (data/'offered').exists():
    (data/'offered').touch();result=[dict(update_id=1,message=dict(message_id=1,date=1,chat=dict(id=1,type='private'),text='isolated'))]
   else:result=[]
  else:result=True
  return 200,json.dumps(dict(ok=True,result=result)).encode()
async def entered(update,context):
 (data/'handler-entered').touch()
 while not (data/'stop-request.json').exists():await asyncio.sleep(.05)
 await asyncio.sleep(.3)
 (data/'handler-committed').write_text('saved exactly once')
async def init(app):
 await asyncio.sleep(4.5)  # Initial startup exceeds the old 3-second threshold.
 app.bot_data['stop']=asyncio.create_task(watch_stop(app,data))
async def stopped(app):
 app.bot_data['stop'].cancel();await asyncio.gather(app.bot_data['stop'],return_exceptions=True)
 (data/'post-stop').touch()
with InstanceLock(data),InstanceLock(Path('data/bot-global-lock')):
 app=Application.builder().token('12345:SIMULATED').request(FakeRequest()).get_updates_request(FakeRequest()).post_init(init).post_stop(stopped).build()
 app.add_handler(TypeHandler(Update,entered))
 app.run_polling(drop_pending_updates=False,stop_signals=None)
''',encoding='utf-8')
        self.start()
        deadline=time.monotonic()+12
        while not (self.data/'handler-entered').exists() and time.monotonic()<deadline:time.sleep(.1)
        self.assertTrue((self.data/'handler-entered').exists(),(self.data/'logs/console-error.log').read_text(encoding='utf-8',errors='replace'))
        stop=self.invoke('stop',timeout=10)
        self.assertEqual(stop.returncode,0,stop.stderr.decode('utf-8',errors='replace'))
        self.assertEqual((self.data/'handler-committed').read_text(),'saved exactly once')
        self.assertTrue((self.data/'post-stop').exists())


if __name__=='__main__':unittest.main()
