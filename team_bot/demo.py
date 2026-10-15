from .reports import NUMERIC, TEXT, PROGRESS_TEMPLATE

DEMO_DAY = '2026-10-09'
EXPECTED = {'精聊': 256, '引流': 124, '维聊': 171}


def report(day, i, incomplete=False):
    counts = [i, 2, 3, 1, 4, 5]
    if incomplete:
        counts[4] = None
    pairs = [('日期', day), ('员工编号', f'E{i:03}')]
    pairs += list(zip(NUMERIC, counts))
    pairs += [(k, '完成培训和工作记录' if k == '今日主要工作' else '无') for k in TEXT]
    return '\n'.join(f'{k}：{v if v is not None else ""}' for k, v in pairs)


def seed(service):
    if service.db.one('SELECT uid FROM employees LIMIT 1'):
        raise ValueError('模拟数据仅允许导入空的独立数据库')
    for i in range(1, 31):
        uid = 10000 + i
        service.register(uid, f'E{i:03}', f'模拟员工{i:02}')
        service.approve(service.c.owner, uid)
    service.leave(service.c.owner, 10029, DEMO_DAY, 1, '模拟请假')
    service.change_employee(service.c.owner, 10030, active=0, reason='模拟停用')
    service.set_roster(service.c.owner, DEMO_DAY, list(range(10001, 10029)), '固定测试数据核实当日应交名单')
    # E1 至 E20 完整提交，E21 缺下半场引流，E22 仅进度，E23..28 未报。
    for i in range(1, 22):
        draft, _, _ = service.draft(10000 + i, report(DEMO_DAY, i, incomplete=i == 21), f'demo:{i}')
        service.confirm(10000 + i, draft)
    service.progress(10022, f'日期：{DEMO_DAY}\n阶段：上半场\n精聊新增：4\n引流新增：2\n维聊新增：3\n遇到的问题：无\n问题是否解决：是\n解决说明：无\n待办事项：培训\n客户内部编号：', 'demo:22')
    task = service.new_task(service.c.owner, DEMO_DAY, '模拟多人任务', '共同完成培训', [10001, 10002], DEMO_DAY + 'T20:00:00')
    service.task_update(10001, task, '已完成', '已阅读')
    service.task_update(10002, task, '受阻', '', '资料待补', '等待补齐')
    return service.save_summary(DEMO_DAY)
