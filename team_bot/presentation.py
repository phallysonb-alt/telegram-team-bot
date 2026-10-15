"""Display-only helpers. Never rewrite persisted timestamps or business payloads."""
import re
from datetime import datetime

ISO_TIME=re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?')
TEST_LABELS=('【验收试用，不计正式数据】','【隔离测试，不计正式数据】','【联调测试；不计正式数据】')

def human_time(value,c,empty='未记录'):
    if not value:return empty
    try:
        at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        at=at.replace(tzinfo=c.zone) if at.tzinfo is None else at.astimezone(c.zone)
        today=c.now().astimezone(c.zone).date()
        return at.strftime('%H:%M' if at.date()==today else '%m-%d %H:%M')
    except (ValueError,TypeError):return str(value)

def clean_text(text,c):
    return ISO_TIME.sub(lambda m:human_time(m.group(),c),str(text))

def clean_blocks(value,c):
    if isinstance(value,list):return [clean_blocks(v,c) for v in value]
    if isinstance(value,dict):
        return {k:clean_text(v,c) if k in ('text','summary','credit') and isinstance(v,str) else clean_blocks(v,c) for k,v in value.items()}
    return clean_text(value,c) if isinstance(value,str) else value

def without_test_label(text):
    for label in TEST_LABELS:text=text.replace(label+'\n','').replace(label,'')
    return text.strip()

def status_label(status):return '明确0' if status=='明确为0' else status

def daily_personal(s,day,payload,title='日报预览',timing=None):
    from .daily import METRICS
    from .rich import heading,paragraph,table
    plain=title+'\n日期：'+day+'\n'+'\n'.join(m+'：'+str(payload[m] if payload[m] is not None else '待填') for m in METRICS)
    blocks=[heading(title),paragraph('日期：'+day),table(['指标','数量'],[[m,payload[m] if payload[m] is not None else '待填'] for m in METRICS])]
    if payload.get('备注'):
        plain+='\n备注：'+payload['备注'];blocks.append(paragraph('备注：\n'+payload['备注']))
    if timing:plain+='\n状态：'+timing;blocks.append(paragraph('状态：'+timing))
    return plain,dict(blocks=blocks)
