"""Async identity/membership gates used by the single polling handler."""
from .db import stamp
from .presentation import human_time

REQUEST_ID=8410404

def b(text,data):return dict(text=text,callback_data=data)

def people_view(a,uid,page=1):
    a.s.require_admin(uid)
    rows=a.db.all('SELECT * FROM employees ORDER BY approved,active DESC,uid');pages=max(1,(len(rows)+7)//8)
    if not 1<=page<=pages:raise ValueError('页码超出范围')
    text='员工管理\n第'+str(page)+'/'+str(pages)+'页\n\n';buttons=[]
    for r in rows[(page-1)*8:page*8]:
        name=a.s.display(r['uid']);state='停用' if not r['active'] else '已批准' if r['approved'] else '待开通'
        text+=name+' · '+state+'\n';buttons.append([b(a.s.display(r['uid'],True),f'people:view:{r["uid"]}')])
    nav=[]
    if page>1:nav.append(b('上一页',f'people:list:{page-1}'))
    if page<pages:nav.append(b('下一页',f'people:list:{page+1}'))
    if nav:buttons.append(nav)
    buttons.append([b('集中核实员工','people:select')]);buttons.append([b('返回菜单','menu')])
    return text or '暂无员工',dict(inline_keyboard=buttons)

async def preflight(a,u,context):
    """Return True only when intercepted. No direct fake business row writes."""
    if a.c.identity_mode!='telegram':return False
    # Retired internal group is completely silent, including callback answers.
    if u.effective_chat and u.effective_chat.id==a.c.internal_chat:return True
    join=getattr(u,'chat_join_request',None)
    if join:
        if join.chat.id!=a.c.chat or join.from_user.is_bot:return True
        if a.db.one('SELECT id FROM updates WHERE id=?',(u.update_id,)):return True
        user=join.from_user;uid=user.id;a.s.profile(user)
        old=a.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
        if a.s.admin(uid):return True
        # Group owners approve admission in Telegram; no second Mini App approval.
        a.db.audit('入群申请',uid,uid,'本人申请加入工作群',None,dict(uid=uid,state='pending_join'))
        a.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)',(u.update_id,stamp()))
        return True
    member_update=u.chat_member or u.my_chat_member
    if member_update:
        if member_update.chat.id==a.c.chat:
            if a.db.one('SELECT id FROM updates WHERE id=?',(u.update_id,)):return True
            member=member_update.new_chat_member;uid=member.user.id
            from .member_registry import reconcile,defer
            if not member.user.is_bot:
                try:await reconcile(a.s,context.bot,uid,member.user)
                except PermissionError:defer(a.s,uid)
            a.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)',(u.update_id,stamp()))
        return True
    if not u.effective_user or not u.effective_message:return True
    uid=u.effective_user.id;chat=u.effective_chat;msg=u.effective_message
    if chat.type!='private' and chat.id!=a.c.chat:return True
    if a.db.one('SELECT id FROM updates WHERE id=?',(u.update_id,)):return True
    a.s.profile(u.effective_user)
    raw=msg.text or '';data=u.callback_query.data if u.callback_query else ''
    if data and a.c.test_run:
        prefix=f'test:{a.c.test_run}:'
        if not data.startswith(prefix):return False # Adapter will explicitly reject expired/other scope.
        data=data[len(prefix):]
    command=data.split(':')[0] if data else raw.split()[0][1:].split('@')[0].lower() if raw.startswith('/') else ''
    private=chat.type=='private' and chat.id==uid
    key='update:'+str(u.update_id)
    text=markup=None
    try:
        shared=getattr(msg,'users_shared',None)
        if shared:
            if not private or not a.s.admin(uid) or shared.request_id!=REQUEST_ID:raise PermissionError('员工选择请求已失效或无权限')
            lines=[]
            for user in shared.users:
                member=await a.s.verify_member(context.bot,user.user_id)
                if member.user.is_bot or str(member.status) not in ('member','restricted') or str(member.status)=='restricted' and not member.is_member:continue
                a.s.open_identity(uid,member.user)
                try:
                    old=await context.bot.get_chat_member(a.c.internal_chat,user.user_id)
                    status=str(old.status)
                except Exception:status='未确认'
                a.db.set('internal_membership:'+str(user.user_id),status)
                lines.append(a.s.display(user.user_id)+' · 已批准 · PH成员：'+status)
            text='集中开通结果\n\n'+('\n'.join(lines) or '没有符合条件的员工');markup=dict(inline_keyboard=[[b('员工管理','people:list:1')]])
        elif command=='apply':
            if not private:raise PermissionError('申请开通请在本人私聊')
            from .member_registry import reconcile
            await reconcile(a.s,context.bot,uid,u.effective_user)
            a.s.employee(uid);text='群成员已核实，已开通日报和工作进度。';markup=dict(inline_keyboard=[[b('返回菜单','menu')]])
        elif command=='member_retry':
            if not private:raise PermissionError('请在本人私聊重试')
            await a.s.verify_member(context.bot,uid);text='群成员校验成功\n请返回菜单继续。';markup=dict(inline_keyboard=[[b('返回菜单','menu')]])
        elif command=='people' or command=='employees':
            if not private:raise PermissionError('员工管理仅管理员私聊')
            a.s.require_admin(uid);parts=data.split(':') if data else ['people','list',raw.split()[1] if len(raw.split())>1 else '1']
            action=parts[1]
            if action=='list':text,markup=people_view(a,uid,int(parts[2]))
            elif action=='select':
                from telegram import KeyboardButton,KeyboardButtonRequestUsers,ReplyKeyboardMarkup
                # Telegram returns numeric identities; display-name selection is never identity matching.
                request=KeyboardButtonRequestUsers(request_id=REQUEST_ID,user_is_bot=False,max_quantity=4,request_name=True,request_username=True)
                keyboard=ReplyKeyboardMarkup([[KeyboardButton('选择工作群的4名员工',request_users=request)]],resize_keyboard=True,one_time_keyboard=True)
                label=a.c.trial_label+'\n' if a.c.test_run else ''
                await context.bot.send_message(chat_id=uid,text=label+'选择工作群员工\n请选择新工作群中的4名普通员工。机器人会逐个通过接口核实成员身份，不会按名字猜测。',reply_markup=keyboard)
                text='已打开员工选择入口。'
            else:
                target=int(parts[2]);r=a.db.one('SELECT * FROM employees WHERE uid=?',(target,))
                if not r:raise ValueError('员工申请不存在')
                if action=='approve':
                    member=await a.s.verify_member(context.bot,target)
                    if member.user.is_bot or str(member.status) not in ('member','restricted') or str(member.status)=='restricted' and not member.is_member:raise PermissionError('仅普通有效群成员可开通业务员')
                    a.s.open_identity(uid,member.user,'管理员按钮批准')
                    text='已批准\n员工：'+a.s.display(target)
                    if a.db.setting('private_started:'+str(target),False):a.scheduler.reply(key+':approved',target,None,'开通成功\n请点击“我的工作”或“日报模板”。',dict(inline_keyboard=[[b('我的工作','pg:menu'),b('日报模板','template')]]))
                elif action in ('reject','disable'):
                    if target==a.c.owner:raise PermissionError('所有者权限不能由员工管理入口停用')
                    a.s.change_employee(uid,target,'管理员'+('拒绝申请' if action=='reject' else '停用员工'),**({'approved':0} if action=='reject' else {'active':0}))
                    a.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',(target,'rejected' if action=='reject' else 'disabled','管理员操作',stamp()));text=('已拒绝' if action=='reject' else '已停用')+'\n'+a.s.display(target)+'\n历史保留。'
                else:
                    text='员工账户\n'+a.s.display(target)+'\n状态：'+('停用' if not r['active'] else '已批准' if r['approved'] else '待开通')
                markup=dict(inline_keyboard=[[b('批准／恢复',f'people:approve:{target}'),b('拒绝申请',f'people:reject:{target}')],[b('停用',f'people:disable:{target}')],[b('返回员工管理','people:list:1')]])
        elif command in ('start','menu','register') and private and not a.s.admin(uid):
            a.db.set('private_started:'+str(uid),True)
            from .member_registry import reconcile
            await reconcile(a.s,context.bot,uid,u.effective_user)
            e=a.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
            if not e or not e['approved'] or not e['active']:
                text='欢迎使用\n'+a.s.display(uid)+'\n\n'+('账号已停用，请联系管理员恢复。' if e and not e['active'] else '请申请开通，无需填写员工编号。')
                markup=dict(inline_keyboard=[[b('申请开通','apply')],[b('重试校验','member_retry')]]) if not e or e['active'] else dict(inline_keyboard=[[b('重试校验','member_retry')]])
        # Verify every approved participant operation; failures never become "left".
        if text is None:
            e=a.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
            if a.c.membership_required and command not in ('whereami','help'):
                if a.s.admin(uid):await a.s.verify_member(context.bot,uid)
                else:
                    from .member_registry import reconcile
                    await reconcile(a.s,context.bot,uid,u.effective_user)
            return False
        if u.callback_query:await u.callback_query.answer()
        a.scheduler.reply(key,uid,None,text,markup)
        a.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)',(u.update_id,stamp()))
        return True
    except (PermissionError,ValueError) as e:
        if u.callback_query:await u.callback_query.answer()
        from .daily import daily_intent
        if a.c.report_version>=2 and daily_intent(raw):
            a.scheduler.reply(key+':identity-error',chat.id,msg.message_thread_id,str(e))
        else:a.scheduler.reply(key+':identity-error',uid,None,'无法完成\n'+str(e),dict(inline_keyboard=[[b('重试校验','member_retry')]]))
        a.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)',(u.update_id,stamp()))
        return True
