"""Immutable per-query export projection; never reads or writes daily ledgers."""
import json
from datetime import datetime
def clock(value):return datetime.fromisoformat(value).strftime('%H:%M')
def datetime_text(value):return datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S') if value else ''
def data(p):
 summaries=[];groups={};problems=[];definitions=[]
 for v in p.db.all('SELECT * FROM pg_versions ORDER BY task,version'):
  t=json.loads(v['definition']);definitions.append([str(v['task']),t['day'],t['title'],'按顺序完成' if t['kind']=='seq' else '自由完成',len(t['stages']),*(t['stages']+['']*(4-len(t['stages']))),f'任务版本{v["version"]}；计划：{t["plan"] or "无"}；截止：{t["deadline"] or "无"}'])
 for rr in p.db.all('SELECT id FROM pg_rounds ORDER BY id'):
  r=p.round(rr['id']);submitted=[p.feedback(r['id'],uid) for uid in r['roster']];received=sum(bool(f) for f in submitted)
  for t in r['tasks']:
   group=groups.setdefault((t['id'],t['version']),dict(task=t,rows=[]));done=issues=0
   for uid,f in zip(r['roster'],submitted):
    profile=p.db.one('SELECT * FROM pg_roster_names WHERE round=? AND uid=?',(r['id'],uid))
    name,username=(f['name'],f['username']) if f else (profile['name'],profile['username']) if profile else ('身份快照未确认','')
    username='@'+username.lstrip('@') if username and username!='—' else '—'
    masks=json.loads(f['payload']) if f else {};value=masks.get(str(t['id']),0);note=p.task_note(r['id'],uid,t['id']) if f else ''
    delta=p.db.one('SELECT added FROM pg_feedback_delta WHERE round=? AND uid=? AND task=?',(r['id'],uid,t['id']))
    # Old mutable-era queries cannot establish a new-completion event. Keep unknown.
    added=delta['added'].bit_count() if delta else None
    total=len(t['stages']);complete=bool(f and value==(1<<total)-1);done+=complete;issues+=bool(note)
    stages=[('已完成' if value&(1<<i) else '未完成') if f else '未反馈' for i in range(total)]+['—']*(4-total)
    group['rows'].append([r['day'],clock(r['at']),name,username,'已提交' if f else '未反馈',*stages,value.bit_count() if f else None,total,f'{value.bit_count()}/{total}' if f else '未反馈',added,note,datetime_text(f['at']) if f else '',str(r['id']),str(t['id']),str(uid),'已完成' if complete else '进行中' if f else '未反馈'])
    if note:problems.append([r['day'],clock(r['at']),name,username,t['title'],f'{value.bit_count()}/{total}',added,note,datetime_text(f['at']),str(r['id']),str(t['id']),str(uid)])
   summaries.append([r['day'],clock(r['at']),t['title'],str(r['id']),len(r['roster']),received,len(r['roster'])-received,done,issues,received/len(r['roster']) if r['roster'] else None,'模拟展示' if r['simulated'] else '验收试用' if p.c.run_mode=='trial' else '',str(t['id'])])
 return dict(label='【验收试用｜不计正式数据】' if p.c.run_mode=='trial' else '工作进度历史',revision=p.db.setting('progress_excel_revision',0),summaries=summaries,groups=list(groups.values()),problems=problems,definitions=definitions)
