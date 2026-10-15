"""Offline restore into a fresh project only. Never starts polling or touches an old PC."""
import argparse,hashlib,json,os,sqlite3,zipfile
from contextlib import closing
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
def safe(name):
 n=name.replace('\\','/');p=PurePosixPath(n)
 if p.is_absolute() or '..' in p.parts or ':' in n:raise ValueError('备份含不安全路径')
 return n
def inspect(path):
 with zipfile.ZipFile(path) as z:
  if z.testzip():raise ValueError('备份ZIP损坏')
  if len(z.namelist())!=len(set(z.namelist())):raise ValueError('文件校验失败：ZIP含重复成员')
  for n in z.namelist():safe(n)
  manifest=json.loads(z.read('manifest.json'))
  if manifest.get('format')!=1:raise ValueError('不支持的备份格式')
  for n,digest in manifest['files'].items():
   if hashlib.sha256(z.read(n)).hexdigest()!=digest:raise ValueError('文件校验失败')
 return manifest
def restore(path,ack=False):
 if not ack:raise ValueError('恢复需明确 --old-pc-stopped；只能在停止旧电脑并获授权后执行，不等于开启正式模式')
 m=inspect(path)
 if list(ROOT.rglob('instance.lock')):
  # A local process may hold a lock; do not probe or remove it blindly.
  for f in ROOT.rglob('instance.lock'):
   if '.venv' not in f.parts:raise ValueError('发现运行锁文件，先用stop.cmd正常停止并核实本机；不要覆盖运行数据库')
 current=ROOT/'.env'
 if current.exists():
  from dotenv import dotenv_values
  if dotenv_values(current).get('TELEGRAM_BOT_TOKEN'):raise ValueError('当前目录已有实值配置；请在新的空部署目录恢复')
 with zipfile.ZipFile(path) as z:
  members=[n for n in z.namelist() if n.startswith('payload/')]
  for n in members:
   rel=safe(n)[len('payload/'):];dest=ROOT/rel
   if not rel.startswith(('data/','exports/')):raise ValueError('备份包含非业务目录')
   if dest.exists():raise ValueError('目标已有业务文件；拒绝覆盖：'+rel)
  staging=ROOT/'migration/restore-staging';staging.mkdir(parents=True,exist_ok=False)
  try:
   for n in members:
    rel=n[len('payload/'):];dest=staging/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(n))
    if dest.suffix=='.sqlite3':
     with closing(sqlite3.connect(dest)) as db:
      if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('数据库损坏，停止恢复')
   changes=[];old=m['source_root'].replace('\\','/')
   def remap(value):
    if isinstance(value,str):
     v=value.replace('\\','/')
     if v==old or v.startswith(old+'/'):return str(ROOT/Path(v[len(old):].lstrip('/')))
    if isinstance(value,list):return [remap(x) for x in value]
    if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
    return value
   for file in staging.rglob('*.sqlite3'):
    with closing(sqlite3.connect(file)) as db:
     tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
     # Remap only known infrastructure columns. Never touch numbers, notes, audits or task snapshots.
     for table,column in [('documents','path'),('outbox','file'),('settings','value'),('pg_outbox','payload')]:
      if table not in tables:continue
      for key,value in db.execute('SELECT rowid,"'+column+'" FROM "'+table+'"').fetchall():
       if not value:continue
       if table in ('settings','pg_outbox'):
        try:parsed=json.loads(value)
        except (ValueError,TypeError):continue
        if table=='settings':
         setting=db.execute('SELECT key FROM settings WHERE rowid=?',(key,)).fetchone()[0]
         if setting in ('daily_excel_path','progress_excel_path') and isinstance(parsed,str):replacement=remap(parsed)
         elif (setting=='live_test' or setting.startswith(('test_update:','catalog:'))) and isinstance(parsed,dict) and isinstance(parsed.get('path'),str):
          replacement=dict(parsed,path=remap(parsed['path']))
         else:continue
        else:
         if not isinstance(parsed,dict) or not isinstance(parsed.get('file'),str):continue
         replacement=dict(parsed,file=remap(parsed['file']))
        new=json.dumps(replacement,ensure_ascii=False,sort_keys=True) if replacement!=parsed else value
       else:new=remap(value)
       if new!=value:
        db.execute('UPDATE "'+table+'" SET "'+column+'"=? WHERE rowid=?',(new,key));changes.append(dict(file=file.relative_to(staging).as_posix(),table=table,row=key,column=column))
     db.commit()
   for f in staging.rglob('*'):
    if f.is_file():
     dest=ROOT/f.relative_to(staging);dest.parent.mkdir(parents=True,exist_ok=True);os.replace(f,dest)
   current.write_bytes(z.read('configuration/.env.newpc'))
   (ROOT/'migration/HANDOVER_PENDING').write_text('已恢复离线快照；仍须授权切换并核实旧机器已停止，禁止双机polling。',encoding='utf8')
   (ROOT/'migration/路径适配审计.json').write_text(json.dumps(dict(created_from=m['created'],changes=changes,business_payloads_changed=False),ensure_ascii=False,indent=2),encoding='utf8')
   print('私有备份已恢复；未启动机器人，验收模式与关闭调度保留。实际切换前必须检查在线快照之后的新记录。')
  finally:
   import shutil
   shutil.rmtree(staging)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['inspect','restore']);p.add_argument('archive');p.add_argument('--old-pc-stopped',action='store_true');a=p.parse_args()
 if a.action=='inspect':
  m=inspect(a.archive);print(json.dumps(dict(created=m['created'],databases=len(m['databases']),integrity='ok',online_snapshot=m['online_snapshot']),ensure_ascii=False))
 else:restore(a.archive,a.old_pc_stopped)
