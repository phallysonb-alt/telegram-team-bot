"""Windows startup plumbing only; never run a second polling loop here.

Use pythonw for the scheduled login action. CREATE_NO_WINDOW, unlike SW_HIDE,
prevents Windows Terminal delegation from allocating a console in the first place.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
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


def start_bot(data_path: Path, root: Path = ROOT) -> int:
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
    # Publish the launcher's PID, preserving existing start/stop/watch validation.
    # If publishing fails, do not leave an untracked new process behind.
    try:
        temporary = data_path / f"process.pid.{os.getpid()}.tmp"
        temporary.write_text(str(child.pid), encoding="ascii")
        os.replace(temporary, data_path / "process.pid")
    except BaseException:
        child.terminate()
        child.wait(timeout=10)
        raise
    time.sleep(3)
    if child.poll() is not None:
        raise RuntimeError("启动失败，请查看数据目录 logs/console-error.log。")
    return child.pid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("monitor", "start"))
    parser.add_argument("--data-path", type=Path)
    args = parser.parse_args()
    if args.action == "monitor":
        return monitor()
    if args.data_path is None:
        parser.error("start requires --data-path")
    print(start_bot(args.data_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
