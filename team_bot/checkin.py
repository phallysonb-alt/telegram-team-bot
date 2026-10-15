"""Self-recorded simulated work tasks, independent from daily reports."""
import json
from datetime import datetime,time
from .db import dumps

class Checkins:
    def __init__(self,s):self.s=s;self.db=s.db;self.c=s.c

    def tasks(self):
        if not self.db.setting('checkin_initialized'):
            self.db.run('INSERT INTO check_tasks(name,target,uids,checks) VALUES (?,?,?,?)',('每日新增',3,'[]',dumps(['16:00','21:00'])))
            self.db.set('checkin_initialized',True)
        return self.db.all('SELECT * FROM check_tasks WHERE active=1 ORDER BY id')

    def task(self,uid,task):
        self.tasks();self.s.employee(uid)
        row=self.db.one('SELECT * FROM check_tasks WHERE id=? AND active=1',(task,))
        if not row:raise ValueError('模拟任务不存在或停用')
        applicable=json.loads(row['uids'])
        if applicable and uid not in applicable:raise PermissionError('不在任务适用人员中')
        return row

    def day(self):return self.c.now().astimezone(self.c.zone).date().isoformat()

    def working(self,now):
        clock=now.astimezone(self.c.zone).time().replace(tzinfo=None)
        return any(time.fromisoformat(self.c.shifts[a])<=clock and (self.c.shifts[b]=='00:00' or clock<time.fromisoformat(self.c.shifts[b])) for a,b in [('am_start','am_end'),('pm_start','pm_end')])

    def amount(self,uid,task,day):
        return self.db.one('SELECT COALESCE(SUM(delta),0) n FROM check_events WHERE uid=? AND task=? AND day=?',(uid,task,day))['n']

    def change(self,uid,task,day,action,callback):
        self.task(uid,task)
        if self.db.one('SELECT id FROM check_events WHERE callback=?',(callback,)):return '重复回调已忽略'
        now=self.c.now().astimezone(self.c.zone)
        if day!=now.date().isoformat():raise ValueError('旧日期卡片已失效，请 /checkin 获取今日卡片')
        if not self.working(now):raise ValueError('当前不在打卡工作时间；午夜后不归入上一工作日')
        last=self.db.one('SELECT server_time FROM check_events WHERE uid=? AND task=? ORDER BY id DESC LIMIT 1',(uid,task))
        if last and (now-datetime.fromisoformat(last['server_time'])).total_seconds()<self.c.click_cooldown:raise ValueError('点击过快，请稍后再试')
        with self.db.transaction():
            undo=None
            if action=='undo':
                row=self.db.one('SELECT e.id FROM check_events e WHERE uid=? AND task=? AND day=? AND delta=1 AND NOT EXISTS(SELECT 1 FROM check_events u WHERE u.undo_of=e.id) ORDER BY id DESC LIMIT 1',(uid,task,day))
                if not row:raise ValueError('没有可撤销打卡，累计不能低于零')
                undo=row['id']
            elif action!='add':raise ValueError('无效打卡操作')
            delta=-1 if undo else 1
            self.db.run('INSERT INTO check_events(uid,task,day,server_time,delta,callback,undo_of) VALUES (?,?,?,?,?,?,?)',(uid,task,day,now.isoformat(),delta,callback,undo))
            self.db.audit('打卡',f'{uid}:{task}:{day}',uid,'撤销上次' if undo else '本人完成1个',None,dict(delta=delta,server_time=now.isoformat(),undo_of=undo))
        return '打卡已保存'

    def card(self,uid,task,day):
        row=self.task(uid,task);e=self.s.employee(uid);n=self.amount(uid,task,day)
        last=self.db.one('SELECT server_time FROM check_events WHERE uid=? AND task=? AND day=? ORDER BY id DESC LIMIT 1',(uid,task,day))
        status='已完成' if n>=row['target'] else '进行中' if n else '未开始'
        filled=min(10,n*10//row['target']);bar='█'*filled+'░'*(10-filled)
        updated=datetime.fromisoformat(last['server_time']).astimezone(self.c.zone).strftime('%H:%M:%S') if last else '无'
        text=f"【模拟任务；仅员工自行记录】\n{e['code']}｜{row['name']}\n工作日期：{day}\n进度：{n}/{row['target']}\n{bar}\n状态：{status}\n最后更新：{updated}"
        buttons=[{'text':label,'callback_data':f'ck:{action}:{uid}:{task}:{day}'} for label,action in [('完成1个','add'),('撤销上次','undo'),('查看今日记录','view')]]
        return text,{'inline_keyboard':[buttons]}

    def configure(self,actor,task,name,target,uids,checks,reason):
        self.s.require_admin(actor)
        if not name.strip() or target<1:raise ValueError('任务名称不能为空，目标必须大于零')
        for uid in uids:self.s.employee(uid)
        clocks=[time.fromisoformat(v).strftime('%H:%M') for v in checks]
        if not clocks or clocks!=sorted(set(clocks)):raise ValueError('检查时间需递增且不重复')
        self.tasks();old=self.db.one('SELECT * FROM check_tasks WHERE id=?',(task,))
        if not old:raise ValueError('任务不存在')
        with self.db.transaction():
            self.db.run('UPDATE check_tasks SET name=?,target=?,uids=?,checks=? WHERE id=?',(name,target,dumps(uids),dumps(clocks),task))
            self.db.audit('打卡配置',task,actor,reason,old,self.db.one('SELECT * FROM check_tasks WHERE id=?',(task,)))

    def inspect(self,actor,day,task,page=1,detail_uid=None):
        self.s.require_admin(actor);self.tasks()
        row=self.db.one('SELECT * FROM check_tasks WHERE id=?',(task,))
        if not row:raise ValueError('任务不存在')
        applicable=json.loads(row['uids']);clocks=json.loads(row['checks']);emps=[e for e in self.db.all('SELECT uid,code FROM employees WHERE active=1 AND approved=1 ORDER BY code') if not applicable or e['uid'] in applicable]
        pages=max(1,(len(emps)+9)//10)
        if not 1<=page<=pages:raise ValueError('页码超出范围')
        groups={'未开始':[],'进行中':[],'已完成':[]};output=[]
        for e in emps:
            n=self.amount(e['uid'],task,day);groups['已完成' if n>=row['target'] else '进行中' if n else '未开始'].append(e['code'])
        for e in emps[(page-1)*10:page*10]:
            events=self.db.all('SELECT * FROM check_events WHERE uid=? AND task=? AND day=? ORDER BY id',(e['uid'],task,day))
            spans=[];start=self.c.shifts['am_start'];previous=None
            for end in clocks:
                values=[]
                for x in events:
                    clock=datetime.fromisoformat(x['server_time']).astimezone(self.c.zone).time().replace(tzinfo=None)
                    if (clock>time.fromisoformat(previous) if previous else clock>=time.fromisoformat(start)) and clock<=time.fromisoformat(end):values.append(x['delta'])
                spans.append(f'{start}–{end}净变化{sum(values)}');start=end;previous=end
            output.append(f"{e['code']}：{self.amount(e['uid'],task,day)}/{row['target']}；"+'；'.join(spans)+'；最后更新：'+(events[-1]['server_time'] if events else '无'))
        text=f"【模拟任务检查；不核实成果】{row['name']} {day} 第{page}/{pages}页\n"+'\n'.join(k+'：'+('、'.join(v) or '无') for k,v in groups.items())+'\n'+'\n'.join(output)
        if detail_uid is not None:
            if detail_uid not in [e['uid'] for e in emps]:raise ValueError('不在适用名单')
            events=self.db.all('SELECT id,server_time,delta,undo_of FROM check_events WHERE uid=? AND task=? AND day=? ORDER BY id',(detail_uid,task,day))
            text+='\n打卡明细（服务器时间、变化、撤销关联）：\n'+dumps(events)
        return text
