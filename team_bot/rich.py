"""Bot API 10.3 native blocks via official HTTPS; PTB 22.8 has no rich method."""
import httpx
from telegram import Message
from telegram.error import RetryAfter


class RichAPIError(Exception):
    def __init__(self, code, reason):
        self.code = code
        self.reason = reason
        super().__init__(f'Rich API {code}: {reason}')


async def send_native(bot, chat, thread, content, markup=None, reply_to=None):
    payload = dict(chat_id=chat, rich_message=content, allow_paid_broadcast=False)
    if reply_to is not None:payload['reply_parameters']=dict(message_id=reply_to,allow_sending_without_reply=True)
    if thread is not None:
        payload['message_thread_id'] = thread
    if markup:
        payload['reply_markup'] = markup
    # Never log this URL, the response body, or underlying HTTP exceptions.
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=20)) as client:
        response = await client.post(str(bot.base_url) + '/sendRichMessage', json=payload)
        data = response.json()
    if not data.get('ok'):
        retry = data.get('parameters', {}).get('retry_after')
        if retry:
            raise RetryAfter(retry)
        description = str(data.get('description', '')).lower()
        reason = next((r for r in ('method not found', 'chat not found', 'bot was kicked', 'not enough rights', 'rich_message', 'table', 'blocks') if r in description), 'request rejected')
        raise RichAPIError(data.get('error_code', response.status_code), reason)
    return Message.de_json(data['result'], bot)


async def edit_native(bot,chat,message_id,content,markup=None):
    # Official editMessageText supports rich_message; SDK uses text-only API.
    payload=dict(chat_id=chat,message_id=message_id,rich_message=content)
    if markup:payload['reply_markup']=markup
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.post(str(bot.base_url)+'/editMessageText',json=payload)
        data=response.json()
    if not data.get('ok'):
        if data.get('parameters',{}).get('retry_after'):raise RetryAfter(data['parameters']['retry_after'])
        if 'message is not modified' in str(data.get('description','')).lower():
            from types import SimpleNamespace
            return SimpleNamespace(message_id=message_id)
        raise RichAPIError(data.get('error_code',response.status_code),'edit rejected')
    return Message.de_json(data['result'],bot) if isinstance(data['result'],dict) else data['result']


def paragraph(text):
    return dict(type='paragraph', text=text)


def heading(text):
    return dict(type='heading', size=2, text=text)


def table(headers, rows):
    def cell(value, header=False):
        result = dict(text=str(value), align='left', valign='top')
        if header:
            result['is_header'] = True
        return result
    return dict(type='table', is_bordered=True, is_striped=True, is_compact=True,
                cells=[[cell(v, True) for v in headers]] + [[cell(v) for v in row] for row in rows])


def listing(items):
    return dict(type='list', items=[dict(blocks=[paragraph(item)]) for item in items])


def details(title, blocks):
    return dict(type='details', summary=title, blocks=blocks)


def report_view(s, r):
    rows=[]
    for metric in ('精聊', '引流', '维聊'):
        rows.append([metric + ('（填报人次）' if metric == '维聊' else ''),
                     r['known']['上半场'+metric+'新增'], r['known']['下半场'+metric+'新增'], r['totals'][metric],
                     f"上 {r['missing_fields']['上半场'+metric+'新增']} / 下 {r['missing_fields']['下半场'+metric+'新增']} 人"])
    rows.append(['维聊去重人数', '未统计', '未统计', '未统计', '现有记录未提供可核实去重数据'])
    blocks=[heading('工作日报 '+r['day']), paragraph(f"提交 {r['submitted']} 人 · 缺报 {r['missing_reports']} 人 · 应交 {r['expected']} 人"),
            table(['指标','上半场','下半场','全天已知量','待补'],rows),
            paragraph('缺项不是零；进度草稿计入已知量。维聊填报人次与去重人数分开，不能从汇总数推算去重人数。')]
    if r.get('roster_warning'):
        blocks.append(paragraph(r['roster_warning']))
    blocks.append(details('缺报编号与统计说明',[paragraph('缺报编号：'+('、'.join(r['missing_codes']) or '无')),paragraph('三项不能相加作为独立客户人数。原始员工工作文字仅在管理员导出中查看。')]))
    # AI text is deliberately excluded from the numerical presentation.
    return dict(blocks=blocks)


def progress_view(s, day, page=None, employee_uid=None):
    import json
    r=s.summary(day)
    goals={(g['uid'],g['metric']):g['target'] for g in r['goals']}
    rows=[]
    if page is None and employee_uid is None:
        for m in ('精聊','引流','维聊'):
            rows.append(['团队',m,goals.get((0,m),'未设置'),r['known']['上半场'+m+'新增'],r['known']['下半场'+m+'新增'],r['totals'][m]])
        title='工作进度总表 '+day
        note='全天为已知量，缺项不是零；员工明细按需分页，每页 5 人。维聊为填报人次，去重人数未统计。'
    else:
        expected,_=s.roster(day)
        employees=[e for e in s.db.all('SELECT uid,code FROM employees ORDER BY code') if e['uid'] in expected and (employee_uid is None or e['uid']==employee_uid)]
        page=page or 1
        pages=max(1,(len(employees)+4)//5)
        if not 1 <= page <= pages:
            raise ValueError('明细页码超出范围')
        for e in employees[(page-1)*5:page*5]:
            record=s.record(e['uid'],day)
            p=json.loads(record['payload']) if record else {}
            for m in ('精聊','引流','维聊'):
                am,pm=p.get('上半场'+m+'新增'),p.get('下半场'+m+'新增')
                total='待补' if am is None and pm is None else str((am or 0)+(pm or 0))+('（待补）' if am is None or pm is None else '')
                rows.append([e['code'],m,goals.get((e['uid'],m),'未设置'),am if am is not None else '待补',pm if pm is not None else '待补',total])
        title=f'员工进度明细 {day} · 第 {page}/{pages} 页'
        note='未设置个人目标时不把团队目标当作个人目标；缺项保持待补。'
    headers=['员工编号','工作指标','目标','上半场','下半场','全天结果']
    plain=title+'\n'+'\n'.join('；'.join(f'{k}：{v}' for k,v in zip(headers,row)) for row in rows)+'\n'+note
    return dict(blocks=[heading(title),table(headers,rows),paragraph(note)]),plain


def management_view(s, r):
    m=r['management']
    items=[]
    for t in m['tasks'][:5]:
        status='已关闭' if t['closed'] else '全部完成' if t['all_done'] else '待完成'
        items.append([dict(type='bold',text=f'#{t["id"]} {t["title"]} · {status}'), '\n截止：'+t['deadline'],
                      '\n'+'；'.join(f'{a["uid"]}：{a["status"]}' for a in t['assignments'])])
    blocks=[heading('管理工作 '+r['day']),paragraph([dict(type='bold',text=f"下发 {m['issued']} · 全部完成 {m['completed']} · 逾期 {m['overdue']}")])]
    blocks.append(listing(items) if items else paragraph('暂无任务'))
    if len(m['tasks'])>5:
        blocks.append(paragraph('群内仅展示最近 5 项；管理员按需 /management_view 日期 页码 查看，每页 5 项。'))
    return dict(blocks=blocks)


def training_view(answer, hits):
    blocks=[heading('培训资料查询'),listing(['按当前有效资料检索','资料引文与来源供核对；没有匹配时明确提示未找到'])]
    for h in hits:
        source=f"{h['name']} · v{h['version']} · {h['location']}"
        quote=dict(type='blockquote',blocks=[paragraph(h['text'])],credit=source)
        blocks.append(details('展开资料：'+source,[quote]) if len(h['text'])>500 else quote)
    blocks.append(details('查看完整查询结果',[paragraph(answer)]))
    blocks.append(dict(type='footer',text='资料来源：'+('；'.join(f"{h['name']} · v{h['version']} · {h['location']}" for h in hits) or '无匹配资料')))
    return dict(blocks=blocks)
