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
    try:
        p = subprocess.run([str(ps), '-NoProfile', '-NonInteractive', '-Command', code], capture_output=True, timeout=25, **no_console_options())
    except subprocess.TimeoutExpired:
        raise RuntimeError('Windows CIM进程核验超时；未执行启动/停止，请稍后以相同用户重试') from None
    except OSError:
        raise RuntimeError('无法运行Windows进程核验；未执行启动/停止') from None
    if p.returncode: raise RuntimeError('无法核实Windows进程，未执行启动/强制停止')
    try: rows = json.loads(p.stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError): raise RuntimeError('Windows进程快照无效；未执行启动/停止') from None
    if not isinstance(rows, (list, dict)): raise RuntimeError('Windows进程快照不完整；未执行启动/停止')
    rows = rows if isinstance(rows, list) else [rows]
    if not rows or any(not isinstance(r, dict) or not {'ProcessId', 'ParentProcessId', 'Birth', 'ExecutablePath', 'CommandLine'} <= r.keys() for r in rows):
        raise RuntimeError('Windows进程快照字段不完整；未执行启动/停止')
    return rows


def is_bot(row, root):
    return (row.get('ExecutablePath') and Path(row['ExecutablePath']).resolve() in
            {root / '.venv/Scripts/python.exe', root / '.venv/Scripts/pythonw.exe'}
            and re.search(r'(?:^|\s)-m\s+team_bot\s+run(?:\s|$)', row.get('CommandLine') or '') is not None)


def process_type():
    spec = importlib.util.spec_from_file_location('local_process_control', ROOT / 'team_bot/process_control.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.ProcessRef


def checked_ref(row, stack, terminate=False):
    try: ref = stack.enter_context(process_type()(row['ProcessId'], terminate=terminate))
    except ProcessLookupError: return None  # Normal exit since the CIM snapshot.
    except OSError: raise RuntimeError('无法核实进程句柄；权限或系统故障，记录保留，未误认进程退出') from None
    # CIM creation time has microsecond resolution; kernel handle has 100ns.
    if ref.birth // 10 != int(row['Birth']) // 10 or ref.image != Path(row['ExecutablePath']).resolve():
        raise RuntimeError('进程身份已变化，未处理该PID')
    return ref


def checked_refs(chain, stack, terminate=False):
    return [ref for row in chain if (ref := checked_ref(row, stack, terminate)) is not None]


def stop_supported(data_path, refs):
    try: value = json.loads((data_path / 'process.stop-capability.json').read_text(encoding='utf-8'))
    except FileNotFoundError: return False
    except (OSError, ValueError): raise RuntimeError('停止能力记录无法核实；保留记录，未停止进程') from None
    if not isinstance(value, dict): raise RuntimeError('停止能力记录格式无效；保留记录，未停止进程')
    return value.get('protocol') == 1 and any(value.get('pid') == r.pid and value.get('birth') == r.birth for r in refs)


def is_watcher(row, root):
    image = Path(row.get('ExecutablePath') or '')
    script = re.escape(str(root / 'scripts/login-watch.ps1'))
    return image.name.lower() == 'powershell.exe' and re.search(r'-File\s+[\"\']?' + script + r'[\"\']?(?:\s|$)', row.get('CommandLine') or '', re.I) is not None


def upgrade_held(data_path, root):
    path = data_path / 'upgrade-hold.json'
    if not path.exists(): return False
    try: value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): raise RuntimeError('升级暂停记录无效；请核查，未启动/停止进程') from None
    if not isinstance(value, dict): raise RuntimeError('升级暂停记录格式无效；未操作进程')
    if value.get('protocol') != 1 or value.get('root') != str(root): raise RuntimeError('升级暂停记录目录不符，未操作进程')
    return True


def upgrade_control(data_path, root=ROOT, release=False):
    """Future explicitly authorized upgrade ONLY. Never stops a monitor/bot.

    The persistent hold is checked even when an OLD watcher invokes start.ps1
    without -Recovery. Release requires the watcher to have exited first.
    """
    root, data_path = root.resolve(), data_path.resolve()
    with lifecycle_lock(data_path):
        rows = snapshot(); bots = [r for r in rows if is_bot(r, root)]
        watchers = [r for r in rows if is_watcher(r, root)]
        if len(bots) > 1: raise RuntimeError('多个本项目实例，未操作升级暂停记录')
        if not bots and (not runtime_free(data_path) or not runtime_free(root / 'data/bot-global-lock')):
            raise RuntimeError('实例锁占用但进程身份未知；未更改升级暂停记录')
        with ExitStack() as stack:
            refs = checked_refs(bots + watchers, stack)
            if release:
                if not upgrade_held(data_path, root): raise RuntimeError('没有待解除的升级暂停记录')
                if watchers: raise RuntimeError('守护进程仍在运行，不能解除升级暂停；先核实停止旧监控')
                # The stop-capability belongs to the interpreter child, not
                # necessarily the venv launcher. Verify the entire known tree.
                if bots:
                    chain, unknown = project_tree(bots[0], rows, root)
                    if unknown: raise RuntimeError('存在未知子进程，不能解除升级暂停')
                    current = checked_refs(chain, stack)
                    if not current or not stop_supported(data_path, current): raise RuntimeError('仍是旧实例或尚未就绪；不能解除升级暂停')
                elif not runtime_free(data_path) or not runtime_free(root / 'data/bot-global-lock'):
                    raise RuntimeError('实例锁未释放，不能解除升级暂停')
                (data_path / 'upgrade-hold.json').unlink()
                return '升级暂停已解除；人工停止标记仍保留，使用 start.cmd 显式恢复。'
            atomic_json(data_path / 'upgrade-hold.json', dict(protocol=1, root=str(root), verified=[r.identity() for r in refs]))
    return '升级暂停已建立；尚未停止任何进程。下一步另行核实暂停计划任务及旧监控。'


def project_tree(root_row, rows, root):
    chain = [root_row]; parents = {root_row['ProcessId']: root_row['Birth']}; seen = set(parents)
    while parents:
        # A historical child may retain a reused ParentProcessId. It cannot
        # belong to a parent born AFTER it. Also bound traversal against cycles.
        children = [r for r in rows if r['ParentProcessId'] in parents and r['ProcessId'] not in seen
                    and r.get('Birth') is not None and r['Birth'] >= parents[r['ParentProcessId']]]
        chain.extend(children); parents = {r['ProcessId']: r['Birth'] for r in children}; seen.update(parents)
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
        refs = checked_refs(chain, stack, terminate=True)
        request_stop(data_path, refs)
        if not drain(refs, 10):
            for ref in reversed(refs): ref.kill()
            drain(refs, 5)
    child.wait(timeout=10)


def start_bot(data_path: Path, root: Path = ROOT, recovery=False, resume=False) -> int:
    root, data_path = root.resolve(), data_path.resolve()
    with lifecycle_lock(data_path):
        if upgrade_held(data_path, root): raise RuntimeError('升级暂停中；禁止守护或人工启动，先完成升级/回退步骤')
        if (data_path / 'autostart.stop').exists() and (recovery or not resume): raise RuntimeError('人工停止标记存在；监控不会重启，人工恢复请用 start.cmd（-Resume）')
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
            if ref is None: raise RuntimeError('新启动进程在核验时已经退出；未写入PID')
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


def stop_bot(data_path, root=ROOT, timeout=60, force=False, legacy_upgrade=False):
    root, data_path = root.resolve(), data_path.resolve()
    with lifecycle_lock(data_path):
        rows = snapshot(); bots = [r for r in rows if is_bot(r, root)]
        if len(bots) > 1: raise RuntimeError('发现多个本项目进程，请核查后处理，未强制停止')
        if not bots:
            if not runtime_free(data_path) or not runtime_free(root / 'data/bot-global-lock'):
                raise RuntimeError('实例锁占用但进程身份未知，未误杀或清除PID')
            # Both original runtime locks and the process inventory prove no
            # bot owns this record. A reused numeric PID is never a kill target.
        else:
            chain, unknown = project_tree(bots[0], rows, root)
            with ExitStack() as stack:
                refs = checked_refs([r for r in chain if r['ProcessId'] not in unknown], stack, terminate=force)
                try:
                    saved = json.loads((data_path / 'process.identity.json').read_text())
                    if not isinstance(saved, dict): raise RuntimeError('进程身份记录格式无效，未停止进程')
                    if any(saved.get('pid') == r.pid and saved.get('birth') != r.birth for r in refs):
                        raise RuntimeError('PID创建时间与记录不符，未停止该进程')
                except FileNotFoundError: pass  # Verified legacy PID-only launch.
                if refs and not stop_supported(data_path, refs):
                    if not (legacy_upgrade and force and upgrade_held(data_path, root)):
                        raise RuntimeError('运行实例未声明支持正常停止（旧版或仍在初始化）。未停止；请等待就绪，或另行授权按升级说明处理旧实例')
                    if any(is_watcher(r, root) for r in rows): raise RuntimeError('旧守护仍在运行；拒绝升级停止，先暂停计划任务并核实停止监控')
                (data_path / 'autostart.stop').write_text('manual stop', encoding='ascii')
                request_stop(data_path, refs)
                if not drain(refs, timeout):
                    if not force: raise RuntimeError('可控停止超时；PID和暂停标记保留。核实后可用 stop.cmd -Force -TimeoutSeconds 0；强停可能中断处理中消息')
                    if unknown: raise RuntimeError('存在未知子进程，拒绝强制停止')
                    for ref in reversed(refs): ref.kill()
                    if not drain(refs, 5): raise RuntimeError('已核实进程尚未退出，PID保留')
        # A snapshot exit race is safe only after both original locks release.
        if not runtime_free(data_path) or not runtime_free(root / 'data/bot-global-lock'):
            raise RuntimeError('进程已退出但实例锁未释放；PID保留，请稍后重试')
        (data_path / 'autostart.stop').write_text('manual stop', encoding='ascii')
        for name in ('process.pid', 'process.identity.json', 'stop-request.json'):
            (data_path / name).unlink(missing_ok=True)
    return '机器人已停止；持久队列保留，监控已暂停。'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("monitor", "start", "stop", "upgrade-hold", "upgrade-release"))
    parser.add_argument("--data-path", type=Path)
    parser.add_argument('--recovery', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--legacy-upgrade', action='store_true')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.action == "monitor":
        return monitor()
    if args.data_path is None:
        parser.error("requires --data-path")
    if args.action.startswith('upgrade-'): print(upgrade_control(args.data_path, release=args.action == 'upgrade-release'))
    elif args.action == 'stop': print(stop_bot(args.data_path, timeout=max(0, args.timeout), force=args.force, legacy_upgrade=args.legacy_upgrade))
    else: print(start_bot(args.data_path, recovery=args.recovery, resume=args.resume))
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except RuntimeError as error:
        print('CONTROL: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('CONTROL: 系统调用或进程记录处理失败；未报告成功，请核查本公司权限、磁盘和进程状态后重试', file=sys.stderr)
        sys.exit(1)
