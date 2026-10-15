"""同一个 polling 进程内的显式测试会话，数据库与正式记录隔离。"""
from dataclasses import replace
from pathlib import Path
import uuid
from .service import Service
from .config import ROOT
from .db import stamp


def sandbox(adapter, state):
    path = Path(state['path']).resolve()
    parent = (adapter.c.data / 'live-tests').resolve()
    if not path.is_relative_to(parent):
        raise ValueError('测试目录越界，拒绝加载')
    if state['run'] in adapter.test_adapters:
        return adapter.test_adapters[state['run']]
    config = replace(adapter.c, data=path, backups=path / 'backups', key='', model='', ai_enabled=False, free_confirmed=False, report_times_confirmed=False, test_run=state['run'], threads={name: adapter.scheduler.thread(name) for name in adapter.c.threads})
    config.now = adapter.c.now
    from .bot import Adapter
    service = Service(config)
    service.preset_personnel()
    child = Adapter(service)
    for name in ('training_excluded_names','training_excluded_hashes'):
        child.db.set(name,adapter.db.setting(name,[]))
    for uid in state['participants']:
        child.db.set(f'private_started:{uid}', adapter.db.setting(f'private_started:{uid}', False))
    adapter.test_adapters[state['run']] = child
    return child


def control(adapter, update, command, args):
    uid, msg, chat = update.effective_user.id, update.effective_message, update.effective_chat
    state = adapter.db.setting('live_test')
    if command in ('test_begin', 'test_allow', 'test_close'):
        adapter.route(update, admin=True)
    if command == 'test_begin':
        if not all(adapter.scheduler.thread(name) for name in adapter.c.threads) or not adapter.c.chat:
            raise ValueError('先完成本地管理员、群配置及四话题绑定，再开始隔离测试')
        if not state:
            run = uuid.uuid4().hex[:12]
            state = dict(run=run, path=str(adapter.c.data / 'live-tests' / run), allowed=[uid], participants=[uid], created=stamp())
            adapter.db.set('live_test', state)
            child = sandbox(adapter, state)
            from .knowledge import Knowledge
            if adapter.c.report_version>=2:
                pass # This revision does not seed or expand training distribution.
            elif adapter.c.training_mode=='directory':
                from .catalog import Catalog
                Catalog(child.s).add(uid,ROOT/'samples/独立模拟目录资料.txt')
            elif not adapter.db.setting('training_demo_disabled', False):
                Knowledge(child.s).import_file(uid, ROOT / 'samples/培训资料样例.md')
        elif uid not in state['participants']:
            state['participants'].append(uid)
            state['allowed'] = sorted(set(state['allowed'] + [uid]))
            adapter.db.set('live_test', state)
        child = sandbox(adapter, state)
        child.db.set(f'private_started:{uid}', adapter.db.setting(f'private_started:{uid}', False))
        training = '/materials 查看模拟资料目录' if adapter.c.training_mode=='directory' else '/ask 本阶段新增量'
        if adapter.c.report_version>=2:
            return f'【联调测试】已进入隔离会话 {state["run"]}。\n登记审批仅管理员私聊处理；日报话题 /template，工作进度 /checkin 获取本人私聊模拟卡片。管理话题静默。\n不新增培训资料。测试数据只写入 live-tests；退出 /test_end，管理员结束 /test_close。'
        return f'【联调测试】已进入隔离会话 {state["run"]}。\n先 /register Y001，再 /approve {uid}。随后在进度话题 /progress_template、日报话题 /template，填写并确认。培训话题 {training}；管理话题可下发本人测试任务。\n所有测试消息带标记，数据只写入 live-tests，不计正式汇总。退出 /test_end，管理员结束所有测试 /test_close。'
    if not state:
        raise ValueError('当前没有正在进行的测试会话')
    if command == 'test_allow':
        target = int(args[0])
        if target <= 0:
            raise ValueError('用户 ID 必须为正整数')
        state['allowed'] = sorted(set(state['allowed'] + [target]))
        adapter.db.set('live_test', state)
        return f'【联调测试】已允许用户 ID {target} 使用 /test_join 加入；不会授予正式权限或自动批准登记。'
    if command == 'test_join':
        if chat.type != 'private' and chat.id != adapter.c.chat:
            raise PermissionError('请在指定群或机器人私聊加入测试')
        if uid not in state['allowed']:
            raise PermissionError('请先由管理员 /test_allow 你的数字ID 授权加入测试')
        state['participants'] = sorted(set(state['participants'] + [uid]))
        adapter.db.set('live_test', state)
        child = sandbox(adapter, state)
        child.db.set(f'private_started:{uid}', adapter.db.setting(f'private_started:{uid}', False))
        return '【联调测试】已加入隔离会话。请 /register 你的编号，等待测试管理员批准；退出 /test_end。'
    if command == 'test_end':
        state['participants'] = [u for u in state['participants'] if u != uid]
        adapter.db.set('live_test', state)
        return '【联调测试】你已退出测试。测试历史保留在隔离数据库，旧测试按钮已失效。'
    if command == 'test_close':
        child = sandbox(adapter, state)
        child.db.run("UPDATE outbox SET state='cancelled' WHERE state='pending'")
        adapter.db.set('live_test', None)
        adapter.db.audit('联调测试', state['run'], uid, '管理员结束隔离测试', state, None)
        return '【联调测试】会话已结束，所有参与者退出。隔离历史保留；没有删除任何正式记录。'
    raise ValueError('未知测试命令')
