"""Read-only progress ledger to an independent XLSX; never touches daily XLSX."""
import json,os,subprocess,uuid,zipfile,asyncio,shutil
from pathlib import Path
from .config import ROOT

def write_history(p,uid):
 p.admin(uid)
 from .progress_history_v2 import data
 return write_export(data(p),export_path(p))

def export_path(p):return Path(os.environ.get('PROGRESS_EXCEL_PATH') or p.db.setting('progress_excel_path',str(p.c.data/'exports'/'工作进度历史.xlsx')))

def write_export(data,dest=None):
 dest=Path(dest or ROOT/'exports'/'工作进度历史.xlsx');dest.parent.mkdir(parents=True,exist_ok=True)
 from .portable_excel import artifact_runtime
 runtime=artifact_runtime();token=uuid.uuid4().hex;source=dest.parent/(token+'.json');temp=dest.parent/(token+'.xlsx');source.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
 try:
  if runtime is not None:
   node,module=runtime;env=dict(os.environ,DAILY_ARTIFACT_MODULE=str(module))
   result=subprocess.run([node,str(ROOT/'scripts/progress_xlsx.mjs'),str(source),str(temp)],capture_output=True,stdin=subprocess.DEVNULL,env=env,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
   if result.returncode:raise ValueError('进度历史导出暂不可用，请重试；反馈记录已保留')
  else:
   from .progress_portable_xlsx import write
   write(data,temp)
  with zipfile.ZipFile(temp) as z:
   if z.testzip():raise ValueError('导出完整性检查失败')
  os.replace(temp,dest);return dest
 except PermissionError:raise ValueError('请关闭工作进度历史Excel后重试；日报文件未受影响')
 finally:source.unlink(missing_ok=True);temp.unlink(missing_ok=True);Path(str(temp)+'.inspect.ndjson').unlink(missing_ok=True)

async def ensure_export(p):
 if getattr(p,'_excel_busy',False):return False
 p._excel_busy=True
 try:
  target=export_path(p)
  if not p.db.setting('progress_excel_dirty',True) and target.exists():return True
  from .progress_history_v2 import data
  snapshot=data(p)
  try:await asyncio.to_thread(write_export,snapshot,target)
  except Exception as e:
   p.db.set('progress_excel_dirty',True);p.db.set('progress_excel_error',dict(type=type(e).__name__,at=p.now()));return False
  p.db.set('progress_excel_done',snapshot['revision']);p.db.set('progress_excel_error',None)
  clean=snapshot['revision']==p.db.setting('progress_excel_revision',0)
  p.db.set('progress_excel_dirty',not clean);return clean
 finally:p._excel_busy=False

async def watch(p):
 while True:
  try:await ensure_export(p)
  except asyncio.CancelledError:raise
  except Exception:pass
  await asyncio.sleep(3)
