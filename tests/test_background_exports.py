"""Export subprocess behavior only. Fixtures never read business databases."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from team_bot import daily_history, progress_export

ROOT = Path(__file__).resolve().parents[1]


class BackgroundExportTests(unittest.TestCase):
    def capture_export_options(self, kind):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'node_modules/@oai/artifact-tool').mkdir(parents=True)
            data = dict(days=[], label='独立模拟', revision=17)
            observed = {}
            def run(args, **options):
                observed.update(options)
                observed['args'] = args
                self.assertEqual(json.loads(Path(args[2]).read_text(encoding='utf8')), data)
                with zipfile.ZipFile(args[3], 'w') as book:
                    book.writestr('fixture.txt', 'isolated fake workbook')
                return SimpleNamespace(returncode=0)
            target = root / 'result.xlsx'
            with patch.dict(os.environ, DAILY_NODE='node.exe', DAILY_ARTIFACT_MODULE=str(root)), \
                    patch.object(subprocess, 'run', side_effect=run):
                if kind == 'daily':
                    daily_history.write_excel(data, target)
                else:
                    progress_export.write_export(data, target)
            self.assertTrue(target.exists())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['node_modules', 'result.xlsx'])
            return observed

    def test_daily_and_progress_export_suppress_console_without_changing_contract(self):
        for kind in ('daily', 'progress'):
            with self.subTest(kind=kind):
                result = self.capture_export_options(kind)
                self.assertEqual(result['creationflags'], subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.assertTrue(result['capture_output'])
                self.assertEqual(result['stdin'], subprocess.DEVNULL)
                self.assertEqual(result['timeout'], 180)
                self.assertIn(kind + '_xlsx.mjs', result['args'][1])

    def test_failed_child_keeps_existing_workbook(self):
        for kind in ('daily', 'progress'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / 'node_modules/@oai/artifact-tool').mkdir(parents=True)
                target = root / 'result.xlsx'
                target.write_bytes(b'existing-valid-fixture')
                with patch.dict(os.environ, DAILY_NODE='node.exe', DAILY_ARTIFACT_MODULE=str(root)), \
                        patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=4)):
                    with self.assertRaises((ValueError, RuntimeError)):
                        (daily_history.write_excel if kind == 'daily' else progress_export.write_export)({}, target)
                self.assertEqual(target.read_bytes(), b'existing-valid-fixture')

    @unittest.skipUnless(os.name == 'nt', 'Windows native console probe')
    def test_real_node_child_has_no_console_window(self):
        node = os.environ.get('DAILY_NODE') or shutil.which('node')
        if not node:
            candidate = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if candidate.exists():
                node = str(candidate)
        if not node:
            self.skipTest('Node not installed; native probe unavailable')
        options = self.capture_export_options('progress')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            probe = root / 'probe.py'
            output = root / 'console.json'
            probe.write_text(
                "import ctypes,json,sys\nfrom pathlib import Path\n"
                "k=ctypes.WinDLL('kernel32',use_last_error=True);k.FreeConsole()\n"
                "k.GetConsoleWindow.restype=ctypes.c_void_p\n"
                "attached=bool(k.AttachConsole(int(sys.argv[1])))\n"
                "error=ctypes.get_last_error()\n"
                "window=k.GetConsoleWindow() if attached else None\n"
                "if attached:k.FreeConsole()\n"
                "Path(sys.argv[2]).write_text(json.dumps({'attached':attached,'error':error,'window':window}))\n", encoding='utf8')
            child = subprocess.Popen([node, '-e', 'setInterval(()=>{},1000)'],
                                     creationflags=options['creationflags'],
                                     stdin=options['stdin'],stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                pythonw = Path(sys.executable).with_name('pythonw.exe')
                checked = subprocess.run([str(pythonw), str(probe), str(child.pid), str(output)],
                                         creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=15)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                status = json.loads(output.read_text())
                # CREATE_NO_WINDOW may retain a console server internally. It
                # must not have a console window; attachment alone is no proof.
                self.assertFalse(status['window'], status)
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
