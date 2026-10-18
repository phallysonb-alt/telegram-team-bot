"""Daily-only presentation, durable feedback and database-to-XLSX projection."""
import asyncio,json,os,subprocess,shutil,uuid,time
from pathlib import Path
from datetime import datetime,timedelta
from .daily import METRICS
from .db import dumps

def demo(s,uid):
    return bool(s.c.test_run and s.admin(uid) and s.db.one('SELECT uid FROM employees WHERE uid=? AND required=0',(uid,)))

def daily_name(s,uid):
    name=s.display(uid)
    if s.c.identity_mode=='telegram':
        p=s.db.one('SELECT username FROM tg_profiles WHERE uid=?',(uid,))
        suffix=' @'+p['username'] if p and p['username'] else ' · '+str(uid)[-6:]
        if suffix not in name:name+=suffix
    return name

def daily_identity(s,uid):
    """Presentation fields only; all joins continue to use the numeric uid."""
    profile=s.db.one('SELECT name,username FROM tg_profiles WHERE uid=?',(uid,))
    employee=s.db.one('SELECT name,code FROM employees WHERE uid=?',(uid,))
    name=profile['name'] if profile else employee['name'] or employee['code'] if employee else '身份未确认'
    username='@'+profile['username'].lstrip('@') if profile and profile['username'] else '—'
    return name,username

def wrap_identity(value,width=16):
    import unicodedata
    lines=[];line='';used=0
    for ch in value:
        size=2 if unicodedata.east_asian_width(ch) in ('W','F') else 1
        if used+size>width and line:lines.append(line);line='';used=0
        line+=ch;used+=size
    return '\n'.join(lines+[line])

def identity_table(headers,rows):
    from .rich import table
    result=table(headers,rows)
    for row_index,row in enumerate(result['cells']):
        for col,cell in enumerate(row):
            cell['align']='center' if row_index==0 or col>=2 else 'left'
            cell['valign']='middle'
    return result

def names_changed(s):
    s.db.set('daily_excel_revision',s.db.setting('daily_excel_revision',0)+1)
    s.db.set('daily_excel_pending',True)
    refresh_boards(s)

def refresh_boards(s):
    for pub in s.db.all('SELECT * FROM daily_publications WHERE chat=?',(s.c.chat,)):
        if not s.db.setting('daily_query_enabled:'+pub['day']) and not s.db.setting('daily_scheduled:'+pub['day']):continue
        s.save_summary(pub['day'])
        s.db.run("UPDATE outbox SET state='cancelled' WHERE key LIKE ? AND state='pending'",('daily-board:'+pub['day']+':%',))
        if pub['state']=='query_pending':continue
        s.db.run("UPDATE daily_publications SET state='pending',next_try=? WHERE day=?",(s.c.now().isoformat(),pub['day']))

def query_delivered(s):
    for item in s.db.all("SELECT key,value FROM settings WHERE key LIKE 'daily_private_delivery:%'"):
        v=json.loads(item['value']);sent=s.db.one("SELECT sent_id FROM outbox WHERE key=? AND state='sent'",(v['key'],))
        if sent:
            s.db.set('daily_private_card:'+str(v['chat'])+':'+v['day'],sent['sent_id'])
            s.db.run('DELETE FROM settings WHERE key=?',(item['key'],))
    for item in s.db.all("SELECT key,value FROM settings WHERE key LIKE 'daily_query_delivery:%'"):
        v=json.loads(item['value']);sent=s.db.one("SELECT sent_id FROM outbox WHERE key=? AND state='sent'",(v['key'],))
        if not sent:continue
        pub=s.db.one('SELECT * FROM daily_publications WHERE day=?',(v['day'],))
        if not pub:continue
        if v.get('fresh') and v.get('request')!=s.db.setting('daily_query_latest:'+v['day']):
            s.db.run('DELETE FROM settings WHERE key=?',(item['key'],));continue
        if not v.get('fresh') and v.get('target') and v['target']!=pub['message_id']:
            s.db.run('DELETE FROM settings WHERE key=?',(item['key'],));continue
        newest=s.db.one('SELECT MAX(version) v FROM daily_summaries WHERE day=?',(v['day'],))['v']
        s.db.run('UPDATE daily_publications SET message_id=?,version=?,state=? WHERE day=?',(sent['sent_id'],v['version'],'sent' if newest==v['version'] else 'pending',v['day']))
        s.db.set('daily_page_ids:'+v['day'],{'1':sent['sent_id']})
        s.db.run('DELETE FROM settings WHERE key=?',(item['key'],))

async def refresh_profiles(s,bot):
    # Query-only API reads; never changes bindings or ownership.
    for e in s.db.all('SELECT uid FROM employees WHERE approved=1 AND active=1 AND required=1'):
        try:
            member=await bot.get_chat_member(s.c.chat,e['uid'])
            if member.user.id!=e['uid']:raise ValueError('member identity mismatch')
            p=s.db.one('SELECT * FROM tg_profiles WHERE uid=?',(e['uid'],))
            # Incoming messages are authoritative during the API's short user-cache lag.
            if p and s.c.now().timestamp()-datetime.fromisoformat(p['updated']).timestamp()<60:continue
            s.profile(member.user)
        except Exception as error:s.db.set('daily_profile_retry:'+str(e['uid']),dict(error=type(error).__name__,at=s.c.now().isoformat()))

def historical(s,day):
    records=s.db.all('SELECT * FROM daily_records WHERE day=? AND confirmed=1 ORDER BY uid',(day,))
    snap=s.db.one('SELECT * FROM attendance WHERE day=?',(day,))
    known=bool(snap) and not s.db.setting('roster_warning:'+day,'')
    uids=set(json.loads(snap['uids'])) if known else set()
    totals={m:sum(json.loads(r['payload']).get(m) or 0 for r in records if not demo(s,r['uid'])) for m in METRICS}
    counted=[r for r in records if not demo(s,r['uid'])]
    submitted=len(counted);expected=len(uids) if known else None
    status='未收到日报' if not counted else '未知' if not known else '未齐' if set(r['uid'] for r in counted)!=uids else '已齐'
    return dict(day=day,totals=totals,submitted=submitted,expected=expected,status=status)

def recent(s):
    today=s.c.now().date()
    return [historical(s,(today-timedelta(days=i)).isoformat()) for i in range(1,8)]

def report_pages(s,r):
    from .rich import heading,paragraph,table
    # Detail pages are bounded independently from notes, so long notes are never lost.
    pieces=[]
    for i in range(0,max(1,len(r['rows'])),10):
        selected=r['rows'][i:i+10]
        fields={x['uid']:daily_identity(s,x['uid']) for x in selected}
        headers=['员工名称','Telegram 用户名']+list(METRICS)
        cells=[[wrap_identity(fields[x['uid']][0])+(' · 未提交' if x['timing']=='未提交' else ''),wrap_identity(fields[x['uid']][1])]+[x['payload'].get(m) if x['payload'].get(m) is not None else '—' for m in METRICS] for x in selected]
        cells.append(['全员合计','—']+[r['totals'][m] for m in METRICS])
        pieces.append(([identity_table(headers,cells)],'\n'.join('｜'.join(map(str,row)) for row in [headers]+cells),'员工明细'))
        notes='\n\n'.join(fields[x['uid']][0]+'（'+fields[x['uid']][1]+'）\n'+x['payload'].get('备注','') for x in selected if x['payload'].get('备注'))
        # Use separate continuation pages before exceeding Telegram limits.
        chunks=[];remaining=notes
        while remaining:
            end=min(1800,len(remaining))
            if end<len(remaining):
                split=remaining.rfind('\n',0,end)
                if split>900:end=split+1
            chunks.append(remaining[:end]);remaining=remaining[end:]
        for j,chunk in enumerate(chunks):
            if j==0 and len(notes)<=1800:
                pieces[-1][0].append(heading('备注'));pieces[-1][0].append(paragraph(chunk))
                old=pieces[-1];pieces[-1]=(old[0],old[1]+'\n\n备注\n'+chunk,old[2])
            else:pieces.append(([heading('备注（续）'),paragraph(chunk)],'备注（续）\n'+chunk,'备注'))
    hist=recent(s)
    hc=[[h['day']+(' · 未齐' if h['status']=='未齐' else '')]+(['未收到日报','—','—','—'] if h['status']=='未收到日报' else [h['totals'][m] for m in METRICS]) for h in hist]
    # Rolling section is on every page, after the day's numbers and notes.
    for index,(blocks,text,kind) in enumerate(pieces):
        subtitle=f"{r['day']} · {kind} · 第{index+1}/{len(pieces)}页 · 已交{r['submitted']}人"
        blocks[:0]=[heading('团队日报'),paragraph(subtitle)]
        blocks += [heading('最近7个已结束自然日'),table(['日期']+list(METRICS),hc)]
        text='团队日报\n'+subtitle+'\n'+text+'\n\n最近7个已结束自然日\n'+'\n'.join('｜'.join(map(str,x)) for x in hc)
        pieces[index]=dict(text=text,rich=dict(blocks=blocks))
    return pieces

def dirty(s,day):
    if day==s.c.now().date().isoformat():s.roster(day)
    s.db.set('daily_excel_revision',s.db.setting('daily_excel_revision',0)+1)
    s.db.set('daily_excel_pending',True)
    # Every published day contains a rolling section; update all existing active boards.
    refresh_boards(s) # Saving alone never creates a team message.

def board_markup(day,page,total):
    rows=[[dict(text='日报模板',callback_data='template:'+day),dict(text='查看日报',callback_data='report_view:'+day+':1')]]
    nav=[]
    if page>1:nav.append(dict(text='上一页',callback_data=f'report_page:{day}:{page-1}'))
    if page<total:nav.append(dict(text='下一页',callback_data=f'report_page:{day}:{page+1}'))
    if nav:rows.append(nav)
    return dict(inline_keyboard=rows)

def queue_feedback(s,update,day):
    s.db.set('daily_feedback:'+str(update.update_id),dict(chat=update.effective_chat.id,thread=update.effective_message.message_thread_id,message=update.effective_message.message_id,day=day,state='pending',attempts=0))

_feedback_inflight={}
async def flush_feedback(s,bot,keys=None):
    from telegram import ReactionTypeEmoji,ReplyParameters
    from telegram.error import BadRequest,Forbidden
    items=[dict(key='daily_feedback:'+k,value=dumps(s.db.setting('daily_feedback:'+k))) for k in keys if s.db.setting('daily_feedback:'+k)] if keys else s.db.all("SELECT key,value FROM settings WHERE key LIKE 'daily_feedback:%'")
    for item in items:
        v=json.loads(item['value'])
        if v['state']!='pending':continue
        inflight_key=str(s.db.path)+':'+item['key']
        if inflight_key in _feedback_inflight:
            await asyncio.shield(_feedback_inflight[inflight_key]);continue
        future=asyncio.get_running_loop().create_future();_feedback_inflight[inflight_key]=future
        start=time.perf_counter()
        try:
            allowed=s.db.setting('daily_reaction_allowed:'+str(v['chat']))
            if allowed is None:
                chat=await bot.get_chat(v['chat']);reactions=getattr(chat,'available_reactions',None)
                # PTB normalizes an omitted optional array to (). The API may still
                # accept bot reactions; the actual setMessageReaction is decisive.
                allowed=not reactions or any(getattr(x,'emoji',None)=='👍' for x in reactions)
                s.db.set('daily_reaction_allowed:'+str(v['chat']),allowed)
            method='setMessageReaction'
            if allowed and not v.get('fallback'):
                try:
                    await bot.set_message_reaction(chat_id=v['chat'],message_id=v['message'],reaction=[ReactionTypeEmoji('👍')])
                except (BadRequest,Forbidden) as e:
                    v['fallback']=True;v['reaction_error']=type(e).__name__
            else:v['fallback']=True
            if v.get('fallback'):
                method='sendMessage'
                await bot.send_message(chat_id=v['chat'],message_thread_id=v['thread'],text='已记录',reply_parameters=ReplyParameters(v['message']))
            v.update(state='sent',method=method,seconds=round(time.perf_counter()-start,4))
        except Exception as e:v.update(attempts=v['attempts']+1,error=type(e).__name__)
        finally:
            s.db.set(item['key'],v)
            _feedback_inflight.pop(inflight_key,None)
            if not future.done():future.set_result(True)

def export_path(s):
    from .config import ROOT
    override=s.db.setting('daily_excel_path')
    return Path(override) if override else (ROOT if s.c.test_run=='newgroup' else s.c.data)/'exports'/'日报历史记录.xlsx'

def export_data(s):
    dates=[x['day'] for x in s.db.all('SELECT day FROM daily_records UNION SELECT day FROM attendance ORDER BY day')]
    result=[]
    for day in dates:
        h=historical(s,day);snap=s.db.one('SELECT * FROM attendance WHERE day=?',(day,))
        uids=set(json.loads(snap['uids'])) if snap else set()
        uids |= {x['uid'] for x in s.db.all('SELECT uid FROM daily_records WHERE day=? AND confirmed=1',(day,)) if not demo(s,x['uid'])}
        rows=[]
        for uid in sorted(uids):
            rec=s.record(uid,day);effective=rec and rec['confirmed'];p=json.loads(rec['payload']) if effective else {}
            rows.append(list(daily_identity(s,uid))+[p.get(m) if p.get(m) is not None else '—' for m in METRICS]+[p.get('备注',''),'已提交 · '+rec['timing'] if effective else '未提交'])
        result.append(dict(**h,rows=rows))
    return dict(days=result,label=s.c.trial_label if s.c.test_run else '',revision=s.db.setting('daily_excel_revision',0))

def write_excel(data,path):
    from .config import ROOT
    from .portable_excel import artifact_runtime,write_daily
    runtime=artifact_runtime()
    if runtime is None:return write_daily(data,path)
    node,module=runtime
    env=dict(os.environ)
    if module:env['DAILY_ARTIFACT_MODULE']=str(module)
    path.parent.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex;source=path.parent/(token+'.json');temp=path.parent/(token+'.xlsx')
    source.write_text(dumps(data),encoding='utf-8')
    try:
        proc=subprocess.run([node,str(ROOT/'scripts/daily_xlsx.mjs'),str(source),str(temp)],env=env,capture_output=True,stdin=subprocess.DEVNULL,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if proc.returncode:raise RuntimeError('Excel生成失败；退出码'+str(proc.returncode))
        import zipfile
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None:raise RuntimeError('Excel完整性检查失败')
        os.replace(temp,path) # A locked target never replaces the last valid file.
    finally:
        source.unlink(missing_ok=True);temp.unlink(missing_ok=True)
        Path(str(temp)+'.inspect.ndjson').unlink(missing_ok=True)

_locks={}
async def ensure_export(s,force=False):
    path=export_path(s);key=str(path)
    lock=_locks.setdefault(key,asyncio.Lock())
    async with lock:
        for _ in range(3 if force else 1):
            revision=s.db.setting('daily_excel_revision',0)
            if path.exists() and not s.db.setting('daily_excel_pending',False) and s.db.setting('daily_excel_done',-1)==revision:return True
            data=export_data(s)
            try:await asyncio.to_thread(write_excel,data,path)
            except Exception as e:
                s.db.set('daily_excel_pending',True);s.db.set('daily_excel_error',dict(type=type(e).__name__,at=s.c.now().isoformat()));return False
            s.db.set('daily_excel_done',data['revision']);s.db.set('daily_excel_error',None)
            if s.db.setting('daily_excel_revision',0)==data['revision']:
                s.db.set('daily_excel_pending',False);return True
        return False

def initialize(s):
    s.db.set('daily_excel_pending',True)
    s.db.set('daily_reaction_allowed:'+str(s.c.chat),None)
    # Snapshot only today's actual roster. Historical unknown days stay unknown.
    s.roster(s.c.now().date().isoformat())

async def prime_feedback(s,bot):
    try:
        chat=await bot.get_chat(s.c.chat);allowed=getattr(chat,'available_reactions',None)
        s.db.set('daily_reaction_allowed:'+str(s.c.chat),not allowed or any(getattr(x,'emoji',None)=='👍' for x in allowed))
    except Exception:pass
