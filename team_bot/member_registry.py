"""Single numeric identity registry, reconciled only with live group membership."""
from .db import stamp
import asyncio,time

def defer(s,uid):
    pending=s.db.setting('member_reconcile_pending',{})
    pending[str(uid)]=dict(at=time.time()+5)
    s.db.set('member_reconcile_pending',pending)

async def retry_pending(s,bot):
    pending=s.db.setting('member_reconcile_pending',{})
    for key,entry in list(pending.items())[:20]:
        if entry['at']>time.time():continue
        try:await reconcile(s,bot,int(key))
        except PermissionError:pending[key]=dict(at=time.time()+30)
        else:pending.pop(key,None)
    s.db.set('member_reconcile_pending',pending)

async def watch(s,bot):
    while True:
        try:await retry_pending(s,bot)
        except asyncio.CancelledError:raise
        except Exception:pass
        await asyncio.sleep(5)

async def reconcile(s,bot,uid,user=None):
    try: member=await bot.get_chat_member(s.c.chat,uid)
    except Exception: raise PermissionError('群成员核验暂时失败，请重试；尚未开通新权限') from None
    if member.user.id!=uid:raise PermissionError('群成员数字身份不一致')
    return apply(s,member,user)

def apply(s,member,user=None):
    uid=member.user.id;status=str(member.status)
    s.db.run('INSERT OR REPLACE INTO membership VALUES (?,?,?,?)',(uid,s.c.chat,status,stamp()))
    if s.admin(uid):return member # Explicit business admin identity is never changed by group status.
    ordinary=status=='member' or status=='restricted' and bool(getattr(member,'is_member',False))
    ordinary=ordinary and not member.user.is_bot
    old=s.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
    if old and old['role']=='admin':raise PermissionError('管理员账号已停用，请联系所有者恢复')
    reason=s.db.one('SELECT * FROM onboarding WHERE uid=?',(uid,))
    if not ordinary:
        if old and old['role']=='employee' and old['active']:
            s.change_employee(s.c.owner,uid,'工作群成员资格变更：'+status,active=0)
            s.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',(uid,'group_inactive','群成员资格：'+status,stamp()))
        return member
    # An explicit business disable stays effective; membership alone cannot undo it.
    if old and not old['active'] and reason and reason['state'] not in ('group_inactive',) and reason['reason']!='已退出工作群':
        raise PermissionError('账号已由管理员停用，请联系管理员')
    with s.db.transaction():
        if not old or not old['approved'] or not old['active']:
            s.open_identity(s.c.owner,user or member.user,'官方群成员核验后自动登记／恢复')
        elif user:s.profile(user)
    return member
