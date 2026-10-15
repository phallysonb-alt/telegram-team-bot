from datetime import datetime, timedelta, time
import asyncio
import time as perfclock
from .db import dumps


class LegacyScheduler:
    """SQLite 持久化任务及发件箱；只补发时限内任务。"""
    def __init__(self, service):
        self.s = service
        self.db = service.db
        self.c = service.c
        self._sending=set()
        from .ai import AI
        self.ai = AI(service)

    def thread(self, name):
        return self.db.setting('thread:' + name, self.c.threads[name])

    def api_thread(self, name):
        value = self.thread(name)
        if value == 'general':
            return None
        if isinstance(value, int) and value > 0:
            return value
        raise ValueError('话题未绑定：' + name)

    def schedule_enabled(self):
        if not self.c.schedule_allowed or self.c.run_mode=='trial':return False
        if not self.c.owner or not self.c.report_times_confirmed:
            return False
        expected = dict(timezone=self.c.timezone, times=self.c.times, offsets=self.c.offsets, shifts=self.c.shifts, workdays=list(self.c.workdays), chat=self.c.chat, threads={k: self.thread(k) for k in self.c.threads})
        return self.db.setting('schedule_confirmed') == expected

    def confirm(self, actor, timezone, signature):
        self.s.require_admin(actor)
        if not self.c.report_times_confirmed:
            raise ValueError('日报提交窗口、提醒和汇总时间尚未由用户确认，定时任务保持关闭')
        if timezone != self.c.timezone or signature != ','.join(self.c.times.values()):
            raise ValueError('时区或时间确认不匹配；请查看 /schedule 后复制完整确认命令')
        required=('progress','report') if self.c.report_version>=2 else tuple(self.c.threads)
        if not self.c.chat or not all(self.thread(k) for k in required):
            raise ValueError('请先配置群 ID 并绑定工作进度、每日报表话题' if self.c.report_version>=2 else '请先配置群 ID 并绑定四个话题')
        value = dict(timezone=self.c.timezone, times=self.c.times, offsets=self.c.offsets, shifts=self.c.shifts, workdays=list(self.c.workdays), chat=self.c.chat, threads={k: self.thread(k) for k in self.c.threads})
        self.db.set('schedule_confirmed', value)

    def enqueue(self, key, name, text, file=None, chat=None, rich=None):
        chat = self.c.chat if chat is None else chat
        thread = self.api_thread(name) if chat == self.c.chat else None
        if not chat:
            raise ValueError('群或话题未绑定')
        self.reply(key, chat, thread, text, file=file, rich=rich)

    def reply(self, key, chat, thread, text, markup=None, file=None, rich=None, audience='employee', viewer=None, filename=None, literal_daily=False, reply_to=None):
        import copy, uuid
        markup = copy.deepcopy(markup)
        rich = copy.deepcopy(rich)
        from .presentation import clean_text,clean_blocks,without_test_label,TEST_LABELS
        text=str(text) if literal_daily else clean_text(text,self.c)
        rich=rich if literal_daily else clean_blocks(rich,self.c) if rich else None
        if self.db.setting('display_mock',False):
            text='模拟展示\n'+(text.removeprefix('模拟展示\n') if literal_daily else text.replace('模拟展示\n',''))
            if rich:
                from .rich import paragraph
                rich['blocks']=[b for b in rich['blocks'] if b.get('text')!='模拟展示']
                rich['blocks'].insert(0,paragraph('模拟展示'))
        if rich:
            if file:
                raise ValueError('文件保持现有普通发送入口')
            stored = self.db.setting('rich:' + key + ':0')
            token = stored['snapshot'] if stored else uuid.uuid4().hex[:16]
            self.db.set('display:' + token, dict(text=text, audience=audience, viewer=viewer))
            markup = markup or {'inline_keyboard': []}
            markup['inline_keyboard'].append([dict(text='普通文字查看', callback_data='plain:' + token)])
        if self.c.test_run:
            label=self.c.trial_label if self.c.run_mode=='trial' else '【隔离测试，不计正式数据】' if self.c.report_version>=2 else '【联调测试；不计正式数据】'
            if literal_daily:
                for old_label in TEST_LABELS:text=text.removeprefix(old_label+'\n')
                text=label+'\n'+text
            else:text = label+'\n' + without_test_label(text)
            if rich:
                from .rich import heading
                if not literal_daily:rich['blocks']=[b for b in rich['blocks'] if b.get('text') not in TEST_LABELS]
                rich['blocks'].insert(0, heading(label))
                self.db.set('display:' + token, dict(text=text, audience=audience, viewer=viewer))
            if markup:
                markup = copy.deepcopy(markup)
                for row in markup.get('inline_keyboard', []):
                    for button in row:
                        if 'callback_data' in button:
                            button['callback_data'] = f'test:{self.c.test_run}:' + button['callback_data']
        parts = [text] if rich else [text[i:i + 3500] for i in range(0, max(1, len(text)), 3500)]
        for index, part in enumerate(parts):
            self.db.run('INSERT OR IGNORE INTO outbox(key,chat,thread,text,file,markup,next_try) VALUES (?,?,?,?,?,?,?)', (f'{key}:{index}', chat, thread, part, str(file) if file and index == 0 else None, dumps(markup) if markup and index == len(parts) - 1 else None, self.c.now().isoformat()))
        if rich:
            self.db.set('rich:' + key + ':0', dict(message=rich, snapshot=token))
        if reply_to is not None:self.db.set('reply_to:'+key+':0',reply_to)
        if filename:
            self.db.set('file_name:' + key + ':0',filename)

    def plan(self, now):
        if not self.schedule_enabled():
            return
        # 当前日和昨天；跨午夜重启只补时限内昨日任务，不堆积多日。
        for day in (now.date() - timedelta(days=1), now.date()):
            if day.weekday() not in self.c.workdays:
                continue
            for kind, clock in self.c.times.items():
                if kind == 'open':
                    continue
                due = self.c.event_time(day, kind)
                self.db.run('INSERT OR IGNORE INTO jobs(key,day,kind,due) VALUES (?,?,?,?)', (f'{day}:{kind}', day.isoformat(), kind, due.isoformat()))
        for job in self.db.all("SELECT * FROM jobs WHERE state='pending'"):
            due = datetime.fromisoformat(job['due'])
            if due > now:
                continue
            if now - due > timedelta(minutes=self.c.catchup):
                self.db.run("UPDATE jobs SET state='expired' WHERE key=?", (job['key'],))
                continue
            if job['kind'] == 'summary':
                self.publish(job['day'], job['key'])
                self.db.run("UPDATE jobs SET state='done' WHERE key=?", (job['key'],))
                continue
            with self.db.transaction():
                phase = '上半场' if job['kind'].startswith('am') else '下半场'
                if job['kind'] == 'deadline':
                    names, target = self.s.missing(job['day']), 'report'
                    text = '日报截止提醒'
                else:
                    names, target = self.s.missing(job['day'], phase), 'progress'
                    text = phase + ('逾期提醒' if 'overdue' in job['kind'] else '常规提醒') + '（仅填写本阶段新增量）'
                if names:
                    self.enqueue(job['key'], target, f'{job["day"]} {text}\n待填报员工编号：' + '、'.join(names))
                self.db.run("UPDATE jobs SET state='done' WHERE key=?", (job['key'],))

    def publish(self, day, key):
        # 若崩溃发生于保存汇总后、发件箱前，恢复时复用该发布版本。
        with self.db.transaction():
            cached = self.db.setting('publication:' + key)
            if cached:
                result = cached
            else:
                result = self.s.save_summary(day)
                self.db.set('publication:' + key, result)
            from .rich import report_view, management_view
            self.enqueue(key + ':report', 'report', self.s.render_summary(result), rich=report_view(self.s, result))
            self.enqueue(key + ':management', 'management', f'{day} 管理摘要 · 版本 {result["version"]}\n' + self.s.render_management(result), rich=management_view(self.s, result))
        return result

    def refresh(self, now):
        if not self.schedule_enabled():
            return
        for row in self.db.all('SELECT * FROM dirty'):
            if datetime.fromisoformat(row['due']) <= now:
                self.publish(row['day'], 'refresh:' + row['day'] + ':' + row['due'])

    async def flush(self, bot, keys=None):
        where=' AND ('+' OR '.join('key LIKE ?' for _ in keys)+')' if keys else ''
        rows=self.db.all("SELECT * FROM outbox WHERE state='pending' AND next_try<=?"+where+" ORDER BY CASE WHEN key LIKE 'update:%' THEN 0 ELSE 1 END,id LIMIT 12",(self.c.now().isoformat(),)+tuple(k+':%' for k in (keys or [])))
        for row in rows:
            if row['id'] in self._sending or self.db.one('SELECT state FROM outbox WHERE id=?',(row['id'],))['state']!='pending':continue
            daily_demo=self.c.test_run=='mockdaily30' and row['chat']==self.c.chat and row['thread']==self.api_thread('report') and row['key'].startswith(('send-daily30-display:','update:'))
            if self.db.setting('display_mock',False) and row['chat']!=self.c.owner and not daily_demo:
                self.db.run("UPDATE outbox SET state='fixture_only' WHERE id=?",(row['id'],));continue
            if self.c.internal_chat and row['chat']==self.c.internal_chat:
                self.db.run("UPDATE outbox SET state='paused_old_route' WHERE id=?",(row['id'],));continue
            job_key = row['key'].rsplit(':', 1)[0]
            job = self.db.one('SELECT * FROM jobs WHERE key=?', (job_key,))
            if job and job['kind'] != 'summary' and self.c.now() - datetime.fromisoformat(job['due']) > timedelta(minutes=self.c.catchup):
                self.db.run("UPDATE outbox SET state='expired' WHERE id=?", (row['id'],))
                continue
            self._sending.add(row['id']);send_started=perfclock.perf_counter()
            try:
                edit=self.db.setting('edit:'+row['key'])
                if edit:
                    import json
                    from telegram import InlineKeyboardMarkup
                    from telegram.error import BadRequest,RetryAfter
                    from types import SimpleNamespace
                    try:
                        rich_edit=self.db.setting('rich:'+row['key'])
                        if rich_edit:
                            from .rich import edit_native
                            try:sent=await edit_native(bot,row['chat'],edit,rich_edit['message'],json.loads(row['markup']) if row['markup'] else None)
                            except RetryAfter:raise
                            except Exception:sent=await bot.edit_message_text(chat_id=row['chat'],message_id=edit,text=row['text'],reply_markup=InlineKeyboardMarkup.de_json(json.loads(row['markup']),bot) if row['markup'] else None)
                        else:sent=await bot.edit_message_text(chat_id=row['chat'],message_id=edit,text=row['text'],reply_markup=InlineKeyboardMarkup.de_json(json.loads(row['markup']),bot) if row['markup'] else None)
                    except BadRequest as e:
                        if 'message is not modified' not in str(e).lower():raise
                        sent=SimpleNamespace(message_id=edit)
                    if sent is True:sent=SimpleNamespace(message_id=edit)
                    method='editMessageText'
                elif row['file']:
                    with open(row['file'], 'rb') as f:
                        sent = await bot.send_document(chat_id=row['chat'], message_thread_id=row['thread'], document=f, filename=self.db.setting('file_name:'+row['key']), caption=row['text'][:1000])
                else:
                    import json
                    from telegram import InlineKeyboardMarkup
                    from telegram.error import RetryAfter
                    rich = self.db.setting('rich:' + row['key'])
                    markup_data = json.loads(row['markup']) if row['markup'] else None
                    method = 'sendMessage'
                    if rich:
                        try:
                            from .rich import send_native
                            reply_to=self.db.setting('reply_to:'+row['key'])
                            sent = await send_native(bot, row['chat'], row['thread'], rich['message'], markup_data,reply_to=reply_to) if reply_to is not None else await send_native(bot, row['chat'], row['thread'], rich['message'], markup_data)
                            method = 'sendRichMessage'
                        except RetryAfter:
                            raise
                        except Exception as e:
                            # Persist downgrade once; plain segments retain independent outbox keys.
                            failure = dict(error_type=type(e).__name__, code=getattr(e,'code',None), reason=getattr(e,'reason','transport failure'))
                            with self.db.transaction():
                                self.db.set('rich_failure:' + row['key'], failure)
                                self.db.set('rich:' + row['key'], None)
                                parts = [row['text'][i:i+3500] for i in range(0,max(1,len(row['text'])),3500)]
                                self.db.run('UPDATE outbox SET text=? WHERE id=?',(parts[0],row['id']))
                                row['text']=parts[0]
                                for i, part in enumerate(parts[1:],1):
                                    self.db.run('INSERT OR IGNORE INTO outbox(key,chat,thread,text,next_try) VALUES (?,?,?,?,?)',(row['key']+':plain:'+str(i),row['chat'],row['thread'],part,self.c.now().isoformat()))
                            rich = None
                    if not rich:
                        markup = InlineKeyboardMarkup.de_json(markup_data, bot) if markup_data else None
                        from telegram import ReplyParameters
                        reply_to=self.db.setting('reply_to:'+row['key'])
                        extra={'reply_parameters':ReplyParameters(reply_to,allow_sending_without_reply=True)} if reply_to is not None else {}
                        sent = await bot.send_message(chat_id=row['chat'], message_thread_id=row['thread'], text=row['text'], reply_markup=markup,**extra)
                self.db.run("UPDATE outbox SET state='sent',sent_id=? WHERE id=?", (sent.message_id, row['id']))
                card=self.db.setting('card:'+row['key'])
                if card:
                    self.db.run("UPDATE check_cards SET message_id=?,state='sent' WHERE uid=? AND task=? AND day=?",(sent.message_id,card['uid'],card['task'],card['day']))
                wbcard=self.db.setting('wbcard:'+row['key'])
                if wbcard:
                    self.db.run('UPDATE wb_cards SET message_id=? WHERE kind=? AND viewer=? AND day=?',(sent.message_id,wbcard['kind'],wbcard['viewer'],wbcard['day']))
                    if wbcard['kind']=='team':self.db.run('UPDATE wb_boards SET message_id=? WHERE day=?',(sent.message_id,wbcard['day']))
                self.db.set('delivery:' + row['key'], dict(chat=getattr(getattr(sent,'chat',None),'id',row['chat']), thread=getattr(sent,'message_thread_id',row['thread']), is_topic_message=bool(getattr(sent,'is_topic_message',False)), message_id=sent.message_id, method='sendDocument' if row['file'] else method, native_blocks=[b.get('type') for b in getattr(sent,'api_kwargs',{}).get('rich_message',{}).get('blocks',[])]))
                self.db.set('send_timing:'+row['key'],dict(at=self.c.now().isoformat(),seconds=round(perfclock.perf_counter()-send_started,4),success=True))
            except Exception as e:
                retry = getattr(e, 'retry_after', 0)
                retry = retry.total_seconds() if hasattr(retry, 'total_seconds') else retry
                wait = max(float(retry or 0), min(60 * 2 ** min(row['attempts'], 8), 3600))
                self.db.run('UPDATE outbox SET attempts=attempts+1,next_try=? WHERE id=?', ((self.c.now() + timedelta(seconds=wait)).isoformat(), row['id']))
                self.db.set('send_timing:'+row['key'],dict(at=self.c.now().isoformat(),seconds=round(perfclock.perf_counter()-send_started,4),success=False,error=type(e).__name__))
            finally:self._sending.discard(row['id'])

    async def enhance(self):
        import json
        for setting in self.db.all("SELECT key,value FROM settings WHERE key LIKE 'publication:%'"):
            result = json.loads(setting['value'])
            if not result.get('public_notes'):
                continue
            marker = f'ai_summary:{result["day"]}:{result["version"]}'
            if self.db.setting(marker):
                continue
            key = setting['key'][len('publication:'):]
            # 已发送的汇总不改写；每版本最多一次正常请求。崩溃后不再发起相同请求。
            if self.db.one("SELECT id FROM outbox WHERE key LIKE ? AND state='sent'", (key + ':report:%',)):
                continue
            self.db.set(marker, '已尝试')
            answer, status = await self.ai.generate('日报文字归纳', '请仅归纳管理员审核文字，不添加事实、不计算数字：\n' + result['public_notes'])
            result['ai'] = status if answer else '未使用 AI；' + status
            if answer:
                result['ai_text'] = answer
            with self.db.transaction():
                self.db.set(setting['key'], result)
                self.db.run('UPDATE summaries SET payload=? WHERE day=? AND version=?', (dumps(result), result['day'], result['version']))
                self.db.run("DELETE FROM outbox WHERE key LIKE ? AND state='pending'", (key + ':report:%',))
                from .rich import report_view
                self.enqueue(key + ':report', 'report', self.s.render_summary(result), rich=report_view(self.s, result))
            self.s.export_day(result['day'], result)

    async def loop(self, application, test_adapters=None):
        from .maintenance import backup
        import logging
        while True:
            try:
                now = self.c.now()
                self.plan(now)
                self.refresh(now)
                await self.enhance()
                if self.db.setting('last_backup') != now.date().isoformat():
                    backup(self.s)
                    self.db.set('last_backup', now.date().isoformat())
                await self.flush(application.bot)
                if test_adapters:
                    for adapter in test_adapters():
                        await adapter.scheduler.flush(application.bot)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.getLogger(__name__).error('后台任务失败：%s', type(e).__name__)
            await asyncio.sleep(10)


from .daily_scheduler import DailySchedulerMixin


class Scheduler(DailySchedulerMixin,LegacyScheduler):
    pass
