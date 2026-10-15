"""Telegram numeric identity, display profiles and explicit onboarding. No AI."""
import re
from .db import stamp

def tidy_name(value):
    value=''.join(c for c in str(value) if ord(c)>=32 and ord(c)!=127)
    return re.sub(r'\s+',' ',value).strip()[:160] or '未提供名称'

class IdentityMixin:
    def profile(self,user):
        uid=int(user.id)
        name=tidy_name(getattr(user,'full_name',None) or ' '.join(filter(None,[getattr(user,'first_name',''),getattr(user,'last_name','')])))
        username=getattr(user,'username',None) or ''
        old=self.db.one('SELECT * FROM tg_profiles WHERE uid=?',(uid,))
        changed=not old or (old['name'],old['username'])!=(name,username)
        if changed:
            self.db.run('INSERT INTO tg_name_history(uid,name,username,at) VALUES (?,?,?,?)',(uid,name,username,stamp()))
        self.db.run('INSERT OR REPLACE INTO tg_profiles VALUES (?,?,?,?)',(uid,name,username,stamp()))
        # Compatibility code remains unchanged; names are never identity keys.
        self.db.run('UPDATE employees SET name=? WHERE uid=?',(name,uid))
        if changed and self.db.one('SELECT uid FROM employees WHERE uid=?',(uid,)):
            if self.c.report_version>=2:
                from .daily_history import names_changed
                names_changed(self)
            day=self.c.business_date().isoformat()
            self.mark_dirty(day)
            from .workbench import Workbench
            Workbench(self).dirty(day)
        return uid

    def display(self,uid,short=False):
        e=self.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
        if self.c.identity_mode!='telegram':return e['code'] if e else str(uid)
        p=self.db.one('SELECT * FROM tg_profiles WHERE uid=?',(uid,))
        name=p['name'] if p else tidy_name(e['name']) if e else '身份未确认'
        collision=self.db.one('SELECT COUNT(*) n FROM tg_profiles WHERE name=?',(name,))['n']>1
        suffix=(' @'+p['username'] if p and p['username'] else ' · '+str(uid)[-6:]) if collision else ''
        if short:name=name[:18]+('…' if len(name)>18 else '')
        demo='（管理员演示）' if self.c.run_mode=='trial' and self.db.setting('acceptance_demo_owner')==uid else ''
        return name+suffix+demo

    def named_employee(self,uid):
        e=self.employee(uid).copy();e['code']=self.display(uid);return e

    def open_identity(self,actor,user,reason='管理员核实群成员后集中开通'):
        self.require_admin(actor);uid=self.profile(user)
        if getattr(user,'is_bot',False):raise ValueError('机器人不能开通员工权限')
        old=self.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
        if not old:
            self.db.run('INSERT INTO employees(uid,code,name,approved,active,required,role) VALUES (?,?,?,0,1,1,?)',(uid,'TG'+str(uid),self.display(uid),'employee'))
            self.change_employee(actor,uid,reason,approved=1,active=1)
        else:self.change_employee(actor,uid,reason,approved=1,active=1) # Never grants business admin from group status.
        self.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',(uid,'approved',reason,stamp()))
        return uid

    def apply_identity(self,user):
        uid=self.profile(user);old=self.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
        request=self.db.one('SELECT * FROM onboarding WHERE uid=?',(uid,))
        if old and old['approved'] and old['active']:return False
        if request and request['state']=='pending':return False
        if old and not old['active']:raise PermissionError('账号已停用，请联系管理员；不能通过重新申请自动恢复')
        if not old:self.db.run('INSERT INTO employees(uid,code,name) VALUES (?,?,?)',(uid,'TG'+str(uid),self.display(uid)))
        self.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',(uid,'pending','员工申请开通',stamp()))
        self.db.audit('开通申请',uid,uid,'本人申请',None,dict(uid=uid,state='pending'))
        return True

    def export_employees(self,actor):
        if self.c.identity_mode!='telegram':return super().export_employees(actor)
        self.require_admin(actor)
        import csv,io
        from .service import safe_cell
        out=io.StringIO();w=csv.writer(out);w.writerow(['员工','Telegram用户ID','在职','需要日报','批准状态'])
        for r in self.db.all('SELECT * FROM employees ORDER BY uid'):
            w.writerow([safe_cell(self.display(r['uid'])),r['uid'],r['active'],r['required'],r['approved']])
        target=self.c.data/'exports'/'employees.csv';target.write_text(out.getvalue(),encoding='utf-8-sig');return target

    async def verify_member(self,bot,uid):
        import time
        started=time.perf_counter()
        try:member=await bot.get_chat_member(self.c.chat,uid)
        except Exception:raise PermissionError('群成员校验暂时失败，请稍后点击“重试校验”；不会保存新业务操作') from None
        self.db.set('membership_timing:'+str(uid),dict(seconds=round(time.perf_counter()-started,4),at=stamp()))
        if member.user.id!=uid:raise PermissionError('群成员身份校验不一致')
        # A fresh message's sender profile is authoritative for a rename;
        # getChatMember may return an older Telegram cached User object.
        if not self.db.one('SELECT uid FROM tg_profiles WHERE uid=?',(uid,)):
            self.profile(member.user)
        status=str(member.status);ok=status in ('member','administrator','creator') or status=='restricted' and bool(getattr(member,'is_member',False))
        self.db.run('INSERT OR REPLACE INTO membership VALUES (?,?,?,?)',(uid,self.c.chat,status,stamp()))
        old=self.db.one('SELECT * FROM employees WHERE uid=?',(uid,))
        if not ok:
            if old and old['active']:
                self.change_employee(self.c.owner,uid,'接口确认已退出工作群',active=0)
                self.db.run('INSERT OR REPLACE INTO onboarding VALUES (?,?,?,?)',(uid,'disabled','已退出工作群',stamp()))
            raise PermissionError('你已不在工作群，新增业务操作已停用；历史保留')
        return member
