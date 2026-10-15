import unittest
from team_bot.progress_messages import published,queried
from types import SimpleNamespace

class MessageLayoutTests(unittest.TestCase):
 def test_html_bold_line_breaks_no_decorative_divider_and_escaping(self):
  tasks=[dict(title='任务<A&B>',kind='seq',stages=['第一步完整原文','第二步 <特殊字符>','第三步']),dict(title='自由任务',kind='free',stages=['整理','更新','归档'])]
  text=published('2026-10-14',tasks)
  self.assertIn('<b>今日工作任务｜2026-10-14</b>',text);self.assertIn('<b>任务 01｜任务&lt;A&amp;B&gt;</b>',text);self.assertIn('<b>任务 02｜自由任务</b>',text)
  self.assertIn('1. 第一步完整原文\n2. 第二步 &lt;特殊字符&gt;\n3. 第三步',text);self.assertNotRegex(text,'[━─]{2,}');self.assertIn('\n\n<b>任务 02',text)
 def test_query_count_task_lines_without_decorations(self):
  p=SimpleNamespace(round=lambda _:dict(day='2026-10-14',at='2026-10-14T15:00:00+08:00',roster=[1,2],tasks=[dict(title='任务一'),dict(title='任务二')],simulated=False),db=SimpleNamespace(one=lambda *a:dict(n=1)),c=SimpleNamespace(owner=99))
  text=queried(p,1);self.assertIn('<b>已反馈：1 / 2 人</b>',text);self.assertIn('1. 任务一\n2. 任务二',text);self.assertNotRegex(text,'[━─]{2,}')
