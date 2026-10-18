"""Install ONLY the reviewed lifecycle file group into an EXISTING project.

Run from the separate approved release, using the OLD project's Python.
Never stops processes, changes scheduled tasks, loads .env or copies databases.
"""
import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]


def controller(source):
    spec = importlib.util.spec_from_file_location('release_lifecycle', source/'scripts/windows_background.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def paused_tasks(control, target):
    # Read-only: disabled triggers are required BEFORE pausing the old monitor.
    # Never Stop-ScheduledTask: its job tree may also contain the bot.
    code = """$ErrorActionPreference='Stop'; $s=New-Object -ComObject Schedule.Service; $s.Connect();
function Read-Folder($f) { foreach($t in $f.GetTasks(1)) {
 foreach($a in $t.Definition.Actions) {
  if(($a.Arguments -and $a.Arguments.IndexOf($env:LIFECYCLE_ROOT,[StringComparison]::OrdinalIgnoreCase) -ge 0) -or $a.WorkingDirectory -eq $env:LIFECYCLE_ROOT) {
   if($t.Enabled -or $t.State -notin @(1,3)) { throw 'matching-task-not-paused' }
  }
 }
}; foreach($sub in $f.GetFolders(0)){ Read-Folder $sub } }; Read-Folder ($s.GetFolder('\\'))
"""
    env=dict(os.environ, LIFECYCLE_ROOT=str(target))
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],
            env=env,capture_output=True,timeout=25,**control.no_console_options())
    except (OSError,subprocess.TimeoutExpired): raise RuntimeError('无法核实计划任务已禁用；未安装控制文件') from None
    if result.returncode: raise RuntimeError('本项目计划任务仍启用、仍在运行或无法核实；先按升级说明暂停触发并等待退出，不要停止任务进程树')


def quiescent(control, target, data, task_probe=paused_tasks):
    task_probe(control,target)
    rows=control.snapshot()
    # Include the pythonw wrapper and in-flight old start/stop scripts.
    tools=('scripts/login-watch.ps1','scripts/windows_background.py','scripts/start.ps1','scripts/stop.ps1')
    for row in rows:
        cmd=(row.get('CommandLine') or '').lower()
        if any(str(target/name).lower() in cmd for name in tools):
            raise RuntimeError('旧监控或生命周期命令尚未退出；未安装，请等待自然退出并复查')
    bots=[r for r in rows if control.is_bot(r,target)]
    if len(bots)>1: raise RuntimeError('本项目实例不唯一；未安装控制文件')
    if not bots and (not control.runtime_free(data) or not control.runtime_free(target/'data/bot-global-lock')):
        raise RuntimeError('实例锁占用但进程身份未知；未安装控制文件')
    with ExitStack() as stack:
        refs=control.checked_refs(bots,stack)
        if len(refs)!=len(bots): raise RuntimeError('进程状态发生变化；请重新核实，未安装控制文件')
        return [r.identity() for r in refs]


def atomic_bytes(path, raw):
    tmp=path.with_name(path.name+'.lifecycle-tmp')
    try:
        tmp.write_bytes(raw); os.replace(tmp,path)
    finally: tmp.unlink(missing_ok=True)


def install(target, data, backup, source=SOURCE, task_probe=paused_tasks):
    target,data,backup,source=(Path(p).resolve() for p in (target,data,backup,source))
    if any(a==b or a in b.parents or b in a.parents for a,b in ((source,target),(backup,target),(backup,source),(backup,data))):
        raise RuntimeError('升级包、旧项目和专用备份必须互不包含；未安装')
    control=controller(source);control.verify_controls(source,required=True)
    for name in ('team_bot/__main__.py','.venv/Scripts/python.exe','.venv/Scripts/pythonw.exe'):
        if not (target/name).is_file(): raise RuntimeError('目标不是具备运行环境的现有公司项目；未安装')
    names=(*control.CONTROL_FILES,'scripts/lifecycle-control.json')
    for name in names:
        path=target/name
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents) if p!=target.parent):
            raise RuntimeError('目标控制路径存在链接；未安装')
    folder=target/'data/lifecycle-upgrade'
    with control.lifecycle_lock(data):
        original=quiescent(control,target,data,task_probe)
        folder.mkdir(parents=True,exist_ok=True)
        pending=folder/'pending.json';checkpoint=backup/'checkpoint.json'
        if checkpoint.exists():
            info=json.loads(checkpoint.read_text(encoding='utf-8'))
            if not isinstance(info,dict) or set(info.get('old',{}))!=set(names) or info.get('target')!=str(target) or info.get('manifest')!=control.control_digest(source/'scripts/lifecycle-control.json'):
                raise RuntimeError('备份属于其他目录/版本；不能覆盖或继续')
            for name,digest in info['old'].items():
                if digest is not None and control.control_digest(backup/name)!=digest: raise RuntimeError('旧控制文件备份校验失败；未安装')
        else:
            if backup.exists() and any(backup.iterdir()): raise RuntimeError('备份目录非空且没有本次检查点；未覆盖')
            backup.mkdir(parents=True,exist_ok=True)
            info=dict(target=str(target),manifest=control.control_digest(source/'scripts/lifecycle-control.json'),old={})
            for name in names:
                src=target/name
                info['old'][name]=control.control_digest(src) if src.exists() else None
                if src.exists():
                    dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(src.read_bytes())
            control.atomic_json(checkpoint,info)
        control.atomic_json(pending,dict(backup=str(backup),manifest=info['manifest']))
        # Close BOTH legacy stop routes first. Never leave Stop-Process exposed
        # while installing the rest. On failure keep these refusal gates; do not
        # automatically restore unsafe legacy scripts over a partial install.
        gate_ps="throw '生命周期升级未完成；禁止启动/停止，请从原升级包继续安装。'\r\n"
        gate_cmd='@echo off\r\necho Lifecycle upgrade incomplete. Resume the approved installer.\r\nexit /b 1\r\n'
        for name in ('stop.ps1','start.ps1'):
            atomic_bytes(target/'scripts'/name,gate_ps.encode('utf-8-sig'))
        for name in ('stop.cmd','start.cmd'): atomic_bytes(target/name,gate_cmd.encode('ascii'))
        # Install implementations before exposing the final entry scripts.
        order=('scripts/windows_background.py','team_bot/process_control.py','scripts/common.ps1',
               'scripts/login-watch.ps1','scripts/lifecycle-control.json','scripts/start.ps1','scripts/stop.ps1','start.cmd','stop.cmd')
        for name in order: atomic_bytes(target/name,(source/name).read_bytes())
        # Pending blocks runtime calls until EVERY file has been checked.
        manifest=json.loads((source/'scripts/lifecycle-control.json').read_text(encoding='utf-8'))
        if any(control.control_digest(target/name)!=digest for name,digest in manifest['files'].items()):
            raise RuntimeError('安装后的文件校验失败；保持暂停，不允许运行')
        if quiescent(control,target,data,task_probe)!=original:
            raise RuntimeError('安装期间原进程身份变化；保留pending与暂停，请核查后继续')
        control.atomic_json(folder/'complete.json',dict(manifest=info['manifest']))
        pending.unlink()
        control.verify_controls(target,required=True)
    return '完整控制文件组已安装并校验；旧业务源码及运行进程未修改。现在从旧项目目录建立hold。'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target',type=Path,required=True);p.add_argument('--data-path',type=Path,required=True)
    p.add_argument('--backup',type=Path,required=True)
    args=p.parse_args();print(install(args.target,args.data_path,args.backup))


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    try: main()
    except (RuntimeError,OSError,ValueError,KeyError) as error:
        print('未完成：'+(str(error) if isinstance(error,RuntimeError) else '文件或系统检查失败；保留暂停与备份，核查后从同一升级包继续'),file=sys.stderr)
        sys.exit(1)
