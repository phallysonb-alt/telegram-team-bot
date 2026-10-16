"""Local Git/security regression. No GitHub network or business database writes."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import repo_guard as guard
import github_sync as sync

class GitHubSyncTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'repo';self.root.mkdir()
  # Clear inherited hook variables before testing an independent local repository.
  self.env=patch.dict(os.environ,{'GIT_CONFIG_COUNT':'0'});self.env.start()
  self.g('init','-b','main');self.g('config','user.name','Mock Company');self.g('config','user.email','mock@example.invalid')
  self.pol=dict(files=['README.md','AGENTS.md','repo-policy.json','.gitignore','docs/GitHub托管与自动同步.md','docs/note.md'],templates={},source_roots={'scripts/':['.py'],'tests/':['.py']},github_sync=dict(repository='mock/repo',allow_public=False))
  self.write('repo-policy.json',json.dumps(self.pol));self.write('README.md','Mock source\n');self.write('AGENTS.md','Isolated fixture\n');self.write('docs/GitHub托管与自动同步.md','Mock guide\n');self.write('.gitignore','.env\ndata/\n')
 def tearDown(self):self.env.stop();self.temp.cleanup()
 def g(self,*a,check=True):return guard.git(self.root,*a,check=check)
 def write(self,name,data):
  f=self.root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(data,encoding='utf8')
 def commit(self,message='fixture'):
  self.g('add','--all');tid=sync.tree(self.root);sync.receipt(self.root,tid,[dict(command=['isolated-fixture-check'],exit_code=0)]);self.g('commit','-m',message);return sync.head(self.root)
 def test_private_paths_rejected_even_if_forced_staged(self):
  for name in ['data/team.sqlite3','exports/日报历史记录.xlsx','deploy/wrangler.toml','.env','scripts/.env.extra','logs/a.txt','nested/id_rsa']:
   self.assertFalse(guard.permitted(name,self.pol),name)
  self.write('.env','MOCK_SECRET='+('z'*36));self.g('add','-f','.env')
  with self.assertRaises(guard.GuardError):guard.scan(self.root)
 def test_known_secret_not_printed_and_generated_patterns_detected(self):
  secret='private-value-'+('Z'*28);self.write('.env','PROJECT_SECRET='+secret);self.write('README.md','prefix '+secret);self.g('add','--all')
  with self.assertRaises(guard.GuardError) as e:guard.scan(self.root)
  self.assertNotIn(secret,str(e.exception))
  for value in [('ghp_'+'A'*36).encode(),('123456789:'+('X'*35)).encode(),('AIza'+('q'*35)).encode()]:
   with self.assertRaises(guard.GuardError):guard.scan_blob('README.md',value,self.pol)
 def test_template_requires_reviewed_exact_hash(self):
  self.pol['templates']['templates/reviewed.xlsx']='0'*64
  with self.assertRaises(guard.GuardError):guard.scan_blob('templates/reviewed.xlsx',b'not the approved template',self.pol)
 def test_receipt_tracks_exact_index_tree(self):
  self.g('add','--all');tid=sync.tree(self.root)
  with self.assertRaises(guard.GuardError):sync.require_receipt(self.root,tid)
  sync.receipt(self.root,tid,[dict(command=['mock-test'],exit_code=0)]);sync.require_receipt(self.root,tid)
  self.write('README.md','changed');self.g('add','README.md')
  with self.assertRaises(guard.GuardError):sync.require_receipt(self.root,sync.tree(self.root))
 def test_failed_checks_cannot_issue_receipt(self):
  with self.assertRaises(guard.GuardError):sync.receipt(self.root,'abc',[])
  with self.assertRaises(guard.GuardError):sync.receipt(self.root,'abc',[dict(exit_code=1)])
  with self.assertRaises(guard.GuardError):sync.run_checks(self.root,[[sys.executable,'-c','raise SystemExit(2)']])
  self.assertFalse((self.root/'.git/sync-validations').exists());self.assertFalse(sync.head(self.root))
 def test_existing_hook_configuration_not_overwritten(self):
  self.g('config','core.hooksPath','custom-hooks')
  with self.assertRaises(guard.GuardError):sync.install(self.root)
  self.assertEqual(sync.text(self.g('config','core.hooksPath')),'custom-hooks')
 def test_actual_git_commit_hook_rejects_untested_tree(self):
  original=Path(__file__).resolve().parents[1]
  for name in ['scripts/github_sync.py','scripts/repo_guard.py','.githooks/pre-commit']:
   target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original/name,target)
  self.pol['files'].append('.githooks/pre-commit');self.write('repo-policy.json',json.dumps(self.pol));self.g('config','core.hooksPath','.githooks');self.g('add','--all')
  path=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
  with patch.dict(os.environ,{'PATH':path,'PYTHONUTF8':'1'}):
   rejected=self.g('commit','-m','unvalidated mock',check=False)
   self.assertNotEqual(rejected.returncode,0);self.assertIn('测试凭证',rejected.stderr.decode('utf8',errors='replace'));self.assertFalse(sync.head(self.root))
   sync.receipt(self.root,sync.tree(self.root),[dict(command=['isolated-mock-test'],exit_code=0)])
   self.g('commit','-m','validated mock');self.assertTrue(sync.head(self.root))
 def test_remote_identity_and_https_credential_rejection(self):
  self.assertEqual(sync.repo_slug('https://github.com/mock/repo.git'),'mock/repo')
  for url in ['https://user:password@github.com/mock/repo.git','https://example.invalid/mock/repo','file:///tmp/a','https://github.com/mock/repo?secret=yes']:
   with self.assertRaises(guard.GuardError):sync.repo_slug(url)
  self.g('remote','add','origin','https://github.com/mock/repo.git')
  actual_run=subprocess.run
  def public_reply(argv,**kwargs):
   if argv[0]=='gh':return SimpleNamespace(returncode=0,stdout=b'{"isPrivate":false,"nameWithOwner":"mock/repo","viewerPermission":"ADMIN"}')
   return actual_run(argv,**kwargs)
  with patch.object(sync,'gh_executable',return_value='gh'),patch.object(sync.subprocess,'run',side_effect=public_reply):
   with self.assertRaises(guard.GuardError):sync.authorized_remote(self.root)
   self.pol['github_sync']['allow_public']=True;self.write('repo-policy.json',json.dumps(self.pol))
   self.assertEqual(sync.authorized_remote(self.root),'mock/repo')
 def test_authorized_remote_rejects_unknown_repository_and_readonly(self):
  self.g('remote','add','origin','https://github.com/other/repo.git')
  with self.assertRaises(guard.GuardError):sync.authorized_remote(self.root)
  self.g('remote','set-url','origin','https://github.com/mock/repo.git');actual_run=subprocess.run
  def readonly_reply(argv,**kwargs):
   if argv[0]=='gh':return SimpleNamespace(returncode=0,stdout=b'{"isPrivate":false,"nameWithOwner":"mock/repo","viewerPermission":"READ"}')
   return actual_run(argv,**kwargs)
  self.pol['github_sync']['allow_public']=True;self.write('repo-policy.json',json.dumps(self.pol))
  with patch.object(sync,'gh_executable',return_value='gh'),patch.object(sync.subprocess,'run',side_effect=readonly_reply):
   with self.assertRaises(guard.GuardError):sync.authorized_remote(self.root)
 def test_docs_profile_cannot_hide_source_changes(self):
  self.commit();self.write('scripts/new.py','print(1)');self.g('add','--all')
  with self.assertRaises(guard.GuardError):sync.checks_for(self.root,SimpleNamespace(profile='docs',checks_json=None))
 def test_default_gate_includes_bridge_stability(self):
  checks=sync.checks_for(self.root,SimpleNamespace(profile='default',checks_json=None))
  self.assertTrue(any('tests/test_worker_stability.mjs' in command for command in checks))
  self.assertIn(['{python}','-m','team_bot','test'],checks)
 def test_scan_outgoing_history_catches_removed_secret(self):
  old=self.commit();self.write('README.md','ghp_'+'A'*36);bad=self.commit();self.write('README.md','safe again');self.commit()
  guard.scan(self.root,'HEAD')
  with self.assertRaises(guard.GuardError):sync.validate_outgoing(self.root,[bad])
 def test_commit_remains_after_offline_push_and_safe_retry(self):
  self.commit();self.g('remote','add','origin',str(Path(self.temp.name)/'missing.git'));before=sync.head(self.root)
  with patch.object(sync,'authorized_remote',return_value='mock/repo'):
   with self.assertRaises(guard.GuardError):sync.push(self.root)
  self.assertEqual(before,sync.head(self.root))
  bare=Path(self.temp.name)/'bare.git';subprocess.run(['git','init','--bare',str(bare)],capture_output=True,check=True);self.g('remote','set-url','origin',str(bare))
  with patch.object(sync,'authorized_remote',return_value='mock/repo'):sync.push(self.root)
  self.assertEqual(sync.text(self.g('ls-remote','origin','refs/heads/main')).split()[0],before)
 def test_remote_other_changes_never_force_overwritten(self):
  self.commit();bare=Path(self.temp.name)/'bare.git';subprocess.run(['git','init','--bare',str(bare)],capture_output=True,check=True);self.g('remote','add','origin',str(bare))
  with patch.object(sync,'authorized_remote',return_value='mock/repo'):sync.push(self.root)
  other=Path(self.temp.name)/'other';subprocess.run(['git','clone','-b','main',str(bare),str(other)],capture_output=True,check=True)
  guard.git(other,'config','user.name','Other Mock');guard.git(other,'config','user.email','other@example.invalid');(other/'README.md').write_text('remote changed');guard.git(other,'add','--all');guard.git(other,'commit','-m','other');guard.git(other,'push','origin','main')
  remote_before=sync.text(guard.git(other,'rev-parse','HEAD'));self.write('README.md','local changed');self.commit()
  with patch.object(sync,'authorized_remote',return_value='mock/repo'):
   with self.assertRaisesRegex(guard.GuardError,'其他修改'):sync.push(self.root)
  self.assertEqual(sync.text(self.g('ls-remote','origin','refs/heads/main')).split()[0],remote_before)

if __name__=='__main__':unittest.main()
