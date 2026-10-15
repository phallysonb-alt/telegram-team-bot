import unittest,tempfile,sqlite3,importlib.util
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from telegram import User
from test_daily_v2 import DailyBase,raw
from team_bot.service import Service
from team_bot.config import ROOT

class CutoverTests(DailyBase):
 def test_formal_copies_only_verified_identity_not_trial_business_and_rollback_preserves(self):
  folder=self.root/'cutover';folder.mkdir();(folder/'.env').write_text('SCHEDULE_ENABLED=false\nAI_ENABLED=false\n',encoding='utf-8')
  c=replace(self.c,data=folder/'data/acceptance-new-group',backups=folder/'data/acceptance-new-group/backups',identity_mode='telegram',run_mode='trial',test_run='newgroup',membership_required=True,schedule_allowed=False)
  source=Service(c);source.open_identity(c.owner,User(456,'真实身份测试',False));source.db.run('INSERT INTO membership VALUES (?,?,?,?)',(456,c.chat,'member','2026-10-10'))
  source.open_identity(c.owner,User(c.owner,'所有者演示',False));source.open_identity(c.owner,User(789,'未校验模拟',False));draft,_,_=source.draft(456,raw(),'fixture');source.confirm(456,draft)
  source.db.close();spec=importlib.util.spec_from_file_location('cutover_fixture',ROOT/'scripts/cutover.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  with patch.object(module,'ROOT',folder),patch.object(module,'load',return_value=c):
   result=module.switch('formal');self.assertFalse(result['copy_business']);self.assertFalse(result['schedule'])
   db=sqlite3.connect(folder/'data/team.sqlite3');self.assertEqual(db.execute('SELECT uid FROM employees').fetchall(),[(456,)]);self.assertEqual(db.execute('SELECT COUNT(*) FROM daily_records').fetchone()[0],0);self.assertEqual(db.execute('SELECT COUNT(*) FROM wb_events').fetchone()[0],0);db.close()
   module.switch('trial');self.assertIn('RUN_MODE="trial"',(folder/'.env').read_text(encoding='utf-8'));self.assertTrue((folder/'data/team.sqlite3').exists())
