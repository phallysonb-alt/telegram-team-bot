"""Independent task/query/feedback ledger. No daily data access or writes."""
import json,math
from datetime import datetime,timedelta
from .db import DB,dumps
SCHEMA='''
CREATE TABLE IF NOT EXISTS pg_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,day TEXT NOT NULL,version INTEGER NOT NULL,closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pg_versions(task INTEGER,version INTEGER,definition TEXT NOT NULL,actor INTEGER,at TEXT,PRIMARY KEY(task,version));
CREATE TABLE IF NOT EXISTS pg_rounds(id INTEGER PRIMARY KEY AUTOINCREMENT,day TEXT,at TEXT,tasks TEXT,roster TEXT,simulated INTEGER,revision INTEGER DEFAULT 0,board INTEGER,task_page INTEGER DEFAULT 1,people_page INTEGER DEFAULT 1,pending INTEGER DEFAULT 0,due TEXT);
CREATE TABLE IF NOT EXISTS pg_feedback(round INTEGER,uid INTEGER,payload TEXT,version INTEGER,at TEXT,name TEXT,username TEXT,PRIMARY KEY(round,uid));
CREATE TABLE IF NOT EXISTS pg_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,entity TEXT,entity_key TEXT,actor INTEGER,before_value TEXT,after_value TEXT,reason TEXT,at TEXT);
CREATE TABLE IF NOT EXISTS pg_drafts(round INTEGER,uid INTEGER,payload TEXT,page INTEGER DEFAULT 1,PRIMARY KEY(round,uid));
CREATE TABLE IF NOT EXISTS pg_panels(round INTEGER,uid INTEGER,chat INTEGER,message INTEGER,ephemeral INTEGER,PRIMARY KEY(round,uid));
CREATE TABLE IF NOT EXISTS pg_updates(id INTEGER PRIMARY KEY,at TEXT);
CREATE TABLE IF NOT EXISTS pg_events(key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS pg_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE,payload TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,due TEXT,error TEXT,receipt TEXT);
CREATE TABLE IF NOT EXISTS pg_inputs(uid INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS pg_builders(uid INTEGER PRIMARY KEY,payload TEXT,chat INTEGER,message INTEGER);
CREATE TABLE IF NOT EXISTS pg_text_inputs(uid INTEGER PRIMARY KEY,kind TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS pg_note_drafts(round INTEGER,uid INTEGER,note TEXT,PRIMARY KEY(round,uid));
CREATE TABLE IF NOT EXISTS pg_notes(round INTEGER,uid INTEGER,note TEXT,version INTEGER,at TEXT,PRIMARY KEY(round,uid));
CREATE TABLE IF NOT EXISTS pg_batches(token TEXT PRIMARY KEY,tasks TEXT,at TEXT);
CREATE TABLE IF NOT EXISTS pg_task_notes(round INTEGER,uid INTEGER,task INTEGER,note TEXT,PRIMARY KEY(round,uid,task));
'''
class Progress:
 def __init__(self,s):
  self.s=s;self.c=s.c;self.db=DB(s.c.data/('progress-acceptance.sqlite3' if s.c.test_run else 'progress.sqlite3'));self.db.conn.executescript(SCHEMA)
  from .progress_lock import migrate
  migrate(self)
 def now(self):return self.c.now().isoformat()
 def label(self):return self.c.trial_label+'\n' if self.c.test_run else ''
 def admin(self,uid):self.s.require_admin(uid)
 def employee(self,uid):
  e=self.s.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
  if not e or not e['approved'] or not e['active']:raise PermissionError('账号未开通或已停用')
 def identity(self,uid):
  from .daily_history import daily_identity
  from datetime import datetime
  if self.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='pg_app_profiles'"):
   app=self.db.one('SELECT * FROM pg_app_profiles WHERE uid=?',(uid,));source=self.s.db.one('SELECT * FROM tg_profiles WHERE uid=?',(uid,))
   if app and (not source or app['at']>=datetime.fromisoformat(source['updated']).timestamp()):return app['name'],app['username']
  return daily_identity(self.s,uid)
 def task_note(self,rid,uid,task):
  row=self.db.one('SELECT note FROM pg_task_notes WHERE round=? AND uid=? AND task=?',(rid,uid,task))
  return row['note'] if row else self.note(rid,uid)
 def audit(self,entity,key,uid,before,after,reason):self.db.run('INSERT INTO pg_audit(entity,entity_key,actor,before_value,after_value,reason,at) VALUES (?,?,?,?,?,?,?)',(entity,str(key),uid,dumps(before),dumps(after),reason,self.now()))
 def definition(self,day,title,kind,stages,plan='',deadline=''):
  if datetime.strptime(day,'%Y-%m-%d').strftime('%Y-%m-%d')!=day:raise ValueError('日期需填写YYYY-MM-DD')
  if not title.strip() or len(title)>100:raise ValueError('任务名称需1–100字')
  if kind not in ('seq','free') or len(stages) not in (3,4) or any(not x.strip() or len(x)>80 for x in stages):raise ValueError('请选择完成类型，并填写3或4个阶段（每项1–80字）')
  if len(plan)>120:raise ValueError('计划时段过长')
  if deadline:datetime.strptime(deadline,'%Y-%m-%d %H:%M')
  return dict(day=day,title=title,kind=kind,stages=stages,plan=plan,deadline=deadline)
 def task(self,task):
  row=self.db.one('SELECT * FROM pg_tasks WHERE id=?',(task,))
  if not row:raise ValueError('任务不存在')
  v=self.db.one('SELECT definition FROM pg_versions WHERE task=? AND version=?',(task,row['version']))
  return dict(json.loads(v['definition']),id=row['id'],version=row['version'],closed=row['closed'])
 def tasks(self,day,active=False):return [self.task(r['id']) for r in self.db.all('SELECT id FROM pg_tasks WHERE day=?'+(' AND closed=0' if active else '')+' ORDER BY id',(day,))]
 def publish(self,uid,definition,task=None):
  self.admin(uid);definition=self.definition(**definition)
  with self.db.transaction():
   old=self.task(task) if task else None
   if old and old['closed']:raise ValueError('任务已结束，不能修改')
   if old and any(json.loads(f['payload']).get(str(task)) is not None for f in self.db.all('SELECT payload FROM pg_feedback')):
    raise ValueError('任务已有提交反馈，阶段及定义已锁定；请发布独立的新任务')
   if old:
    task=old['id'];version=old['version']+1;self.db.run('UPDATE pg_tasks SET version=?,day=? WHERE id=?',(version,definition['day'],task))
   else:task=self.db.run('INSERT INTO pg_tasks(day,version) VALUES (?,1)',(definition['day'],)).lastrowid;version=1
   self.db.run('INSERT INTO pg_versions VALUES (?,?,?,?,?)',(task,version,dumps(definition),uid,self.now()));self.audit('任务',task,uid,old,definition,'发布任务' if old is None else '结构修改保留旧版本')
   from .progress_lock import dirty
   dirty(self)
  return task
 def close(self,uid,task):
  self.admin(uid);old=self.task(task);self.db.run('UPDATE pg_tasks SET closed=1 WHERE id=?',(task,));self.audit('任务',task,uid,old,self.task(task),'结束任务')
 def new_round(self,uid,day,task=None,roster=None,simulated=False):
  self.admin(uid);tasks=[self.task(task)] if task else self.tasks(day,True)
  if not tasks or any(t['day']!=day or t['closed'] for t in tasks):raise ValueError('该日没有所选有效任务')
  if roster is None:
   roster=[r['uid'] for r in self.s.db.all("SELECT uid FROM employees WHERE approved=1 AND active=1 AND role='employee' ORDER BY uid") if not self.s.admin(r['uid'])]
  if not roster:raise ValueError('暂无已批准在职员工')
  for employee in roster:self.employee(employee)
  with self.db.transaction():
   rid=self.db.run('INSERT INTO pg_rounds(day,at,tasks,roster,simulated) VALUES (?,?,?,?,?)',(day,self.now(),dumps(tasks),dumps(roster),int(simulated))).lastrowid
   self.audit('查询',rid,uid,None,dict(day=day,tasks=tasks,roster=roster,simulated=simulated),'管理员主动发起查询')
   for employee in roster:
    name,username=self.identity(employee);self.db.run('INSERT INTO pg_roster_names VALUES (?,?,?,?)',(rid,employee,name,username))
   from .progress_lock import dirty
   dirty(self)
   return rid
 def round(self,rid):
  r=self.db.one('SELECT * FROM pg_rounds WHERE id=?',(rid,))
  if not r:raise ValueError('查询不存在')
  r['tasks']=json.loads(r['tasks']);r['roster']=json.loads(r['roster']);return r
 def latest(self,day=None):return self.db.one('SELECT id FROM pg_rounds'+(' WHERE day=?' if day else '')+' ORDER BY id DESC LIMIT 1',(day,) if day else ())
 def guard(self,rid,uid):
  self.employee(uid);r=self.round(rid)
  if self.latest(r['day'])['id']!=rid:raise ValueError('这是旧轮查询，请进入最新查询反馈')
  if uid not in r['roster']:raise PermissionError('你不在本轮应反馈名单中')
  return r
 def draft(self,rid,uid):
  r=self.guard(rid,uid);old=self.db.one('SELECT payload FROM pg_drafts WHERE round=? AND uid=?',(rid,uid))
  if old:return json.loads(old['payload'])
  from .progress_lock import mask
  f=self.db.one('SELECT payload FROM pg_feedback WHERE round=? AND uid=?',(rid,uid));data=json.loads(f['payload']) if f else {str(t['id']):mask(self,t,uid) for t in r['tasks']}
  self.db.run('INSERT INTO pg_drafts(round,uid,payload) VALUES (?,?,?)',(rid,uid,dumps(data)));return data
 def select(self,rid,uid,owner,task,value,key):
  if owner!=uid:raise PermissionError('只能修改本人面板')
  r=self.guard(rid,uid);t=next((t for t in r['tasks'] if t['id']==task),None)
  if self.feedback(rid,uid):raise ValueError('本轮已提交，反馈已锁定')
  if not t:raise ValueError('任务不属于本轮')
  if not 0<=value<=len(t['stages']):raise ValueError('阶段超出范围')
  with self.db.transaction():
   if self.db.one('SELECT key FROM pg_events WHERE key=?',(key,)):return
   data=self.draft(rid,uid)
   if t['kind']=='seq':data[str(task)]=(1<<value)-1
   else:
    if value==0:data[str(task)]=0
    else:data[str(task)]^=1<<(value-1)
   from .progress_lock import mask
   if data[str(task)]&mask(self,t,uid)!=mask(self,t,uid):raise ValueError('已提交阶段已锁定，不能取消')
   self.db.run('UPDATE pg_drafts SET payload=? WHERE round=? AND uid=?',(dumps(data),rid,uid));self.db.run('INSERT INTO pg_events VALUES (?)',(key,))
 def submit(self,rid,uid,owner,key):
  if owner!=uid:raise PermissionError('只能提交本人反馈')
  r=self.guard(rid,uid)
  with self.db.transaction():
   if self.db.one('SELECT key FROM pg_events WHERE key=?',(key,)):return False
   data=self.draft(rid,uid)
   for t in r['tasks']:
    mask=data[str(t['id'])];maximum=(1<<len(t['stages']))-1
    if not isinstance(mask,int) or not 0<=mask<=maximum or t['kind']=='seq' and mask&(mask+1):raise ValueError('顺序任务不能跳过前置阶段')
    if self.task(t['id'])['closed']:raise ValueError('任务已结束，请进入新的有效任务查询')
   old=self.db.one('SELECT * FROM pg_feedback WHERE round=? AND uid=?',(rid,uid))
   self.db.run('INSERT INTO pg_events VALUES (?)',(key,))
   note=self.note(rid,uid,True)
   if old:
    if json.loads(old['payload'])==data and note==self.note(rid,uid):return False
    raise ValueError('本轮已提交，反馈已锁定')
   from .progress_lock import lock,dirty
   for t in r['tasks']:lock(self,rid,uid,t,data[str(t['id'])])
   dirty(self)
   name,user=self.identity(uid);version=old['version']+1 if old else 1
   before=dict(old,note=self.note(rid,uid)) if old else None
   self.db.run('INSERT OR REPLACE INTO pg_feedback VALUES (?,?,?,?,?,?,?)',(rid,uid,dumps(data),version,self.now(),name,user));self.db.run('INSERT OR REPLACE INTO pg_notes VALUES (?,?,?,?,?)',(rid,uid,note,version,self.now()));self.audit('反馈',f'{rid}:{uid}',uid,before,dict(uid=uid,name=name,username=user,at=self.now(),payload=data,note=note,version=version),'本人反馈替换' if old else '本人完成反馈')
   self.db.run('UPDATE pg_rounds SET revision=revision+1,pending=1,due=? WHERE id=?',((self.c.now()+timedelta(seconds=1)).isoformat(),rid));return True
 def feedback(self,rid,uid):return self.db.one('SELECT * FROM pg_feedback WHERE round=? AND uid=?',(rid,uid))
 def note(self,rid,uid,draft=False):
  row=self.db.one('SELECT note FROM '+('pg_note_drafts' if draft else 'pg_notes')+' WHERE round=? AND uid=?',(rid,uid))
  return row['note'] if row else self.note(rid,uid) if draft else ''
 def edit_note(self,rid,uid,owner,text):
  if owner!=uid:raise PermissionError('只能填写本人备注')
  self.guard(rid,uid)
  if self.feedback(rid,uid):raise ValueError('本轮已提交，当前问题已锁定')
  if len(text)>3500:raise ValueError('备注最多3500字，请精简后重新发送')
  self.db.run('INSERT OR REPLACE INTO pg_note_drafts VALUES (?,?,?)',(rid,uid,text))
 def queue(self,key,payload):self.db.run('INSERT OR IGNORE INTO pg_outbox(key,payload,due) VALUES (?,?,?)',(key,dumps(payload),self.now()))
 def export(self,uid):
  self.admin(uid);rows=[]
  for rr in self.db.all('SELECT id FROM pg_rounds ORDER BY id'):
   r=self.round(rr['id'])
   for employee in r['roster']:
    f=self.feedback(r['id'],employee);name,user=self.identity(employee);data=json.loads(f['payload']) if f else {}
    for t in r['tasks']:rows.append([r['day'],r['id'],datetime.fromisoformat(r['at']).strftime('%Y-%m-%d %H:%M'),name,user,t['title'],t['version'],'顺序完成' if t['kind']=='seq' else '自由完成',f"{data[str(t['id'])].bit_count()}/{len(t['stages'])}" if f else '未反馈','、'.join(stage for index,stage in enumerate(t['stages']) if f and data[str(t['id'])]&(1<<index)),t['plan'],t['deadline'],datetime.fromisoformat(f['at']).strftime('%Y-%m-%d %H:%M') if f else '',f['version'] if f else None,'模拟查询' if r['simulated'] else '验收查询' if self.c.test_run else '业务查询',self.task_note(r['id'],employee,t['id'])])
  audit=self.db.all("SELECT * FROM pg_audit ORDER BY id")
  return dict(label='验收试用，不计正式数据' if self.c.test_run else '工作进度历史',rows=rows,audit=[[x['entity'],x['entity_key'],x['actor'],x['reason'],datetime.fromisoformat(x['at']).strftime('%Y-%m-%d %H:%M'),x['before_value'],x['after_value']] for x in audit])
