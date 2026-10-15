"""Owner-only display sandbox, strictly separate from trial and formal employees."""
from dataclasses import replace
from .service import Service
from .bot import Adapter

def display_adapter(parent):
    if 'mock30' not in parent.test_adapters:
        c=replace(parent.c,data=parent.c.data/'mock30-v2',backups=parent.c.data/'mock30-v2/backups',test_run='mock30',membership_required=False)
        child=Adapter(Service(c));child.db.set('display_mock',True)
        parent.test_adapters['mock30']=child
    child=parent.test_adapters['mock30']
    # Owner's real private start is transport consent, not a synthetic employee.
    if parent.db.setting('private_started:'+str(parent.c.owner),False):child.db.set('private_started:'+str(parent.c.owner),True)
    return child

async def route(parent,u,context):
    if parent.c.run_mode!='trial' or parent.c.test_run!='newgroup' or not u.effective_user or not u.effective_chat:return False
    data=u.callback_query.data if u.callback_query else ''
    if data.startswith('test:mockdaily30:'):
        if u.effective_user.id!=parent.c.owner:return False
        # Daily-only read-only simulation; cannot submit as synthetic employees.
        if data.split(':')[2] not in ('report_view','report_plain','plain'):
            await u.callback_query.answer('模拟展示只提供查看，不接受提交。',show_alert=True);return True
        if 'mockdaily30' not in parent.test_adapters:
            c=replace(parent.c,data=parent.c.data/'mockdaily30',backups=parent.c.data/'mockdaily30/backups',test_run='mockdaily30',membership_required=False)
            child=Adapter(Service(c));child.db.set('display_mock',True);parent.test_adapters['mockdaily30']=child
        await parent.test_adapters['mockdaily30'].handle(u,context);return True
    if data.startswith('test:mock30:'):
        if u.effective_user.id!=parent.c.owner:return False
        await display_adapter(parent).handle(u,context);return True
    return False
