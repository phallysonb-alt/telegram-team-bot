"""Chinese progress UI, durable delivery and official ephemeral HTTP fallback."""
import asyncio,json,uuid
from datetime import datetime
from types import SimpleNamespace
from .progress_rounds import Progress
from .db import dumps,stamp
from .rich import heading,paragraph,table,send_native,edit_native

def controller(s):
 if not hasattr(s,'_progress'):s._progress=Progress(s)
 return s._progress

def button(text,data):return dict(text=text[:35],callback_data='pg:'+data)
def kb(*rows):return dict(inline_keyboard=[r for r in rows if r])
def time_text(value):
 if not value:return '—'
 from zoneinfo import ZoneInfo
 zone=ZoneInfo('Asia/Shanghai');dt=datetime.fromisoformat(value).astimezone(zone)
 return dt.strftime('%H:%M' if dt.date()==datetime.now(zone).date() else '%m-%d %H:%M')
from .progress_simple import menu,query_card,task_list,panel,team,employee_view

class ProgressAPIError(Exception):
 def __init__(self,code,reason):self.code=code;self.reason=reason;super().__init__('临时接口未完成')

async def official(bot,method,payload):
 import httpx
 from telegram.error import RetryAfter
 async with httpx.AsyncClient(timeout=25) as client:
  response=await client.post(str(bot.base_url)+'/'+method,json=payload);data=response.json()
 if not data.get('ok'):
  if data.get('parameters',{}).get('retry_after'):raise RetryAfter(data['parameters']['retry_after'])
  description=str(data.get('description','')).lower();reason=next((x for x in ('method not found','not enough rights','query is too old','ephemeral','chat not found') if x in description),'request rejected')
  raise ProgressAPIError(data.get('error_code'),reason)
 return data['result']

async def flush(p,bot,keys=None):
 if getattr(p,'_sending',False):return
 p._sending=True
 try:
  if keys is None:
   for r in p.db.all('SELECT * FROM pg_rounds WHERE pending=1 AND due<=?',(p.now(),)):
    if p.latest(r['day'])['id']==r['id']:
     from .progress_miniapp import enabled,query_card as app_card
     if enabled():
      message=p.db.setting('query_card:'+str(r['id']))
      if message:
       text,markup=app_card(p,r['id']);p.queue(f'appcard:{r["id"]}:{r["revision"]}',dict(text=text,markup=markup,parse_mode='HTML',chat=p.c.chat,thread=p.c.threads['progress'],edit=message,kind='appcard',round=r['id'],revision=r['revision']))
     else:
      text,markup,rich=team(p,r['id'],r['task_page'],r['people_page']);p.queue(f'board:{r["id"]}:{r["revision"]}',dict(text=text,markup=markup,rich=rich,chat=p.c.chat,thread=p.c.threads['progress'],edit=r['board'],kind='board',round=r['id'],revision=r['revision']))
  for row in p.db.all("SELECT * FROM pg_outbox WHERE state='pending' AND due<=? ORDER BY id LIMIT 16",(p.now(),)):
   if keys and row['key'] not in keys:continue
   v=json.loads(row['payload'])
   if v.get('kind')=='board':
    current=p.round(v['round']);v['text'],v['markup'],v['rich']=team(p,current['id'],current['task_page'],current['people_page']);v['revision']=current['revision']
   if v.get('kind')=='appcard':
    from .progress_miniapp import query_card as app_card
    current=p.round(v['round']);v['text'],v['markup']=app_card(p,current['id']);v['revision']=current['revision'];v['parse_mode']='HTML'
   label='【验收试用｜不计正式数据】\n\n' if p.c.run_mode=='trial' else ''
   text=(label if v.get('parse_mode')=='HTML' else p.label())+v['text'];markup=v.get('markup');chat=v['chat'];target=v.get('edit');method='sendMessage';ephemeral=None
   if p.c.test_run and markup:
    for buttons in markup['inline_keyboard']:
     for b in buttons:
      if 'callback_data' in b:b['callback_data']='test:'+p.c.test_run+':'+b['callback_data']
   if chat<0 and (chat!=p.c.chat or v.get('thread')!=p.c.threads['progress']):
    p.db.run("UPDATE pg_outbox SET state='paused_old_route' WHERE id=?",(row['id'],));continue
   try:
    if v.get('export'):
     from .progress_export import ensure_export,export_path
     p.admin(v['uid'])
     if not await ensure_export(p):raise ValueError('跟进表待更新，请关闭占用文件后重试')
     v['file']=str(export_path(p))
    if v.get('ephemeral'):
     try:
      if target:
       method='editEphemeralMessageText';await official(bot,method,dict(chat_id=chat,receiver_user_id=v['uid'],ephemeral_message_id=target,text=text,reply_markup=markup));ephemeral=target
      else:
       method='sendMessage(ephemeral)';result=await official(bot,'sendMessage',dict(chat_id=chat,message_thread_id=p.c.threads['progress'],text=text,reply_markup=markup,allow_paid_broadcast=False,ephemeral_message_parameters=dict(receiver_user_id=v['uid'],**({'callback_query_id':v['callback']} if v.get('callback') else {}))))
       ephemeral=result.get('ephemeral_message_id')
       if not ephemeral:raise ValueError('接口未返回个人临时消息标识')
      sent=SimpleNamespace(message_id=ephemeral)
     except Exception as err:
      p.db.set('ephemeral_last_failure',dict(type=type(err).__name__,code=getattr(err,'code',None),reason=getattr(err,'reason','transport failure'),at=p.now()));chat=v['uid'];target=None;method='private fallback'
      private=p.db.one('SELECT * FROM pg_panels WHERE round=? AND uid=? AND chat=? AND ephemeral=0',(v['round'],v['uid'],chat))
      if private and private['message']:
       target=private['message'];sent=await bot.edit_message_text(chat_id=chat,message_id=target,text=text,reply_markup=__import__('telegram').InlineKeyboardMarkup.de_json(markup,bot))
      else:sent=await bot.send_message(chat_id=chat,text=text,reply_markup=__import__('telegram').InlineKeyboardMarkup.de_json(markup,bot))
    elif v.get('file'):
     with open(v['file'],'rb') as f:sent=await bot.send_document(chat_id=chat,document=f,filename='工作进度历史.xlsx',caption=text)
     method='sendDocument'
    elif v.get('rich'):
     rich=v['rich'];rich['blocks'].insert(0,heading(p.c.trial_label)) if p.c.test_run else None
     if target:method='editMessageText(rich)';sent=await edit_native(bot,chat,target,rich,markup)
     else:method='sendRichMessage';sent=await send_native(bot,chat,v.get('thread'),rich,markup)
    else:
     from telegram import InlineKeyboardMarkup,ReplyParameters
     m=InlineKeyboardMarkup.de_json(markup,bot) if markup else None
     if target:method='editMessageText';sent=await bot.edit_message_text(chat_id=chat,message_id=target,text=text,reply_markup=m,parse_mode=v.get('parse_mode'))
     else:sent=await bot.send_message(chat_id=chat,message_thread_id=v.get('thread'),text=text,reply_markup=m,parse_mode=v.get('parse_mode'),**({'reply_parameters':ReplyParameters(v['reply'],allow_sending_without_reply=True)} if v.get('reply') else {}))
    receipt=dict(message=getattr(sent,'message_id',target),chat=chat,method=method,ephemeral=ephemeral,at=p.now());p.db.run("UPDATE pg_outbox SET state='sent',receipt=? WHERE id=?",(dumps(receipt),row['id']))
    if v.get('kind')=='panel':p.db.run('INSERT OR REPLACE INTO pg_panels VALUES (?,?,?,?,?)',(v['round'],v['uid'],chat,receipt['message'],int(bool(ephemeral))))
    elif v.get('kind')=='builder':p.db.run('UPDATE pg_builders SET chat=?,message=? WHERE uid=?',(chat,receipt['message'],v['uid']))
    elif v.get('kind')=='board':p.db.run('UPDATE pg_rounds SET board=?,pending=CASE WHEN revision=? THEN 0 ELSE 1 END WHERE id=?',(receipt['message'],v['revision'],v['round']))
    elif v.get('kind')=='query':p.db.set('query_card:'+str(v['round']),receipt['message'])
    elif v.get('kind')=='appcard':p.db.run('UPDATE pg_rounds SET pending=CASE WHEN revision=? THEN 0 ELSE 1 END WHERE id=?',(v['revision'],v['round']))
   except Exception as e:
    if target and 'not modified' in str(e).lower():
     p.db.run("UPDATE pg_outbox SET state='sent',receipt=? WHERE id=?",(dumps(dict(message=target,chat=chat,method=method,result='not_modified',at=p.now())),row['id']))
     if v.get('kind') in ('board','appcard'):p.db.run('UPDATE pg_rounds SET pending=0 WHERE id=? AND revision=?',(v['round'],v['revision']))
    else:
     from datetime import timedelta
     p.db.run('UPDATE pg_outbox SET attempts=attempts+1,error=?,due=? WHERE id=?',(type(e).__name__,(p.c.now()+timedelta(seconds=min(300,2**min(row['attempts']+1,8)))).isoformat(),row['id']))
 finally:p._sending=False

def request_refresh(p,bot):
 if getattr(p,'_refresh_task',None) and not p._refresh_task.done():return
 async def run():
  await asyncio.sleep(1.1)
  await flush(p,bot)
 p._refresh_task=asyncio.create_task(run())

async def handle(a,u,ctx):
 if not u.effective_message or not u.effective_user:return False
 raw=u.effective_message.text or '';cb=u.callback_query;uid=u.effective_user.id;msg=u.effective_message;chat=u.effective_chat;p=controller(a.s);data=(cb.data or '').removeprefix('test:'+a.c.test_run+':') if cb else ''
 mapping={'发布任务':'new','查询任务':'query','今日任务':'tasks','发起查询':'choose','查看进度':'latest','查询历史':'history','导出跟进表':'export','导出进度历史':'export','导出工作进度历史':'export','我的工作':'menu'}
 command='';parts=[]
 if data.startswith('pg:'):parts=data[3:].split(':');command=parts.pop(0)
 elif raw.strip().split(' ')[0] in mapping:
  words=raw.strip().split();command=mapping[words[0]];parts=words[1:]
 elif raw.startswith('/start pgnote_'):command='note';parts=[raw.split('pgnote_',1)[1],str(uid),'1']
 elif raw.startswith('/start pg_'):command='open';parts=[raw.split('pg_',1)[1]]
 elif raw.split(' ')[0].split('@')[0] in ('/work','/team_work','/progress_view','/progress_plain','/progress_template','/checkin'):command='menu'
 elif raw.split(' ')[0].split('@')[0] in ('/wb','/wb_input','/progress_raw','/check_config','/check_admin','/check_detail'):command='retired'
 elif data.startswith('wb:') or data.startswith('ck:'):command='retired'
 elif raw.startswith('/start work'):command='menu'
 elif chat.type=='private' and p.db.one('SELECT uid FROM pg_text_inputs WHERE uid=?',(uid,)) and not raw.startswith('/') and not __import__('team_bot.daily',fromlist=['daily_intent']).daily_intent(raw):command='textinput'
 elif p.db.one('SELECT uid FROM pg_inputs WHERE uid=?',(uid,)) and raw.strip().startswith(('日期：','日期:','任务：','任务:','取消')) and not __import__('team_bot.daily',fromlist=['daily_intent']).daily_intent(raw):command='input'
 else:return False
 if chat.type!='private' and (chat.id!=a.c.chat or msg.message_thread_id!=a.scheduler.api_thread('progress')):return False
 from .progress_miniapp import enabled
 if enabled():
  from .progress_miniapp_bot import handle_miniapp
  return await handle_miniapp(a,u,ctx,command,parts)
 if p.db.one('SELECT id FROM pg_updates WHERE id=?',(u.update_id,)):return True
 if cb:
  if a.c.test_run and not (cb.data or '').startswith('test:'+a.c.test_run+':'):await cb.answer('旧入口已失效，请打开新版工作进度入口。');return True
  await cb.answer()
 key='update:'+str(u.update_id);extra_keys=[];text='';markup=None;rich=None;target=chat.id;thread=msg.message_thread_id;kind=None;rid=None;edit=None;ephem=False;file=None
 try:
  with p.db.transaction():
   if command=='retired':text='旧工作台已停用，历史保留。请从“工作进度”新版查询入口反馈。';text2,markup=menu(p,uid)
   elif command=='menu':text,markup=menu(p,uid)
   elif command in ('new','build','textinput','note','noteshow'):
    from .progress_simple import builder,blank,builder_view,save_builder,task_card
    target=uid;thread=None
    if command in ('new','build'):p.admin(uid)
    if command=='new':
     d=dict(day=a.c.now().date().isoformat(),tasks=[blank()],selected=0,token=uuid.uuid4().hex[:12]);p.db.run('INSERT OR REPLACE INTO pg_builders VALUES (?,?,NULL,NULL)',(uid,dumps(d)));p.db.run('INSERT OR REPLACE INTO pg_text_inputs VALUES (?,?,?)',(uid,'builder',dumps(dict(token=d['token']))));text,markup=builder_view(p,uid);kind='builder'
    elif command=='build':
     d,row=builder(p,uid);action=parts[0]
     if parts[-1]!=d['token']:raise ValueError('任务草稿已失效，请打开当前发布任务入口')
     if p.db.one('SELECT token FROM pg_batches WHERE token=?',(d['token'],)):text='任务已发布。'
     else:
      if action=='publish':
       definitions=[p.definition(d['day'],t['title'],t['kind'],t['stages']) for t in d['tasks']];ids=[p.publish(uid,x) for x in definitions];p.db.run('INSERT INTO pg_batches VALUES (?,?,?)',(d['token'],dumps(ids),p.now()));p.db.run('DELETE FROM pg_text_inputs WHERE uid=?',(uid,));text,markup=task_card(d['day'],[p.task(x) for x in ids]);target=a.c.chat;thread=a.scheduler.api_thread('progress');edit=None
       if row['message']:
        ack=key+':draft';p.queue(ack,dict(text='任务已发布。',markup=menu(p,uid)[1],chat=uid,edit=row['message'],thread=None));extra_keys.append(ack)
       if len(text)>3500:
        chunks=[];current=[]
        for task_id in ids:
         candidate=current+[p.task(task_id)]
         if current and len(task_card(d['day'],candidate)[0])>3000:chunks.append(current);current=[]
         current.append(p.task(task_id))
        chunks.append(current)
        for i,chunk in enumerate(chunks[:-1]):
         cardkey=key+':tasks:'+str(i);cardtext,cardbuttons=task_card(d['day'],chunk);p.queue(cardkey,dict(text=cardtext,markup=cardbuttons,chat=target,thread=thread));extra_keys.append(cardkey)
        text,markup=task_card(d['day'],chunks[-1])
      else:
       if action=='add':d['tasks'].append(blank());d['selected']=len(d['tasks'])-1
       elif action=='edit':d['selected']=int(parts[1])
       elif action=='toggle':t=d['tasks'][d['selected']];t['kind']='free' if t['kind']=='seq' else 'seq'
       elif action=='four':t=d['tasks'][d['selected']];t['stages']=t['stages']+[''] if len(t['stages'])==3 else t['stages'][:3]
       else:raise ValueError('请使用当前发布按钮')
       save_builder(p,uid,d);p.db.run('INSERT OR REPLACE INTO pg_text_inputs VALUES (?,?,?)',(uid,'builder',dumps(dict(token=d['token']))));text,markup=builder_view(p,uid);kind='builder';edit=row['message']
    elif command=='textinput':
     entry=p.db.one('SELECT * FROM pg_text_inputs WHERE uid=?',(uid,));state=json.loads(entry['payload'])
     if raw.strip()=='取消':p.db.run('DELETE FROM pg_text_inputs WHERE uid=?',(uid,));text='已取消填写。'
     elif entry['kind']=='builder':
      p.admin(uid);d,row=builder(p,uid)
      if state['token']!=d['token'] or p.db.one('SELECT token FROM pg_batches WHERE token=?',(d['token'],)):raise ValueError('任务草稿已失效')
      fields={}
      for line in raw.splitlines():k,sep,v=line.replace('：',':',1).partition(':');fields[k.strip()]=v.strip() if sep else ''
      t=d['tasks'][d['selected']];title=fields.get('任务主题',fields.get('任务',''));stages=[fields.get('步骤'+str(i+1),fields.get('阶段'+str(i+1),'')) for i in range(len(t['stages']))]
      if fields.get('阶段'):stages=fields['阶段'].split('｜')
      if fields.get('步骤4'):stages=[fields.get('步骤'+str(i+1),'') for i in range(4)]
      definition=p.definition(d['day'],title,t['kind'],stages);t.update(title=definition['title'],stages=definition['stages']);save_builder(p,uid,d);text,markup=builder_view(p,uid);kind='builder';edit=row['message'];p.db.run('DELETE FROM pg_text_inputs WHERE uid=?',(uid,))
     else:
      rid=state['round'];p.edit_note(rid,uid,uid,'' if raw.strip()=='清空备注' else raw);p.db.run('DELETE FROM pg_text_inputs WHERE uid=?',(uid,));text,markup=panel(p,rid,uid,state['page'],'备注已填写；点击提交反馈保存。');kind='panel';old=p.db.one('SELECT * FROM pg_panels WHERE round=? AND uid=? AND chat=? AND ephemeral=0',(rid,uid,uid));edit=old['message'] if old else None
    elif command=='note':
     rid,owner,page=map(int,parts)
     if owner!=uid:raise PermissionError('只能填写本人备注')
     p.guard(rid,uid);p.db.run('INSERT OR REPLACE INTO pg_text_inputs VALUES (?,?,?)',(uid,'note',dumps(dict(round=rid,page=page))));text='填写备注\n请在私聊发送文字，可分多行。\n不填写也可提交；发送“清空备注”清空，发送“取消”返回。'
     from .bot_links import private
     markup=kb([dict(text='打开私聊填写',url=private(p.c,f'pgnote_{rid}'))]) if chat.type!='private' else None
     if chat.type!='private':target=chat.id;thread=a.scheduler.api_thread('progress');ephem=True;kind='panel';edit=getattr(msg,'ephemeral_message_id',None) or msg.api_kwargs.get('ephemeral_message_id')
    else:
     rid,owner,is_draft=map(int,parts)
     if owner!=uid:p.admin(uid)
     if is_draft and owner!=uid:raise PermissionError('不能查看他人未提交备注')
     text='备注｜'+p.identity(owner)[0]+'\n'+(p.note(rid,owner,bool(is_draft)) or '未填写')
   elif command in ('type','edit','input'):
    p.admin(uid)
    if command=='new':text='发布任务\n请选择完成类型。';markup=kb([button('顺序完成','type:seq'),button('自由完成','type:free')])
    elif command in ('type','edit'):
     old=p.task(int(parts[0])) if command=='edit' else None;state=dict(kind=old['kind'] if old else parts[0],task=old['id'] if old else None);p.db.run('INSERT OR REPLACE INTO pg_inputs VALUES (?,?)',(uid,dumps(state)))
     text='任务登记\n请发送以下字段，阶段用“｜”分隔，3或4项。\n日期：'+(old['day'] if old else a.c.now().date().isoformat())+'\n任务：'+(old['title'] if old else '')+'\n阶段：'+('｜'.join(old['stages']) if old else '')+'\n计划：'+(old['plan'] if old else '')+'\n截止：'+(old['deadline'] if old else '')+'\n\n计划/截止可空；截止格式 YYYY-MM-DD HH:mm。发送“取消”结束登记。'
    else:
     if raw.strip()=='取消':p.db.run('DELETE FROM pg_inputs WHERE uid=?',(uid,));text='已取消登记。'
     else:
      state=json.loads(p.db.one('SELECT payload FROM pg_inputs WHERE uid=?',(uid,))['payload']);fields={}
      for line in raw.splitlines():
       k,_,v=line.replace('：',':',1).partition(':');fields[k.strip()]=v.strip()
      task=p.publish(uid,dict(day=fields.get('日期') or a.c.now().date().isoformat(),title=fields.get('任务',''),kind=state['kind'],stages=fields.get('阶段','').split('｜'),plan=fields.get('计划',''),deadline=fields.get('截止','')),state['task']);p.db.run('DELETE FROM pg_inputs WHERE uid=?',(uid,));text=f'任务已登记 · #{task}\n'+p.task(task)['title'];markup=kb([button('今日任务','tasks:'+p.task(task)['day']),button('发起查询','choose:'+p.task(task)['day'])])
   elif command in ('tasks','task','close'):
    if command=='close':p.close(uid,int(parts[0]));text='任务已结束，历史保留。'
    elif command=='task':
     t=p.task(int(parts[0]));text=t['title']+'\n'+t['day']+'\n'+('顺序完成' if t['kind']=='seq' else '自由完成')+'\n'+'\n'.join(f'{i+1}. {x}' for i,x in enumerate(t['stages']))+'\n计划：'+(t['plan'] or '未设置')+'\n截止：'+(t['deadline'] or '未设置')+'\n状态：'+('已结束' if t['closed'] else '有效');markup=kb([button('修改',f'edit:{t["id"]}'),button('结束任务',f'close:{t["id"]}')]) if p.s.admin(uid) and not t['closed'] else None
    else:
     day=parts[0] if parts else a.c.now().date().isoformat();text,markup=task_list(p,uid,day,int(parts[1]) if len(parts)>1 else 1)
   elif command=='choose':
    day=parts[0] if parts else a.c.now().date().isoformat();text,markup=task_list(p,uid,day,int(parts[1]) if len(parts)>1 else 1,True)
   elif command in ('newquery','query'):
    rid=p.new_round(uid,a.c.now().date().isoformat() if command=='query' else parts[0],None if command=='query' else int(parts[1]) or None);text,markup=query_card(p,rid);target=a.c.chat;thread=a.scheduler.api_thread('progress');kind='query'
   elif command in ('open','page','select','done'):
    rid=int(parts[0]);owner=int(parts[1]) if command!='open' else uid
    if owner!=uid:raise PermissionError('只能打开或操作本人的反馈')
    p.guard(rid,uid);page=int(parts[-1]) if command!='open' else 1
    if command=='select':p.select(rid,uid,owner,int(parts[2]),int(parts[3]),'callback:'+cb.id);page=int(parts[4])
    if command=='done':p.submit(rid,uid,owner,'callback:'+cb.id)
    text,markup=panel(p,rid,uid,page,'反馈已提交。' if command=='done' else '')
    kind='panel';target=uid;thread=None
    if chat.type!='private':
     ephem=True;target=chat.id;thread=a.scheduler.api_thread('progress');incoming=getattr(msg,'ephemeral_message_id',None) or msg.api_kwargs.get('ephemeral_message_id')
     if command!='open':edit=incoming
    elif command!='open':edit=msg.message_id
    else:
     old=p.db.one('SELECT * FROM pg_panels WHERE round=? AND uid=? AND chat=? AND ephemeral=0',(rid,uid,uid));edit=old['message'] if old else None
   elif command in ('latest','team','detail','employee','history','audit','export'):
    p.admin(uid)
    if command=='latest':
     last=p.latest(parts[0] if parts else a.c.now().date().isoformat())
     if not last:raise ValueError('该日尚未发起查询')
     command='team';parts=[str(last['id']),'1','1']
    if command=='team':
     rid,tp,page=map(int,parts);text,markup,rich=team(p,rid,tp,page);kind='board';r=p.round(rid);edit=r['board'] if chat.id==a.c.chat else None
     if chat.id==a.c.chat:p.db.run('UPDATE pg_rounds SET task_page=?,people_page=? WHERE id=?',(tp,page,rid))
     else:kind=None
    elif command=='detail':
     rid,tp,page=map(int,parts);r=p.round(rid);t=r['tasks'][tp-1];pages=max(1,(len(r['roster'])+3)//4)
     if not 1<=page<=pages:raise ValueError('详情页码超出范围')
     text='任务详情 · '+t['title']+'\n'+r['day']+f' · 查询 {time_text(r["at"])} · 第{page}/{pages}页\n'
     for employee in r['roster'][(page-1)*4:page*4]:
      f=p.feedback(rid,employee);mask=json.loads(f['payload'])[str(t['id'])] if f else 0;name,user=p.identity(employee);text+='\n'+name+'（'+user+'） · '+('未反馈' if not f else f'{mask.bit_count()}/{len(t["stages"])}')+'\n'+'\n'.join(('☑' if mask&(1<<i) else '□')+' '+stage for i,stage in enumerate(t['stages']))+'\n'
     target=uid;thread=None;markup=kb([button('上一页',f'detail:{rid}:{tp}:{page-1}') for _ in [0] if page>1]+[button('下一页',f'detail:{rid}:{tp}:{page+1}') for _ in [0] if page<pages],[button('返回总表',f'team:{rid}:{tp}:{(page-1)//2+1}')]);edit=msg.message_id if cb and chat.type=='private' else None
    elif command=='employee':rid,employee,page=map(int,parts);text,markup=employee_view(p,rid,employee,page);target=uid;thread=None;edit=msg.message_id if cb and chat.type=='private' else None
    elif command=='history':
     page=int(parts[0]) if parts else 1;rows=p.db.all('SELECT id,day,at FROM pg_rounds ORDER BY id DESC LIMIT 8 OFFSET ?',((page-1)*8,));text='进度查询历史\n第'+str(page)+'页\n'+('\n'.join(f"{x['day']} · {time_text(x['at'])}" for x in rows) or '暂无记录');target=uid;thread=None;markup=kb(*[[button(f"{x['day']} {time_text(x['at'])}",f'team:{x["id"]}:1:1')] for x in rows],[button('上一页',f'history:{page-1}') for _ in [0] if page>1]+[button('下一页',f'history:{page+1}') for _ in [0] if len(rows)==8])
    elif command=='audit':
     rid,employee,page=map(int,parts);rows=p.db.all('SELECT * FROM pg_audit WHERE entity=? AND entity_key=? ORDER BY id DESC LIMIT 6 OFFSET ?',('反馈',f'{rid}:{employee}',(page-1)*6));text='反馈历史 · '+p.identity(employee)[0]+f'\n第{rid}轮\n'+'\n\n'.join(time_text(x['at'])+' · '+x['reason']+'\n选择：'+x['after_value'] for x in rows);target=uid;thread=None;markup=kb([button('返回员工',f'employee:{rid}:{employee}:1')],[button('上一页',f'audit:{rid}:{employee}:{page-1}') for _ in [0] if page>1]+[button('下一页',f'audit:{rid}:{employee}:{page+1}') for _ in [0] if len(rows)==6])
    elif command=='export':
     target=uid;thread=None;text='导出跟进表\n截至 '+time_text(p.now())
   else:return False
   payload=dict(text=text,markup=markup,chat=target,thread=thread,edit=edit,rich=rich,file=str(file) if file else None,export=command=='export',kind=kind,round=rid,uid=uid,ephemeral=ephem,callback=cb.id if cb else None,revision=p.round(rid)['revision'] if kind=='board' else None)
   p.queue(key,payload);p.db.run('INSERT INTO pg_updates VALUES (?,?)',(u.update_id,p.now()))
  if command=='export':return True # Durable background export must not block the single update dispatcher.
  await flush(p,ctx.bot,[key]+extra_keys)
  if command=='done':request_refresh(p,ctx.bot)
  return True
 except (ValueError,PermissionError,IndexError,KeyError) as e:
  text=str(e) if not isinstance(e,(IndexError,KeyError)) else '参数或页码不完整，请使用新版按钮'
  p.queue(key+':error',dict(text='无法完成\n'+text,chat=uid,thread=None));p.db.run('INSERT OR IGNORE INTO pg_updates VALUES (?,?)',(u.update_id,p.now()));await flush(p,ctx.bot,[key+':error']);return True
