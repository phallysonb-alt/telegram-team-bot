"""Local-only workbench model. Legacy check_events and daily_records are untouched."""
import json
from datetime import datetime,time,timedelta
from .db import dumps
from .rich import heading,paragraph,table
from .presentation import human_time,status_label

DEFAULT_TASKS=[dict(id=1,name='资料整理',unit='份',target=3,kind='quantity',order=1,uids=[]),dict(id=2,name='记录更新',unit='条',target=5,kind='quantity',order=2,uids=[]),dict(id=3,name='待办检查',unit='次',target=1,kind='complete',order=3,uids=[])]
DEFAULT_PERIODS=[['12:00','14:00'],['14:00','17:00'],['19:00','21:00'],['21:00','00:00']]

class Workbench:
 def __init__(self,s):self.s=s;self.db=s.db;self.c=s.c
 def now(self):return self.c.now().astimezone(self.c.zone)
 def day(self):return self.now().date().isoformat()
 def seed(self):
  if not self.db.one('SELECT id FROM wb_config LIMIT 1'):
   self.db.run('INSERT INTO wb_config(effective,tasks,periods,actor,reason) VALUES (?,?,?,?,?)',('0001-01-01',dumps(DEFAULT_TASKS),dumps(DEFAULT_PERIODS),0,'独立模拟工作台初始配置'))
 def config(self,day):
  self.seed();r=self.db.one('SELECT * FROM wb_config WHERE effective<=? ORDER BY effective DESC,id DESC LIMIT 1',(day,))
  return json.loads(r['tasks']),json.loads(r['periods'])
 def ensure(self,day):
  if datetime.fromisoformat(day).date().isoformat()!=day:raise ValueError("日期需YYYY-MM-DD")
  if day>self.day():raise ValueError('不能操作或查看未来日期')
  r=self.db.one('SELECT * FROM wb_days WHERE day=?',(day,))
  if not r:
   tasks,periods=self.config(day);self.db.run('INSERT INTO wb_days VALUES (?,?,?)',(day,dumps(tasks),dumps(periods)));r=self.db.one('SELECT * FROM wb_days WHERE day=?',(day,))
  return json.loads(r['tasks']),json.loads(r['periods'])
 def employees(self):
  rows=self.db.all("SELECT uid,code FROM employees WHERE approved=1 AND active=1 AND NOT EXISTS(SELECT 1 FROM personnel_slots p WHERE p.uid=employees.uid AND p.kind='management') ORDER BY code")
  return [dict(r,code=self.s.display(r['uid'])) for r in rows]
 def tasks(self,uid,day):
  self.s.employee(uid)
  if self.db.one("SELECT code FROM personnel_slots WHERE uid=? AND kind='management'",(uid,)):raise PermissionError('员工工作台仅用于员工编号，管理编号请使用管理员查看入口')
  tasks,_=self.ensure(day)
  return [t for t in sorted(tasks,key=lambda t:(t['order'],t['id'])) if not t['uids'] or uid in t['uids']]
 def period_at(self,day,at):
  _,periods=self.ensure(day);start=datetime.fromisoformat(day).replace(tzinfo=self.c.zone)
  for i,(a,b) in enumerate(periods):
   lo=datetime.combine(start.date(),time.fromisoformat(a),self.c.zone);hi=datetime.combine(start.date(),time.fromisoformat(b),self.c.zone)
   if b=='00:00':hi+=timedelta(days=1)
   if lo<=at<hi:return i
  return None
 def effective(self,uid,task,day):
  return self.db.all('SELECT e.* FROM wb_events e WHERE e.uid=? AND e.task=? AND e.day=? AND e.undo_of IS NULL AND NOT EXISTS(SELECT 1 FROM wb_events u WHERE u.undo_of=e.id) ORDER BY e.id',(uid,task,day))
 def amount(self,uid,task,day,period=None):
  rows=self.effective(uid,task,day)
  return sum(e['delta'] for e in rows if period is None or (e['period'] is None if period==-1 else e['period']==period))
 def state(self,uid,t,day):
  n=self.amount(uid,t['id'],day);last=self.db.one('SELECT at FROM wb_events WHERE uid=? AND task=? AND day=? ORDER BY id DESC LIMIT 1',(uid,t['id'],day))
  status='未记录' if not last else '已完成' if n>=t['target'] else '明确为0' if n==0 else '进行中'
  return dict(n=n,status=status,last=last['at'] if last else None)
 def change(self,actor,uid,task,day,action,key,value=None,reason='',period=None):
  if actor!=uid:self.s.require_admin(actor)
  tasks=self.tasks(uid,day);t=next((t for t in tasks if t['id']==task),None)
  if not t:raise PermissionError('任务不适用于该员工')
  with self.db.transaction():
   if self.db.one('SELECT id FROM wb_events WHERE opkey=?',(key,)):return '重复回调已忽略'
   now=self.now()
   if action!='correct' and (day!=self.day() or actor!=uid):raise PermissionError('仅本人当天工作台可打卡')
   if action=='correct':
    self.s.require_admin(actor)
    if not reason.strip():raise ValueError('更正必须填写原因')
    if value is None or value<0:raise ValueError('更正数量必须为非负整数')
   else:
    last=self.db.one('SELECT at FROM wb_events WHERE uid=? AND task=? AND day=? ORDER BY id DESC LIMIT 1',(uid,task,day))
    if last and (now-datetime.fromisoformat(last['at'])).total_seconds()<self.c.click_cooldown:raise ValueError('点击过快，请稍后再试')
   n=self.amount(uid,task,day);undo=None;operation_period=self.period_at(self.day(),now);p=operation_period if day==self.day() else None
   if action=='undo':
    entries=self.effective(uid,task,day);original=next((e for e in reversed(entries) if e['actor']==uid and e['action'] in ('add','complete') and e['delta']>0),None)
    if not original:raise ValueError('没有可撤销记录')
    delta=-original['delta'];undo=original['id'];p=original['period']
   elif action=='add' and t['kind']=='quantity':delta=1
   elif action=='complete' and t['kind']=='complete':
    if n>=t['target']:raise ValueError('已完成，请勿重复记录')
    delta=t['target']-n
   elif action=='zero':
    if n:raise ValueError('非零进度不能记为0，请撤销或由管理员更正')
    delta=0
   elif action=='correct':
    delta=value-n
    if period is not None:
     if period not in range(len(self.ensure(day)[1])):raise ValueError('时段不存在')
     p=period
   else:raise ValueError('任务类型或操作不匹配')
   if action=='correct' and self.amount(uid,task,day,-1 if p is None else p)+delta<0:raise ValueError('更正不能使选定时段数量为负；请选择原时段或分时段更正')
   if n+delta<0:raise ValueError('累计不能低于0；原操作已被更正，请联系管理员')
   self.capture(day) # Freeze ended periods before applying a late change or undo.
   reason=reason or ('撤销原操作' if undo else '员工自行记录')
   event=self.db.run('INSERT INTO wb_events(uid,task,day,at,delta,action,period,operation_period,outside,undo_of,opkey,actor,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid,task,day,now.isoformat(),delta,action,p,operation_period,int(operation_period is None),undo,key,actor,reason))
   self.db.run('INSERT INTO wb_event_names VALUES (?,?,?)',(event.lastrowid,self.s.display(uid),self.s.display(actor)))
   self.db.audit('协同工作台',f'{uid}:{task}:{day}',actor,reason,dict(amount=n),dict(amount=n+delta,undo_of=undo,period=p))
   self.dirty(day);self.capture(day)
  return '已保存'
 def dirty(self,day):
  self.db.run('UPDATE wb_boards SET pending=1,due=COALESCE(due,?) WHERE day=?',((self.now()+timedelta(seconds=3)).isoformat(),day))
 def help(self,uid,day,state,actor=None):
  actor=uid if actor is None else actor
  if actor!=uid:self.s.require_admin(actor)
  self.s.employee(uid)
  if day!=self.day():raise ValueError('仅当天可以求助')
  old=self.db.one('SELECT state FROM wb_help WHERE uid=? AND day=?',(uid,day))
  if old and old['state']==state:return False
  self.db.run('INSERT OR REPLACE INTO wb_help VALUES (?,?,?,?)',(uid,day,state,self.now().isoformat()));self.db.audit('工作台求助',f'{uid}:{day}',actor,state,old,dict(state=state));self.dirty(day);return True
 def configure(self,actor,tasks,periods,reason):
  self.s.require_admin(actor)
  if not reason.strip():raise ValueError('配置修改必须填写原因')
  ids=set()
  if not 1<=len(tasks)<=20:raise ValueError('任务数量需1至20个')
  for t in tasks:
   if t['id'] in ids or t['kind'] not in ('quantity','complete') or not isinstance(t['target'],int) or isinstance(t['target'],bool) or t['target']<1 or not t['name'].strip() or not t['unit'].strip() or len(t['name'])>20 or len(t['unit'])>6:raise ValueError('任务编号、类型、名称、单位或目标无效')
   ids.add(t['id'])
   for uid in t['uids']:self.s.employee(uid)
  if not 1<=len(periods)<=8:raise ValueError('时段数量需1至8个')
  ends=[]
  for a,b in periods:
   lo=time.fromisoformat(a);hi=time.fromisoformat(b);x=lo.hour*60+lo.minute;y=1440 if b=='00:00' else hi.hour*60+hi.minute
   if x>=y or (ends and x<ends[-1]):raise ValueError('时段必须递增、不重叠；午夜终点写00:00')
   ends.append(y)
  self.ensure(self.day());effective=(self.now().date()+timedelta(days=1)).isoformat()
  with self.db.transaction():
   before=self.config(effective);self.db.run('INSERT INTO wb_config(effective,tasks,periods,actor,reason) VALUES (?,?,?,?,?)',(effective,dumps(tasks),dumps(periods),actor,reason));self.db.audit('工作台配置',effective,actor,reason,before,dict(tasks=tasks,periods=periods))
  return effective
 def summary(self,day,period=None):
  tasks,periods=self.ensure(day);tasks=sorted(tasks,key=lambda t:(t['order'],t['id']));rows=[];totals=[0]*len(tasks);counts={t['id']:dict(已完成=0,进行中=0,未记录=0,明确为0=0) for t in tasks}
  for e in self.employees():
   cells=[]
   for i,t in enumerate(tasks):
    if t['uids'] and e['uid'] not in t['uids']:cells.append('不适用');continue
    state=self.state(e['uid'],t,day);counts[t['id']][state['status']]+=1
    n=self.amount(e['uid'],t['id'],day,period);totals[i]+=n
    recorded=state['status']!='未记录' if period is None else bool(self.db.one('SELECT id FROM wb_events WHERE uid=? AND task=? AND day=? AND period=? AND undo_of IS NULL LIMIT 1',(e['uid'],t['id'],day,period)))
    if period==-1:recorded=bool(self.db.one('SELECT id FROM wb_events WHERE uid=? AND task=? AND day=? AND period IS NULL AND undo_of IS NULL LIMIT 1',(e['uid'],t['id'],day)))
    cells.append('未记录' if not recorded else f'{n}{t["unit"]}' if period is not None else f'{n}/{t["target"]}{t["unit"]}')
   last=self.db.one('SELECT MAX(at) at FROM (SELECT at FROM wb_events WHERE uid=? AND day=? UNION ALL SELECT updated at FROM wb_help WHERE uid=? AND day=?)',(e['uid'],day,e['uid'],day));help=self.db.one('SELECT state FROM wb_help WHERE uid=? AND day=?',(e['uid'],day))
   rows.append(dict(uid=e['uid'],code=e['code'],cells=cells,last=last['at'],help=help['state'] if help else '无'))
  return dict(day=day,period=period,periods=periods,tasks=tasks,rows=rows,totals=totals,counts=counts)
 def capture(self,day=None):
  for d in ([day] if day else [r['day'] for r in self.db.all('SELECT day FROM wb_days')]):
   _,periods=self.ensure(d)
   for i,(_,end) in enumerate(periods):
    stop=datetime.combine(datetime.fromisoformat(d).date(),time.fromisoformat(end),self.c.zone)
    if end=='00:00':stop+=timedelta(days=1)
    if self.now()<stop:continue
    r=self.summary(d,i);payload=dumps(dict(day=d,period=i,tasks=r['tasks'],periods=r['periods'],totals=r['totals'],rows=[dict(uid=e['uid'],code=e['code'],cells=e['cells']) for e in r['rows']]));old=self.db.one('SELECT * FROM wb_snapshots WHERE day=? AND period=? ORDER BY version DESC LIMIT 1',(d,i))
    if old and old['payload']==payload:continue
    self.db.run('INSERT INTO wb_snapshots VALUES (?,?,?,?,?,?)',(d,i,(old['version']+1 if old else 1),payload,self.now().isoformat(),int(bool(old))))
 def personal(self,uid,day,page=1,readonly=False):
  e=self.s.named_employee(uid);tasks=self.tasks(uid,day);size=4 if readonly else 1;pages=max(1,(len(tasks)+size-1)//size)
  if page not in range(1,pages+1):raise ValueError('页码超出范围')
  selected=tasks[(page-1)*size:page*size]
  text=('员工详情 · '+e['code'] if readonly else '我的工作')+f'\n{day} · {e["code"]} · {page}/{pages}页\n模拟任务'
  for t in selected:
   x=self.state(uid,t,day);done=min(8,x['n']*8//t['target']);bar='▰'*done+'▱'*(8-done)
   text+=f'\n\n{t["name"]}\n{x["n"]}/{t["target"]}{t["unit"]}\n{bar} · {status_label(x["status"])}\n更新：{human_time(x["last"],self.c)}'
  help=self.db.one('SELECT state,updated FROM wb_help WHERE uid=? AND day=?',(uid,day));text+='\n\n求助：'+(help['state'] if help else '无')
  last=self.db.one('SELECT MAX(at) at FROM (SELECT at FROM wb_events WHERE uid=? AND day=? UNION ALL SELECT updated at FROM wb_help WHERE uid=? AND day=?)',(uid,day,uid,day));text+='\n最后反馈：'+human_time(last['at'],self.c)
  if readonly:
   _,periods=self.ensure(day)
   text+='\n\n时段增量'
   for i,(a,b) in enumerate(periods):text+='\n'+a+'–'+b+'（'+self.period_status(day,i)+'）\n'+'；'.join(t['name']+'：'+self.display_amount(uid,t,day,i) for t in selected)
   text+='\n时间外\n'+'；'.join(t['name']+'：'+self.display_amount(uid,t,day,-1) for t in selected)
  return text,selected,pages
 def period_status(self,day,period):
  if period is None:return '当前累计'
  if period==-1:return '时间外'
  start,end=self.ensure(day)[1][period];d=datetime.fromisoformat(day).date()
  lo=datetime.combine(d,time.fromisoformat(start),self.c.zone);hi=datetime.combine(d,time.fromisoformat(end),self.c.zone)+(timedelta(days=1) if end=='00:00' else timedelta())
  return '未开始' if self.now()<lo else '进行中' if self.now()<hi else '已结束'
 def display_amount(self,uid,t,day,period=None):
  if t['uids'] and uid not in t['uids']:return '不适用'
  if period is not None and self.period_status(day,period)=='未开始':return '未开始'
  state=self.state(uid,t,day)
  if period is None:return '未记录' if state['status']=='未记录' else f'{state["n"]}/{t["target"]}{t["unit"]}'
  entries=self.db.all('SELECT action FROM wb_events WHERE uid=? AND task=? AND day=? AND '+('period IS NULL' if period==-1 else 'period=?'),(uid,t['id'],day) if period==-1 else (uid,t['id'],day,period))
  if not entries:return '无记录' if self.period_status(day,period)=='已结束' else '未记录'
  n=self.amount(uid,t['id'],day,period)
  return f'{n}{t["unit"]}'+('（明确0）' if n==0 and any(e['action']=='zero' for e in entries) else '')
 def detail_view(self,uid,day,page=1):
  text,tasks,pages=self.personal(uid,day,page,True);e=self.s.named_employee(uid)
  rows=[]
  for t in tasks:
   x=self.state(uid,t,day);rows.append([t['name'],self.display_amount(uid,t,day),status_label(x['status']),human_time(x['last'],self.c)])
  periods=self.ensure(day)[1]
  spans=[[a+'–'+b]+[self.display_amount(uid,t,day,i) for t in tasks] for i,(a,b) in enumerate(periods)]
  spans.append(['时间外']+[self.display_amount(uid,t,day,-1) for t in tasks])
  footer=text.split('\n\n求助：')[1].split('\n\n时段增量')[0]
  return text,dict(blocks=[heading('员工详情 · '+e['code']),paragraph(f'{day} · {page}/{pages}页 · 模拟任务'),table(['任务','进度','状态','更新'],rows),paragraph('求助：'+footer),heading('时段增量'),table(['时段']+[t['name'] for t in tasks],spans)]),pages
 def timeline(self,uid,day,page=1):
  events=self.db.all('SELECT id,task,at,delta,action,period,operation_period,outside,undo_of,actor,reason FROM wb_events WHERE uid=? AND day=? ORDER BY id DESC LIMIT 10 OFFSET ?',(uid,day,(page-1)*10));return events
 def team_view(self,day,page=1,period=None):
  r=self.summary(day,period);people_pages=max(1,(len(r['rows'])+9)//10);task_pages=max(1,(len(r['tasks'])+2)//3);pages=people_pages*task_pages
  if page not in range(1,pages+1):raise ValueError('页码超出范围')
  people_page=(page-1)//task_pages;task_page=(page-1)%task_pages;selected=r['tasks'][task_page*3:(task_page+1)*3];r['page_start']=people_page*10
  title='当前累计' if period is None else '时间外增量' if period==-1 else '时段增量 '+ '–'.join(r['periods'][period])+' · '+self.period_status(day,period)
  rows=[[e['code']]+[self.display_amount(e['uid'],t,day,period) for t in selected]+[human_time(e['last'],self.c),e['help']] for e in r['rows'][people_page*10:(people_page+1)*10]]
  future=period is not None and self.period_status(day,period)=='未开始'
  totals={t['id']:n for n,t in zip(r['totals'],r['tasks'])}
  rows.append(['全员合计']+['未开始' if future else str(totals[t['id']])+t['unit'] for t in selected]+['',''])
  meta=f'{day} · {len(r["rows"])}人 · 第{page}/{pages}页\n刷新：{human_time(self.now().isoformat(),self.c)}'
  if task_pages>1:meta+=f' · 员工{people_page+1}/{people_pages}页 · 任务{task_page+1}/{task_pages}页'
  text='团队进度\n'+meta+'\n'+title+'\n\n'+'\n'.join('｜'.join(row) for row in rows)
  stats=['当日任务状态 · 全员']+[t['name']+'：'+' / '.join(status_label(k)+' '+str(v)+'人' for k,v in r['counts'][t['id']].items()) for t in selected]
  text+='\n\n'+'\n'.join(stats)
  snap=self.db.one('SELECT version,revised FROM wb_snapshots WHERE day=? AND period=? ORDER BY version DESC LIMIT 1',(day,period)) if period is not None else None
  if snap:text+=f'\n时段快照版本{snap["version"]}'+('（修订）' if snap['revised'] else '')
  rich={'blocks':[heading('团队进度'),paragraph(meta+'\n'+title),table(['员工']+[t['name'] for t in selected]+['反馈','求助'],rows)]+[paragraph(line) for line in stats]}
  if snap:rich['blocks'].append(paragraph(f'时段快照版本{snap["version"]}'+('（修订）' if snap['revised'] else '')))
  return text,rich,pages,r
