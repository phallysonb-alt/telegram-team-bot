import re
from datetime import date

METRICS = ('精聊', '引流', '维聊')
NUMERIC = [f'{p}半场{m}新增' for p in ('上', '下') for m in METRICS]
TEXT = ['今日主要工作', '遇到的问题', '已解决事项', '未解决事项', '明日待办']
FIELDS = NUMERIC + TEXT
TEMPLATE = '\n'.join(f'{key}：' for key in ['日期', '员工编号'] + FIELDS)
PROGRESS_TEMPLATE = '日期：\n阶段：上半场\n精聊新增：\n引流新增：\n维聊新增：\n遇到的问题：\n问题是否解决：\n解决说明：\n待办事项：\n客户内部编号：'


def lines(raw):
    data = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        pair = re.split('[：:]', line, maxsplit=1)
        if len(pair) != 2:
            raise ValueError('每一行请使用“字段：内容”格式')
        key, value = (v.strip() for v in pair)
        if key in data:
            raise ValueError('字段重复：' + key)
        data[key] = value
    return data


def valid_day(value, today):
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('日期需明确填写 YYYY-MM-DD') from None
    if parsed.isoformat() != value:
        raise ValueError('日期需使用 YYYY-MM-DD')
    if parsed > today:
        raise ValueError('不能填报未来日期')
    return value


def number(value):
    if value in ('', None, '待补'):
        return None
    if not re.fullmatch(r'[0-9]+', str(value)) or len(str(value)) > 9:
        raise ValueError('数量只接受不超过 9 位的非负整数；空白保留为待补')
    return int(value)


def parse_report(raw, today, code):
    data = lines(raw)
    unknown = set(data) - set(FIELDS + ['日期', '员工编号'])
    if unknown:
        raise ValueError('未知字段：' + '、'.join(sorted(unknown)))
    day = valid_day(data.get('日期'), today)
    if data.get('员工编号') != code:
        raise ValueError('员工编号与本人登记信息不一致')
    payload = {key: number(data.get(key)) for key in NUMERIC}
    payload.update({key: data.get(key, '') or None for key in TEXT})
    return day, payload


def parse_progress(raw, today):
    # Some clients flatten copied templates. Accept only the complete five-field
    # numeric form; never guess dates or split arbitrary narrative text.
    compact = re.fullmatch(
        r'\s*日期\s*[：:]\s*(\d{4}-\d{2}-\d{2})\s+阶段\s*[：:]\s*(上半场|下半场)'
        r'\s+精聊新增\s*[：:]\s*([0-9]{1,9})\s+引流新增\s*[：:]\s*([0-9]{1,9})'
        r'\s+维聊新增\s*[：:]\s*([0-9]{1,9})\s*', raw)
    if compact:
        raw = '\n'.join(f'{key}：{value}' for key, value in zip(
            ('日期', '阶段', '精聊新增', '引流新增', '维聊新增'), compact.groups()))
    data = lines(raw)
    allowed = {'日期', '阶段', '精聊新增', '引流新增', '维聊新增', '遇到的问题', '问题是否解决', '解决说明', '待办事项', '客户内部编号'}
    if set(data) - allowed:
        raise ValueError('进度模板存在未知字段')
    day = valid_day(data.get('日期'), today)
    phase = data.get('阶段')
    if phase not in ('上半场', '下半场'):
        raise ValueError('阶段只能填上半场或下半场；填写本阶段新增量')
    result = {phase + m + '新增': number(data.get(m + '新增')) for m in METRICS}
    result['阶段备注_' + phase] = {key: data.get(key) or None for key in allowed if key not in {'日期', '阶段', '精聊新增', '引流新增', '维聊新增'}}
    return day, result


def preview(day, payload):
    return f'工作日期：{day}\n' + '\n'.join(f'{k}：{payload.get(k) if payload.get(k) is not None else "待补"}' for k in FIELDS)
