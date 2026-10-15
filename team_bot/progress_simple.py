"""Presentation and task draft editor; numeric identities and query isolation stay in the ledger."""
import json,uuid
from .db import dumps
from .rich import heading,paragraph,details

def b(text,data):return dict(text=text[:35],callback_data='pg:'+data)
def kb(*rows):return dict(inline_keyboard=[x for x in rows if x])
def clock(value):
 from .progress_ui import time_text
 return time_text(value)
def menu(p,uid):
 day=p.c.now().date().isoformat()
 if p.s.admin(uid):return '工作进度\n'+day,kb([b('发布任务','new'),b('查询任务','query')])
 r=p.latest(day)
 return '工作进度\n'+day+('\n等待管理员查询。' if not r else ''),kb([b('反馈进度','open:'+str(r['id']))]) if r else None
def query_card(p,rid):
 r=p.round(rid);text='进度查询｜'+r['day']+'\n查询时间：'+clock(r['at'])
 if r['simulated']:text+='\n模拟展示'
 text+='\n\n'+'\n'.join(t['title'] for t in r['tasks'])
 from .bot_links import private
 return text,kb([b('反馈进度',f'open:{rid}'),dict(text='私聊反馈',url=private(p.c,f'pg_{rid}'))],[b('查看结果',f'team:{rid}:1:1'),b('查询某项','choose:'+r['day'])])
def task_list(p,uid,day,page=1,choose=False):
 if choose:p.admin(uid)
 tasks=p.tasks(day,choose);pages=max(1,(len(tasks)+9)//10)
 if not 1<=page<=pages:raise ValueError('页码超出范围')
 text=('选择任务' if choose else '今日任务')+'｜'+day+f'\n第{page}/{pages}页'
 rows=[[b(x['title'],f'newquery:{day}:{x["id"]}' if choose else f'task:{x["id"]}')] for x in tasks[(page-1)*10:page*10]]
 command='choose' if choose else 'tasks'
 rows+=[[b('上一页',f'{command}:{day}:{page-1}') for _ in [0] if page>1]+[b('下一页',f'{command}:{day}:{page+1}') for _ in [0] if page<pages]]
 if choose:rows+=[[b('查询全部',f'newquery:{day}:0')]]
 return text,kb(*rows)
def blank():return dict(title='',stages=['','',''],kind='seq')
def builder(p,uid):
 row=p.db.one('SELECT * FROM pg_builders WHERE uid=?',(uid,))
 if not row:raise ValueError('请点击发布任务')
 return json.loads(row['payload']),row
def save_builder(p,uid,data):p.db.run('UPDATE pg_builders SET payload=? WHERE uid=?',(dumps(data),uid))
def builder_view(p,uid):
 d,row=builder(p,uid);i=d['selected'];t=d['tasks'][i]
 text='发布任务｜'+d['day']+'\n'
 shown=enumerate(d['tasks']) if len(d['tasks'])<=3 else [(i,t)]
 if len(d['tasks'])>3:text+=f'共{len(d["tasks"])}项 · 当前第{i+1}项\n'
 for j,x in shown:
  text+='\n'+('正在填写 · ' if j==i else '')+'任务主题：'+x['title']+'\n'+('按顺序完成' if x['kind']=='seq' else '自由完成')+'\n'+'\n'.join(f'步骤{k+1}：'+s for k,s in enumerate(x['stages']))+'\n'
 text+='\n发送“任务主题”和“步骤1–'+str(len(t['stages']))+'”填写当前任务。'
 rows=[[b('填写任务',f'build:edit:{i}'),b('＋第4步' if len(t['stages'])==3 else '保留3步','build:four'),b('按顺序：开' if t['kind']=='seq' else '按顺序：关','build:toggle')]]
 if 1<len(d['tasks'])<=6:rows+=[[b(x['title'] or f'任务{j+1}',f'build:edit:{j}') for j,x in enumerate(d['tasks'])][k:k+2] for k in range(0,len(d['tasks']),2)]
 elif len(d['tasks'])>6:rows+=[[b('上一任务',f'build:edit:{i-1}') for _ in [0] if i>0]+[b('下一任务',f'build:edit:{i+1}') for _ in [0] if i<len(d['tasks'])-1]]
 rows+=[[b('＋任务','build:add'),b('发布','build:publish')]]
 for row in rows:
  for item in row:item['callback_data']+=':'+d['token']
 return text,kb(*rows)
def task_card(day,tasks):
 text='今日任务｜'+day
 for t in tasks:text+='\n\n任务：'+t['title']+'\n'+('按顺序完成' if t['kind']=='seq' else '自由完成')+'\n'+'\n'.join(f'{i+1}. {s}' for i,s in enumerate(t['stages']))
 return text,kb([b('查询任务','query'),b('查询某项','choose:'+day)])
def pages(r):
 groups=[];group=[];size=0;buttons=0
 for t in r['tasks']:
  n=len(t['title'])+sum(map(len,t['stages']))+150;count=len(t['stages'])+1
  if group and (size+n>1450 or buttons+count>14):groups.append(group);group=[];size=buttons=0
  group.append(t);size+=n;buttons+=count
 if group:groups.append(group)
 return groups
def panel(p,rid,uid,page=1,result=''):
 r=p.guard(rid,uid);data=p.draft(rid,uid);groups=pages(r)
 if not 1<=page<=len(groups):raise ValueError('页码超出范围')
 text='进度反馈｜'+r['day']+'\n';rows=[]
 for t in groups[page-1]:
  mask=data[str(t['id'])];ordinal=r['tasks'].index(t)+1;text+='\n'+(str(ordinal)+'. ' if len(groups[page-1])>1 else '')+t['title']+' · '+('按顺序完成' if t['kind']=='seq' else '自由完成')+f' · {mask.bit_count()}/{len(t["stages"])}\n'+'\n'.join(('● ' if mask&(1<<i) else '○ ')+f'{i+1}. {stage}' for i,stage in enumerate(t['stages']))+'\n'
  name=str(ordinal)+' '+t['title'][:4]+'：' if len(groups[page-1])>1 else ''
  prefix=f'{rid}:{uid}:{t["id"]}'
  if t['kind']=='seq':choices=[b(name+('暂未完成' if i==0 else '全部完成' if i==len(t['stages']) else f'到第{i}步'),f'select:{prefix}:{i}:{page}') for i in range(len(t['stages'])+1)]
  else:choices=[b(name+('● ' if mask&(1<<i) else '○ ')+f'步骤{i+1}',f'select:{prefix}:{i+1}:{page}') for i in range(len(t['stages']))]+[b(name+'暂未完成',f'select:{prefix}:0:{page}')]
  rows.extend([choices[i:i+2] for i in range(0,len(choices),2)])
 note=p.note(rid,uid,True);text+='\n备注：\n'+(note if len(note)<=450 else note[:150]+'…\n点击“展开备注”查看全文。') if note else '\n备注：未填写'
 if result:text+='\n\n'+result
 rows+=[[b('填写备注',f'note:{rid}:{uid}:{page}')]+([b('展开备注',f'noteshow:{rid}:{uid}:1')] if len(note)>450 else []),[b('上一页',f'page:{rid}:{uid}:{page-1}') for _ in [0] if page>1]+[b('下一页',f'page:{rid}:{uid}:{page+1}') for _ in [0] if page<len(groups)],[b('提交反馈',f'done:{rid}:{uid}:{page}')]]
 return text,kb(*rows)
def team(p,rid,tp=1,page=1):
 r=p.round(rid);totalpages=max(1,(len(r['roster'])+7)//8)
 if not 1<=tp<=len(r['tasks']) or not 1<=page<=totalpages:raise ValueError('页码超出范围')
 task=r['tasks'][tp-1];cells=[];people=r['roster'][(page-1)*8:page*8];count=p.db.one('SELECT COUNT(*) n FROM pg_feedback WHERE round=?',(rid,))['n'];note_blocks=[];note_text=[];note_buttons=[]
 for uid in people:
  name,user=p.identity(uid);f=p.feedback(rid,uid);mask=json.loads(f['payload']).get(str(task['id']),0) if f else None
  progress='—' if mask is None else ''.join('●' if mask&(1<<i) else '○' for i in range(len(task['stages'])))+f' {mask.bit_count()}/{len(task["stages"])}'
  cells.append([name,user,progress,'未反馈' if f is None else '已反馈'])
  note=p.note(rid,uid)
  if note:
   note_text.append(name+'（'+user+'）\n'+note)
   note_blocks.append(paragraph(name+'（'+user+'）'))
   if len(note)>500:note_blocks.append(details('展开备注',[paragraph(note[i:i+1000]) for i in range(0,len(note),1000)]))
   else:note_blocks.append(paragraph(note+'\n'))
   if len(note)>500:note_buttons.append(b(name[:12]+'备注',f'noteshow:{rid}:{uid}:0'))
 from .daily_history import identity_table,wrap_identity
 grid=identity_table(['员工名称','Telegram 用户名','完成进度','反馈状态'],[[wrap_identity(x[0]),wrap_identity(x[1])]+x[2:] for x in cells])
 for row in grid['cells']:
  for i,c in enumerate(row):c['align']='center' if i>=2 or c.get('is_header') else 'left';c['valign']='middle'
 title='团队进度｜'+r['day'];subtitle=('模拟展示\n' if r['simulated'] else '')+task['title']+' · '+('按顺序完成' if task['kind']=='seq' else '自由完成')+f'\n查询 {clock(r["at"])} · 更新 {clock(p.now())}\n应反馈{len(r["roster"])}人 · 已反馈{count}人 · 第{page}/{totalpages}页'
 blocks=[heading(title),paragraph(subtitle),grid]
 if note_blocks:blocks.extend([heading('问题备注')]+note_blocks)
 rows=[[b(p.identity(u)[0],f'employee:{rid}:{u}:1') for u in people[i:i+2]] for i in range(0,len(people),2)]
 rows+=[[b('上一页',f'team:{rid}:{tp}:{page-1}') for _ in [0] if page>1]+[b('刷新',f'team:{rid}:{tp}:{page}')]+[b('下一页',f'team:{rid}:{tp}:{page+1}') for _ in [0] if page<totalpages],[b('上一任务',f'team:{rid}:{tp-1}:1') for _ in [0] if tp>1]+[b('下一任务',f'team:{rid}:{tp+1}:1') for _ in [0] if tp<len(r['tasks'])]]
 rows.extend([note_buttons[i:i+2] for i in range(0,len(note_buttons),2)])
 return title+'\n'+subtitle+'\n'+'\n'.join('｜'.join(x) for x in cells)+('\n\n问题备注\n'+'\n\n'.join(note_text) if note_text else ''),kb(*rows),dict(blocks=blocks)
def employee_view(p,rid,uid,page=1):
 r=p.round(rid)
 if uid not in r['roster']:raise ValueError('员工不在查询名单')
 t=r['tasks'][page-1];f=p.feedback(rid,uid);mask=json.loads(f['payload']).get(str(t['id']),0) if f else 0;name,user=p.identity(uid)
 text='员工详情｜'+name+'\nTelegram 用户名：'+user+'\n'+r['day']+' · 查询 '+clock(r['at'])+'\n\n'+t['title']+' · '+('按顺序完成' if t['kind']=='seq' else '自由完成')+'\n'+('未反馈' if not f else f'已反馈 · {mask.bit_count()}/{len(t["stages"])}')+'\n'+'\n'.join(('● ' if mask&(1<<i) else '○ ')+x for i,x in enumerate(t['stages']))+'\n最近反馈：'+(clock(f['at']) if f else '—')
 note=p.note(rid,uid)
 if note:text+='\n\n问题备注\n'+(note if len(note)<500 else '已填写，点击展开备注。')
 rows=[[b('上一任务',f'employee:{rid}:{uid}:{page-1}') for _ in [0] if page>1]+[b('下一任务',f'employee:{rid}:{uid}:{page+1}') for _ in [0] if page<len(r['tasks'])],[b('返回总表',f'team:{rid}:1:1')]+([b('展开备注',f'noteshow:{rid}:{uid}:0')] if len(note)>=500 else [])]
 return text,kb(*rows)
