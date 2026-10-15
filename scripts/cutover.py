"""Local mode switch. Never copies acceptance business records. Bot must be stopped."""
import argparse,json,sqlite3,shutil,sys
from pathlib import Path
from dataclasses import replace
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import dotenv_values
from team_bot.config import load
from team_bot.service import Service
from team_bot.maintenance import InstanceLock

def switch(mode,dry=False):
 c=load();values=dict(dotenv_values(ROOT/'.env'))
 if mode not in ('formal','trial'):raise ValueError('模式只支持 formal 或 trial')
 with InstanceLock(ROOT/'data'/'bot-global-lock'):
  if dry:
   return dict(target=mode,schedule=False,copy_business=False,formal_path='data/team.sqlite3',trial_path='data/acceptance-new-group/team.sqlite3')
  backup=ROOT/'data'/'backups'/('cutover-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'));backup.mkdir(parents=True)
  shutil.copy2(ROOT/'.env',backup/'config.env.private')
  for name,folder in [('formal',ROOT/'data'),('trial',ROOT/'data'/'acceptance-new-group')]:
   src=folder/'team.sqlite3'
   if src.exists():
    with sqlite3.connect(src) as source,sqlite3.connect(backup/(name+'.sqlite3')) as dest:source.backup(dest)
   for pg_name in ('progress.sqlite3','progress-acceptance.sqlite3'):
    pg_source=folder/pg_name
    if pg_source.exists():
     with sqlite3.connect(pg_source) as source,sqlite3.connect(backup/(name+'-'+pg_name)) as dest:source.backup(dest)
  if mode=='formal':
   target=Service(replace(c,data=ROOT/'data',test_run='',run_mode='formal'))
   source=sqlite3.connect(ROOT/'data'/'acceptance-new-group'/'team.sqlite3');source.row_factory=sqlite3.Row
   # Approvals are configuration; all daily/workbench/history business stays in trial.
   for row in source.execute("SELECT e.* FROM employees e JOIN membership m ON m.uid=e.uid WHERE m.chat=? AND m.status IN ('member','administrator','creator','restricted') AND e.uid!=?",(c.chat,c.owner)):
    r=dict(row);profile=source.execute('SELECT * FROM tg_profiles WHERE uid=?',(r['uid'],)).fetchone()
    if not profile:continue
    old=target.db.one('SELECT * FROM employees WHERE uid=?',(r['uid'],))
    if old:
     target.change_employee(c.owner,r['uid'],'验收批准身份切换到正式配置',active=r['active'],approved=r['approved'],required=r['required'])
    else:
     target.db.run('INSERT INTO employees VALUES (?,?,?,?,?,?,?)',(r['uid'],r['code'],profile['name'],r['active'],r['required'],'employee',r['approved']))
     target.db.audit('员工',r['uid'],c.owner,'验收身份配置切换；未复制业务',None,r)
    target.db.run('INSERT OR REPLACE INTO tg_profiles VALUES (?,?,?,?)',tuple(profile))
    request=source.execute('SELECT * FROM onboarding WHERE uid=?',(r['uid'],)).fetchone()
    if request:target.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',tuple(request))
   # Retire old routing without deleting old sessions, messages or audit.
   if target.db.setting('live_test'):target.db.set('archived_live_test_before_new_group',target.db.setting('live_test'))
   target.db.set('live_test',None);target.db.set('isolation_only_run',False);target.db.set('schedule_confirmed',None)
   for name in ('progress','report','management','training'):target.db.set('thread:'+name,c.threads[name])
   target.db.close();source.close()
  values.update(RUN_MODE=mode,DATA_DIR='data' if mode=='formal' else 'data/acceptance-new-group',BACKUP_DIR='data/backups' if mode=='formal' else 'data/acceptance-new-group/backups',TRIAL_RUN_ID='' if mode=='formal' else 'newgroup',SCHEDULE_ENABLED='false',AI_ENABLED='false')
  (ROOT/'.env').write_text('\n'.join(k+'='+json.dumps(v or '',ensure_ascii=False) for k,v in values.items())+'\n',encoding='utf-8')
  return dict(target=mode,schedule=False,copy_business=False,backup=str(backup))

if __name__=='__main__':
 sys.stdout.reconfigure(encoding='utf-8');p=argparse.ArgumentParser();p.add_argument('mode',choices=['formal','trial']);p.add_argument('--dry-run',action='store_true');args=p.parse_args()
 try:print(json.dumps(switch(args.mode,args.dry_run),ensure_ascii=False))
 except Exception as e:print('切换未完成：'+type(e).__name__);sys.exit(1)
