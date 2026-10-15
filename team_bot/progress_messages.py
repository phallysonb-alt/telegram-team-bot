"""Escaped Telegram HTML, complete task text, compact narrow-screen sections."""
from html import escape
def published(day,tasks,start=1):
 text=f'<b>今日工作任务｜{escape(day)}</b>'
 for i,t in enumerate(tasks,start):
  text+=f'\n\n<b>任务 {i:02}｜{escape(t["title"])}</b>\n\n执行方式：'+('按顺序完成' if t['kind']=='seq' else '自由完成')+'\n\n<b>任务阶段</b>\n'
  text+='\n'.join(f'{j}. {escape(stage)}' for j,stage in enumerate(t['stages'],1))
 return text
def queried(p,rid):
 from .progress_ui import time_text
 r=p.round(rid);count=p.db.one('SELECT COUNT(*) n FROM pg_feedback WHERE round=?',(rid,))['n']
 text=f'<b>工作进度查询｜{r["day"]}</b>\n\n查询时间：{time_text(r["at"])}\n<b>已反馈：{count} / {len(r["roster"])} 人</b>'
 if r['simulated']:text+='\n模拟展示'+(' · 仅所有者联调' if r['roster']==[p.c.owner] else '')
 text+=f'\n\n<b>本轮查询任务（{len(r["tasks"])}项）</b>\n\n'
 text+='\n'.join(f'{i}. {escape(t["title"])}' for i,t in enumerate(r['tasks'],1))
 return text+'\n\n请点击下方「填写进度」提交本轮反馈。'
