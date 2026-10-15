import csv
import io
import json
from datetime import datetime, timedelta, time
from pathlib import Path
from .db import DB, dumps, stamp
from .reports import NUMERIC, TEXT, FIELDS, METRICS, parse_report, parse_progress, valid_day


def safe_cell(value):
    text = '' if value is None else str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


class LegacyService:
    def __init__(self, config, db=None):
        self.c = config
        self.db = db or DB(config.data / 'team.sqlite3')
        for folder in ('materials', 'exports', 'logs'):
            (config.data / folder).mkdir(parents=True, exist_ok=True)

    def preset_personnel(self):
        with self.db.transaction():
            for prefix, count, kind in [('Y', 30, 'employee'), ('G', 10, 'management')]:
                for i in range(1, count + 1):
                    self.db.run('INSERT OR IGNORE INTO personnel_slots(code,kind) VALUES (?,?)', (f'{prefix}{i:03}', kind))
            self.db.set('preset_personnel', True)

    def admin(self, uid):
        row = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
        return bool(self.c.owner and uid == self.c.owner) or bool(row and row['active'] and row['approved'] and row['role'] == 'admin')

    def require_admin(self, uid):
        if not self.admin(uid):
            raise PermissionError('此操作仅管理员可用')

    def employee(self, uid):
        row = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
        if not row or not row['approved'] or not row['active']:
            raise PermissionError('请先加入工作群；核实有效普通成员后自动开通，校验失败可重试' if self.c.identity_mode=='telegram' else '请先 /register 人员编号，等待管理员批准；不需要真实姓名')
        return row

    def register(self, uid, code, name=''):
        name = name.strip() or code
        if not code.strip() or not name.strip():
            raise ValueError('员工编号和姓名不能为空')
        if self.db.one('SELECT uid FROM employees WHERE uid=?', (uid,)):
            raise ValueError('已登记，请联系管理员修改')
        if self.db.setting('preset_personnel') and not self.db.one('SELECT code FROM personnel_slots WHERE code=?', (code,)):
            raise ValueError('请使用预置编号 Y001–Y030 或 G001–G010')
        with self.db.transaction():
            if self.db.one('SELECT uid FROM employees WHERE code=?', (code,)):
                raise ValueError('该人员编号已申请绑定，请联系管理员核实')
            slot = self.db.one('SELECT kind FROM personnel_slots WHERE code=?', (code,))
            required = 0 if slot and slot['kind'] == 'management' else 1
            self.db.run('INSERT INTO employees(uid,code,name,required) VALUES (?,?,?,?)', (uid, code, name, required))
            self.db.run('UPDATE personnel_slots SET uid=? WHERE code=?', (uid, code))

    def approve(self, actor, uid):
        self.require_admin(actor)
        self.change_employee(actor, uid, approved=1, reason='管理员批准登记')

    def change_employee(self, actor, uid, reason, **values):
        self.require_admin(actor)
        if set(values) - {'code', 'name', 'active', 'required', 'role', 'approved'}:
            raise ValueError('不支持的员工字段')
        if 'role' in values and values['role'] not in ('admin', 'employee'):
            raise ValueError('角色只能是 admin 或 employee')
        for k in ('active', 'required', 'approved'):
            if k in values and values[k] not in (0, 1):
                raise ValueError('状态只能为 0 或 1')
        with self.db.transaction():
            old = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
            if not old:
                raise ValueError('员工不存在')
            if 'code' in values and values['code'] != old['code']:
                slot = self.db.one('SELECT * FROM personnel_slots WHERE code=?', (values['code'],))
                if self.db.setting('preset_personnel') and not slot:
                    raise ValueError('新编号必须在预置人员名单内')
                if slot and slot['uid'] not in (None, uid):
                    raise ValueError('新编号已经申请绑定其他用户')
                self.db.run('UPDATE personnel_slots SET uid=NULL WHERE uid=?', (uid,))
                self.db.run('UPDATE personnel_slots SET uid=? WHERE code=?', (uid, values['code']))
            for k, v in values.items():
                self.db.run(f'UPDATE employees SET {k}=? WHERE uid=?', (v, uid))
            self.db.audit('员工', uid, actor, reason, old, self.db.one('SELECT * FROM employees WHERE uid=?', (uid,)))
            today = self.c.business_date().isoformat()
            snapshot = self.db.one('SELECT * FROM attendance WHERE day=?', (today,))
            if snapshot:
                updated = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
                expected = set(json.loads(snapshot['uids']))
                leaves = set(json.loads(snapshot['leave_uids']))
                if updated['active'] and updated['required'] and updated['approved'] and uid not in leaves:
                    expected.add(uid)
                else:
                    expected.discard(uid)
                self.db.run('UPDATE attendance SET uids=? WHERE day=?', (dumps(sorted(expected)), today))
                self.mark_dirty(today)

    def import_employees(self, actor, content):
        self.require_admin(actor)
        rows = list(csv.DictReader(io.StringIO(content.lstrip('\ufeff'))))
        with self.db.transaction():
            for r in rows:
                uid = int(r['用户ID'])
                if uid <= 0:
                    raise ValueError('用户 ID 必须为正整数')
                active, required = int(r['在职']), int(r['需要日报'])
                if active not in (0, 1) or required not in (0, 1) or not r['员工编号']:
                    raise ValueError('CSV 员工字段无效')
                name = r.get('显示名') or r.get('姓名') or r['员工编号']
                old = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
                slot = self.db.one('SELECT * FROM personnel_slots WHERE code=?', (r['员工编号'],))
                if self.db.setting('preset_personnel') and not slot:
                    raise ValueError('CSV 编号必须在预置人员名单内')
                if slot and slot['uid'] not in (None, uid):
                    raise ValueError('CSV 编号已申请绑定其他用户')
                self.db.run('INSERT INTO employees(uid,code,name,active,required,approved) VALUES (?,?,?,?,?,1) ON CONFLICT(uid) DO UPDATE SET code=excluded.code,name=excluded.name,active=excluded.active,required=excluded.required,approved=1', (uid, r['员工编号'], name, active, required))
                self.db.run('UPDATE personnel_slots SET uid=NULL WHERE uid=?', (uid,))
                self.db.run('UPDATE personnel_slots SET uid=? WHERE code=?', (uid, r['员工编号']))
                self.db.audit('员工', uid, actor, 'CSV 导入', old, self.db.one('SELECT * FROM employees WHERE uid=?', (uid,)))
        return len(rows)

    def export_employees(self, actor):
        self.require_admin(actor)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(['员工编号', '姓名', '用户ID', '在职', '需要日报'])
        for r in self.db.all('SELECT * FROM employees ORDER BY code'):
            w.writerow([safe_cell(r[k]) for k in ('code', 'name', 'uid', 'active', 'required')])
        path = self.c.data / 'exports/employees.csv'
        path.write_text(out.getvalue(), encoding='utf-8-sig')
        return path

    def leave(self, actor, uid, day, excluded, reason):
        self.require_admin(actor)
        valid_day(day, self.c.now().date() + timedelta(days=365))
        if not self.db.one('SELECT uid FROM employees WHERE uid=?', (uid,)):
            raise ValueError('员工不存在')
        if excluded not in (0, 1):
            raise ValueError('请假状态只能为 0 或 1')
        with self.db.transaction():
            old = self.db.one('SELECT * FROM leave_days WHERE uid=? AND day=?', (uid, day))
            self.db.run('INSERT OR REPLACE INTO leave_days VALUES (?,?,?)', (uid, day, excluded))
            self.db.audit('请假', f'{uid}:{day}', actor, reason, old, dict(uid=uid, day=day, excluded=excluded))
            # 已生成的当日名册，明确请假变动同步到快照。
            snapshot = self.db.one('SELECT * FROM attendance WHERE day=?', (day,))
            if snapshot:
                expected, leaves = set(json.loads(snapshot['uids'])), set(json.loads(snapshot['leave_uids']))
                if excluded:
                    expected.discard(uid)
                    leaves.add(uid)
                else:
                    leaves.discard(uid)
                    emp = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
                    if emp['active'] and emp['required'] and emp['approved']:
                        expected.add(uid)
                self.db.run('UPDATE attendance SET uids=?,leave_uids=? WHERE day=?', (dumps(sorted(expected)), dumps(sorted(leaves)), day))
            self.mark_dirty(day)

    def roster(self, day):
        cached = self.db.one('SELECT * FROM attendance WHERE day=?', (day,))
        if cached:
            return json.loads(cached['uids']), json.loads(cached['leave_uids'])
        eligible = [r['uid'] for r in self.db.all('SELECT uid FROM employees WHERE active=1 AND required=1 AND approved=1')]
        leaves = [r['uid'] for r in self.db.all('SELECT uid FROM leave_days WHERE day=? AND excluded=1', (day,)) if r['uid'] in eligible]
        expected = sorted(set(eligible) - set(leaves))
        self.db.run('INSERT OR IGNORE INTO attendance VALUES (?,?,?)', (day, dumps(expected), dumps(leaves)))
        self.db.set('roster_warning:' + day, '历史应交名单尚未核实，当前使用现有名单推定，请管理员核对' if day < self.c.business_date().isoformat() else '')
        return expected, leaves

    def set_roster(self, actor, day, uids, reason):
        self.require_admin(actor)
        valid_day(day, self.c.now().date())
        for uid in uids:
            if not self.db.one('SELECT uid FROM employees WHERE uid=?', (uid,)):
                raise ValueError('名单包含未登记用户 ID')
        with self.db.transaction():
            old = self.db.one('SELECT * FROM attendance WHERE day=?', (day,))
            leaves = [r['uid'] for r in self.db.all('SELECT uid FROM leave_days WHERE day=? AND excluded=1', (day,))]
            value = dict(day=day, uids=dumps(sorted(set(uids) - set(leaves))), leave_uids=dumps(leaves))
            self.db.run('INSERT OR REPLACE INTO attendance VALUES (:day,:uids,:leave_uids)', value)
            self.db.set('roster_warning:' + day, '')
            self.db.audit('当日名册', day, actor, reason, old, value)
            self.mark_dirty(day)

    def missing(self, day, phase=None):
        expected, _ = self.roster(day)
        result = []
        for uid in expected:
            emp = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
            if not emp['active'] or not emp['approved'] or not emp['required']:
                continue
            row = self.db.one('SELECT * FROM records WHERE uid=? AND day=?', (uid, day))
            if phase:
                keys = [phase + m + '新增' for m in METRICS]
                incomplete = not row or any(json.loads(row['payload']).get(k) is None for k in keys)
            else:
                incomplete = not row or not row['confirmed']
            if incomplete:
                result.append(emp['code'])
        return result

    def record(self, uid, day):
        return self.db.one('SELECT * FROM records WHERE uid=? AND day=?', (uid, day))

    def mark_dirty(self, day):
        if self.db.one('SELECT day FROM summaries WHERE day=?', (day,)):
            self.db.run('INSERT OR REPLACE INTO dirty VALUES (?,?)', (day, (self.c.now() + timedelta(seconds=self.c.repost_delay)).isoformat()))

    def write_record(self, uid, day, payload, actor, reason, raw, source, confirmed, now=None):
        old = self.record(uid, day)
        version = old['version'] + 1 if old else 1
        now = now or self.c.now()
        deadline = self.c.event_time(datetime.fromisoformat(day).date(), 'deadline')
        late = (int(now > deadline) if self.c.report_times_confirmed else -1) if confirmed else 0
        row = dict(uid=uid, day=day, payload=dumps(payload), confirmed=int(confirmed), version=version, late=late, raw=raw, source=source, updated=now.isoformat())
        self.db.run('INSERT OR REPLACE INTO records VALUES (:uid,:day,:payload,:confirmed,:version,:late,:raw,:source,:updated)', row)
        self.db.audit('日报', f'{uid}:{day}', actor, reason, old, row)
        self.mark_dirty(day)
        return version

    def progress(self, uid, raw, source):
        self.employee(uid)
        day, patch = parse_progress(raw, self.c.now().date())
        with self.db.transaction():
            old = self.record(uid, day)
            if old and old['confirmed']:
                raise ValueError('当日日报已确认，请编辑日报并重新预览确认')
            payload = json.loads(old['payload']) if old else {k: None for k in FIELDS}
            payload.update(patch)
            self.write_record(uid, day, payload, uid, '本人阶段进度填报', raw, source, False)
        return day

    def draft(self, uid, raw, source):
        emp = self.employee(uid)
        day, payload = parse_report(raw, self.c.now().date(), emp['code'])
        if self.c.report_times_confirmed and day == self.c.business_date().isoformat() and self.c.now() < self.c.event_time(datetime.fromisoformat(day).date(), 'open'):
            raise ValueError('日报提交窗口未开始；可先填写阶段进度')
        with self.db.transaction():
            old = self.record(uid, day)
            # 编辑相同来源使旧预览失效；重启后预览仍可恢复。
            self.db.run("UPDATE drafts SET state='superseded' WHERE uid=? AND day=? AND state='draft'", (uid, day))
            cur = self.db.run('INSERT INTO drafts(uid,day,payload,raw,source,base_version,created) VALUES (?,?,?,?,?,?,?)', (uid, day, dumps(payload), raw, source, old['version'] if old else 0, stamp()))
            return cur.lastrowid, day, payload

    def confirm(self, uid, draft_id, now=None):
        self.employee(uid)
        with self.db.transaction():
            draft = self.db.one('SELECT * FROM drafts WHERE id=? AND uid=?', (draft_id, uid))
            if not draft:
                raise PermissionError('无法确认他人的日报')
            if draft['state'] == 'confirmed':
                return '此日报已经提交，未重复入账'
            if draft['state'] != 'draft':
                raise ValueError('预览已失效，请重新填写')
            old = self.record(uid, draft['day'])
            if (old['version'] if old else 0) != draft['base_version']:
                raise ValueError('数据已变更，请重新预览确认')
            version = self.write_record(uid, draft['day'], json.loads(draft['payload']), uid, '本人确认日报', draft['raw'], draft['source'], True, now)
            self.db.run("UPDATE drafts SET state='confirmed' WHERE id=?", (draft_id,))
        row = self.record(uid, draft['day'])
        return f'提交回执：{draft["day"]}，版本 {version}，' + ('已提交；截止时间待确认，暂不判定迟交' if row['late'] == -1 else '迟交' if row['late'] else '已提交')

    def revoke(self, actor, uid, day, reason):
        if actor != uid:
            self.require_admin(actor)
        else:
            self.employee(actor)
        valid_day(day, self.c.now().date())
        with self.db.transaction():
            old = self.record(uid, day)
            if not old:
                raise ValueError('没有该日期的填报')
            self.db.audit('日报', f'{uid}:{day}', actor, reason, old, None)
            self.db.run('DELETE FROM records WHERE uid=? AND day=?', (uid, day))
            self.db.run("UPDATE drafts SET state='revoked' WHERE uid=? AND day=?", (uid, day))
            self.mark_dirty(day)

    def correct(self, actor, uid, day, raw, reason):
        self.require_admin(actor)
        emp = self.db.one('SELECT * FROM employees WHERE uid=?', (uid,))
        if not emp:
            raise ValueError('员工不存在')
        parsed_day, payload = parse_report(raw, self.c.now().date(), emp['code'])
        if parsed_day != day:
            raise ValueError('更正日期不一致')
        with self.db.transaction():
            return self.write_record(uid, day, payload, actor, reason, raw, '管理员纠错', True)

    def set_metric(self, actor, metric, definition, target=None, uid=0):
        self.require_admin(actor)
        if metric not in METRICS:
            raise ValueError('指标只能为精聊、引流、维聊')
        if target is not None and (not isinstance(target, int) or target < 0):
            raise ValueError('目标必须为非负整数或空')
        key = f'metric:{uid}:{metric}'
        with self.db.transaction():
            old = self.db.setting(key)
            value = dict(definition=definition, target=target)
            self.db.set(key, value)
            self.db.audit('指标', key, actor, '管理员设置口径或目标', old, value)

    def new_task(self, actor, day, title, description, uids, deadline):
        self.require_admin(actor)
        valid_day(day, self.c.now().date() + timedelta(days=365))
        when = datetime.fromisoformat(deadline)
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.c.zone)
        if not uids or not title.strip():
            raise ValueError('任务标题及负责人不能为空')
        for uid in uids:
            self.employee(uid)
        with self.db.transaction():
            task = self.db.run('INSERT INTO tasks(day,title,description,deadline) VALUES (?,?,?,?)', (day, title, description, when.isoformat())).lastrowid
            for uid in set(uids):
                self.db.run('INSERT INTO assignments(task,uid) VALUES (?,?)', (task, uid))
            self.db.audit('任务', task, actor, '管理员下发任务', None, self.task(task))
        return task

    def task(self, task):
        row = self.db.one('SELECT * FROM tasks WHERE id=?', (task,))
        if not row:
            raise ValueError('任务不存在')
        row['assignments'] = self.db.all('SELECT * FROM assignments WHERE task=? ORDER BY uid', (task,))
        row['all_done'] = bool(row['assignments']) and all(a['status'] == '已完成' for a in row['assignments'])
        return row

    def task_update(self, uid, task, status, note, problem='', next_step=''):
        self.employee(uid)
        if status not in ('待处理', '进行中', '已完成', '受阻'):
            raise ValueError('状态无效')
        with self.db.transaction():
            old = self.task(task)
            if old['closed']:
                raise ValueError('任务已关闭')
            if not any(a['uid'] == uid for a in old['assignments']):
                raise PermissionError('只能更新本人负责的任务')
            self.db.run('UPDATE assignments SET status=?,note=?,problem=?,next_step=? WHERE task=? AND uid=?', (status, note, problem, next_step, task, uid))
            self.db.audit('任务', task, uid, '负责人更新状态', old, self.task(task))
            self.mark_dirty(old['day'])

    def task_edit(self, actor, task, deadline=None, uids=None, closed=None, reason=''):
        self.require_admin(actor)
        with self.db.transaction():
            old = self.task(task)
            if deadline:
                parsed = datetime.fromisoformat(deadline)
                parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=self.c.zone)
                self.db.run('UPDATE tasks SET deadline=? WHERE id=?', (parsed.isoformat(), task))
            if closed is not None:
                if closed not in (0, 1):
                    raise ValueError('关闭状态只能为 0 或 1')
                self.db.run('UPDATE tasks SET closed=? WHERE id=?', (closed, task))
            if uids is not None:
                if not uids:
                    raise ValueError('负责人不能为空')
                for uid in set(uids):
                    self.employee(uid)
                for a in old['assignments']:
                    if a['uid'] not in uids:
                        self.db.run('DELETE FROM assignments WHERE task=? AND uid=?', (task, a['uid']))
                for uid in set(uids):
                    self.db.run('INSERT OR IGNORE INTO assignments(task,uid) VALUES (?,?)', (task, uid))
            self.db.audit('任务', task, actor, reason, old, self.task(task))
            self.mark_dirty(old['day'])

    def summary(self, day, now=None):
        now = now or self.c.now()
        valid_day(day, now.date())
        expected, leaves = self.roster(day)
        roster_warning = self.db.setting('roster_warning:' + day, '')
        # 历史名册保留；停用/无需报表的当日变动排除，历史日期不受影响。
        if day == self.c.business_date(now).isoformat():
            expected = [uid for uid in expected if (self.db.one('SELECT active,required FROM employees WHERE uid=?', (uid,)) or {}).get('active') and self.db.one('SELECT required FROM employees WHERE uid=?', (uid,))['required']]
        rows = self.db.all('SELECT * FROM records WHERE day=?', (day,))
        submitted = [r for r in rows if r['confirmed'] and r['uid'] in expected]
        counted = [r for r in rows if r['uid'] in expected]
        payloads = [json.loads(r['payload']) for r in counted]
        known, missing = {}, {}
        for field in NUMERIC:
            values = [p.get(field) for p in payloads]
            known[field] = sum(v for v in values if v is not None)
            missing[field] = len(expected) - sum(v is not None for v in values)
        totals = {m: known['上半场' + m + '新增'] + known['下半场' + m + '新增'] for m in METRICS}
        goals = []
        for row in self.db.all("SELECT key,value FROM settings WHERE key LIKE 'metric:%'"):
            _, uid, metric = row['key'].split(':')
            goal = json.loads(row['value'])
            if goal['target'] is None:
                continue
            if int(uid) == 0:
                amount = totals[metric]
            else:
                p = next((json.loads(r['payload']) for r in counted if r['uid'] == int(uid)), {})
                amount = sum(p.get(k) or 0 for k in ('上半场' + metric + '新增', '下半场' + metric + '新增'))
            goals.append(dict(uid=int(uid), metric=metric, target=goal['target'], known=amount, gap=goal['target'] - amount))
        tasks = [self.task(r['id']) for r in self.db.all('SELECT id FROM tasks WHERE day=?', (day,))]
        overdue = [t for t in tasks if not t['closed'] and not t['all_done'] and datetime.fromisoformat(t['deadline']) < now]
        management = dict(issued=len(tasks), completed=sum(t['all_done'] for t in tasks), overdue=len(overdue), tasks=tasks)
        text_missing = {f: len(expected) - sum(bool(p.get(f)) for p in payloads) for f in TEXT}
        return dict(day=day, expected=len(expected), submitted=len(submitted), missing_reports=len(expected) - len(submitted), leave=len(leaves), roster_warning=roster_warning, missing_codes=[self.db.one('SELECT code FROM employees WHERE uid=?', (u,))['code'] for u in expected if u not in {r['uid'] for r in submitted}], known=known, missing_fields=missing, missing_texts=text_missing, totals=totals, goals=goals, texts={f: [json.loads(r['payload'])[f] for r in counted if json.loads(r['payload']).get(f)] for f in TEXT}, public_notes=self.db.setting('public_notes:' + day, ''), management=management, complete=not roster_warning and not any(missing.values()) and not any(text_missing.values()) and len(submitted) == len(expected), ai='未使用 AI；本地规则汇总', created=now.isoformat())

    def save_summary(self, day):
        result = self.summary(day)
        with self.db.transaction():
            version = self.db.one('SELECT COALESCE(MAX(version),0)+1 AS v FROM summaries WHERE day=?', (day,))['v']
            result['version'] = version
            self.db.run('INSERT INTO summaries VALUES (?,?,?,?)', (day, version, dumps(result), result['created']))
            self.db.run('DELETE FROM dirty WHERE day=?', (day,))
        self.export_day(day, result)
        return result

    def render_summary(self, result):
        r = result
        text = [f'工作日报汇总 {r["day"]} · 版本 {r.get("version", "预览")}', f'应提交 {r["expected"]}，已提交 {r["submitted"]}，未提交 {r["missing_reports"]}，请假 {r["leave"]}', '统计完整' if r['complete'] else '统计未完整：以下为已知填报量，缺项不是零；进度草稿也计入已知量', '未提交编号：' + ('、'.join(r['missing_codes']) or '无')]
        if r.get('roster_warning'):
            text.append(r['roster_warning'])
        for k in NUMERIC:
            text.append(f'{k}：{r["known"][k]}；待补 {r["missing_fields"][k]} 人')
        text.append('全天已知合计：' + '，'.join(f'{m} {r["totals"][m]}' for m in METRICS))
        text.append('三项分别计数，不能相加称为独立客户总人数；未进行客户去重。')
        text.append('维聊以上为填报人次；维聊去重人数未统计，不能用上下半场之和推算。')
        for g in r['goals']:
            text.append(f'目标（{g["uid"] or "团队"}）{g["metric"]}：目标 {g["target"]}，已知 {g["known"]}，差额 {g["gap"]}')
        for key in TEXT:
            # 群汇总不发布原始员工文字；管理员私聊导出可查看完整内容。
            text.append(f'{key}：有记录 {len(r["texts"][key])} 条，待补 {r.get("missing_texts", {}).get(key, 0)} 人（详情见管理员私聊导出）')
        if r.get('public_notes'):
            text.append('管理员审核的非敏感工作文字：\n' + r['public_notes'])
        if r.get('ai_text'):
            text.append('AI 文字归纳（供参考）：\n' + r['ai_text'])
        text.append(self.render_management(r))
        text.extend([r['ai'], f'生成时间：{r["created"]}'])
        return '\n'.join(text)

    def render_management(self, r):
        m = r['management']
        parts = [f'管理工作：下发 {m["issued"]}，全部负责人完成 {m["completed"]}，逾期 {m["overdue"]}']
        for t in m['tasks']:
            pending = [a for a in t['assignments'] if a['status'] != '已完成']
            parts.append(f'任务 #{t["id"]} {t["title"]}：' + ('已关闭；' if t['closed'] else '') + ('全部完成' if not pending else '；'.join(f'负责人 {a["uid"]} {a["status"]}，问题 {a["problem"] or "未填写"}，下一步 {a["next_step"] or "未填写"}' for a in pending)))
        return '\n'.join(parts)

    def export_day(self, day, result=None):
        result = result or self.summary(day)
        folder = self.c.data / 'exports' / day
        folder.mkdir(parents=True, exist_ok=True)
        suffix = f'v{result.get("version", "preview")}'
        (folder / f'{suffix}.json').write_text(dumps(result), encoding='utf-8')
        md = self.render_summary(result)
        for field in TEXT:
            md += '\n\n' + field + '\n' + '\n'.join(result['texts'][field])
        (folder / f'{suffix}.md').write_text(md, encoding='utf-8')
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['日期', '员工编号', '用户ID', '确认', '迟交', '版本'] + FIELDS)
        for row in self.db.all('SELECT r.*,e.code FROM records r JOIN employees e ON e.uid=r.uid WHERE day=? ORDER BY e.code', (day,)):
            p = json.loads(row['payload'])
            writer.writerow([safe_cell(v) for v in [day, row['code'], row['uid'], row['confirmed'], row['late'], row['version']] + [p.get(k) if p.get(k) is not None else '待补' for k in FIELDS]])
        (folder / f'{suffix}.csv').write_text(out.getvalue(), encoding='utf-8-sig')
        return sorted(folder.glob(f'{suffix}.*'))

    def range_export(self, actor, start, end):
        self.require_admin(actor)
        valid_day(start, self.c.now().date())
        valid_day(end, self.c.now().date())
        if start > end or (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days > 366:
            raise ValueError('范围须按顺序且不超过 366 天')
        days = set(r['day'] for r in self.db.all('SELECT DISTINCT day FROM records WHERE day BETWEEN ? AND ?', (start, end)))
        paths = []
        for day in sorted(days):
            paths.extend(self.export_day(day))
        import zipfile
        dest = self.c.data / 'exports' / f'{start}_{end}.zip'
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                z.write(p, p.relative_to(self.c.data / 'exports'))
        return dest


from .daily import DailyMixin


from .identity import IdentityMixin

class Service(IdentityMixin, DailyMixin, LegacyService):
    pass
