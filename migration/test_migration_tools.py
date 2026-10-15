import unittest,tempfile,zipfile,json,hashlib,sqlite3,importlib.util
from contextlib import closing
from pathlib import Path
class MigrationToolsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  spec=importlib.util.spec_from_file_location('restore_tool',Path(__file__).with_name('restore_private.py'));self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m);self.m.ROOT=self.root
  db=self.root/'fixture.sqlite3'
  with closing(sqlite3.connect(db)) as c:
   c.executescript('CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE daily_records(uid INTEGER,day TEXT,payload TEXT); CREATE TABLE documents(id INTEGER,path TEXT);')
   c.execute('INSERT INTO settings VALUES (?,?)',('daily_excel_path',json.dumps('C:/old/project/exports/日报历史记录.xlsx')))
   c.execute('INSERT INTO settings VALUES (?,?)',('ui_note_snapshot',json.dumps('C:/old/project/员工自己填写的原文，不能改')))
   c.execute('INSERT INTO daily_records VALUES (7,?,?)',('2026-10-10','{"新增":0,"备注":"不可改原文"}'))
   c.execute('INSERT INTO documents VALUES (1,?)',('C:/old/project/data/materials/mock.txt',))
   c.commit()
  self.original=db.read_bytes();self.archive=self.root/'private.zip'
  data={'payload/data/acceptance-new-group/team.sqlite3':self.original,'configuration/.env.newpc':b'RUN_MODE=trial\nSCHEDULE_ENABLED=false\nAI_ENABLED=false\nTELEGRAM_BOT_TOKEN=\n'}
  manifest=dict(format=1,source_root='C:/old/project',created='mock only',databases=[{}],online_snapshot=True,files={n:hashlib.sha256(v).hexdigest() for n,v in data.items()})
  with zipfile.ZipFile(self.archive,'w') as z:
   z.writestr('manifest.json',json.dumps(manifest))
   for n,v in data.items():z.writestr(n,v)
 def tearDown(self):self.tmp.cleanup()
 def test_inspection_no_writes(self):
  self.m.inspect(self.archive);self.assertFalse((self.root/'data').exists())
 def test_requires_explicit_stopped_flag(self):
  with self.assertRaisesRegex(ValueError,'old-pc-stopped'):self.m.restore(self.archive)
 def test_restore_keeps_business_and_remaps_only_paths(self):
  self.m.restore(self.archive,True)
  with closing(sqlite3.connect(self.root/'data/acceptance-new-group/team.sqlite3')) as c:
   self.assertEqual(c.execute('SELECT * FROM daily_records').fetchall(),[(7,'2026-10-10','{"新增":0,"备注":"不可改原文"}')])
   self.assertEqual(json.loads(c.execute('SELECT value FROM settings').fetchone()[0]),str(self.root/'exports/日报历史记录.xlsx'))
   self.assertEqual(json.loads(c.execute("SELECT value FROM settings WHERE key='ui_note_snapshot'").fetchone()[0]),'C:/old/project/员工自己填写的原文，不能改')
  self.assertTrue((self.root/'migration/HANDOVER_PENDING').exists())
  with self.assertRaisesRegex(ValueError,'已有业务文件'):self.m.restore(self.archive,True)
 def test_tamper_rejected_before_restore(self):
  with zipfile.ZipFile(self.archive,'a') as z:z.writestr('configuration/.env.newpc',b'tampered')
  with self.assertRaisesRegex(ValueError,'校验失败'):self.m.inspect(self.archive)
  self.assertFalse((self.root/'data').exists())
 def test_path_traversal_rejected(self):
  with zipfile.ZipFile(self.archive,'a') as z:z.writestr('payload/../../leak',b'bad')
  with self.assertRaisesRegex(ValueError,'不安全'):self.m.inspect(self.archive)
 def test_running_lock_blocks_restore(self):
  (self.root/'instance.lock').write_bytes(b'0')
  with self.assertRaisesRegex(ValueError,'运行锁'):self.m.restore(self.archive,True)
if __name__=='__main__':unittest.main()
