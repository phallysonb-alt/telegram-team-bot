"""Version 2 daily reports. Never converts the legacy phase metrics."""
import csv
import io
import json
import zipfile
import re
from datetime import datetime,timedelta
from .reports import lines,number,valid_day
from .db import dumps,stamp

METRICS = ('新增','维聊','有效','精聊')

def parse_daily(raw,today):
    parts=raw.split('\n')
    while parts and not parts[0].strip():parts.pop(0)
    if parts and re.fullmatch(r'\s*\d{4}-\d{2}-\d{2}\s*',parts[0]):
        parts[0]='日期：'+parts[0].strip()
    # Notes start at their own field; all subsequent lines are literal text.
    note='';numeric=[];in_note=False
    for line in parts:
        if not in_note and re.match(r'^\s*备注\s*[：:]',line):
            note=re.split(r'[：:]',line,maxsplit=1)[1];in_note=True
        elif in_note:note+='\n'+line
        else:numeric.append(line)
    data=lines('\n'.join(numeric))
    if set(data)-set(('日期',)+METRICS):raise ValueError('请只填写日期、四项数字和可选备注')
    day=valid_day(data.get('日期'),today)
    missing=[m for m in METRICS if m not in data or not data[m].strip()]
    if missing:raise ValueError('请填写四项非负整数，0有效；尚未填写：'+'、'.join(missing))
    payload={}
    for m in METRICS:
        if not re.fullmatch(r'[0-9]+',data[m]):raise ValueError(m+'需填写非负整数，不能为负数、小数或文字')
        payload[m]=int(data[m])
    payload['备注']=note
    return day,payload

def daily_intent(raw):
    """Conservative structured-report detection; ordinary narrative is not a report."""
    parts=[x.strip() for x in raw.splitlines() if x.strip()]
    fields=sum(bool(re.match(r'^(新增|维聊|有效|精聊)\s*[：:]',x)) for x in parts)
    date=bool(parts and re.match(r'^(?:\d{4}-\d{2}-\d{2}|日期\s*[：:])',parts[0]))
    return fields>=2 or (date and fields>=1)

def preview_daily(day,payload):
    return '日期：'+day+'\n'+'\n'.join(m+'：'+str(payload[m] if payload[m] is not None else '待填') for m in METRICS)

def daily_view(s,r,page=1):
    from .daily_history import report_pages
    pages=report_pages(s,r)
    if not 1<=page<=len(pages):raise ValueError('页码超出范围')
    return pages[page-1]['rich']


class DailyMixin:
    def mark_dirty(self,day):
        if self.c.report_version<2:return super().mark_dirty(day)
        self.dirty_daily(day)

    def daily_window(self,day):
        d=datetime.fromisoformat(day).date()
        return self.c.event_time(d,'open'),self.c.event_time(d,'deadline')

    def record(self,uid,day):
        if self.c.report_version<2:return super().record(uid,day)
        return self.db.one('SELECT * FROM daily_records WHERE uid=? AND day=?',(uid,day))

    def draft(self,uid,raw,source):
        if self.c.report_version<2:return super().draft(uid,raw,source)
        self.employee(uid)
        day,payload=parse_daily(raw,self.c.now().date())
        with self.db.transaction():
            old=self.record(uid,day)
            pending=self.db.one("SELECT * FROM daily_drafts WHERE uid=? AND day=? AND state='draft' ORDER BY id DESC LIMIT 1",(uid,day))
            if pending and json.loads(pending['payload'])==payload and pending['base_version']==(old['version'] if old else 0):
                return pending['id'],day,payload
            self.db.run("UPDATE daily_drafts SET state='superseded' WHERE uid=? AND day=? AND state='draft'",(uid,day))
            cur=self.db.run('INSERT INTO daily_drafts(uid,day,payload,raw,source,base_version,created) VALUES (?,?,?,?,?,?,?)',
                (uid,day,dumps(payload),raw,source,old['version'] if old else 0,stamp()))
        return cur.lastrowid,day,payload

    def save_daily(self,uid,day,payload,actor,raw,source,reason,now=None):
        now=(now or self.c.now()).astimezone(self.c.zone)
        start,end=self.daily_window(day)
        old=self.record(uid,day)
        timing='截止后修订' if old and now>=end else '提前提交' if now<start else '按时提交' if now<end else '迟交'
        version=(old['version'] if old else 0)+1
        row=dict(uid=uid,day=day,payload=dumps(payload),confirmed=1,version=version,timing=timing,confirmed_at=now.isoformat(),raw=raw,source=source)
        self.db.run('INSERT OR REPLACE INTO daily_records VALUES (:uid,:day,:payload,:confirmed,:version,:timing,:confirmed_at,:raw,:source)',row)
        self.db.audit('新版日报',f'{uid}:{day}',actor,reason,old,row)
        self.dirty_daily(day)
        return row

    def dirty_daily(self,day):
        from .daily_history import dirty
        dirty(self,day)

    def confirm(self,uid,draft_id,now=None):
        if self.c.report_version<2:return super().confirm(uid,draft_id,now)
        self.employee(uid)
        with self.db.transaction():
            draft=self.db.one('SELECT * FROM daily_drafts WHERE id=? AND uid=?',(draft_id,uid))
            if not draft:raise PermissionError('无法确认他人的日报')
            if draft['state']=='confirmed':return '此日报已经提交，未重复入账'
            if draft['state']!='draft':raise ValueError('预览已失效，请重新填写')
            parse_daily(draft['raw'],self.c.now().date())
            old=self.record(uid,draft['day'])
            if old and old['confirmed'] and json.loads(old['payload'])==json.loads(draft['payload']):
                self.db.run("UPDATE daily_drafts SET state='confirmed' WHERE id=?",(draft_id,))
                return '此日报已经提交，未重复入账'
            if (old['version'] if old else 0)!=draft['base_version']:raise ValueError('数据已变化，请重新预览')
            row=self.save_daily(uid,draft['day'],json.loads(draft['payload']),uid,draft['raw'],draft['source'],'本人确认每日四项日报',now)
            self.db.run("UPDATE daily_drafts SET state='confirmed' WHERE id=?",(draft_id,))
        return f"提交回执：{row['day']} · 版本{row['version']} · {row['timing']}"

    def progress(self,*args,**kwargs):
        if self.c.report_version<2:return super().progress(*args,**kwargs)
        raise ValueError('上下半场数字进度已停用，请 /checkin 使用模拟任务打卡')

    def revoke(self,actor,uid,day,reason):
        if self.c.report_version<2:return super().revoke(actor,uid,day,reason)
        if actor!=uid:self.require_admin(actor)
        valid_day(day,self.c.now().date())
        with self.db.transaction():
            old=self.record(uid,day)
            if not old:raise ValueError('没有新版日报')
            self.db.run('UPDATE daily_records SET confirmed=0,version=version+1 WHERE uid=? AND day=?',(uid,day))
            self.db.audit('新版日报',f'{uid}:{day}',actor,reason,old,self.record(uid,day))
            self.dirty_daily(day)

    def correct(self,actor,uid,day,raw,reason):
        if self.c.report_version<2:return super().correct(actor,uid,day,raw,reason)
        self.require_admin(actor);self.employee(uid)
        parsed,payload=parse_daily(raw,self.c.now().date())
        if parsed!=day:raise ValueError('更正日期不一致')
        with self.db.transaction():return self.save_daily(uid,day,payload,actor,raw,'管理员更正',reason)['version']

    def missing(self,day,phase=None):
        if self.c.report_version<2:return super().missing(day,phase)
        return self.summary(day)['missing_codes']

    def summary(self,day,now=None):
        if self.c.report_version<2:return super().summary(day,now)
        valid_day(day,(now or self.c.now()).date())
        expected,leaves=self.roster(day)
        rows=[];missing=[];totals={m:0 for m in METRICS};unfilled={m:0 for m in METRICS};submitted=0
        for e in self.db.all('SELECT uid,code FROM employees ORDER BY code'):
            if e['uid'] not in expected:continue
            record=self.record(e['uid'],day)
            effective=record and record['confirmed']
            payload=json.loads(record['payload']) if effective else {m:None for m in METRICS}
            if effective:submitted+=1
            else:missing.append(self.display(e['uid']))
            for m in METRICS:
                if payload[m] is None:unfilled[m]+=1
                else:totals[m]+=payload[m]
            from .daily_history import daily_name
            rows.append(dict(code=daily_name(self,e['uid']),uid=e['uid'],payload=payload,timing=record['timing'] if effective else '未提交'))
        return dict(schema=2,day=day,expected=len(expected),submitted=submitted,missing_reports=len(missing),missing_codes=missing,
            leave=len(leaves),rows=rows,totals=totals,missing_fields=unfilled,roster_warning=self.db.setting('roster_warning:'+day,''),created=(now or self.c.now()).isoformat())

    def render_summary(self,r):
        if r.get('schema')!=2:return super().render_summary(r)
        from .daily_history import report_pages
        return '\n\n'.join(x['text'] for x in report_pages(self,r))

    def submit_daily(self,uid,raw,source,now=None):
        self.employee(uid)
        day,payload=parse_daily(raw,self.c.now().date())
        with self.db.transaction():
            old=self.record(uid,day)
            if old and old['confirmed']:
                prior=json.loads(old['payload']);prior.setdefault('备注','')
                if prior==payload:return day,False
            self.save_daily(uid,day,payload,uid,raw,source,'本人发送每日四项日报',now)
        return day,True

    def save_summary(self,day):
        if self.c.report_version<2:return super().save_summary(day)
        r=self.summary(day)
        version=self.db.one('SELECT COALESCE(MAX(version),0)+1 v FROM daily_summaries WHERE day=?',(day,))['v']
        r['version']=version
        self.db.run('INSERT INTO daily_summaries VALUES (?,?,?,?)',(day,version,dumps(r),r['created']))
        return r

    def legacy_view(self,day):
        from .reports import NUMERIC
        from .rich import heading,paragraph,table
        valid_day(day,self.c.now().date())
        rows=self.db.all('SELECT r.*,e.code FROM records r JOIN employees e ON e.uid=r.uid WHERE day=? ORDER BY e.code',(day,))
        display=[[self.display(r['uid'])]+[json.loads(r['payload']).get(m) if json.loads(r['payload']).get(m) is not None else '待补' for m in NUMERIC] for r in rows]
        plain='旧版三项分阶段记录 '+day+'\n'+'｜'.join(['员工']+NUMERIC)+'\n'+'\n'.join('｜'.join(map(str,row)) for row in display)
        return dict(blocks=[heading('旧版记录 '+day),table(['员工']+NUMERIC,display),paragraph('旧版原字段保留；不转换、不计入新版四项合计。')]),plain

    def range_export(self,actor,start,end):
        if self.c.report_version<2:return super().range_export(actor,start,end)
        self.require_admin(actor);valid_day(start,self.c.now().date());valid_day(end,self.c.now().date())
        if start>end or (datetime.fromisoformat(end)-datetime.fromisoformat(start)).days>366:raise ValueError('日期范围错误或超过366天')
        folder=self.c.data/'exports'/f'daily-v2-{start}-{end}';folder.mkdir(parents=True,exist_ok=True)
        records=self.db.all('SELECT r.*,e.code FROM daily_records r JOIN employees e ON e.uid=r.uid WHERE day BETWEEN ? AND ? ORDER BY day,e.code',(start,end))
        out=io.StringIO();w=csv.writer(out);w.writerow(['日期','员工']+list(METRICS)+['备注','版本','提交状态','有效'])
        for r in records:
            payload=json.loads(r['payload']);
            from .service import safe_cell
            w.writerow([r['day'],safe_cell(self.display(r['uid']))]+[payload[m] if payload[m] is not None else '待填' for m in METRICS]+[safe_cell(payload.get('备注','')),r['version'],r['timing'],r['confirmed']])
        (folder/'新版四项.csv').write_text(out.getvalue(),encoding='utf-8-sig')
        (folder/'新版四项.json').write_text(dumps([dict(r,code=self.display(r['uid']),name_at_submission=self.db.one("SELECT n.target_name FROM audit_names n JOIN history h ON h.id=n.history_id WHERE h.entity='新版日报' AND h.entity_key=? ORDER BY h.id DESC LIMIT 1",(str(r['uid'])+':'+r['day'],))) for r in records]),encoding='utf-8')
        days=sorted({r['day'] for r in records})
        (folder/'新版四项.md').write_text('\n\n'.join(self.render_summary(self.summary(day)) for day in days),encoding='utf-8')
        legacy=self.db.all('SELECT * FROM records WHERE day BETWEEN ? AND ? ORDER BY day,uid',(start,end))
        (folder/'旧版原字段.json').write_text(dumps(dict(schema=1,note='不转换为新版指标',records=legacy)),encoding='utf-8')
        dest=folder.with_suffix('.zip')
        with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
            for f in folder.iterdir():z.write(f,f.name)
        return dest
