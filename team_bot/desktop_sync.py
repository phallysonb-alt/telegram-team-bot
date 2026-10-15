"""Optional byte-for-byte Excel mirrors; business ledgers and exporters stay unchanged."""
import asyncio,ctypes,hashlib,json,os,uuid,zipfile,logging
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

FILES={'daily':('日报历史记录.xlsx',['每日汇总','员工明细']),
       'progress':('工作进度历史.xlsx',['查询汇总','员工进度历史','当前问题历史','任务定义'])}

def destination(value):
    if not value:return None
    if value.startswith('desktop:'):
        name=value[len('desktop:'):]
        if not name or name in ('.','..') or any(x in name for x in ('/','\\',':')):raise ValueError('桌面文件夹名称无效')
        if os.name!='nt':raise ValueError('desktop:路径只适用于Windows')
        buffer=ctypes.create_unicode_buffer(32768)
        fn=ctypes.windll.shell32.SHGetFolderPathW
        fn.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_wchar_p];fn.restype=ctypes.c_long
        if fn(None,0x10,None,0,buffer)!=0 or not buffer.value:raise ValueError('无法取得Windows实际桌面路径')
        return (Path(buffer.value)/name).resolve()
    return Path(os.path.expandvars(value)).expanduser().resolve()

def signature(path):
    s=path.stat();return (str(path.resolve()),s.st_mtime_ns,s.st_size)

class DesktopSync:
    def __init__(self,service,value=None,progress=None):
        self.s=service;self.value=os.getenv('DESKTOP_HISTORY_DIR','').strip() if value is None else value
        self.progress=progress;self.path=service.c.data/'desktop-sync-state.json';self.cache={};self.busy=False
        try:self.state=json.loads(self.path.read_text(encoding='utf8'))
        except (OSError,ValueError):self.state={}

    def jobs(self):
        from .daily_history import export_path as daily_path
        from .progress_export import export_path as progress_path
        from .progress_ui import controller
        p=self.progress or controller(self.s);daily=self.s.db;pg=p.db
        return [('daily',daily_path(self.s),daily.setting('daily_excel_revision',0),
                 not daily.setting('daily_excel_pending',False) and daily.setting('daily_excel_done',-1)==daily.setting('daily_excel_revision',0)),
                ('progress',progress_path(p),pg.setting('progress_excel_revision',0),
                 not pg.setting('progress_excel_dirty',True) and pg.setting('progress_excel_done',-1)==pg.setting('progress_excel_revision',0))]

    def persist(self):
        temp=self.path.with_name('.desktop-sync-state-'+uuid.uuid4().hex+'.tmp')
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,self.path)
        except OSError:pass # State reporting must never block saving or exporting business data.
        finally:
            try:temp.unlink(missing_ok=True)
            except OSError:pass

    def mirror(self,kind,source,revision,target):
        prior=self.state.get(kind,{})
        state=dict(prior,source=str(source),target=str(target/FILES[kind][0]),revision=revision,pending=True,status='待同步')
        temp=None
        try:
            if not target.is_dir():raise FileNotFoundError('目标文件夹不存在；不会自动新建')
            dest=target/FILES[kind][0];a=signature(source);b=signature(dest) if dest.exists() else None
            if self.cache.get(kind)==(a,b,revision) and prior.get('status')=='已同步':
                state=prior;return
            raw=source.read_bytes()
            with zipfile.ZipFile(__import__('io').BytesIO(raw)) as z:
                if z.testzip():raise ValueError('源Excel损坏')
                names=[x.attrib['name'] for x in ET.fromstring(z.read('xl/workbook.xml')).findall('{*}sheets/{*}sheet')]
                if names!=FILES[kind][1]:raise ValueError('源Excel工作表结构不符')
            if signature(source)!=a:raise RuntimeError('源文件正在更新，稍后重试')
            digest=hashlib.sha256(raw).hexdigest()
            if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:
                temp=target/('.desktop-sync-'+kind+'-'+uuid.uuid4().hex+'.tmp')
                with temp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
                os.replace(temp,dest) # Windows sharing/OneDrive errors keep the previous valid destination.
            state.update(pending=False,status='已同步',source_sha256=digest,target_sha256=digest,
                         last_success=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),error=None)
            self.cache[kind]=(a,signature(dest),revision)
        except Exception as e:
            state.update(error=type(e).__name__,winerror=getattr(e,'winerror',None))
            self.cache.pop(kind,None)
        finally:
            if temp:
                try:temp.unlink(missing_ok=True)
                except OSError:pass
            self.state[kind]=state

    async def once(self):
        if self.busy or not self.value:return
        self.busy=True
        try:
            try:target=destination(self.value)
            except Exception as e:
                self.state['configuration']=dict(status='待同步',pending=True,error=type(e).__name__);self.persist();return
            self.state.pop('configuration',None)
            for kind,source,revision,ready in self.jobs():
                if not ready:
                    self.state[kind]=dict(self.state.get(kind,{}),pending=True,status='等待原始Excel更新',revision=revision)
                else:await asyncio.to_thread(self.mirror,kind,source,revision,target)
            self.persist()
        finally:self.busy=False

    async def loop(self):
        while True:
            try:await self.once()
            except asyncio.CancelledError:raise
            except Exception as e:logging.getLogger(__name__).warning('桌面副本同步待重试：%s',type(e).__name__)
            await asyncio.sleep(2)

async def start(app,service):
    if os.getenv('DESKTOP_HISTORY_DIR','').strip():
        app.bot_data['desktop_sync_task']=asyncio.create_task(DesktopSync(service).loop())

async def stop(app):
    task=app.bot_data.pop('desktop_sync_task',None)
    if task:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
