import asyncio
import json
import logging
import shlex
from pathlib import Path
from .reports import TEMPLATE, PROGRESS_TEMPLATE, FIELDS, preview, valid_day
from .db import dumps, stamp
from .scheduler import Scheduler
from .knowledge import Knowledge
from .ai import AI

HELP = '''团队工作机器人
先私聊机器人发送 /start，便于接收个人信息与导出。
/whereami 查看群、话题和本人 ID
/register 人员编号 —— 申请登记，可选显示名，不要求真实姓名
/menu 显示按钮
/template [日期] —— 日报模板（可预填进度）
/progress_template —— 阶段进度模板
在进度话题粘贴阶段模板；在日报话题粘贴日报模板，预览后点击确认。
/revoke 日期 原因 —— 撤销本人当日报表
/materials [页码] —— 资料目录
/material_get 编号 —— 原文件私聊下载
/ask 问题 —— 正文问答仅在问答模式可用；目录模式停用
/tasks —— 查看本人任务
/task_update 编号 状态 "完成说明" "问题" "下一步"
/progress_view [日期] —— 查看进度总表
/progress_details 日期 页码 —— 管理员私聊员工明细，每页 5 人
/report_view [日期] —— 查看日报总表
/management_view 日期 [页码] —— 管理员按需查看任务
/plain 查看码 —— 富文本消息的普通文字版本，也可点击消息按钮
/progress_plain [日期]、/report_plain [日期]、/ask_plain 问题 —— 独立普通格式入口
管理员命令和详细格式见随项目提供的管理员说明。'''

def history_change(row):
    """Readable audit differences; raw message bodies and internal keys stay in storage."""
    def value(raw):
        r=json.loads(raw) if raw else {}
        if not isinstance(r,dict):return {}
        payload=r.get('payload',r)
        return json.loads(payload) if isinstance(payload,str) else payload
    before=value(row['before_value']);after=value(row['after_value'])
    keys=[k for k in ('新增','维聊','有效','精聊','amount','active','required','approved','state') if k in before or k in after]
    names={'amount':'数量','active':'在职','required':'日报要求','approved':'审批','state':'状态'}
    return '；'.join(names.get(k,k)+' '+str(before.get(k,'无'))+' → '+str(after.get(k,'无')) for k in keys) or '已保存修改，详细历史保留在导出中。'


class Adapter:
    def __init__(self, service):
        self.s = service
        self.db = service.db
        self.c = service.c
        self.scheduler = Scheduler(service)
        self.kb = Knowledge(service)
        self.ai = AI(service)
        self.test_adapters = {}
        if not self.c.test_run and self.db.setting('live_test'):
            from .live_test import sandbox
            sandbox(self, self.db.setting('live_test'))

    def route(self, update, topic=None, admin=False):
        uid = update.effective_user.id
        chat = update.effective_chat
        msg = update.effective_message
        if self.c.report_version>=2 and admin and chat.type!='private':
            raise PermissionError('必要管理操作仅在管理员私聊入口使用')
        if admin:
            self.s.require_admin(uid)
        else:
            self.s.employee(uid)
        if chat.type == 'private':
            return
        if chat.id != self.c.chat or not chat.is_forum:
            raise PermissionError('请在配置的 Telegram 话题群内使用')
        if topic and msg.message_thread_id != self.scheduler.api_thread(topic):
            raise ValueError('此操作请在对应话题内使用：' + dict(progress='工作进度', training='培训', management='管理任务', report='日报')[topic])

    def template(self, uid, day):
        emp = self.s.employee(uid)
        valid_day(day, self.c.now().date())
        row = self.s.record(uid, day)
        payload = json.loads(row['payload']) if row else {}
        return '\n'.join(f'{key}：{value}' for key, value in [('日期', day), ('员工编号', emp['code'])] + [(key, payload.get(key) if payload.get(key) is not None else '') for key in FIELDS])

    @staticmethod
    def menu():
        return {'inline_keyboard': [[{'text': '日报模板', 'callback_data': 'template'}, {'text': '进度模板', 'callback_data': 'progress_template'}], [{'text': '我的任务', 'callback_data': 'tasks'}, {'text': '帮助', 'callback_data': 'help'}]]}

    def dispatch(self, update, command, args, raw):
        if self.c.report_version>=2:
            from .daily_bot import dispatch
            result=dispatch(self,update,command,args,raw)
            if result is not None:return result
        uid, msg = update.effective_user.id, update.effective_message
        source = f'{update.effective_chat.id}:{msg.message_id}'
        today = self.c.business_date().isoformat()
        # 返回 text, markup, file, 私聊目标（可选）
        text, markup, file, private = '', None, None, None
        self.presentation = None
        self.presentation_audience = 'employee'
        self.presentation_viewer = None
        self.presentation_filename = None
        if command == 'whereami':
            text = f'群/会话 ID：{update.effective_chat.id}\n话题 ID：{msg.message_thread_id or "无"}\n你的用户 ID：{uid}'
            self.db.set(f'whereami:{uid}:{update.effective_chat.id}:{msg.message_thread_id if msg.message_thread_id is not None else "absent"}', dict(uid=uid, chat=update.effective_chat.id, thread=msg.message_thread_id, thread_field_present='message_thread_id' in msg.to_dict(), is_topic_message=bool(msg.is_topic_message), update_id=update.update_id, message_id=msg.message_id, forum=bool(update.effective_chat.is_forum), received=stamp()))
            if not self.c.owner:
                text += '\n当前仅查看 ID；管理员需在本地确认配置，没有自动授权任何人。'
        elif command in ('start', 'help', 'menu'):
            text, markup = HELP, self.menu()
            if update.effective_chat.type == 'private':
                self.db.set(f'private_started:{uid}', True)
        elif command == 'materials':
            self.route(update,'training')
            from .catalog import Catalog
            entries=Catalog(self.s).entries()
            page=int(args[0]) if args else 1
            pages=max(1,(len(entries)+9)//10)
            if not 1<=page<=pages: raise ValueError('目录页码超出范围')
            selected=entries[(page-1)*10:page*10]
            text=f'资料目录\n第{page}/{pages}页\n\n'+('\n\n'.join(f'{e["name"]}\n版本 {e["version"]} · {e["bytes"]}字节' for e in selected) or '暂无可下载资料')
            markup={'inline_keyboard':[[dict(text='下载 '+e['name'][:25],callback_data='material_get:'+str(e['id']))] for e in selected]}
        elif command == 'material_get':
            self.route(update,'training')
            from .catalog import Catalog
            name,file=Catalog(self.s).download(uid,int(args[0]))
            text='文件下载\n'+name
            self.presentation_filename=name
            private=uid
        elif command == 'register':
            if update.effective_chat.type != 'private' and update.effective_chat.id != self.c.chat:
                raise PermissionError('请在机器人私聊或指定群登记')
            self.s.register(uid, args[0], ' '.join(args[1:]))
            text = '登记已申请\n员工：'+args[0]+'\n状态：等待管理员批准。'
        elif command in ('template', 'progress_template'):
            self.route(update, 'report' if command == 'template' else 'progress')
            text = self.template(uid, args[0] if args else today) if command == 'template' else PROGRESS_TEMPLATE.replace('日期：', '日期：' + today, 1)
        elif command == 'report_raw':
            self.route(update, 'report')
            draft, day, payload = self.s.draft(uid, raw, source)
            text = '请核对本阶段新增量。空项为待补，确认后替换当前有效日报。\n' + preview(day, payload)
            markup = {'inline_keyboard': [[{'text': '确认提交', 'callback_data': f'confirm:{draft}'}, {'text': '取消预览', 'callback_data': f'cancel:{draft}'}]]}
        elif command == 'progress_raw':
            self.route(update, 'progress')
            day = self.s.progress(uid, raw, source)
            text = f'{day} 阶段进度已保存；再次填写会替换该阶段，不累加。'
            from .rich import progress_view
            self.presentation, plain = progress_view(self.s, day, employee_uid=uid)
            text += '\n' + plain
        elif command in ('progress_view', 'progress_details', 'report_view', 'management_view', 'progress_plain', 'report_plain'):
            from .rich import progress_view, report_view, management_view
            plain_only=command.endswith('_plain')
            command={'progress_plain':'progress_view','report_plain':'report_view'}.get(command,command)
            parts = args[0].split(':') if args else [today]
            day = parts[0]
            page = int(parts[1] if len(parts)>1 else args[1] if len(args)>1 else 1)
            valid_day(day, self.c.now().date())
            topic='progress' if command.startswith('progress') else 'report' if command=='report_view' else 'management'
            self.route(update, topic, admin=command in ('progress_details','management_view'))
            if command.startswith('progress'):
                self.presentation, text = progress_view(self.s, day, page if command=='progress_details' else None)
                if command=='progress_details':
                    private=uid
                    self.presentation_audience='admin'
                    expected,_=self.s.roster(day)
                    pages=max(1,(len(expected)+4)//5)
                    buttons=[]
                    if page>1: buttons.append(dict(text='上一页',callback_data=f'progress_details:{day}:{page-1}'))
                    if page<pages: buttons.append(dict(text='下一页',callback_data=f'progress_details:{day}:{page+1}'))
                    markup={'inline_keyboard':[buttons]} if buttons else None
                elif self.s.admin(uid):
                    markup={'inline_keyboard':[[dict(text='员工明细（私聊）',callback_data=f'progress_details:{day}:1')]]}
            elif command=='report_view':
                result=self.s.summary(day)
                self.presentation=report_view(self.s,result)
                text=self.s.render_summary(result)
            else:
                result=self.s.summary(day)
                tasks=result['management']['tasks']
                pages=max(1,(len(tasks)+4)//5)
                if not 1<=page<=pages: raise ValueError('任务页码超出范围')
                result['management']['tasks']=tasks[(page-1)*5:page*5]
                self.presentation=management_view(self.s,result)
                self.presentation['blocks'][0]['text']+=f' · 第 {page}/{pages} 页'
                text=f'第 {page}/{pages} 页\n'+self.s.render_management(result)
                private=uid
                self.presentation_audience='admin'
                buttons=[]
                if page>1: buttons.append(dict(text='上一页',callback_data=f'management_view:{day}:{page-1}'))
                if page<pages: buttons.append(dict(text='下一页',callback_data=f'management_view:{day}:{page+1}'))
                markup={'inline_keyboard':[buttons]} if buttons else None
            if plain_only:
                self.presentation=None
                markup=None
        elif command == 'plain':
            chat=update.effective_chat
            snapshot=self.db.setting('display:'+args[0])
            if not snapshot: raise ValueError('普通文字查看码不存在或测试会话已失效')
            # Reading a saved view is not a private-only management mutation.
            if snapshot['audience']=='admin':self.s.require_admin(uid)
            if self.c.report_version>=2 and self.s.admin(uid):
                if chat.type!='private' and (chat.id!=self.c.chat or msg.message_thread_id not in (self.scheduler.api_thread('report'),self.scheduler.api_thread('progress'))):
                    raise PermissionError('请在工作群对应话题或机器人私聊查看')
            else:self.route(update, admin=snapshot['audience']=='admin')
            if snapshot.get('viewer') and snapshot['viewer']!=uid:
                raise PermissionError('此资料仅供原请求者查看')
            text=snapshot['text']
            private=uid
        elif command == 'confirm':
            self.route(update, 'report')
            text = self.s.confirm(uid, int(args[0]))
        elif command == 'cancel':
            self.route(update, 'report')
            self.db.run("UPDATE drafts SET state='cancelled' WHERE id=? AND uid=? AND state='draft'", (int(args[0]), uid))
            text = '预览已取消；当前有效日报保持原版本。'
        elif command == 'revoke':
            self.route(update, 'report')
            self.s.revoke(uid, uid, args[0], ' '.join(args[1:]))
            text = '已撤销；历史保留。删除群消息不会自动撤销。'
        elif command == 'approve':
            self.route(update, admin=True)
            self.s.approve(uid, int(args[0]))
            text = '员工已批准\n员工：'+self.s.display(int(args[0]))
        elif command == 'employees':
            self.route(update, admin=True)
            private = uid
            from .rich import heading,paragraph,table
            rows=self.db.all('SELECT * FROM employees ORDER BY code');page=int(args[0]) if args else 1;pages=max(1,(len(rows)+9)//10)
            if not 1<=page<=pages:raise ValueError('页码超出范围')
            cells=[[r['code'],r['uid'],('在职' if r['active'] else '离职')+' / '+('已批准' if r['approved'] else '待批准'),'需要' if r['required'] else '免交','管理员' if r['role']=='admin' else '员工'] for r in rows[(page-1)*10:page*10]]
            text='人员管理\n'+f'{len(rows)}人 · 第{page}/{pages}页\n\n'+'\n\n'.join(row[0]+'\n账号：'+str(row[1])+'\n状态：'+row[2]+'\n日报：'+row[3]+' · '+row[4] for row in cells) if cells else '人员管理\n暂无员工'
            self.presentation=dict(blocks=[heading('人员管理'),paragraph(f'{len(rows)}人 · 第{page}/{pages}页'),table(['员工','账号','状态','日报','身份'],cells)])
            self.presentation_audience='admin';nav=[]
            if page>1:nav.append(dict(text='上一页',callback_data='employees:'+str(page-1)))
            if page<pages:nav.append(dict(text='下一页',callback_data='employees:'+str(page+1)))
            markup=dict(inline_keyboard=[nav]) if nav else None
        elif command == 'employee_set':
            self.route(update, admin=True)
            field = args[1]
            value = int(args[2]) if field in ('active', 'required', 'approved') else args[2]
            self.s.change_employee(uid, int(args[0]), reason=' '.join(args[3:]), **{field: value})
            text = '员工信息已修改并记录原因'
        elif command == 'leave':
            self.route(update, admin=True)
            self.s.leave(uid, int(args[0]), args[1], int(args[2]), ' '.join(args[3:]))
            text = '当日请假/排除状态已更新'
        elif command == 'roster_set':
            self.route(update, admin=True)
            uids = [] if args[1] == '无' else [int(x) for x in args[1].split(',')]
            self.s.set_roster(uid, args[0], uids, ' '.join(args[2:]))
            text = '当日应交名单已由管理员核实保存，并记录原因'
        elif command == 'bind':
            self.route(update, admin=True)
            if update.effective_chat.type == 'private' or not update.effective_chat.is_forum:
                raise ValueError('请在指定群的已有话题内执行绑定')
            binding = msg.message_thread_id if msg.message_thread_id is not None else 'general'
            names = {'进度': 'progress', '培训': 'training', '管理': 'management', '日报': 'report'}
            topic = names[args[0]]
            if any(self.scheduler.thread(k) == binding for k in names.values() if k != topic):
                raise ValueError('四个板块必须绑定不同的话题')
            old = self.scheduler.thread(topic)
            self.db.set('thread:' + topic, binding)
            self.db.audit('话题绑定', topic, uid, '管理员在已有话题绑定', old, binding)
            text = f'{args[0]}话题已绑定：{binding}'
        elif command == 'schedule':
            self.route(update, admin=True)
            labels = ['上半场提醒', '上半场逾期', '下半场提醒', '下半场逾期', '日报开始', '日报截止', '汇总']
            times = '\n'.join(f'{label}：{clock}' for label, clock in zip(labels, self.c.times.values()))
            text = f'时区：{self.c.timezone}\n{times}\n工作日（0=周一）：{self.c.workdays}\n当前启用：{"是" if self.scheduler.schedule_enabled() else "否"}\n确认所有配置后执行：\n/schedule_confirm {self.c.timezone} {",".join(self.c.times.values())}\n停用：/schedule_off'
            text += '\n工作班次：上半场 12:00–17:00；下半场 19:00–次日 00:00，归开始当天。'
            if not self.c.report_times_confirmed:
                text += '\n上面的提醒及日报时间仅为旧示例，尚未获用户确认；启用命令会拒绝执行。'
        elif command == 'schedule_confirm':
            self.route(update, admin=True)
            self.scheduler.confirm(uid, args[0], args[1])
            text = '定时任务已确认启用；修改时间、时区、群或话题后需重新确认'
        elif command == 'schedule_off':
            self.route(update, admin=True)
            self.db.set('schedule_confirmed', None)
            # 尚未发送的定时提醒取消；显式的私聊导出不受影响。
            self.db.run("UPDATE outbox SET state='cancelled' WHERE state='pending' AND chat=?", (self.c.chat,))
            text = '定时任务已关闭，未发送群发件箱已取消'
        elif command == 'metric':
            self.route(update, admin=True)
            self.s.set_metric(uid, args[0], args[3], None if args[1] == '无' else int(args[1]), int(args[2]))
            text = '工作口径与目标已保存（用户 ID 0 表示团队目标）'
        elif command == 'definitions':
            self.route(update, 'progress')
            defaults = {'精聊': '符合团队有效客户标准并完成当阶段沟通的客户数；有效标准由团队制定', '引流': '从社交平台转入指定沟通软件的客户数', '维聊': '当日完成维护沟通的在聊客户数'}
            text = '\n'.join(f'{k}：{self.db.setting("metric:0:" + k, dict(definition=v))["definition"]}' for k, v in defaults.items()) + '\n上下半场填本阶段新增量；两阶段相加；三项不能相加称为独立客户数。'
        elif command == 'task_new':
            self.route(update, 'management', admin=True)
            task = self.s.new_task(uid, args[0], args[3], args[4], [int(x) for x in args[2].split(',')], args[1])
            row = self.s.task(task)
            from .rich import management_view
            result=self.s.summary(row['day'])
            result['management']['tasks']=[row]
            self.scheduler.enqueue(f'task:{task}', 'management', f'新任务 #{task} {row["title"]}\n工作日期：{row["day"]}\n说明：{row["description"]}\n截止：{row["deadline"]}\n负责人用户 ID：' + '、'.join(str(a['uid']) for a in row['assignments']), rich=management_view(self.s,result))
            text = f'任务 #{task} 已下发'
        elif command == 'tasks':
            self.route(update, 'management')
            rows = self.db.all('SELECT t.*,a.status,a.note,a.problem,a.next_step FROM tasks t JOIN assignments a ON a.task=t.id WHERE a.uid=? ORDER BY t.id DESC LIMIT 50', (uid,))
            text = '\n\n'.join(f'任务 #{r["id"]} {r["title"]}\n工作日期：{r["day"]}\n说明：{r["description"]}\n截止：{r["deadline"]}\n状态：{r["status"]}\n完成说明：{r["note"] or "待补"}\n问题：{r["problem"] or "待补"}\n下一步：{r["next_step"] or "待补"}' for r in rows) if rows else '暂无本人任务'
            private = uid
            from .rich import heading, listing
            self.presentation=dict(blocks=[heading('本人管理任务'),listing([r for r in text.split('\n\n')])])
            self.presentation_viewer=uid
        elif command == 'task_update':
            self.route(update, 'management')
            self.s.task_update(uid, int(args[0]), args[1], args[2], args[3] if len(args) > 3 else '', args[4] if len(args) > 4 else '')
            text = '本人任务状态已保存；所有负责人完成后才标记全部完成'
        elif command == 'task_edit':
            self.route(update, 'management', admin=True)
            edits = {'deadline': args[2]} if args[1] == 'deadline' else {'uids': [int(x) for x in args[2].split(',')]} if args[1] == 'uids' else {'closed': int(args[2])} if args[1] == 'closed' else None
            if edits is None:
                raise ValueError('可编辑 deadline、uids、closed')
            self.s.task_edit(uid, int(args[0]), reason=' '.join(args[3:]), **edits)
            text = '任务修改已保存并审计'
        elif command == 'summary':
            self.route(update, admin=True)
            day = args[0] if args else today
            valid_day(day, self.c.now().date())
            r = self.scheduler.publish(day, 'manual:' + str(update.update_id))
            text = '日报汇总已生成\n日期：'+day if self.c.report_version>=2 else f'汇总版本 {r["version"]} 已进入两个对应话题发件箱'
        elif command == 'public_note':
            self.route(update, admin=True)
            day = args[0]
            valid_day(day, self.c.now().date())
            note = ' '.join(args[1:])
            if not note or len(note) > 3000:
                raise ValueError('审核文字需为 1 至 3000 字；请只填写可公开且可外发的非敏感内容')
            if not self.ai.safe(note):
                raise ValueError('审核文字含敏感特征，请去除员工信息、客户信息及保密内容')
            old = self.db.setting('public_notes:' + day, '')
            self.db.set('public_notes:' + day, note)
            self.db.audit('公开文字', day, uid, '管理员明确审核为可公开及可外发', old, note)
            self.s.mark_dirty(day)
            text = '非敏感文字已审核保存；下一次汇总可选使用 AI，员工原文不会外发'
        elif command in ('export', 'employee_export', 'history'):
            self.route(update, admin=True)
            private = uid
            if command == 'employee_export':
                file, text = self.s.export_employees(uid), '员工名单 CSV'
            elif command == 'export':
                file = self.s.range_export(uid, args[0], args[1] if len(args) > 1 else args[0])
                text = '日报 CSV、JSON、Markdown 导出（包含员工文字，请妥善保管）'
            else:
                if len(args) != 2:
                    raise ValueError('/history 日报 用户ID:日期 或 /history 任务 编号')
                from .presentation import human_time
                rows=self.db.all('SELECT h.*,n.actor_name,n.target_name FROM history h LEFT JOIN audit_names n ON n.history_id=h.id WHERE entity=? AND entity_key=? ORDER BY h.id', args)
                text='修改历史\n'+args[0]+' · '+args[1]+'\n\n'+('\n\n'.join(human_time(r['created'],self.c)+('\n当时名称：'+r['target_name'] if r.get('target_name') else '')+'\n原因：'+r['reason']+'\n修改：'+history_change(r) for r in rows) or '暂无修改记录')
        elif command == 'correct':
            self.route(update, admin=True)
            header, _, body = raw.partition('\n')
            parts = shlex.split(header)[1:]
            version = self.s.correct(uid, int(parts[0]), parts[1], body, ' '.join(parts[2:]))
            text = f'更正完成，当前版本 {version}；前后值已留存'
        elif command == 'kb_list':
            self.route(update, 'training', admin=True)
            private = uid
            text = '\n'.join(f'资料 #{r["id"]} {r["name"]} · 版本 {r["version"]} · 启用 {r["active"]} · 允许外发 {r["external"]} · 导入 {r["imported"]}' for r in self.db.all('SELECT id,name,version,active,external,imported FROM documents ORDER BY id')) or '暂无资料'
        elif command == 'kb_set':
            self.route(update, 'training', admin=True)
            self.kb.change(uid, int(args[0]), args[1])
            text = '资料状态已更新。外发允许应只用于逐项人工核实的非敏感资料。'
        elif command == 'kb_reindex':
            self.route(update, 'training', admin=True)
            text = self.kb.reindex(uid)
        elif command == 'ai_status':
            self.route(update, admin=True)
            private = uid
            text = f'启用：{self.c.ai_enabled}，免费项目已确认：{self.c.free_confirmed}，模型：{self.c.model or "未配置"}\n' + dumps(self.db.all('SELECT day,status,COUNT(*) AS 次数 FROM ai_calls GROUP BY day,status ORDER BY day DESC LIMIT 30'))
        elif command == 'outbox_retry':
            self.route(update, admin=True)
            self.db.run("UPDATE outbox SET next_try=?,state='pending' WHERE id=? AND state<>'sent'", (self.c.now().isoformat(), int(args[0])))
            text = '已安排重试（已发送记录不会重复发送）'
        else:
            raise ValueError('未知命令；请查看 /help 或中文使用说明')
        if private and not self.db.setting(f'private_started:{private}', False):
            raise ValueError('请先在机器人私聊发送 /start，再执行此操作')
        return text, markup, file, private

    async def diagnose(self, update, bot):
        self.route(update, admin=True)
        me = await bot.get_me()
        chat = await bot.get_chat(self.c.chat)
        membership = await bot.get_chat_member(self.c.chat, me.id)
        lines = [f'目标群：{chat.id}，话题群：{bool(chat.is_forum)}', f'机器人身份：{membership.status}', f'普通群消息接收（Bot API 声明）：{bool(me.can_read_all_group_messages)}', '若隐私模式启用：私聊 @BotFather，/setprivacy → 选择此机器人 → Disable；按 Telegram 提示重新加入群。无需授予删除消息或管理成员权限。', '普通员工消息实际探测：员工在进度话题发送“接收测试”；收到回执才证明实际收到了普通消息。']
        for topic in self.c.threads:
            label = dict(progress='工作进度', training='培训', management='管理任务', report='日报')[topic]
            thread = self.scheduler.thread(topic)
            if not thread:
                lines.append(f'{label}：未绑定')
                continue
            try:
                await bot.send_message(chat_id=self.c.chat, message_thread_id=self.scheduler.api_thread(topic), text='诊断：此话题文字发送正常')
                lines.append(f'{label}：文字发送通过')
            except Exception as e:
                lines.append(f'{label}：文字发送失败（{type(e).__name__}）')
        try:
            import io
            await bot.send_document(chat_id=self.c.chat, message_thread_id=self.scheduler.api_thread('report'), document=io.BytesIO('机器人文件发送测试'.encode()), filename='权限诊断.txt')
            lines.append('文件发送通过')
        except Exception as e:
            lines.append(f'文件发送失败（{type(e).__name__}）')
        lines.append('若发送受限：群设置→权限，允许发送消息和文件；话题需开放。话题共享成员，不能做独立访问隔离。')
        return '\n'.join(lines)

    async def handle(self, update, context):
        import time,re
        started=time.perf_counter();received=stamp();profile_seconds=0
        from .acceptance_views import route as display_route
        if await display_route(self,update,context):return
        from .onboarding import preflight
        if await preflight(self,update,context):
            if self.c.report_version>=2 and update.effective_message:
                from .daily import daily_intent
                if daily_intent(update.effective_message.text or ''):
                    await self.scheduler.flush(context.bot,[f'update:{update.update_id}'])
            return
        gate_seconds=time.perf_counter()-started
        if self.c.report_version>=2 and update.effective_message and update.effective_chat and update.effective_chat.type!='private' and update.effective_chat.id==self.c.chat and self.scheduler.thread('management') and update.effective_message.message_thread_id==self.scheduler.api_thread('management'):
            return # No callback answers or messages in the retired management topic.
        from .progress_ui import handle as progress_handle
        if await progress_handle(self,update,context):return
        if not update.effective_user or not update.effective_message:
            return
        if self.db.one('SELECT id FROM updates WHERE id=?', (update.update_id,)):
            return
        msg, uid = update.effective_message, update.effective_user.id
        chat = update.effective_chat
        if chat.type != 'private' and chat.id != self.c.chat and not (msg.text or '').startswith('/whereami'):
            return
        raw = msg.text or ''
        key = f'update:{update.update_id}'
        ack_task=None;ack_measure={};callback_seconds=0
        try:
            head = raw.split()[0].split('@')[0][1:].lower() if raw.startswith('/') else ''
            if not self.c.test_run and self.db.setting('isolation_only_run',False):
                session=self.db.setting('live_test')
                if not session or uid not in session['participants']:return
                if msg.document or head in ('materials','material_get','ask','ask_plain','kb_list','kb_set','kb_reindex','test_begin','test_allow','test_join','test_end','test_close'):return
                if chat.type!='private' and msg.message_thread_id==self.scheduler.api_thread('training'):return
            if not self.c.test_run and head in ('test_begin', 'test_allow', 'test_join', 'test_end', 'test_close'):
                from .live_test import control
                with self.db.transaction():
                    text = control(self, update, head, shlex.split(raw)[1:])
                    self.scheduler.reply(key, chat.id, msg.message_thread_id, text)
                    self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
                return
            if not self.c.test_run:
                pinned = self.db.setting('test_update:' + str(update.update_id))
                if pinned:
                    from .live_test import sandbox
                    child = sandbox(self, pinned)
                    await child.handle(update, context)
                    if child.db.one('SELECT id FROM updates WHERE id=?', (update.update_id,)):
                        self.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)', (update.update_id, stamp()))
                    return
                state = self.db.setting('live_test')
                callback_data = update.callback_query.data if update.callback_query else ''
                if callback_data and callback_data.startswith('test:') and (not state or uid not in state['participants'] or not callback_data.startswith('test:' + state['run'] + ':')):
                    await update.callback_query.answer('测试会话已退出或失效，请重新预览。', show_alert=True)
                    return
                if state and uid in state['participants'] and (self.db.setting('isolation_only_run',False) or head not in ('whereami', 'bind', 'diagnose', 'schedule', 'schedule_off', 'schedule_confirm')):
                    from .live_test import sandbox
                    self.db.set('test_update:' + str(update.update_id), state)
                    child = sandbox(self, state)
                    await child.handle(update, context)
                    if child.db.one('SELECT id FROM updates WHERE id=?', (update.update_id,)):
                        self.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)', (update.update_id, stamp()))
                    return
            if update.callback_query:
                data = update.callback_query.data or ''
                daily_data=data.removeprefix(f'test:{self.c.test_run}:') if self.c.test_run else data
                if self.c.report_version>=2 and daily_data.split(':')[0] in ('template','report_view','report_page','daily_confirm','daily_cancel'):
                    async def ack():
                        t=time.perf_counter()
                        try:await update.callback_query.answer()
                        except Exception:pass # Callback acknowledgement cannot undo a committed report.
                        finally:ack_measure['seconds']=round(time.perf_counter()-t,4)
                    ack_task=asyncio.create_task(ack())
                else:
                    t=time.perf_counter();await update.callback_query.answer();callback_seconds=time.perf_counter()-t
                if self.c.test_run:
                    prefix = f'test:{self.c.test_run}:'
                    if not data.startswith(prefix):
                        raise ValueError('测试会话不处理正式按钮；请退出测试后操作正式预览')
                    data = data[len(prefix):]
                command, _, suffix = data.partition(':')
                args = [suffix] if suffix else []
            elif raw.startswith('/'):
                command = raw.split()[0][1:].split('@')[0].lower()
                args = shlex.split(raw.split('\n')[0])[1:]
            elif msg.document:
                self.route(update, 'training', admin=True)
                document = msg.document
                filename = Path(document.file_name or '').name
                if self.c.training_mode=='directory' and filename in self.db.setting('training_excluded_names',[]):
                    raise ValueError('该文件未获准接入资料分发；不会下载或读取正文')
                caption = msg.caption or ''
                is_csv = filename.lower().endswith('.csv') and caption == '/employee_import'
                if not is_csv and Path(filename).suffix.lower() not in ('.txt', '.md', '.docx', '.pdf'):
                    raise ValueError('上传类型不支持；名单 CSV 需附 /employee_import')
                if not document.file_size or document.file_size > self.c.max_file_mb * 1024 * 1024:
                    raise ValueError('文件大小未知或超出限制')
                folder = self.c.data / 'uploads' / str(update.update_id)
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / filename
                remote = await document.get_file()
                await remote.download_to_drive(target)
                try:
                    with self.db.transaction():
                        if is_csv:
                            count = self.s.import_employees(uid, target.read_text(encoding='utf-8-sig'))
                            reply = f'员工名单导入完成：{count} 条'
                        else:
                            if self.c.training_mode=='directory':
                                from .catalog import Catalog
                                doc,version=Catalog(self.s).add(uid,target)
                            else:
                                doc, version = self.kb.import_file(uid, target)
                            reply = f'资料 #{doc} v{version} 已登记目录；未解析或索引正文' if self.c.training_mode=='directory' else f'资料 #{doc} v{version} 已导入；默认仅本地使用'
                        self.scheduler.reply(key, chat.id, msg.message_thread_id, reply)
                        self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
                finally:
                    target.unlink(missing_ok=True)
                return
            elif self.c.report_version>=2 and raw.strip() and raw.strip().split(maxsplit=1)[0] in ('日报模板','查看日报','导出日报表','导出历史报表','导出日报历史'):
                parts=raw.strip().split();command={'日报模板':'template','查看日报':'report_view','导出日报表':'daily_export','导出历史报表':'daily_export','导出日报历史':'daily_export'}[parts[0]];args=parts[1:]
                if len(args)>(1 if command=='template' else 2):raise ValueError('请发送“日报模板 日期”或“查看日报 日期 页码”')
            elif self.c.report_version>=2 and __import__('team_bot.daily',fromlist=['daily_intent']).daily_intent(raw):
                command,args='report_raw',[]
            elif self.c.report_version>=2 and chat.type=='private' and self.db.setting('wb_input:'+str(uid)):
                command,args='wb_input',[]
            elif '日期' in raw and ('上半场精聊新增' in raw or '员工编号' in raw):
                command, args = 'report_raw', []
            elif '日期' in raw and '阶段' in raw:
                command, args = 'progress_raw', []
            elif raw == '接收测试':
                self.route(update, 'progress')
                self.db.set('ordinary_message_received', stamp())
                with self.db.transaction():
                    self.scheduler.reply(key, chat.id, msg.message_thread_id, '已收到普通员工消息；接收测试通过。')
                    self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
                return
            elif msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == context.bot.id and self.scheduler.thread('training') and msg.message_thread_id == self.scheduler.api_thread('training'):
                command, args = 'ask', [raw]
            else:
                return
            if not self.c.owner and command not in ('start', 'help', 'whereami'):
                raise PermissionError('当前为查看 ID 模式，只支持 /start、/help、/whereami；管理员必须在本地确认 OWNER_USER_ID，首位使用者不会获得权限')
            if not self.c.owner and command in ('start', 'help'):
                with self.db.transaction():
                    if chat.type == 'private':
                        self.db.set(f'private_started:{uid}', True)
                    self.scheduler.reply(key, chat.id, msg.message_thread_id, '当前为查看 ID 模式。请发送 /whereami；群和话题中可发送 /whereami 并添加本公司机器人用户名。管理员、群与启用话题 ID 需在本地确认后才开放业务功能；不会自动授权首位用户。')
                    self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
                return
            if command in ('ask','ask_plain'):
                self.route(update, 'training')
                if self.c.training_mode=='directory':
                    self.scheduler.reply(key,chat.id,msg.message_thread_id,'正文问答已停用，不读取资料或调用模型。请 /materials 查看目录，/material_get 编号 下载原文件。')
                    self.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)',(update.update_id,stamp()))
                    return
                query = ' '.join(args).strip()
                if not query or len(query) > 1000:
                    raise ValueError('问题需为 1 至 1000 个字符')
                hits = self.kb.search(query)
                # 知识原文可能机密，问答结果只私聊请求者，不群发。
                if not self.db.setting(f'private_started:{uid}', False):
                    raise ValueError('请先私聊机器人 /start；培训答案与资料原文通过私聊返回')
                answer = await self.ai.ask(query, hits)
                with self.db.transaction():
                    from .rich import training_view
                    self.scheduler.reply(key, uid, None, answer, rich=training_view(answer,hits) if command=='ask' else None,viewer=uid)
                    self.scheduler.reply(key + ':receipt', chat.id, msg.message_thread_id, '培训检索结果已发送到你的机器人私聊。')
                    self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
            elif command == 'diagnose':
                answer = await self.diagnose(update, context.bot)
                with self.db.transaction():
                    self.scheduler.reply(key, uid if chat.type == 'private' else chat.id, msg.message_thread_id, answer)
                    self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
            else:
                if command=='report_view' and self.s.admin(uid) and self.c.identity_mode=='telegram':
                    from .daily_history import refresh_profiles
                    profile_started=time.perf_counter()
                    await refresh_profiles(self.s,context.bot)
                    profile_seconds=time.perf_counter()-profile_started
                if command=='daily_export':
                    self.route(update,admin=True)
                    from .daily_history import ensure_export
                    if not await ensure_export(self.s,force=True):raise ValueError('日报表正在更新或被占用，请关闭Excel后重试；日报已保留')
                with self.db.transaction():
                    text, markup, file, private = self.dispatch(update, command, args, raw)
                    if text is not None:self.scheduler.reply(key, private or chat.id, None if private else msg.message_thread_id, text, markup, file, rich=self.presentation, audience=self.presentation_audience, viewer=self.presentation_viewer, filename=self.presentation_filename,literal_daily=self.c.report_version>=2 and command in ('report_view','report_page','report_plain','template','my_daily'),reply_to=getattr(self,'query_reply_to',None))
                    if self.c.report_version>=2 and getattr(self,'edit_target',None):
                        self.db.set('edit:'+key+':0',self.edit_target)
                        for old_edit in self.db.all("SELECT key,value FROM settings WHERE key LIKE 'edit:%'"):
                            if old_edit['key']!='edit:'+key+':0' and json.loads(old_edit['value'])==self.edit_target:
                                self.db.run("UPDATE outbox SET state='cancelled' WHERE key=? AND chat=? AND state='pending'",(old_edit['key'][5:],private or chat.id))
                    if self.c.report_version>=2 and getattr(self,'pending_card',None):
                        u,t,d,target=self.pending_card
                        self.db.run('INSERT OR IGNORE INTO check_cards(uid,task,day,chat) VALUES (?,?,?,?)',(u,t,d,target))
                        self.db.set('card:'+key+':0',dict(uid=u,task=t,day=d))
                    if getattr(self,'pending_workbench',None):
                        card=self.pending_workbench
                        self.db.run('INSERT OR IGNORE INTO wb_cards(kind,viewer,day,chat) VALUES (?,?,?,?)',(card['kind'],card['viewer'],card['day'],card['chat']))
                        self.db.set('wbcard:'+key+':0',card)
                    self.db.run('INSERT INTO updates VALUES (?,?)', (update.update_id, stamp()))
                    if private and chat.type != 'private':
                        self.scheduler.reply(key + ':receipt', chat.id, msg.message_thread_id, '请到机器人私聊查看。')
        except (ValueError, PermissionError, IndexError, KeyError) as e:
            text = str(e) if not isinstance(e, (IndexError, KeyError)) else '参数不完整或格式错误，请查看中文管理员/员工说明'
            with self.db.transaction():
                self.scheduler.reply(key + ':error', chat.id, msg.message_thread_id, text if locals().get('command')=='report_raw' else '无法完成\n'+text)
                self.db.run('INSERT OR IGNORE INTO updates VALUES (?,?)', (update.update_id, stamp()))
        except Exception as e:
            logging.getLogger(__name__).error('处理更新失败：%s', type(e).__name__)
            # 不记录正文或异常 URL；失败更新不入账，保留可重试状态。
            self.scheduler.reply(key + ':error', chat.id, msg.message_thread_id, '暂时处理失败，请稍后重试或联系管理员查看诊断。')
        finally:
            if self.c.report_version>=2 and locals().get('command') in ('template','report_view','report_page','report_plain','report_raw','daily_confirm','daily_cancel','daily_export'):
                queued=time.perf_counter()
                await self.scheduler.flush(context.bot,[key])
                if command in ('report_view','report_page'):
                    from .daily_history import query_delivered
                    query_delivered(self.s)
                if command=='report_raw':
                    from .daily_history import flush_feedback
                    await flush_feedback(self.s,context.bot,[str(update.update_id)])
                completed=time.perf_counter()
                if ack_task:await ack_task
                self.db.set('daily_timing:'+str(update.update_id),dict(received=received,telegram_date=msg.date.isoformat() if not update.callback_query else None,command=command,preflight_seconds=round(gate_seconds,4),membership_seconds=self.db.setting('membership_timing:'+str(uid),{}).get('seconds'),profile_api_seconds=round(profile_seconds,4),callback_seconds=ack_measure.get('seconds',round(callback_seconds,4)),local_seconds=round(queued-started-gate_seconds-callback_seconds-profile_seconds,4),receive_to_sent_seconds=round(completed-started,4),sent=bool(self.db.one("SELECT id FROM outbox WHERE key LIKE ? AND state='sent'",(key+':%',))) or self.db.setting('daily_feedback:'+str(update.update_id),{}).get('state')=='sent',uid=uid))
                if command=='report_raw':self.scheduler.request_daily_refresh(context.bot)
            elif ack_task:await ack_task


def run(service):
    from telegram.ext import Application, TypeHandler
    from telegram import Update, BotCommand
    from .maintenance import InstanceLock
    if not service.c.token:
        raise ValueError('请先配置 TELEGRAM_BOT_TOKEN')
    adapter = Adapter(service)
    if service.c.run_mode=='trial' and service.c.test_run=='newgroup' and (service.c.data/'mock30-v2'/'team.sqlite3').exists():
        from .acceptance_views import display_adapter
        display_adapter(adapter)
    async def startup(app):
        from .process_control import watch_stop
        app.bot_data['process_stop_task'] = asyncio.create_task(watch_stop(app, service.c.data))
        service.c.bot_username=app.bot.username
        commands = [BotCommand('start', '开始使用'), BotCommand('whereami', '查看本人、群和话题 ID'), BotCommand('help', '使用帮助')]
        if service.c.owner:
            commands += [BotCommand('menu', '显示操作按钮'), BotCommand('template', '日报模板'), BotCommand('progress_template', '进度模板'), BotCommand('ask', '培训资料查询'), BotCommand('tasks', '查看我的任务')]
            commands += [BotCommand('progress_view','查看进度总表'),BotCommand('report_view','查看日报总表'),BotCommand('plain','查看普通文字版本')]
            commands += [BotCommand('progress_plain','进度普通文字'),BotCommand('report_plain','日报普通文字'),BotCommand('ask_plain','培训普通文字查询')]
            if service.c.training_mode=='directory':
                commands=[item for item in commands if item.command not in ('ask','ask_plain')]
                commands += [BotCommand('materials','资料目录'),BotCommand('material_get','原文件私聊下载')]
        if service.c.report_version>=2:
            commands=[BotCommand(k,v) for k,v in [('start','开始'),('help','新版帮助'),('whereami','查看ID'),('register','申请登记'),('template','每日四项日报'),('report_view','新版日报表格'),('report_plain','普通文字日报'),('report_legacy','旧版记录'),('work','进度反馈入口'),('team_work','团队进度入口'),('progress_view','查看本轮进度'),('materials','资料目录'),('material_get','原文件下载')]]
        if service.c.identity_mode=='telegram':commands=[item for item in commands if item.command not in ('register','materials','material_get')]
        await app.bot.set_my_commands(commands)
        for old_input in service.db.all("SELECT key FROM settings WHERE key LIKE 'wb_input:%'"):
            service.db.set(old_input['key'],None)
        from .daily_history import initialize
        initialize(service)
        from .daily_history import prime_feedback
        await prime_feedback(service,app.bot)
        service.db.set('runtime', dict(state='初始化成功，等待 polling 响应', started=stamp(), bootstrap=not bool(service.c.owner)))
        app.bot_data['scheduler_task'] = asyncio.create_task(adapter.scheduler.loop(app, lambda: list(adapter.test_adapters.values())))
        if service.c.identity_mode=='telegram':
            from .member_registry import watch as member_watch
            app.bot_data['member_task']=asyncio.create_task(member_watch(service,app.bot))
        from .miniapp_server import start
        await start(app,service)
        from .miniapp_bridge import start as start_bridge
        await start_bridge(app,service)
        from .desktop_sync import start as start_desktop_sync
        await start_desktop_sync(app,service)

    async def shutdown(app):
        process_stop = app.bot_data.pop('process_stop_task', None)
        if process_stop:
            process_stop.cancel()
            await asyncio.gather(process_stop, return_exceptions=True)
        from .desktop_sync import stop as stop_desktop_sync
        await stop_desktop_sync(app)
        member_task=app.bot_data.pop('member_task',None)
        if member_task:
            member_task.cancel()
            await asyncio.gather(member_task,return_exceptions=True)
        from .miniapp_bridge import stop as stop_bridge
        await stop_bridge(app)
        from .miniapp_server import stop
        await stop(app)
        task = app.bot_data.get('scheduler_task')
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def error(update, context):
        logging.getLogger(__name__).error('Telegram 错误：%s', type(context.error).__name__)

    from telegram.request import HTTPXRequest
    class PollingRequest(HTTPXRequest):
        async def do_request(self, *args, **kwargs):
            result = await super().do_request(*args, **kwargs)
            url = kwargs.get('url', args[0] if args else '')
            if str(url).endswith('/getUpdates') and result[0] == 200:
                response = json.loads(result[1])
                if response.get('ok'):
                    service.db.set('polling_last_success', dict(received=stamp(), updates=len(response.get('result', []))))
            return result

    with InstanceLock(service.c.data), InstanceLock(__import__('team_bot.config',fromlist=['ROOT']).ROOT/'data'/'bot-global-lock'):
        application = Application.builder().token(service.c.token).request(HTTPXRequest(connect_timeout=20, read_timeout=35, connection_pool_size=16, pool_timeout=3)).get_updates_request(PollingRequest(connect_timeout=20, read_timeout=35)).concurrent_updates(False).post_init(startup).post_stop(shutdown).build()
        application.add_handler(TypeHandler(Update, adapter.handle))
        application.add_error_handler(error)
        # 保留待收取更新，禁止 drop_pending_updates 丢弃未确认员工提交。
        application.run_polling(allowed_updates=['message', 'edited_message', 'callback_query','chat_member','my_chat_member','chat_join_request'], drop_pending_updates=False)
