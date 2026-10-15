"""Button UI for the workbench. Admin views have no employee mutation buttons."""
import json
from datetime import datetime,timedelta
from .workbench import Workbench
from .presentation import human_time,status_label
from .rich import heading,paragraph,table

def button(text,data):return dict(text=text,callback_data='wb:'+data)
def dispatch(a,u,command,args,raw):
 if command not in ('wb','work','team_work','checkin','progress_view','progress_plain','wb_input','ck'):return None
 a.pending_workbench=None
 uid=u.effective_user.id;chat=u.effective_chat;msg=u.effective_message;w=Workbench(a.s);day=w.day();page=1
 if command=='ck':return '旧打卡按钮已停用，历史保留。请私聊“我的工作”。',None,None,None
 action='mine' if command in ('work','checkin') else 'team' if command in ('team_work','progress_view','progress_plain') else args[0].split(':')[0] if command=='wb' else 'input'
 parts=args[0].split(':') if command=='wb' and args else []
 if command=='wb' and chat.type=='private':a.db.set('wb_input:'+str(uid),None)
 admin=a.s.admin(uid)
 if action in ('team','view','timeline','dates','customdate','config','edit','periodcfg','correct','correct_task','input','adminhelp') and admin:
  if chat.type!='private' and (chat.id!=a.c.chat or msg.message_thread_id!=a.scheduler.api_thread('progress')):raise PermissionError('团队入口仅在进度话题或管理员私聊')
 else:a.route(u,'progress')
 if action=='adminhelp':
  if chat.type!='private' or not admin:raise PermissionError('处理求助仅管理员私聊')
  owner=int(parts[1]);day=parts[2];w.help(owner,day,'已解决',actor=uid)
  notice='wb-admin-help:'+str(u.update_id);old_notice=a.db.setting('wb-help-notice:'+str(owner)+':'+day)
  old=a.db.one('SELECT sent_id FROM outbox WHERE key=?',(old_notice+':0',)) if old_notice else None
  if old and old['sent_id']:
   a.scheduler.reply(notice,a.c.owner,None,help_notice(w,owner,day),{'inline_keyboard':[[button('查看员工',f'view:{owner}:{day}:1')]]},audience='admin');a.db.set('edit:'+notice+':0',old['sent_id'])
  card=a.db.one("SELECT * FROM wb_cards WHERE kind='mine' AND viewer=? AND day=?",(owner,day))
  if card and card['message_id']:
   from types import SimpleNamespace
   page=a.db.setting('wb-mine-page:'+str(owner)+':'+day,1)
   fake=SimpleNamespace(effective_user=SimpleNamespace(id=owner),effective_chat=SimpleNamespace(id=owner,type='private'),effective_message=SimpleNamespace(message_id=card['message_id']))
   personal,buttons,_,_=dispatch(a,fake,'wb',[f'mine:{day}:{page}'],'')
   a.scheduler.reply(notice+':personal',owner,None,personal,buttons);a.db.set('edit:'+notice+':personal:0',card['message_id'])
  a.pending_workbench=None;a.edit_target=None
  return '求助已处理\n'+a.s.display(owner)+' · '+day+'\n状态：已解决',{'inline_keyboard':[[button('查看员工',f'view:{owner}:{day}:1')]]},None,None
 if action=='input':
  if chat.type!='private' or not admin:raise PermissionError('配置与更正仅管理员私聊')
  state=a.db.setting('wb_input:'+str(uid));a.db.set('wb_input:'+str(uid),None)
  if not state:raise ValueError('输入已失效，请从配置按钮重新进入')
  from .reports import lines
  f=lines(raw);tasks,periods=w.config((w.now().date()+timedelta(days=1)).isoformat());reason=f.get('原因','')
  if state['kind']=='task':
   t=next(t for t in tasks if t['id']==state['task']);t.update(name=f['名称'],unit=f['单位'],target=int(f['目标']),order=int(f['排序']),kind={'数量型':'quantity','完成型':'complete'}[f['类型']],uids=[] if f['员工']=='全部' else [int(x) for x in f['员工'].split(',')]);effective=w.configure(uid,tasks,periods,reason)
   return '配置已保存\n生效日期：'+effective,{'inline_keyboard':[[button('返回配置','config')]]},None,None
  if state['kind']=='period':
   periods=[v.split('–') for v in f['时段'].split(',')];effective=w.configure(uid,tasks,periods,reason);return '时段配置已保存\n生效日期：'+effective,{'inline_keyboard':[[button('返回配置','config')]]},None,None
  if state['kind']=='correct':
   p=f.get('时段','时间外');p=None if p=='时间外' else int(p)-1
   w.change(uid,state['uid'],state['task'],state['day'],'correct','update:'+str(u.update_id),int(f['数量']),reason,p)
   return '更正已保存\n日期：'+state['day']+'\n原因：'+reason,{'inline_keyboard':[[button('返回详情',f'view:{state["uid"]}:{state["day"]}:1')]]},None,None
  if state['kind']=='date':
   day=f['日期'];w.ensure(day);action='team';page=1;parts=['team',day,'1','all']
 if action in ('config','edit','periodcfg','correct','correct_task'):
  if chat.type!='private' or not admin:raise PermissionError('配置及更正只允许管理员私聊')
  if action=='config':
   tasks,periods=w.config((w.now().date()+timedelta(days=1)).isoformat());markup=[[button('修改 '+t['name'],f'edit:{t["id"]}')] for t in tasks];markup+=[[button('修改统计时段','periodcfg')],[button('查看团队','team:'+day+':1:all')]]
   effective=(w.now().date()+timedelta(days=1)).isoformat()
   return '工作台配置\n生效日期：'+effective+'\n模拟任务\n\n'+'\n\n'.join(f'{t["name"]}\n目标：{t["target"]}{t["unit"]} · '+('数量型' if t['kind']=='quantity' else '完成型')+f'\n排序：{t["order"]} · 员工：'+('全部' if not t['uids'] else '、'.join(a.s.display(x) for x in t['uids'])) for t in tasks),{'inline_keyboard':markup},None,None
  if action=='edit':
   task=int(parts[1]);t=next(t for t in w.config((w.now().date()+timedelta(days=1)).isoformat())[0] if t['id']==task);a.db.set('wb_input:'+str(uid),dict(kind='task',task=task))
   return '请按下面字段发送一条消息；原因必填，员工填写全部或Telegram ID逗号列表。\n'+f'名称：{t["name"]}\n单位：{t["unit"]}\n目标：{t["target"]}\n排序：{t["order"]}\n类型：'+('数量型' if t['kind']=='quantity' else '完成型')+'\n员工：'+('全部' if not t['uids'] else ','.join(map(str,t['uids'])))+'\n原因：',None,None,None
  if action=='periodcfg':
   a.db.set('wb_input:'+str(uid),dict(kind='period'));return '请发送一条消息（起点含，终点不含，午夜终点00:00）：\n时段：'+','.join('–'.join(p) for p in w.config((w.now().date()+timedelta(days=1)).isoformat())[1])+'\n原因：',None,None,None
  owner=int(parts[1]);day=parts[2]
  if action=='correct':return '管理员更正\n'+day+' · '+a.s.display(owner)+'\n请选择任务。',{'inline_keyboard':[[button(t['name'],f'correct_task:{owner}:{day}:{t["id"]}')] for t in w.tasks(owner,day)]},None,None
  task=int(parts[3]);a.db.set('wb_input:'+str(uid),dict(kind='correct',uid=owner,day=day,task=task));return '管理员更正\n'+day+' · '+a.s.display(owner)+'\n填写最终数量、所属时段及原因。\n时段使用序号1至'+str(len(w.ensure(day)[1]))+'，或“时间外”。\n\n数量：\n时段：时间外\n原因：',None,None,None
 if action in ('mine','add','undo','complete','zero','help','resolved'):
  if chat.type=='private' and chat.id!=uid:
   raise PermissionError('仅本人私聊可以操作工作台')
  if chat.type!='private':
   return '请点击私聊菜单“我的工作”，工作台仅在本人私聊更新。',{'inline_keyboard':[[button('我的工作','mine')]]},None,uid
  if parts and len(parts)>1:day=parts[1]
  if action=='mine' and len(parts)>2:page=int(parts[2])
  if action in ('help','resolved') and len(parts)>2:page=int(parts[2])
  if action!='mine':
   card=a.db.one("SELECT * FROM wb_cards WHERE kind='mine' AND viewer=? AND day=?",(uid,day))
   if not card or card['chat']!=uid or card['message_id']!=msg.message_id:raise PermissionError('仅当前本人工作台可以操作')
   if action in ('help','resolved'):
    helpkey='wb-help-callback:'+u.callback_query.id if u.callback_query else 'wb-help-update:'+str(u.update_id)
    changed=False
    if not a.db.setting(helpkey):
     changed=w.help(uid,day,'需要帮助' if action=='help' else '已解决');a.db.set(helpkey,True)
    notice='wb-help:'+str(u.update_id);notice_key='wb-help-notice:'+str(uid)+':'+day
    if changed and action=='help':
     a.scheduler.reply(notice,a.c.owner,None,help_notice(w,uid,day),{'inline_keyboard':[[button('查看员工',f'view:{uid}:{day}:1'),button('标记已处理',f'adminhelp:{uid}:{day}')]]},audience='admin');a.db.set(notice_key,notice)
    elif changed and action=='resolved':
     old_notice=a.db.setting(notice_key);old=a.db.one('SELECT sent_id FROM outbox WHERE key=?',(old_notice+':0',)) if old_notice else None
     if old and old['sent_id']:
      a.scheduler.reply(notice,a.c.owner,None,help_notice(w,uid,day),{'inline_keyboard':[[button('查看员工',f'view:{uid}:{day}:1')]]},audience='admin');a.db.set('edit:'+notice+':0',old['sent_id'])
   else:
    task=int(parts[2]);page=int(parts[3]) if len(parts)>3 else 1;key='callback:'+u.callback_query.id if u.callback_query else 'update:'+str(u.update_id);w.change(uid,uid,task,day,action,key)
  text,tasks,pages=w.personal(uid,day,page);a.db.set('wb-mine-page:'+str(uid)+':'+day,page);markup=[]
  for t in tasks:
   act='add' if t['kind']=='quantity' else 'complete';label='＋1' if act=='add' else '完成'
   markup.append([button(label,f'{act}:{day}:{t["id"]}:{page}'),button('撤销',f'undo:{day}:{t["id"]}:{page}'),button('记为0',f'zero:{day}:{t["id"]}:{page}')])
  markup.append([button('需要帮助',f'help:{day}:{page}'),button('求助已解决',f'resolved:{day}:{page}')])
  markup.append([button(t['name'],f'mine:{day}:{i+1}') for i,t in enumerate(w.tasks(uid,day))] if pages<=4 else [])
  markup.append([button('上一项',f'mine:{day}:{page-1}') for _ in [0] if page>1]+[button('刷新',f'mine:{day}:{page}')]+[button('下一项',f'mine:{day}:{page+1}') for _ in [0] if page<pages])
  kind='mine';target=uid
 elif action in ('team','view','timeline','dates','customdate'):
  if not admin:raise PermissionError('团队及员工详情仅管理员查看')
  if len(parts)>1:day=parts[1] if action not in ('view','timeline') else parts[2]
  if action=='customdate':
   if chat.type!='private':return '请在管理员私聊选择日期。',None,None,uid
   a.db.set('wb_input:'+str(uid),dict(kind='date'));return '请发送：\n日期：YYYY-MM-DD',None,None,None
  if action=='dates':
   markup=[[button((datetime.fromisoformat(day)-timedelta(days=i)).date().isoformat(),'team:'+(datetime.fromisoformat(day)-timedelta(days=i)).date().isoformat()+':1:all')] for i in range(7)];markup.append([button('其他日期（私聊）','customdate')]);return '选择工作日期',{'inline_keyboard':markup},None,None
  if action=='team':
   page=int(parts[2]) if len(parts)>2 else int(args[1]) if command!='wb' and len(args)>1 else 1
   if command!='wb' and args:day=args[0]
   p=parts[3] if len(parts)>3 else 'all';period=None if p=='all' else int(p);text,rich,pages,r=w.team_view(day,page,period);a.presentation_audience='admin';a.presentation=None if command=='progress_plain' else rich
   start=r['page_start'];end=min(start+10,len(r['rows']))
   # Keep names distinguishable on narrow clients; native buttons otherwise elide all suffixes.
   labels=[a.s.display(e['uid'],short=True) for e in r['rows'][start:end]]
   columns=2 if any(len(label)>12 for label in labels) else 3
   markup=[[button(a.s.display(e['uid'],short=True),f'view:{e["uid"]}:{day}:1') for e in r['rows'][i:min(i+columns,end)]] for i in range(start,end,columns)]
   markup.append([button('上一页',f'team:{day}:{page-1}:{p}') for _ in [0] if page>1]+[button('下一页',f'team:{day}:{page+1}:{p}') for _ in [0] if page<pages])
   markup.append([button('当前累计',f'team:{day}:{page}:all'),button('刷新',f'team:{day}:{page}:{p}'),button('日期',f'dates:{day}')])
   spans=[button('–'.join(span),f'team:{day}:{page}:{i}') for i,span in enumerate(r['periods'])];markup.extend([spans[i:i+2] for i in range(0,len(spans),2)]);markup.append([button('时间外',f'team:{day}:{page}:-1')])
   kind='team' if chat.type!='private' else 'admin';target=chat.id
   if kind=='team':a.db.run('INSERT INTO wb_boards(day,page,period) VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET page=excluded.page,period=excluded.period',(day,page,period))
  else:
   owner=int(parts[1]);page=int(parts[3]) if len(parts)>3 else 1;text,rich,pages=w.detail_view(owner,day,1 if action=='timeline' else page);target=uid;kind='admin';a.presentation=rich;a.presentation_audience='admin'
   if action=='timeline':
    events=w.timeline(owner,day,page);tasks={t['id']:t for t in w.tasks(owner,day)};names={'add':'＋1','complete':'完成','undo':'撤销','zero':'记为0','correct':'更正'};rows=[]
    for e in events:
     all_events=a.db.all('SELECT id,delta FROM wb_events WHERE uid=? AND task=? AND day=? AND id<=?',(owner,e['task'],day,e['id']));n=sum(x['delta'] for x in all_events)
     snap=a.db.one('SELECT * FROM wb_event_names WHERE event_id=?',(e['id'],))
     rows.append([human_time(e['at'],a.c),tasks[e['task']]['name'],names[e['action']],str(n)+tasks[e['task']]['unit']])
     if a.c.identity_mode=='telegram' and snap and snap['name']!=a.s.display(owner):rows[-1][2]+='（当时：'+snap['name']+'）'
    pages=max(1,(a.db.one('SELECT COUNT(*) n FROM wb_events WHERE uid=? AND day=?',(owner,day))['n']+9)//10);text='时间线 · '+a.s.display(owner)+'\n'+day+'\n'+'\n'.join('｜'.join(row) for row in rows)
    a.presentation=dict(blocks=[heading('时间线 · '+a.s.display(owner)),paragraph(day+f' · 第{page}/{pages}页'),table(['时间','任务','操作','结果'],rows)]+[paragraph('撤销 #'+str(e['undo_of']) if e['action']=='undo' else '更正原因：'+e['reason'][:80]) for e in events if e['action'] in ('undo','correct')])
   if action=='timeline':
    if not 1<=page<=pages:raise ValueError('时间线页码超出范围')
    text+=f'\n时间线第{page}/{pages}页'
   emps=w.employees();idx=next(i for i,e in enumerate(emps) if e['uid']==owner);markup=[[button('返回总表',f'team:{day}:1:all'),button('日期',f'dates:{day}')]]
   markup.append([button('上一人',f'view:{emps[idx-1]["uid"]}:{day}:1') for _ in [0] if idx>0]+[button('下一人',f'view:{emps[idx+1]["uid"]}:{day}:1') for _ in [0] if idx+1<len(emps)])
   markup.append([button('上一页',f'{action}:{owner}:{day}:{page-1}') for _ in [0] if page>1]+[button('下一页',f'{action}:{owner}:{day}:{page+1}') for _ in [0] if page<pages]);markup.append([button('时间线' if action=='view' else '返回详情',f'timeline:{owner}:{day}:1' if action=='view' else f'view:{owner}:{day}:1')]);markup.append([button('管理员更正',f'correct:{owner}:{day}')])
 else:raise ValueError('工作台操作不存在')
 markup={'inline_keyboard':[row for row in markup if row]}
 old=a.db.one('SELECT * FROM wb_cards WHERE kind=? AND viewer=? AND day=?',(kind,uid if kind!='team' else 0,day))
 if old and old['message_id']:a.edit_target=old['message_id']
 if kind=='team':a.db.set('wb_board_markup:'+day,markup)
 a.pending_workbench=dict(kind=kind,viewer=uid if kind!='team' else 0,day=day,chat=target)
 return text,markup,None,target if target!=chat.id else None

def help_notice(w,uid,day):
 row=w.db.one('SELECT state,updated FROM wb_help WHERE uid=? AND day=?',(uid,day))
 return '求助通知\n'+day+' · '+w.s.display(uid)+'\n\n时间：'+human_time(row['updated'],w.c)+'\n说明：员工通过“需要帮助”发起，未附文字说明。\n当前状态：'+row['state']
