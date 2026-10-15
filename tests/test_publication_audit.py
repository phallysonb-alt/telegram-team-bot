"""Synthetic security fixtures only; no online calls or business writes."""
import base64
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publication_audit
import repo_guard as guard
import github_sync


class PublicationAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rules = dict(files=['README.md', 'repo-policy.json'], source_roots={}, templates={})
        self.write('repo-policy.json', json.dumps(self.rules))
        guard.git(self.root, 'init', '-b', 'main')
        guard.git(self.root, 'config', 'user.name', 'Mock')
        guard.git(self.root, 'config', 'user.email', 'mock@example.invalid')
        guard.git(self.root, 'config', 'core.hooksPath', 'no-hooks-in-synthetic-repo')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value, encoding='utf8')

    def commit(self, message='synthetic fixture'):
        guard.git(self.root, 'add', 'README.md', 'repo-policy.json')
        guard.git(self.root, 'commit', '-m', message)

    def template(self, content):
        file = io.BytesIO()
        with zipfile.ZipFile(file, 'w') as z:
            for name, body in content.items():
                z.writestr(name, body)
        raw = file.getvalue()
        self.rules['templates']['templates/mock.xlsx'] = hashlib.sha256(raw).hexdigest()
        return raw

    def test_readonly_identity_and_config_comparison(self):
        self.write('.env', 'DATA_DIR=private\nPROJECT_SECRET=synthetic-secret-for-test-comparison\n')
        db = self.root / 'private/team.sqlite3'
        db.parent.mkdir()
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('CREATE TABLE tg_profiles(uid INTEGER,name TEXT,username TEXT)')
            conn.execute('INSERT INTO tg_profiles VALUES(?,?,?)', (700000001, '模拟员工甲', 'mock_employee'))
            conn.commit()
        before = db.read_bytes()
        known = guard.known_values(self.root)
        self.assertEqual(before, db.read_bytes())
        for content in ['700000001', '模拟员工甲', 'mock_employee']:
            with self.assertRaises(guard.GuardError) as e:
                guard.scan_blob('README.md', content.encode(), self.rules, known)
            self.assertNotIn(content, str(e.exception))
        db.unlink()  # proves the read-only handle was closed, also on Windows
        guard.known_values(self.root)
        self.assertFalse(db.exists())

    def test_encoded_known_secrets(self):
        secret = 'synthetic-secret-' + 'q' * 30
        self.write('.env', 'BRIDGE_SECRET=' + secret)
        known = guard.known_values(self.root)
        for raw in [base64.b64encode(secret.encode()), ''.join('&#'+str(ord(c))+';' for c in secret).encode(),
                    ''.join('%'+format(ord(c), '02x') for c in secret).encode()]:
            with self.assertRaises(guard.GuardError):
                guard.scan_blob('README.md', raw, self.rules, known)

    def test_relations_and_embedded_files_in_templates(self):
        secret = ('ghp_' + 'x' * 36).encode()
        raw = self.template({'_rels/.rels': secret})
        with self.assertRaisesRegex(guard.GuardError, 'GitHub'):
            guard.scan_blob('templates/mock.xlsx', raw, self.rules)
        raw = self.template({'xl/embeddings/object.bin': b'mock'})
        with self.assertRaisesRegex(guard.GuardError, '嵌入'):
            guard.scan_blob('templates/mock.xlsx', raw, self.rules)

    def test_local_path_and_private_key_patterns(self):
        for raw in [('C:' + chr(92) + 'Users' + chr(92) + 'MockAccount').encode(),
                    ('-----BEGIN ' + 'OPENSSH PRIVATE KEY-----').encode(),
                    ('-----BEGIN ' + 'ENCRYPTED PRIVATE KEY-----').encode()]:
            with self.assertRaises(guard.GuardError):
                guard.scan_blob('README.md', raw, self.rules)

    def test_removed_secret_and_commit_message_are_found(self):
        secret = 'ghp_' + 'Q' * 36
        self.write('README.md', secret)
        self.commit()
        self.write('README.md', 'clean HEAD')
        self.commit('fixture ' + ('C:' + chr(92) + 'Users' + chr(92) + 'MockAccount'))
        guard.scan(self.root, 'HEAD')
        report = publication_audit.audit(self.root)
        self.assertEqual(report['commits'], 2)
        self.assertTrue(any(x['rule'].startswith('GitHub') for x in report['findings']))
        self.assertTrue(any('本机用户目录' in x['rule'] for x in report['findings']))
        self.assertNotIn(secret, json.dumps(report))

    def test_tags_and_deleted_branch_objects_are_checked(self):
        self.write('README.md', 'safe')
        self.commit()
        guard.git(self.root, 'tag', '-a', 'mock', '-m', 'ghp_' + 'X' * 36)
        guard.git(self.root, 'checkout', '-b', 'discarded')
        self.write('README.md', 'AIza' + 'X' * 35)
        self.commit()
        guard.git(self.root, 'checkout', 'main')
        guard.git(self.root, 'branch', '-D', 'discarded')
        report = publication_audit.audit(self.root)
        self.assertEqual(report['objects']['tag'], 1)
        self.assertEqual(len(report['findings']), 2)

    def test_node_receipt_uses_portable_placeholder(self):
        with patch.object(github_sync, 'node_executable', return_value=sys.executable):
            result = github_sync.run_checks(self.root, [['{node}', '-c', 'pass']])
        self.assertEqual(result[0]['command'][0], '{node}')
        self.assertEqual(result[0]['exit_code'], 0)


if __name__ == '__main__':
    unittest.main()
