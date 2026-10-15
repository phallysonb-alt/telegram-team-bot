import json,uuid
from datetime import datetime,timedelta
from .db import dumps
from .daily import daily_view

class DailySchedulerMixin:
    def confirm(self,actor,timezone,signature):
        result=super().confirm(actor,timezone,signature)
        if self.c.report_version>=2:
            self.db.set('daily_scan_start',self.c.now().date().isoformat())
        return result

    def plan(self,now):
        if self.c.report_version<2:return super().plan(now)
        if not self.schedule_enabled():return
        anchor=self.db.setting('daily_scan_start')
        if not anchor:
            anchor=(now.date()-timedelta(days=1)).isoformat();self.db.set('daily_scan_start',anchor)
        day=datetime.fromisoformat(anchor).date()
        # Persist all due dates, not just a two-hour catch-up window.
        while day<now.date():
            if day.weekday() in self.c.workdays and self.c.event_time(day,'summary')<=now:
                key=f'daily-v2:{day}'
                if not self.db.one('SELECT key FROM jobs WHERE key=?',(key,)):
                    with self.db.transaction():
                        self.publish(day.isoformat(),key)
                        self.db.run("INSERT INTO jobs(key,day,kind,due,state) VALUES (?,?,?,?,'done')",(key,day.isoformat(),'daily-v2',self.c.event_time(day,'summary').isoformat()))
            day+=timedelta(days=1)

    def refresh(self,now):
        if self.c.report_version<2:return super().refresh(now)
        # Revisions use durable publication pending state, independent of timers.
        return

    def publish(self,day,key):
        if self.c.report_version<2:return super().publish(day,key)
        self.db.set('daily_scheduled:'+day,True)
        old=self.db.one('SELECT * FROM daily_publications WHERE day=?',(day,))
        if old:
            return json.loads(self.db.one('SELECT payload FROM daily_summaries WHERE day=? ORDER BY version DESC LIMIT 1',(day,))['payload'])
        with self.db.transaction():
            r=self.s.save_summary(day)
            self.db.run('INSERT INTO daily_publications(day,version,chat,next_try) VALUES (?,?,?,?)',(day,r['version'],self.c.chat,self.c.now().isoformat()))
        return r

    async def enhance(self):
        if self.c.report_version<2:return await super().enhance()
        return # This release never calls Gemini.

    async def flush(self,bot,keys=None):
        if keys is not None:return await super().flush(bot,keys)
        if self.c.report_version<2:return await super().flush(bot)
        # Retired management queues are never flushed, even if restored.
        thread=self.thread('management')
        self.db.run("UPDATE outbox SET state='cancelled' WHERE chat=? AND thread=? AND state='pending'",(self.c.chat,thread))
        if not self.db.setting('progress_rounds_enabled',True):
            from .workbench import Workbench
            w=Workbench(self.s)
            # Local snapshots and coalesced edits do not enable scheduled reminders.
            w.capture()
            for board in self.db.all("SELECT * FROM wb_boards WHERE pending=1 AND due<=?",(self.c.now().isoformat(),)):
                if not board['message_id']:continue
                text,rich,_,_=w.team_view(board['day'],board['page'],board['period'])
                key='wb-refresh:'+board['day']+':'+uuid.uuid4().hex[:12]
                markup=self.db.setting('wb_board_markup:'+board['day'])
                self.reply(key,self.c.chat,self.api_thread('progress'),text,markup,rich=rich,audience='admin')
                self.db.set('edit:'+key+':0',board['message_id'])
                self.db.set('wbcard:'+key+':0',dict(kind='team',viewer=0,day=board['day'],chat=self.c.chat))
                # Keep only the latest pending board edit, including retries.
                for row in self.db.all("SELECT key,value FROM settings WHERE key LIKE 'wbcard:wb-refresh:%'"):
                    if row['key']!='wbcard:'+key+':0' and json.loads(row['value'])['day']==board['day']:
                        self.db.run("UPDATE outbox SET state='cancelled' WHERE key=? AND state='pending'",(row['key'][7:],))
                self.db.run('UPDATE wb_boards SET pending=0,due=NULL WHERE day=?',(board['day'],))
        await super().flush(bot)
        await self.daily_refresh(bot)
        from .progress_ui import controller,flush as progress_flush
        await progress_flush(controller(self.s),bot)

    def request_daily_refresh(self,bot):
        import asyncio
        if getattr(self,'_daily_task',None) and not self._daily_task.done():return
        async def run():
            await asyncio.sleep(.25)
            await self.daily_refresh(bot)
        self._daily_task=asyncio.create_task(run())

    async def daily_refresh(self,bot):
        import asyncio
        from .daily_history import flush_feedback,ensure_export,report_pages,query_delivered,refresh_boards
        if getattr(self,'_daily_busy',False):return
        self._daily_busy=True
        try:
            query_delivered(self.s)
            today=self.c.now().date().isoformat()
            self.s.roster(today)
            if self.c.now().strftime('%H:%M')>='00:05' and self.db.setting('daily_rollover_refresh')!=today:
                self.db.set('daily_rollover_refresh',today)
                self.db.set('daily_excel_pending',True)
                self.db.set('daily_excel_revision',self.db.setting('daily_excel_revision',0)+1)
                refresh_boards(self.s)
            await flush_feedback(self.s,bot)
            for pub in self.db.all("SELECT * FROM daily_publications WHERE state='pending' AND next_try<=?",(self.c.now().isoformat(),)):
                if not self.db.setting('daily_query_enabled:'+pub['day']) and not self.db.setting('daily_scheduled:'+pub['day']):continue
                if not pub['message_id'] and not self.db.setting('daily_scheduled:'+pub['day']):continue
                if pub['chat']!=self.c.chat:
                    self.db.run("UPDATE daily_publications SET state='paused_old_route' WHERE day=?",(pub['day'],));continue
                stored=self.db.one('SELECT * FROM daily_summaries WHERE day=? ORDER BY version DESC LIMIT 1',(pub['day'],))
                if not stored:continue
                r=json.loads(stored['payload']);pages=report_pages(self.s,r)
                ids=self.db.setting('daily_page_ids:'+pub['day'],{})
                if pub['message_id'] and '1' not in ids:ids['1']=pub['message_id']
                # First page is updated on each submission; further pages are accessed by buttons.
                from .daily_history import board_markup
                key=f"daily-board:{pub['day']}:{r['version']}"
                page=min(self.db.setting('daily_query_page:'+pub['day'],1),len(pages))
                self.reply(key,pub['chat'],self.api_thread('report'),pages[page-1]['text'],board_markup(pub['day'],page,len(pages)),rich=pages[page-1]['rich'],literal_daily=True)
                if ids.get('1'):self.db.set('edit:'+key+':0',ids['1'])
                await super().flush(bot,[key])
                sent=self.db.one("SELECT * FROM outbox WHERE key=? AND state='sent'",(key+':0',))
                if sent:
                    ids['1']=sent['sent_id'];self.db.set('daily_page_ids:'+pub['day'],ids)
                    newest=self.db.one('SELECT MAX(version) v FROM daily_summaries WHERE day=?',(pub['day'],))['v']
                    self.db.run('UPDATE daily_publications SET message_id=?,version=?,state=? WHERE day=?',(sent['sent_id'],r['version'],'sent' if newest==r['version'] else 'pending',pub['day']))
                    self.db.set('daily_delivery:'+pub['day'],dict(chat=pub['chat'],message_id=sent['sent_id'],version=r['version'],edited=bool(pub['message_id'])))
            await ensure_export(self.s)
        finally:self._daily_busy=False
