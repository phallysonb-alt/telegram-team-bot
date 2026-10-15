import json
from .daily import METRICS,preview_daily,daily_view
from .checkin import Checkins

HELP='''使用帮助

开始使用
私聊点击“开始”→“申请开通”，等待管理员批准，无需员工编号。

我的工作
新版工作进度从群内持久查询入口反馈；顺序任务累计选择，自由任务独立勾选。
点击一次“提交反馈”，备注可选；本次可修改。旧工作台历史保留。

每日日报
发送“日报模板 [日期]”获取模板，填写后发送即记录，以👍或“已记录”为准。
四项：新增、维聊、有效、精聊。重新发送完整日报替换，不累加。
窗口23:00–次日00:00；提前、迟交或修订会标记。
空白与缺报不是零；四项独立计算，不与工作台互相计入。
管理员发送“查看日报 [日期] [页码]”查看明细和合计；旧英文命令兼容。
员工提交不主动发布团队表；每次查看在聊天底部回复，后续只同步最新查询表。

其他
资料目录 /materials，下载 /material_get 编号。正文问答停用。
管理话题静默，管理操作仅在管理员私聊。
正式调度关闭。'''

def dispatch(a,update,command,args,raw):
    uid=update.effective_user.id;msg=update.effective_message;chat=update.effective_chat
    source=f'{chat.id}:{msg.message_id}';today=a.c.now().date().isoformat();s=a.s
    a.presentation=None;a.presentation_audience='employee';a.presentation_viewer=None;a.presentation_filename=None
    a.edit_target=None;a.query_reply_to=None
    a.pending_card=None
    a.pending_workbench=None
    if command=='start' and chat.type=='private' and args and args[0] in ('work','daily'):
        a.db.set(f'private_started:{uid}',True)
        command,args=('work',[]) if args[0]=='work' else ('template',[])
    from .workbench_bot import dispatch as wb_dispatch
    result=wb_dispatch(a,update,command,args,raw)
    if result is not None:return result
    if command in ('start','help','menu'):
        if chat.type=='private':
            a.db.set(f'private_started:{uid}',True);a.db.set('wb_input:'+str(uid),None)
        text=HELP if command=='help' else '工作菜单\n'+today+'\n\n选择要查看的内容。'
        if command=='help' and chat.type=='private' and s.admin(uid):
            text+='\n\n管理员私聊\n菜单“员工管理”批准、拒绝或停用；“团队进度”看本轮反馈；“发布任务”登记阶段任务，“查询任务”查询今天。\n“导出日报表”获取日报历史记录.xlsx；“导出跟进表”获取工作进度历史.xlsx。旧导出触发词仍兼容。\n/leave、/correct、/export、/history 兼容入口保留。\n查询时间由管理员决定；不自动周期询问。/schedule 查看配置。'
        markup={'inline_keyboard':[[{'text':'我的工作','callback_data':'pg:menu'},{'text':'日报模板','callback_data':'template'}]]}
        if s.admin(uid):markup['inline_keyboard'].append([{'text':'查看日报','callback_data':'report_view:'+today+':1'}])
        if a.c.identity_mode=='telegram':
            markup['inline_keyboard'].append([{'text':'我的日报','callback_data':'my_daily:'+today}])
            if s.admin(uid):markup['inline_keyboard'].append([{'text':'导出日报表','callback_data':'daily_export'},{'text':'导出跟进表','callback_data':'pg:export'}])
        if s.admin(uid):markup['inline_keyboard'].append([{'text':'员工管理','callback_data':'people:list:1'}])
        if s.admin(uid):markup['inline_keyboard'].append([{'text':'查询任务','callback_data':'pg:query'},{'text':'发布任务','callback_data':'pg:new'}])
        if a.c.identity_mode=='telegram' and s.admin(uid) and not a.db.one('SELECT uid FROM employees WHERE uid=? AND approved=1 AND active=1',(uid,)):
            markup['inline_keyboard']=[row for row in markup['inline_keyboard'] if not any(b.get('callback_data','').startswith(('pg:menu','template','my_daily')) for b in row)]
        markup['inline_keyboard'].append([{'text':'使用帮助','callback_data':'help'}])
        if chat.type!='private':markup['inline_keyboard']=[row for row in markup['inline_keyboard'] if not any(b['callback_data']=='pg:new' for b in row)]
        return text,markup,None,None
    if command=='progress_raw':
        a.route(update,'progress')
        return '旧数字进度模板已停用，请从新版查询卡片“反馈进度”进入。',None,None,None
    if command=='my_daily':
        a.route(update,'report');day=args[0] if args else today;r=s.record(uid,day)
        if not r or not r['confirmed']:return '我的日报\n'+day+'\n尚未提交。',{'inline_keyboard':[[{'text':'日报模板','callback_data':'template:'+day}]]},None,uid
        from .presentation import daily_personal
        text,a.presentation=daily_personal(s,day,json.loads(r['payload']),'我的日报',r['timing'])
        a.presentation['blocks'][1]['text']+=' · '+s.display(uid)
        return text,{'inline_keyboard':[[{'text':'修改日报','callback_data':'template:'+day}]]},None,uid
    if command=='schedule':
        a.route(update,admin=True)
        days='、'.join(('周一','周二','周三','周四','周五','周六','周日')[i] for i in a.c.workdays)
        text=f"调度设置\n中国时间\n\n正常提交：{a.c.times['open']}–次日{a.c.times['deadline']}\n日报汇总：次日{a.c.times['summary']}\n工作日：{days}（未确认）\n当前状态："+('已启用' if a.scheduler.schedule_enabled() else '关闭')
        return text,None,None,None
    if command in ('task_new','task_update','task_edit','tasks','management_view','confirm','cancel','metric','definitions','public_note','progress_template'):
        return '旧任务、旧日报按钮和分阶段数字填报已停用；使用 /template 或 /checkin。',None,None,None
    if command=='template':
        a.route(update,'report');day=args[0] if args else today
        from .reports import valid_day
        valid_day(day,a.c.now().date());r=s.record(uid,day);p=json.loads(r['payload']) if r else {}
        text='日期：'+day+'\n'+'\n'.join(m+'：'+str(p[m] if p.get(m) is not None else '') for m in METRICS)
        from .rich import heading,paragraph
        a.presentation=dict(blocks=[heading('日报模板'),paragraph('员工：'+s.display(uid)),paragraph(text)])
        text+='\n备注：'+p.get('备注','')
        a.presentation=None
        return text,{'inline_keyboard':[[{'text':'查看日报' if s.admin(uid) else '我的日报','callback_data':('report_view:'+day+':1') if s.admin(uid) else ('my_daily:'+day)}]]},None,None
    if command=='report_raw':
        a.route(update,'report')
        if getattr(msg,'forward_origin',None):raise ValueError('请本人发送日报，不接受转发代交')
        # Minimal trusted sender envelope; no duplicate body log and no name-based binding.
        s.db.set('daily_origin:'+source,dict(update=update.update_id,uid=uid,name=update.effective_user.full_name,username=update.effective_user.username,chat=chat.id,message=msg.message_id,thread=msg.message_thread_id,at=msg.date.isoformat()))
        day,changed=s.submit_daily(uid,raw,source)
        from .daily_history import queue_feedback
        queue_feedback(s,update,day)
        return None,None,None,None
    if command in ('daily_confirm','daily_cancel'):
        return '旧确认按钮已停用，请直接发送完整日报。',None,None,None
    if command=='daily_export':
        a.route(update,admin=True)
        from .daily_history import export_path
        return '导出日报表',None,export_path(s),uid
    if command in ('report_view','report_page','report_plain','report_legacy'):
        s.require_admin(uid)
        if args and ':' in args[0]:args=args[0].split(':')
        if s.admin(uid) and chat.type!='private':
            if chat.id!=a.c.chat or msg.message_thread_id!=a.scheduler.api_thread('report'):raise PermissionError('查看日报请在每日报表话题或机器人私聊使用')
        else:a.route(update,'report',admin=s.admin(uid) and chat.type=='private')
        day=args[0] if args else today
        if command=='report_legacy':a.presentation,text=s.legacy_view(day)
        else:
            r=s.summary(day);page=int(args[1]) if len(args)>1 else 1
            from .daily_history import report_pages
            pages_data=report_pages(s,r)
            if not 1<=page<=len(pages_data):raise ValueError('页码超出范围')
            a.presentation=pages_data[page-1]['rich'];text=pages_data[page-1]['text']
        if command=='report_plain':a.presentation=None
        if command=='report_page':
            if not update.callback_query:raise ValueError('翻页请使用按钮')
            a.edit_target=msg.message_id
        if command in ('report_view','report_page') and chat.id==a.c.chat:
            pub=s.db.one('SELECT * FROM daily_publications WHERE day=?',(day,))
            current=command=='report_view' or bool(pub and pub['message_id']==msg.message_id)
            if current:
                s.db.run("UPDATE outbox SET state='cancelled' WHERE key LIKE ? AND state='pending'",('daily-board:'+day+':%',))
                saved=s.save_summary(day)
                if not pub:s.db.run("INSERT INTO daily_publications(day,version,chat,next_try,state) VALUES (?,?,?,?,'query_pending')",(day,0,chat.id,a.c.now().isoformat()))
                if command=='report_view':
                    s.db.set('daily_query_latest:'+day,update.update_id)
                    s.db.run("UPDATE daily_publications SET state='query_pending' WHERE day=?",(day,))
                s.db.set('daily_query_enabled:'+day,True);s.db.set('daily_query_page:'+day,page)
                key='update:'+str(update.update_id)+':0'
                s.db.set('daily_query_delivery:'+key,dict(key=key,day=day,version=saved['version'],fresh=command=='report_view',request=update.update_id,target=msg.message_id if command=='report_page' else None))
        elif command in ('report_view','report_page') and chat.type=='private':
            key='update:'+str(update.update_id)+':0'
            s.db.set('daily_private_delivery:'+key,dict(key=key,day=day,chat=chat.id))
        if command=='report_view' and not update.callback_query:a.query_reply_to=msg.message_id
        markup=None
        if command!='report_legacy':
            pages=len(pages_data);nav=[]
            if page>1:nav.append({'text':'上一页','callback_data':f'report_page:{day}:{page-1}'})
            if page<pages:nav.append({'text':'下一页','callback_data':f'report_page:{day}:{page+1}'})
            markup={'inline_keyboard':([[{'text':'日报模板','callback_data':'template:'+day},{'text':'查看日报','callback_data':'report_view:'+day+':'+str(page)}]]+([nav] if nav else []))}
        return text,markup,None,None
    if command in ('checkin','progress_view','progress_plain','ck'):
        a.route(update,'progress');ck=Checkins(s)
        if command=='ck':
            action,owner,task,day=args[0].split(':');owner=int(owner);task=int(task)
            if owner!=uid:raise PermissionError('只能操作自己的任务卡片')
            if day!=ck.day():raise ValueError('旧日期卡片失效，请重新 /checkin')
            card=a.db.one('SELECT * FROM check_cards WHERE uid=? AND task=? AND day=?',(uid,task,day))
            if not card or card['chat']!=chat.id or card['message_id']!=msg.message_id:raise PermissionError('不是当前本人卡片')
            if not action.startswith('view'):
                callback_key='callback:'+update.callback_query.id if update.callback_query else 'update:'+str(update.update_id)
                ck.change(uid,task,day,action,callback_key)
            text,markup=ck.card(uid,task,day)
            if action.startswith('view'):
                page=int(action[4:] or '1');count=a.db.one('SELECT COUNT(*) n FROM check_events WHERE uid=? AND task=? AND day=?',(uid,task,day))['n'];pages=max(1,(count+19)//20)
                if not 1<=page<=pages:raise ValueError('今日记录页码超出范围')
                events=a.db.all('SELECT server_time,delta,undo_of FROM check_events WHERE uid=? AND task=? AND day=? ORDER BY id DESC LIMIT 20 OFFSET ?',(uid,task,day,(page-1)*20))
                text+=f'\n今日记录 第{page}/{pages}页（最近优先）：\n'+json.dumps(events,ensure_ascii=False)
                nav=[]
                for label,p in [('上一页',page-1),('下一页',page+1)]:
                    if 1<=p<=pages:nav.append(dict(text=label,callback_data=f'ck:view{p}:{uid}:{task}:{day}'))
                if nav:markup['inline_keyboard'].append(nav)
            a.edit_target=msg.message_id
            return text,markup,None,None
        task=int(args[0]) if command=='checkin' and args else ck.tasks()[0]['id'];day=args[0] if command.startswith('progress_') and args else ck.day()
        text,markup=ck.card(uid,task,day)
        old=a.db.one('SELECT * FROM check_cards WHERE uid=? AND task=? AND day=?',(uid,task,day))
        target=uid # Each employee has a private card; keeps the group quiet.
        if not a.db.setting(f'private_started:{uid}',False):raise ValueError('请先私聊 /start，任务卡片通过私聊返回')
        if old and old['chat']==target and old['message_id']:a.edit_target=old['message_id']
        a.pending_card=(uid,task,day,target)
        return text,markup,None,target
    if command=='check_config':
        a.route(update,admin=True)
        return '旧单任务配置入口已停用。请使用新版“发布任务／查询任务”；旧历史未转换。',None,None,None
    if command in ('check_admin','check_detail'):
        a.route(update,admin=True);ck=Checkins(s)
        if command=='check_config':
            if not args:return '\n'.join(f"#{t['id']}【模拟】{t['name']} 目标{t['target']} 检查{t['checks']} 适用{t['uids'] or '全部'}" for t in ck.tasks()),None,None,None
            task=int(args[0]);uids=[] if args[3]=='全部' else [int(x) for x in args[3].split(',')]
            ck.configure(uid,task,args[1],int(args[2]),uids,args[4].split(','),' '.join(args[5:]))
            return '模拟任务配置已保存并审计；未开启提醒。',None,None,None
        day=args[0] if args else today;task=int(args[1]) if len(args)>1 else ck.tasks()[0]['id']
        from .reports import valid_day
        valid_day(day,a.c.now().date())
        detail=int(args[2]) if command=='check_detail' else None;page=int(args[2]) if command=='check_admin' and len(args)>2 else 1
        return ck.inspect(uid,day,task,page,detail),None,None,None
    return None
