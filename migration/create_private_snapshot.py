"""Online, read-only SQLite snapshots. Never imports the running Service or training text."""
from pathlib import Path
import sqlite3,zipfile,json,hashlib,shutil,sys,os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
import argparse
a=argparse.ArgumentParser();a.add_argument('--output-dir',required=True);a.add_argument('--confirmed-stopped',action='store_true');args=a.parse_args()
p=Path(__file__).resolve().parents[1];r=p;out=Path(args.output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
sys.stdout.reconfigure(encoding='utf8');stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
tmp=out/('.snapshot-staging-'+stamp);tmp.mkdir();(tmp/'configuration').mkdir();payload=tmp/'payload';payload.mkdir()
env=dict(dotenv_values(p/'.env'));old=str(p);portable=dict(env)
for key in ('DATA_DIR','BACKUP_DIR','PROGRESS_EXCEL_PATH'):
 value=portable.get(key)
 if value:
  f=Path(value)
  if f.is_absolute() and f.is_relative_to(p):portable[key]=f.relative_to(p).as_posix()
  elif f.is_absolute():
   portable[key]={'DATA_DIR':'data/acceptance-new-group','BACKUP_DIR':'data/backups','PROGRESS_EXCEL_PATH':'exports/工作进度历史.xlsx'}[key]
for key in ('DAILY_NODE','DAILY_ARTIFACT_MODULE'):
 portable.pop(key,None)
portable.update(RUN_MODE='trial',SCHEDULE_ENABLED='false',AI_ENABLED='false')
shutil.copy2(p/'.env',tmp/'configuration/.env.original')
(tmp/'configuration/.env.newpc').write_text('\n'.join(k+'='+json.dumps(v or '',ensure_ascii=False) for k,v in portable.items())+'\n',encoding='utf8')
databases=[];schemas={}
# Only actual/former acceptance, formal and named demonstration stores. Thousands of disposable test-runs are excluded.
selected=[p/'data/team.sqlite3']
active=p/portable.get('DATA_DIR','data')
if active.exists():selected.extend(f for f in active.rglob('*.sqlite3') if not any(x in f.relative_to(active).parts for x in ('backups','logs','test-runs')))
for folder in ('acceptance-new-group','live-tests','workbench-demo','demo'):
 base=p/'data'/folder
 if base.exists():selected.extend(f for f in base.rglob('*.sqlite3') if not any(x in f.relative_to(base).parts for x in ('backups','logs','test-runs')))
for src in sorted(set(selected)):
 if not src.exists():continue
 rel=src.relative_to(p);dst=payload/rel;dst.parent.mkdir(parents=True,exist_ok=True)
 with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True,timeout=15) as source,sqlite3.connect(dst) as target:
  source.backup(target,pages=1000,sleep=.01);assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  tables=[t[0] for t in target.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
  counts={t:target.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in tables}
  schema={t:[dict(name=c[1],type=c[2],notnull=bool(c[3]),pk=c[5]) for c in target.execute('PRAGMA table_info("'+t+'")')] for t in tables}
  schemas[rel.as_posix()]=schema
  role='活动验收' if rel.as_posix() in ('data/acceptance-new-group/team.sqlite3','data/acceptance-new-group/progress-acceptance.sqlite3') else '正式旧库' if rel.as_posix()=='data/team.sqlite3' else '独立模拟或旧验收（不得转成正式记录）'
  databases.append(dict(file=rel.as_posix(),role=role,integrity='ok',counts=counts))
# Opaque file copies only: no training body parsing, indexing, upload or model calls.
for base in (p/'exports',p/'data/materials',p/'data/exports',p/'data/acceptance-new-group/exports',p/'data/live-tests'):
 if not base.exists():continue
 for src in base.rglob('*'):
  if not src.is_file() or (base.name=='live-tests' and 'exports' not in src.relative_to(base).parts):continue
  if src.suffix not in ('.xlsx','.txt','.md','.pdf','.docx','.csv','.json'):continue
  rel=src.relative_to(p);dst=payload/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
(tmp/'configuration/数据库结构与数量.json').write_text(json.dumps(dict(databases=databases,schemas=schemas),ensure_ascii=False,indent=2),encoding='utf8')
files={f.relative_to(tmp).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in tmp.rglob('*') if f.is_file()}
manifest=dict(format=1,created=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),source_root=old,data_relative=portable.get('DATA_DIR','data'),
 files=files,databases=databases,online_snapshot=not args.confirmed_stopped,final_cutover_snapshot=args.confirmed_stopped,
 notes=['每库SQLite一致备份；未停止机器人，跨数据库非全局事务快照。','实际切换需授权停旧实例后重新制作最终快照。','不含运行锁/PID/WAL/SHM、日志、虚拟环境、海量临时测试库或外部桌面培训原文。','云端D1短期中转队列仍在原HTTPS服务，没有把它转成业务数据库。'])
(tmp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
(tmp/'configuration/数据库结构与数量.json').write_text(json.dumps(dict(databases=databases,schemas=schemas),ensure_ascii=False,indent=2),encoding='utf8')
archive=out/('Telegram_Team_Bot_Private_Snapshot_'+stamp+'.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for f in sorted(tmp.rglob('*')):
  if f.is_file():z.write(f,f.relative_to(tmp).as_posix())
with zipfile.ZipFile(archive) as z:
 assert z.testzip() is None
 for name,digest in files.items():assert hashlib.sha256(z.read(name)).hexdigest()==digest
Path(str(archive)+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='utf8')
(out/('snapshot-'+stamp+'-index.json')).write_text(json.dumps(dict(archive=str(archive),staging=str(tmp),manifest=manifest),ensure_ascii=False),encoding='utf8')
print(json.dumps(dict(private_archive=str(archive),databases=len(databases),integrity='ok',credentials_separate=True),ensure_ascii=False))
