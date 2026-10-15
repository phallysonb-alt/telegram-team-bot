"""Startup-only tests. Real Windows probes use dummy scripts, never Telegram."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_background", ROOT / "scripts/windows_background.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@unittest.skipUnless(os.name == "nt", "Windows startup plumbing")
class WindowsStartupTests(unittest.TestCase):
    def test_bot_launcher_retains_pid_logs_and_no_console_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            child = Mock(pid=9876)
            child.poll.return_value = None
            with patch.object(launcher.subprocess, "Popen", return_value=child) as start, \
                    patch.object(launcher.time, "sleep"):
                self.assertEqual(launcher.start_bot(root / "data", root), 9876)
            self.assertEqual((root / "data/process.pid").read_text(), "9876")
            self.assertEqual(start.call_args.args[0], [str(root / ".venv/Scripts/pythonw.exe"), "-m", "team_bot", "run"])
            self.assertEqual(start.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertEqual(start.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue((root / "data/logs/console-error.log").exists())

    def test_failed_launch_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as td:
            child = Mock(pid=9876)
            child.poll.return_value = 1
            with patch.object(launcher.subprocess, "Popen", return_value=child), \
                    patch.object(launcher.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "启动失败"):
                    launcher.start_bot(Path(td) / "data", Path(td))

    def test_pid_write_failure_cleans_up_only_new_child(self):
        with tempfile.TemporaryDirectory() as td:
            child = Mock(pid=9876)
            with patch.object(launcher.subprocess, "Popen", return_value=child), \
                    patch.object(launcher.os, "replace", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    launcher.start_bot(Path(td) / "data", Path(td))
            child.terminate.assert_called_once()
            child.wait.assert_called_once()

    def test_monitor_propagates_exit_for_task_scheduler_retry(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(launcher.subprocess, "call", return_value=7) as call:
                self.assertEqual(launcher.monitor(Path(td)), 7)
            self.assertEqual(call.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertIn("-NonInteractive", call.call_args.args[0])

    @unittest.skipUnless(os.name == "nt", "Windows native process check")
    def test_real_gui_launcher_and_powershell_have_no_console(self):
        # A separate temporary watcher records console attachment, and runs the
        # same native-Python invocation used by `team_bot paths` in the watcher.
        # It never imports the bot or reads company configuration.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/windows_background.py", scripts)
            shutil.copy2(ROOT / "scripts/common.ps1", scripts)
            (scripts / "probe.py").write_text(
                "import ctypes,json\n"
                "k=ctypes.WinDLL('kernel32',use_last_error=True)\n"
                "a=(ctypes.c_ulong*16)()\n"
                "print(json.dumps({'console_clients':k.GetConsoleProcessList(a,16)}))\n",
                encoding="utf8",
            )
            probe = str(scripts / "probe.py").replace("'", "''")
            python = str(ROOT / ".venv/Scripts/pythonw.exe").replace("'", "''")
            (scripts / "login-watch.ps1").write_text(
                "Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class ConsoleProbe{[DllImport(\"kernel32.dll\")]public static extern IntPtr GetConsoleWindow();}'\n"
                "Write-Output ('ps_console=' + [ConsoleProbe]::GetConsoleWindow().ToInt64())\n"
                f". \"$PSScriptRoot/common.ps1\"\n$PythonWindowless='{python}'\n"
                f"Invoke-WindowlessPython -PythonArgs @('{probe}')\nexit 7\n", encoding="utf8",
            )
            pythonw = ROOT / ".venv/Scripts/pythonw.exe"
            result = subprocess.run([str(pythonw), str(scripts / "windows_background.py"), "monitor"],
                                    cwd=root, timeout=20, **launcher.no_console_options())
            self.assertEqual(result.returncode, 7)
            log = (root / "data/logs/login-watch.log").read_text(encoding="utf8")
            self.assertIn("ps_console=0", log)
            self.assertIn('"console_clients": 0', log)

    @unittest.skipUnless(os.name == "nt", "Windows watcher fixture")
    def test_watcher_retries_failed_recovery_and_honors_stop_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = root / "scripts"
            scripts.mkdir()
            data = root / "data"
            data.mkdir()
            (root / "team_bot").mkdir()
            (root / "team_bot/__init__.py").write_text("")
            (root / "team_bot/__main__.py").write_text("print(" + repr(str(data)) + ")")
            shutil.copy2(ROOT / "scripts/login-watch.ps1", scripts)
            python = str(ROOT / ".venv/Scripts/pythonw.exe").replace("'", "''")
            (scripts / "common.ps1").write_text(
                (ROOT / "scripts/common.ps1").read_text(encoding="utf-8-sig") +
                f"\n$PythonWindowless='{python}'\n"
                "function Start-Sleep { param([int]$Seconds) }\n", encoding="utf-8-sig")
            (scripts / "start.ps1").write_text(
                "$countPath=Join-Path $ProjectRoot 'attempts'\n"
                "if (-not(Test-Path $countPath)){Set-Content $countPath 1;throw 'fixture retry'}\n"
                "Set-Content $countPath 2\n"
                "New-Item -ItemType File -Path (Join-Path $DataPath 'autostart.stop')|Out-Null\n", encoding="utf8")
            code = launcher.monitor(root)
            self.assertEqual(code, 0, (root / 'data/logs/login-watch.log').read_text(errors='replace'))
            self.assertEqual((root / "attempts").read_text().strip(), "2")
            self.assertTrue((data / "autostart.stop").exists())

    def test_real_start_duplicate_guard_and_stop_with_dummy_service(self):
        # Exercise the real start/stop scripts against a disposable Python module.
        # The fake module has no Telegram imports, credentials or business data.
        with tempfile.TemporaryDirectory(prefix="startup fixture ") as td:
            root = Path(td)
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("common.ps1", "start.ps1", "stop.ps1", "windows_background.py"):
                shutil.copy2(ROOT / "scripts" / name, scripts)
            venv = root / ".venv"
            (venv / "Scripts").mkdir(parents=True)
            for name in ("python.exe", "pythonw.exe"):
                shutil.copy2(ROOT / ".venv/Scripts" / name, venv / "Scripts" / name)
            shutil.copy2(ROOT / ".venv/pyvenv.cfg", venv)
            data = root / "fixture data"
            data.mkdir()
            module = root / "team_bot"
            module.mkdir()
            (module / "__init__.py").write_text("")
            (module / "__main__.py").write_text(
                "import ctypes,json,sys,time\nfrom pathlib import Path\n"
                f"data=Path({str(data)!r})\n"
                "if sys.argv[1]=='paths':print(data)\n"
                "else:\n"
                " k=ctypes.WinDLL('kernel32');a=(ctypes.c_ulong*16)()\n"
                " (data/'native.json').write_text(json.dumps({'console_clients':k.GetConsoleProcessList(a,16)}))\n"
                " while True:time.sleep(0.1)\n", encoding="utf8")
            ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            def invoke(name):
                return subprocess.run([str(ps), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(scripts/name)],
                                      cwd=root, capture_output=True, timeout=20, **launcher.no_console_options())
            try:
                first = invoke("start.ps1")
                self.assertEqual(first.returncode, 0, first.stderr.decode(errors="replace"))
                pid = (data / "process.pid").read_text()
                self.assertEqual(json.loads((data / "native.json").read_text())["console_clients"], 0)
                second = invoke("start.ps1")
                self.assertNotEqual(second.returncode, 0)
                self.assertEqual((data / "process.pid").read_text(), pid)
            finally:
                stopped = invoke("stop.ps1")
                self.assertEqual(stopped.returncode, 0, stopped.stderr.decode(errors="replace"))
            self.assertFalse((data / "process.pid").exists())
            self.assertTrue((data / "autostart.stop").exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell parser")
    def test_powershell_scripts_parse_and_task_keeps_recovery_policy(self):
        ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        for name in ("common.ps1", "autostart.ps1", "login-watch.ps1", "start.ps1", "stop.ps1"):
            file = str(ROOT / "scripts" / name).replace("'", "''")
            check = f"$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile('{file}',[ref]$t,[ref]$e)|Out-Null;if($e.Count){{exit 1}}"
            self.assertEqual(subprocess.run([str(ps), "-NoProfile", "-Command", check],
                             timeout=15, **launcher.no_console_options()).returncode, 0, name)
        text = (ROOT / "scripts/autostart.ps1").read_text(encoding="utf-8-sig")
        for required in ("pythonw.exe", "windows_background.py", "IgnoreNew", "RestartCount 3", "AtLogOn"):
            self.assertIn(required, text)

    def test_identity_guard_accepts_old_and_new_launchers_only(self):
        common = str(ROOT / "scripts/common.ps1").replace("'", "''")
        command = (
            f". '{common}';"
            "$a=Test-BotProcess ([pscustomobject]@{ExecutablePath=$PythonExe;CommandLine='python -m team_bot run'});"
            "$b=Test-BotProcess ([pscustomobject]@{ExecutablePath=$PythonWindowless;CommandLine='pythonw -m team_bot run'});"
            "$c=Test-BotProcess ([pscustomobject]@{ExecutablePath='C:\\other\\pythonw.exe';CommandLine='pythonw -m team_bot run'});"
            "$d=Test-BotProcess ([pscustomobject]@{ExecutablePath=$PythonExe;CommandLine='python other_app.py'});"
            "if (-not $a -or -not $b -or $c -or $d) {exit 1}"
        )
        ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.assertEqual(subprocess.run([str(ps), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                         timeout=15, **launcher.no_console_options()).returncode, 0)


if __name__ == "__main__":
    unittest.main()
