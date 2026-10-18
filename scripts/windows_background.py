"""Windows startup plumbing only; never run a second polling loop here.

Use pythonw for the scheduled login action. CREATE_NO_WINDOW, unlike SW_HIDE,
prevents Windows Terminal delegation from allocating a console in the first place.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def no_console_options() -> dict:
    if os.name != "nt":
        raise RuntimeError("This launcher requires Windows")
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "stdin": subprocess.DEVNULL}


def monitor(root: Path = ROOT) -> int:
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # The GUI launcher stays alive so Task Scheduler still supervises the watcher
    # and applies its existing restart policy. It does not start the bot itself.
    log_path = root / "data/logs"
    log_path.mkdir(parents=True, exist_ok=True)
    with (log_path / "login-watch.log").open("ab", buffering=0) as log:
        return subprocess.call(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(root / "scripts/login-watch.ps1")],
            cwd=root, stdout=log, stderr=log, **no_console_options(),
        )


@contextmanager
def lifecycle_lock(data_path, timeout=30):
    import msvcrt
    data_path.mkdir(parents=True, exist_ok=True)
    f = (data_path / 'lifecycle.lock').open('a+b')
    try:
        if not f.seek(0, 2): f.write(b'0'); f.flush()
        deadline = time.monotonic() + timeout
        while True:
            f.seek(0)
            try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); break
            except OSError:
                if time.monotonic() >= deadline: raise RuntimeError('另一个启动/停止操作尚未结束') from None
                time.sleep(.05)
        yield
    finally: f.close()


def runtime_free(data_path):
    # Existing run() still owns its original instance locks. Also detects an
    # older/untracked live process without trusting its stale PID file.
    import msvcrt
    data_path.mkdir(parents=True, exist_ok=True)
    with (data_path / 'instance.lock').open('a+b') as f:
        if not f.seek(0, 2): f.write(b'0'); f.flush()
        f.seek(0)
        try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: return False
        f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    return True


def snapshot():
    # Command lines stay in memory; never log or return them to the console.
    ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    code = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,@{n='Birth';e={$_.CreationDate.ToUniversalTime().ToFileTimeUtc()}}) | ConvertTo-Json -Compress"
    p = subprocess.run([str(ps), '-NoProfile', '-NonInteractive', '-Command', code], capture_output=True, timeout=25, **no_console_options())
    if p.returncode: raise RuntimeError('无法核实Windows进程，未执行启动/强制停止')
    rows = json.loads(p.stdout.decode('utf-8-sig'))
    return rows if isinstance(rows, list) else [rows]


def is_bot(row, root):
    return (row.get('ExecutablePath') and Path(row['ExecutablePath']).resolve() in
            {root / '.venv/Scripts/python.exe', root / '.venv/Scripts/pythonw.exe'}
            and re.search(r'(?:^|\s)-m\s+team_bot\s+run(?:\s|$)', row.get('CommandLine') or '') is not None)


def process_type():
    spec = importlib.util.spec_from_file_location('local_process_control', ROOT / 'team_bot/process_control.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.ProcessRef


def checked_ref(row, stack, terminate=False):
    ref = stack.enter_context(process_type()(row['ProcessId'], terminate=terminate))
    # CIM creation time has microsecond resolution; kernel handle has 100ns.
    if ref.birth // 10 != int(row['Birth']) // 10 or ref.image != Path(row['ExecutablePath']).resolve():
        raise RuntimeError('进程身份已变化，未处理该PID')
    return ref


def project_tree(root_row, rows, root):
    chain = [root_row]; parents = {root_row['ProcessId']}
    while parents:
        children = [r for r in rows if r['ParentProcessId'] in parents]
        chain.extend(children); parents = {r['ProcessId'] for r in children}
    base = Path(getattr(sys, '_base_executable', sys.executable)).parent
    unknown = []
    for r in chain[1:]:
        image = Path(r.get('ExecutablePath') or '').resolve(); cmd = r.get('CommandLine') or ''
        python = image in {base / 'python.exe', base / 'pythonw.exe'} and re.search(r'(?:^|\s)-m\s+team_bot\s+run(?:\s|$)', cmd)
        conhost = image == Path(os.environ['SystemRoot']) / 'System32/conhost.exe'
        export = image.name.lower() == 'node.exe' and any(str(root / 'scripts' / n).lower() in cmd.lower() for n in ('daily_xlsx.mjs', 'progress_xlsx.mjs'))
        if not (python or conhost or export): unknown.append(r['ProcessId'])
    return chain, unknown


def atomic_json(path, value):
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        tmp.write_text(json.dumps(value), encoding='utf-8'); os.replace(tmp, path)
    finally: tmp.unlink(missing_ok=True)


def drain(refs, timeout):
    deadline = time.monotonic() + timeout
    while any(r.alive() for r in refs):
        if time.monotonic() >= deadline: return False
        time.sleep(.1)
    return True


def request_stop(data_path, refs):
    atomic_json(data_path / 'stop-request.json', dict(targets=[r.identity() for r in refs]))


def cleanup_new_child(child, root, data_path):
    # Failed PID publication must not leave an interpreter child orphaned.
    rows = snapshot(); row = next((r for r in rows if r['ProcessId'] == child.pid), None)
    if not row: return
    if not is_bot(row, root): raise RuntimeError('启动失败后的进程无法核实，请检查本项目日志')
    chain, unknown = project_tree(row, rows, root)
    if unknown: raise RuntimeError('新进程存在未知子进程，未强制停止')
    with ExitStack() as stack:
        refs = [checked_ref(r, stack, terminate=True) for r in chain]
        request_stop(data_path, refs)
        if not drain(refs, 10):
            for ref in reversed(refs): ref.kill()
            drain(refs, 5)
    child.wait(timeout=10)


def start_bot(data_path: Path, root: Path = ROOT, recovery=False) -> int:
    root, data_path = root.resolve(), data_path.resolve()
    with lifecycle_lock(data_path):
        if recovery and (data_path / 'autostart.stop').exists(): raise RuntimeError('人工停止标记存在，监控不会重启')
        if any(is_bot(r, root) for r in snapshot()) or not runtime_free(data_path) or not runtime_free(root / 'data/bot-global-lock'):
            raise RuntimeError('机器人已经运行或实例锁仍被占用；未覆盖PID')
        (data_path / 'autostart.stop').unlink(missing_ok=True)
        return launch_and_publish(data_path, root)


def launch_and_publish(data_path, root):
    logs = data_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    # GUI venv launcher also avoids a console in the interpreter subprocess.
    python = root / ".venv/Scripts/pythonw.exe"
    with (logs / "console.log").open("ab", buffering=0) as out, \
            (logs / "console-error.log").open("ab", buffering=0) as err:
        child = subprocess.Popen(
            [str(python), "-m", "team_bot", "run"], cwd=root,
            stdout=out, stderr=err, **no_console_options(),
        )
    # Confirm the launcher survives before publishing; lifecycle_lock serializes
    # other starts/stops through this entire interval, and run() keeps its lock.
    try:
        time.sleep(3)
        if child.poll() is not None:
            child.wait(timeout=5)
            raise RuntimeError('启动失败，请查看数据目录 logs/console-error.log。')
        row = next((r for r in snapshot() if r['ProcessId'] == child.pid), None)
        if not row or not is_bot(row, root): raise RuntimeError('新启动进程身份核实失败')
        with ExitStack() as stack:
            ref = checked_ref(row, stack)
            atomic_json(data_path / 'process.identity.json', ref.identity())
        temporary = data_path / f"process.pid.{os.getpid()}.tmp"
        temporary.write_text(str(child.pid), encoding="ascii")
        os.replace(temporary, data_path / "process.pid")
    except BaseException:
        if child.poll() is None: cleanup_new_child(child, root, data_path)
        raise
    finally:
        (data_path / f'process.pid.{os.getpid()}.tmp').unlink(missing_ok=True)
    return child.pid


def stop_bot(data_path, root=ROOT, timeout=60, force=False):
    root, data_path = root.resolve(), data_path.resolve()
    with lifecycle_lock(data_path):
        (data_path / 'autostart.stop').write_text('manual stop', encoding='ascii')
        rows = snapshot(); bots = [r for r in rows if is_bot(r, root)]
        if len(bots) > 1: raise RuntimeError('发现多个本项目进程，请核查后处理，未强制停止')
        if not bots:
            if not runtime_free(data_path) or not runtime_free(root / 'data/bot-global-lock'):
                raise RuntimeError('实例锁占用但进程身份未知，未误杀或清除PID')
            try: saved = int((data_path / 'process.pid').read_text().strip())
            except (OSError, ValueError): saved = None
            if any(r['ProcessId'] == saved for r in rows): raise RuntimeError('PID属于其他进程，未停止该进程')
        else:
            chain, unknown = project_tree(bots[0], rows, root)
            with ExitStack() as stack:
                refs = [checked_ref(r, stack, terminate=force) for r in chain if r['ProcessId'] not in unknown]
                try:
                    saved = json.loads((data_path / 'process.identity.json').read_text())
                    if saved.get('pid') == refs[0].pid and saved.get('birth') != refs[0].birth:
                        raise RuntimeError('PID创建时间与记录不符，未停止该进程')
                except FileNotFoundError: pass  # Verified legacy PID-only launch.
                request_stop(data_path, refs)
                if not drain(refs, timeout):
                    if not force: raise RuntimeError('可控停止超时；PID和暂停标记保留。核实后可用 stop.cmd -Force -TimeoutSeconds 0；强停可能中断处理中消息')
                    if unknown: raise RuntimeError('存在未知子进程，拒绝强制停止')
                    for ref in reversed(refs): ref.kill()
                    if not drain(refs, 5): raise RuntimeError('已核实进程尚未退出，PID保留')
        for name in ('process.pid', 'process.identity.json', 'stop-request.json'):
            (data_path / name).unlink(missing_ok=True)
    return '机器人已停止；持久队列保留，监控已暂停。'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("monitor", "start", "stop"))
    parser.add_argument("--data-path", type=Path)
    parser.add_argument('--recovery', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.action == "monitor":
        return monitor()
    if args.data_path is None:
        parser.error("start requires --data-path")
    if args.action == 'stop': print(stop_bot(args.data_path, timeout=max(0, args.timeout), force=args.force))
    else: print(start_bot(args.data_path, recovery=args.recovery))
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except RuntimeError as error:
        print('CONTROL: ' + str(error), file=sys.stderr)
        sys.exit(1)
