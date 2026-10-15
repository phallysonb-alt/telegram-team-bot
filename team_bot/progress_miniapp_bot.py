"""Enabled only once the persistent HTTPS endpoint and BotFather app are ready."""
from .progress_miniapp import link,query_card

def entry(p):
    return dict(inline_keyboard=[[dict(text='发布任务',url=link('publish')),dict(text='查看总表',url=link('summary'))]])

async def handle_miniapp(a,u,ctx,command,parts):
    from .progress_ui import controller,flush
    p=controller(a.s);uid=u.effective_user.id;msg=u.effective_message;key='app:update:'+str(u.update_id)
    if p.db.one('SELECT id FROM pg_updates WHERE id=?',(u.update_id,)):return True
    if u.callback_query:
        try:await u.callback_query.answer()
        except Exception:pass
    try:
        target=uid;thread=None;kind=None;rid=None
        if command=='export':
            p.admin(uid)
            p.queue(key,dict(text='导出跟进表',chat=uid,export=True,uid=uid))
            p.db.run('INSERT INTO pg_updates VALUES (?,?)',(u.update_id,p.now()))
            return True
        if command in ('query','newquery'):
            p.admin(uid);day=a.c.now().date().isoformat() if command=='query' else parts[0]
            with p.db.transaction():
                rid=p.new_round(uid,day,None if command=='query' else int(parts[1]) or None)
                text,markup=query_card(p,rid);kind='query';target=a.c.chat;thread=a.c.threads['progress']
                p.queue(key,dict(text=text,markup=markup,parse_mode='HTML',chat=target,thread=thread,kind=kind,round=rid))
                p.db.run('INSERT INTO pg_updates VALUES (?,?)',(u.update_id,p.now()))
            await flush(p,ctx.bot,[key]);return True
        if command in ('menu','new','latest','team','history','tasks','choose'):
            if p.s.admin(uid):text='工作进度';markup=entry(p)
            else:
                p.employee(uid);latest=p.latest(a.c.now().date().isoformat())
                text='工作进度'+('\n等待管理员查询。' if not latest else '')
                markup=dict(inline_keyboard=[[dict(text='填写进度',url=link('feedback_'+str(latest['id']))) ]]) if latest else None
        else:
            # All obsolete personal/public stage callbacks become inert after cutover.
            text='此入口已停用，请从工作进度话题打开最新小程序入口。';markup=None
        p.queue(key,dict(text=text,markup=markup,chat=target,thread=thread))
        p.db.run('INSERT INTO pg_updates VALUES (?,?)',(u.update_id,p.now()))
    except (ValueError,PermissionError,IndexError) as e:
        text='今日尚未发布任务' if '有效任务' in str(e) else str(e)
        p.queue(key,dict(text=text,chat=uid));p.db.run('INSERT OR IGNORE INTO pg_updates VALUES (?,?)',(u.update_id,p.now()))
    await flush(p,ctx.bot,[key]);return True
