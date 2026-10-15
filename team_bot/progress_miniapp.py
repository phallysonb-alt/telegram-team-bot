"""Mini App API. Telegram signatures and privileges are checked on the server.

Runs on the polling event loop; all SQLite writes therefore remain serialized.
No daily report tables or exported workbooks are written here.
"""
import hashlib
import hmac
import json
import os
import re
import time
from contextvars import ContextVar
request_timing=ContextVar("miniapp_timing",default=None)
from datetime import datetime,timezone
from urllib.parse import parse_qsl
from .db import dumps

SCHEMA = '''
CREATE TABLE IF NOT EXISTS pg_task_notes(round INTEGER,uid INTEGER,task INTEGER,note TEXT,PRIMARY KEY(round,uid,task));
CREATE TABLE IF NOT EXISTS pg_app_requests(uid INTEGER,key TEXT,payload TEXT,response TEXT,PRIMARY KEY(uid,key));
CREATE TABLE IF NOT EXISTS pg_app_profiles(uid INTEGER PRIMARY KEY,name TEXT,username TEXT,at REAL);
'''

def validate_init_data(raw, token, now=None, max_age=3600):
    if not isinstance(raw, str) or not raw or len(raw)>16384:
        raise PermissionError('请从 Telegram 群内入口打开小程序')
    pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
    data=dict(pairs)
    if len(data)!=len(pairs):raise PermissionError('登录资料无效')
    signature=data.pop('hash', '')
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    check='\n'.join(k+'='+v for k,v in sorted(data.items()))
    expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):raise PermissionError('Telegram 登录校验失败')
    current=time.time() if now is None else now
    try:
        age=current-int(data['auth_date']);user=json.loads(data['user'])
        if age < -30 or age>max_age:raise ValueError()
        if type(user['id']) is not int or user['id']<=0 or user.get('is_bot'):raise ValueError()
    except (KeyError,TypeError,ValueError):raise PermissionError('登录已过期，请重新打开小程序') from None
    return user, data.get('start_param','')

def enabled():
    return os.environ.get('PROGRESS_MINIAPP_ENABLED','').lower()=='true'

def link(param):
    base=os.environ.get('PROGRESS_MINIAPP_LINK','').rstrip('/')
    if not re.fullmatch(r'https://t\.me/[A-Za-z][A-Za-z0-9_]{4,31}(?:/[A-Za-z0-9_]+)?',base):
        raise ValueError('尚未配置已注册的 Mini App 链接')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,512}',param):raise ValueError('入口无效')
    return base+'?startapp='+param

def query_card(p,rid):
    r=p.round(rid)
    count=p.db.one('SELECT COUNT(*) n FROM pg_feedback WHERE round=?',(rid,))['n']
    from .progress_ui import time_text
    from .progress_messages import queried
    text=queried(p,rid)
    prefix='verify_' if getattr(p,'_verification',False) else ''
    return text,dict(inline_keyboard=[[dict(text='填写进度',url=link(prefix+'feedback_'+str(rid))),dict(text='查看总表',url=link(prefix+'summary_'+str(rid)))]] )

class MiniAPI:
    def __init__(self,p,bot,membership=None):
        self.p=p;self.bot=bot;self.membership=membership
        self.limits={}
        p.db.conn.executescript(SCHEMA)

    async def authorize(self,raw):
        user,start=validate_init_data(raw,self.p.c.token)
        uid=user['id']
        now=time.monotonic();recent=[x for x in self.limits.get(uid,[]) if now-x<1]
        if len(recent)>=12:raise PermissionError('操作过快，请稍候重试')
        self.limits[uid]=recent+[now]
        # Live Telegram membership is required; a transient failure blocks the request.
        member_started=time.perf_counter()
        if self.membership:await self.membership(uid)
        else:
            from .member_registry import reconcile
            from telegram import User
            identity=User(uid,str(user.get('first_name','')),False,last_name=user.get('last_name'),username=user.get('username'))
            profile=self.p.s.db.one('SELECT * FROM tg_profiles WHERE uid=?',(uid,))
            if profile and datetime.fromisoformat(profile['updated']).timestamp()>int(dict(parse_qsl(raw))['auth_date']):
                identity=User(uid,profile['name'],False,username=profile['username'] or None)
            member=await reconcile(self.p.s,self.bot,uid,identity)
            if member.user.id!=uid or not (str(member.status) in ('member','administrator','creator') or str(member.status)=='restricted' and member.is_member):
                raise PermissionError('你已不在工作群，不能新增业务操作')
        if request_timing.get() is not None:request_timing.get()['telegram_ms']=round((time.perf_counter()-member_started)*1000,2)
        if not self.p.s.admin(uid):self.p.employee(uid)
        name=' '.join(str(user.get(k,'')) for k in ('first_name','last_name')).strip()
        if not name:raise PermissionError('Telegram 身份资料无效')
        # Signed account profile, isolated from daily data and contact-book aliases.
        auth_at=int(dict(parse_qsl(raw))['auth_date'])
        old=self.p.db.one('SELECT at FROM pg_app_profiles WHERE uid=?',(uid,))
        if not old or old['at']<=auth_at:self.p.db.run('INSERT OR REPLACE INTO pg_app_profiles VALUES (?,?,?,?)',(uid,name,user.get('username',''),auth_at))
        return uid,user,start.removeprefix('verify_') if getattr(self,'is_verification',False) else start

    def identity(self,uid):
        app=self.p.db.one('SELECT * FROM pg_app_profiles WHERE uid=?',(uid,))
        source=self.p.s.db.one('SELECT * FROM tg_profiles WHERE uid=?',(uid,))
        if app and (not source or app['at']>=datetime.fromisoformat(source['updated']).timestamp()):return app['name'],app['username']
        name,username=self.p.identity(uid)
        return name,username.lstrip('@') if username and username!='—' else ''

    def task_notes(self,rid,uid):
        return {str(x['task']):x['note'] for x in self.p.db.all('SELECT task,note FROM pg_task_notes WHERE round=? AND uid=?',(rid,uid))}

    def feedback_view(self,rid,uid):
        r=self.p.guard(rid,uid);f=self.p.feedback(rid,uid)
        source=f;previous=None
        if not f:
            previous=self.p.db.one('SELECT f.* FROM pg_feedback f JOIN pg_rounds r ON r.id=f.round WHERE f.uid=? AND r.day=? AND r.id<? ORDER BY r.id DESC LIMIT 1',(uid,r['day'],rid))
            source=previous
        masks=json.loads(source['payload']) if source else {}
        notes=self.task_notes(source['round'],uid) if source else {}
        # Prefill only if the exact task version is identical, never reinterpret old stages.
        prior={str(t['id']):t for t in self.p.round(source['round'])['tasks']} if source else {}
        values={};text={};has_prefill=False
        for t in r['tasks']:
            k=str(t['id']);same=k in prior and prior[k]['version']==t['version']
            from .progress_lock import mask
            values[k]=masks.get(k,0) if f else mask(self.p,t,uid)
            text[k]=notes.get(k,'') if f and same else ''
            has_prefill=has_prefill or bool(not f and values[k])
        name,username=self.identity(uid)
        return dict(round=rid,day=r['day'],time=r['at'],tasks=r['tasks'],masks=values,locked=dict(values) if f else {str(t['id']):mask(self.p,t,uid) for t in r['tasks']},notes=text,name=name,username=username,submitted=bool(f),prefilled=has_prefill,legacyNote=self.p.note(rid,uid) if not self.task_notes(rid,uid) else '')

    def summary(self,rid,uid,issue_pages=None):
        self.p.admin(uid);r=self.p.round(rid);rows=[]
        from .progress_admin_projection import project
        projection=project(self.p,r,issue_pages)
        feedback={f['uid']:f for f in self.p.db.all('SELECT * FROM pg_feedback WHERE round=?',(rid,))}
        all_notes={}
        for n in self.p.db.all('SELECT uid,task,note FROM pg_task_notes WHERE round=?',(rid,)):
            all_notes.setdefault(n['uid'],{})[str(n['task'])]=n['note']
        legacy={n['uid']:n['note'] for n in self.p.db.all('SELECT uid,note FROM pg_notes WHERE round=?',(rid,))}
        for employee in r['roster']:
            f=feedback.get(employee);name,username=self.identity(employee);notes=all_notes.get(employee,{})
            rows.append(dict(name=name,username=username,submitted=bool(f),masks=json.loads(f['payload']) if f else {},
                cumulative_masks=projection['cumulative'][employee],prior_masks=projection['prior'][employee],
                added_masks=projection['added'][employee] if f else {},uncertain_masks=projection['unknown'][employee],stage_states=projection['states'][employee],
                notes=notes,legacyNote=legacy.get(employee,'') if not notes else '',time=f['at'] if f else None))
        completed={str(t['id']):sum(x['cumulative_masks'].get(str(t['id']))==(1<<len(t['stages']))-1 for x in rows) for t in r['tasks']}
        return dict(round=rid,day=r['day'],time=r['at'],tasks=r['tasks'],rows=rows,received=sum(x['submitted'] for x in rows),expected=len(rows),
            cumulative_completed=completed,issues=projection['issues'],revision=r['revision'],simulated=bool(r['simulated']),updated=self.p.now())

    def once(self,uid,key,body,action):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise ValueError('请求标识无效，请重新打开')
        payload=dumps(body)
        old=self.p.db.one('SELECT * FROM pg_app_requests WHERE uid=? AND key=?',(uid,key))
        if old:
            if old['payload']!=payload:raise ValueError('请求内容已变化，请重新提交')
            return json.loads(old['response'])
        with self.p.db.transaction():
            result=action()
            self.p.db.run('INSERT INTO pg_app_requests VALUES (?,?,?,?)',(uid,key,payload,dumps(result)))
        return result

    def submit(self,rid,uid,body):
        r=self.p.guard(rid,uid);masks=body.get('masks');notes=body.get('notes')
        keys={str(t['id']) for t in r['tasks']}
        if not isinstance(masks,dict) or not isinstance(notes,dict) or set(masks)!=keys or set(notes)!=keys:raise ValueError('请填写本次全部任务')
        for t in r['tasks']:
            k=str(t['id']);mask=masks[k];note=notes[k]
            if type(mask) is not int or not 0<=mask<(1<<len(t['stages'])) or t['kind']=='seq' and mask&(mask+1):raise ValueError('顺序任务不能跳过前置步骤')
            if not isinstance(note,str) or len(note)>3500:raise ValueError('每项当前问题最多3500字')
            current=self.p.task(t['id'])
            if current['closed'] or current['version']!=t['version']:raise ValueError('任务已调整，请进入新的查询')
        old=self.p.feedback(rid,uid);oldnotes=self.task_notes(rid,uid)
        if old:
            if json.loads(old['payload'])==masks and {k:oldnotes.get(k,'') for k in keys}==notes:return dict(ok=True,message='已提交',changed=False)
            raise ValueError('本轮已提交，阶段和当前问题已锁定；请在新查询继续反馈')
        from .progress_lock import lock,dirty
        for t in r['tasks']:lock(self.p,rid,uid,t,masks[str(t['id'])])
        dirty(self.p)
        name,username=self.identity(uid);version=old['version']+1 if old else 1;at=self.p.now()
        before=dict(feedback=old,notes=oldnotes)
        self.p.db.run('INSERT OR REPLACE INTO pg_feedback VALUES (?,?,?,?,?,?,?)',(rid,uid,dumps(masks),version,at,name,username))
        for task,note in notes.items():self.p.db.run('INSERT OR REPLACE INTO pg_task_notes VALUES (?,?,?,?)',(rid,uid,int(task),note))
        self.p.audit('反馈',f'{rid}:{uid}',uid,before,dict(payload=masks,notes=notes,version=version,name=name,username=username),'Mini App 本人反馈替换' if old else 'Mini App 本人提交')
        self.p.db.run('UPDATE pg_rounds SET revision=revision+1,pending=1,due=? WHERE id=?',(at,rid))
        if request_timing.get() is not None:request_timing.get().update(saved_at=at,round=rid,revision=self.p.round(rid)['revision'])
        return dict(ok=True,message='已提交',changed=True)

    async def dispatch(self,path,envelope):
        if not isinstance(envelope,dict):raise ValueError('请求格式错误')
        if not getattr(self,'is_verification',False):
            user,start=validate_init_data(envelope.get('initData',''),self.p.c.token)
            if start.startswith('verify_'):
                from .progress_verification import enabled,isolated
                if user['id']!=self.p.c.owner or not enabled(self.p):raise PermissionError('隔离验证入口未启用或无权限')
                return await isolated(self).dispatch(path,envelope)
        if path=='/api/metrics':
            user,_=validate_init_data(envelope.get('initData',''),self.p.c.token)
            self.p.admin(user['id']);body=envelope.get('body',{})
            if not isinstance(body,dict) or len(dumps(body))>12000 or body.get('kind') not in ('form_open','summary_open','submit','summary_visible'):raise ValueError('测速资料无效')
            allowed={'kind','phase','start_epoch','ready_epoch','duration_ms','calls','resources','round','revision','client_id'}
            if set(body)-allowed:raise ValueError('测速字段无效')
            self.p.db.set('perf:client:'+str(__import__('uuid').uuid4()),body)
            return dict(ok=True)
        uid,user,start=await self.authorize(envelope.get('initData',''))
        body=envelope.get('body',{})
        if not isinstance(body,dict):raise ValueError('请求格式错误')
        if any(x in body for x in ('uid','user_id','owner','role')):raise PermissionError('身份由 Telegram 校验，不接受网页身份参数')
        today=self.p.c.now().date().isoformat()
        if path=='/api/session':
            name,username=self.identity(uid)
            result=dict(name=name,username=username,admin=self.p.s.admin(uid),today=today,start=start,label=self.p.c.trial_label if self.p.c.test_run else '',group=f'https://t.me/c/{str(self.p.c.chat)[4:]}/{self.p.c.threads["progress"]}')
            result['perf']=bool(uid==self.p.c.owner and self.p.db.setting('perf_enabled',False));result['perf_phase']=self.p.db.setting('perf_phase','before')
            # One authenticated relay round trip for the initial administrator view.
            if result['admin'] and start not in ('publish','people') and not start.startswith('feedback_'):
                rid=int(start[8:]) if start.startswith('summary_') else None
                day=self.p.round(rid)['day'] if rid else today
                history=self.p.db.all('SELECT id,day,at FROM pg_rounds WHERE day=? ORDER BY id DESC',(day,))
                rid=rid or (history[0]['id'] if history else None)
                result['initial']=dict(round=rid,rounds=history,summary=self.summary(rid,uid) if rid else None,tasks=len(self.p.tasks(day,True)))
            if start.startswith('feedback_'):result['initial_feedback']=self.feedback_view(int(start[9:]),uid)
            if start=='people':result['start']='summary'
            return result
        if path in ('/api/people','/api/people/invite','/api/people/approve','/api/people/reject'):
            raise PermissionError('人员由工作群成员资格自动登记；此小程序入口已停用')
        if path=='/api/history':
            self.p.admin(uid);day=body.get('day',today)
            return dict(rounds=self.p.db.all('SELECT id,day,at FROM pg_rounds WHERE day=? ORDER BY id DESC',(day,)))
        if path=='/api/feedback':return self.feedback_view(int(body['round']),uid)
        if path=='/api/summary':return self.summary(int(body['round']),uid,body.get('issue_pages'))
        if path=='/api/tasks':
            self.p.admin(uid);return dict(tasks=self.p.tasks(body.get('day',today),True))
        key=body.get('key')
        if path=='/api/publish':
            self.p.admin(uid);tasks=body.get('tasks');day=body.get('day',today)
            if not isinstance(tasks,list) or not 1<=len(tasks)<=20:raise ValueError('请填写1–20项任务')
            definitions=[self.p.definition(day,t.get('title',''),t.get('kind','seq'),t.get('stages',[])) for t in tasks]
            def publish():
                ids=[self.p.publish(uid,t) for t in definitions]
                from .progress_messages import published
                # At most 3 tasks per Telegram message; whole batch is durable and idempotent.
                for i in range(0,len(ids),3):
                    text=published(day,[self.p.task(x) for x in ids[i:i+3]],i+1)
                    self.p.queue(f'app:publish:{uid}:{key}:{i}',dict(text=text,parse_mode='HTML',chat=self.p.c.chat,thread=self.p.c.threads['progress']))
                return dict(ok=True,message='任务已发布',tasks=ids)
            return self.once(uid,key,body,publish)
        if path=='/api/query':
            self.p.admin(uid)
            def query():
                day=body.get('day',today);rid=self.p.new_round(uid,day,body.get('task') or None,roster=[uid] if getattr(self,'is_verification',False) else None,simulated=getattr(self,'is_verification',False))
                text,markup=query_card(self.p,rid)
                self.p.queue(f'app:query:{uid}:{key}',dict(text=text,markup=markup,parse_mode='HTML',chat=self.p.c.chat,thread=self.p.c.threads['progress'],kind='query',round=rid))
                return dict(ok=True,message='查询已发起',round=rid)
            return self.once(uid,key,body,query)
        if path=='/api/submit':
            return self.once(uid,key,body,lambda:self.submit(int(body['round']),uid,body))
        raise ValueError('入口不存在')
