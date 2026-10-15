import unittest,json,zipfile,xml.etree.ElementTree as E
from pathlib import Path
import test_daily_send
from team_bot.daily_history import daily_identity,report_pages,export_data,write_excel
class NameColumnTests(unittest.TestCase):
 def setUp(self):self.f=test_daily_send.SendTests();self.f.setUp();self.s=self.f.s
 def tearDown(self):self.f.tearDown()
 def test_separate_identity_notes_align_missing_and_long_values(self):
  name='长姓名中文演示😀空格 '+('非常长的姓名'*6);username='very_long_telegram_user_name_1234'
  u=self.f.approve(456,name,username);v=self.f.approve(789,'无用户名');self.s.submit_daily(u,self.f.raw(note='完整备注\n数字99不计入'),'synthetic')
  before=self.s.record(u,'2026-10-10').copy();pages=report_pages(self.s,self.s.summary('2026-10-10'));t=next(b for b in pages[0]['rich']['blocks'] if b['type']=='table');cells=t['cells']
  self.assertEqual([c['text'] for c in cells[0]],['员工名称','Telegram 用户名','新增','维聊','有效','精聊'])
  self.assertEqual(cells[1][0]['text'].replace('\n',''),name);self.assertEqual(cells[1][1]['text'].replace('\n',''),'@'+username)
  self.assertEqual(cells[2][1]['text'],'—');self.assertIn('未提交',cells[2][0]['text'])
  self.assertTrue(all(c['align']=='center' for c in cells[0]));self.assertEqual([c['align'] for c in cells[1]],['left','left','center','center','center','center'])
  text=pages[0]['text'];self.assertIn(name+'（@'+username+'）\n完整备注\n数字99不计入',text);self.assertEqual(before,self.s.record(u,'2026-10-10'))
 def test_next_generation_keeps_columns_full_names_and_numbers(self):
  name='长姓名中文 '+('不截断'*20);username='long_username_1234567890123456789';u=self.f.approve(456,name,username);v=self.f.approve(789,'无用户名')
  self.s.submit_daily(u,self.f.raw(note='完整备注\n保留第二行'),'synthetic');self.s.set_roster(self.s.c.owner,'2026-10-10',[u,v],'模拟名单')
  path=self.s.c.data/'exports/names.xlsx';data=export_data(self.s);n={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
  for _ in range(2):
   write_excel(data,path)
   with zipfile.ZipFile(path) as z:
    sh=E.fromstring(z.read('xl/worksheets/sheet2.xml'));styles=E.fromstring(z.read('xl/styles.xml'));xf=styles.findall('s:cellXfs/s:xf',n);c={x.get('r'):x for x in sh.findall('.//s:c',n)}
    value=lambda ref:c[ref].find('s:v',n).text if c[ref].find('s:v',n) is not None else ''.join(c[ref].find('s:is',n).itertext())
    self.assertEqual([value(chr(65+i)+'5') for i in range(8)],['员工名称','Telegram 用户名','新增','维聊','有效','精聊','备注','提交状态'])
    self.assertEqual(value('A6'),name);self.assertEqual(value('B6'),'@'+username);self.assertEqual(value('B7'),'—')
    self.assertEqual(value('G6'),'完整备注\n保留第二行')
    for col in 'ABG':self.assertEqual(xf[int(c[col+'6'].get('s'))].find('s:alignment',n).get('horizontal'),'left')
    for col in 'CDEF':
     self.assertIn(c[col+'6'].get('t'),(None,'n'));self.assertEqual(xf[int(c[col+'6'].get('s'))].find('s:alignment',n).get('horizontal'),'center')
    self.assertTrue(all(xf[int(c[col+'5'].get('s'))].find('s:alignment',n).get('horizontal')=='center' for col in 'ABCDEFGH'))
    self.assertEqual(sh.find('.//s:pane',n).get('ySplit'),'2');self.assertIn('A4:H4',[x.get('ref') for x in sh.findall('s:mergeCells/s:mergeCell',n)])
    widths={x.get('min'):x.get('width') for x in sh.findall('s:cols/s:col',n)};self.assertEqual(len({widths[str(i)] for i in range(3,7)}),1)
