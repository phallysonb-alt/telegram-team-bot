"""One employee identity registry for daily reports and work progress."""
from .db import stamp

def people(api):
    s=api.p.s;rows=[]
    for employee in s.db.all("SELECT * FROM employees WHERE role='employee' ORDER BY uid"):
        uid=employee['uid']
        if s.admin(uid) or uid==s.db.setting('acceptance_demo_owner'):continue
        profile=s.db.one('SELECT * FROM tg_profiles WHERE uid=?',(uid,))
        pending=s.db.one('SELECT * FROM onboarding WHERE uid=?',(uid,))
        member=s.db.one('SELECT * FROM membership WHERE uid=? AND chat=?',(uid,s.c.chat))
        opened=bool(employee['approved'] and employee['active'] and (not pending or pending['state']=='approved') and (not member or member['status'] not in ('left','kicked')))
        state='已开通' if opened else '已停用' if not employee['active'] else '待入群及审批' if pending and pending['state']=='pending_join' else '已拒绝' if pending and pending['state']=='rejected' else '待审批'
        name,username=api.identity(uid)
        rows.append(dict(target=uid,name=name,username=username,state=state,opened=opened,pending=bool(pending and pending['state'] in ('pending','pending_join')),reason=pending['reason'] if pending else ''))
    return dict(count=sum(r['opened'] for r in rows),rows=rows,pending=sum(r['pending'] for r in rows))

async def invite(api,actor):
    s=api.p.s;s.require_admin(actor)
    existing=s.db.setting('business_join_link')
    if existing:return dict(link=existing,message='加入同一个工作群后申请开通，无需分别加入话题。')
    try:
        me=await api.bot.get_me();member=await api.bot.get_chat_member(s.c.chat,me.id)
        if not getattr(member,'can_invite_users',False):raise PermissionError('机器人缺少工作群“邀请用户”权限，暂不能生成申请加入链接。')
        created=await api.bot.create_chat_invite_link(s.c.chat,name='业务员申请加入',creates_join_request=True)
    except PermissionError:raise
    except Exception:raise ValueError('加入链接创建失败，请重试；未开通任何账号。') from None
    s.db.set('business_join_link',created.invite_link)
    return dict(link=created.invite_link,message='员工本人申请加入；管理员在待审批中批准入群及统一开通。')

async def approve(api,actor,target):
    s=api.p.s;s.require_admin(actor)
    if type(target) is not int or target==s.c.owner:raise ValueError('申请身份无效')
    employee=s.db.one("SELECT * FROM employees WHERE uid=? AND role='employee'",(target,))
    request=s.db.one('SELECT * FROM onboarding WHERE uid=?',(target,))
    if not employee or not request:raise ValueError('没有此账号的真实申请，请员工本人申请。')
    try:
        member=await api.bot.get_chat_member(s.c.chat,target)
        joined=str(member.status) in ('member','administrator','creator') or str(member.status)=='restricted' and bool(getattr(member,'is_member',False))
        if not joined:
            if request['state']!='pending_join':raise ValueError('尚未加入工作群，保持待处理；请先申请入群。')
            if not s.db.setting('onboarding_join_approved:'+str(target),False):
                await api.bot.approve_chat_join_request(s.c.chat,target)
                s.db.set('onboarding_join_approved:'+str(target),True)
            member=await api.bot.get_chat_member(s.c.chat,target)
            joined=str(member.status) in ('member','administrator','creator') or str(member.status)=='restricted' and bool(getattr(member,'is_member',False))
            if not joined:raise ValueError('入群已批准，群成员确认仍待处理；请重试，尚未开通。')
        if member.user.id!=target or member.user.is_bot:raise ValueError('申请数字身份不一致或属于机器人。')
        s.db.run('INSERT OR REPLACE INTO membership VALUES (?,?,?,?)',(target,s.c.chat,str(member.status),stamp()))
        with s.db.transaction():
            # Another authorized approval may have finished while Telegram was awaited.
            employee=s.db.one('SELECT * FROM employees WHERE uid=?',(target,))
            request=s.db.one('SELECT * FROM onboarding WHERE uid=?',(target,))
            if employee['approved'] and employee['active'] and request['state']=='approved':
                return dict(ok=True,message='已开通日报和工作进度',people=people(api))
            profile=s.db.one('SELECT name,username FROM tg_profiles WHERE uid=?',(target,))
            from telegram import User
            identity=User(target,profile['name'],False,username=profile['username'] or None) if profile else member.user
            s.open_identity(actor,identity,'统一业务员窗口批准：日报与工作进度共用身份')
        return dict(ok=True,message='已开通日报和工作进度',people=people(api))
    except (ValueError,PermissionError):raise
    except Exception:
        s.db.run('UPDATE onboarding SET reason=?,updated=? WHERE uid=?',('入群或开通接口暂时失败，等待重试',stamp(),target))
        raise ValueError('入群或开通未完成，保持待处理，请重试。') from None

async def reject(api,actor,target):
    s=api.p.s;s.require_admin(actor)
    if type(target) is not int:raise ValueError('申请身份无效')
    row=s.db.one("SELECT uid FROM employees WHERE uid=? AND role='employee'",(target,))
    if not row or target==s.c.owner:raise ValueError('申请不存在')
    request=s.db.one('SELECT state FROM onboarding WHERE uid=?',(target,))
    if request and request['state']=='pending_join':
        try:
            member=await api.bot.get_chat_member(s.c.chat,target)
            if member.user.id!=target or member.user.is_bot:raise ValueError('申请数字身份不一致')
            joined=str(member.status) in ('member','administrator','creator') or str(member.status)=='restricted' and bool(getattr(member,'is_member',False))
            if not joined:await api.bot.decline_chat_join_request(s.c.chat,target)
        except Exception:raise ValueError('拒绝入群尚未完成，仍保留待处理状态，请重试。') from None
    s.change_employee(actor,target,'统一业务员窗口拒绝申请',approved=0)
    s.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',(target,'rejected','管理员拒绝',stamp()))
    return dict(ok=True,message='已拒绝，历史保留',people=people(api))
