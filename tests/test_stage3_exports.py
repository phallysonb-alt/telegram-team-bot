"""Real no-artifact/no-Node Excel output, against existing provider contracts."""
from copy import deepcopy
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import load_workbook
from team_bot import config, daily_history, portable_excel, progress_export
from team_bot.progress_portable_xlsx import write as portable_progress
import test_progress_excel_v2 as progress_fixtures


def daily_fixture():
    days=[]
    for day in ('2026-10-09','2026-10-10','2026-10-11'):
        rows=[['模拟员工'+str(i),'@mock'+str(i),i,0,2,1,
               ('=完整问题原文，不能当公式。\n第二行。'*30) if i==0 else '', '已提交 · 迟交'] for i in range(30)]
        days.append(dict(day=day,submitted=30,expected=30,status='已齐',
                         totals=dict(新增=435,维聊=0,有效=60,精聊=30),rows=rows))
    return dict(days=days,label='独立模拟，不计正式数据',revision=3)


def workbook_contract(path):
    """Compare semantic content/style, not volatile ZIP metadata or style IDs."""
    book=load_workbook(path)
    try:
        out={}
        for sh in book:
            cells={}
            for row in sh:
                for c in row:
                    if c.value is None: continue
                    value=c.value
                    if isinstance(value,datetime) and value.time().isoformat()=='00:00:00': value=value.date()
                    cells[c.coordinate]=(value,c.number_format,c.alignment.horizontal,c.alignment.vertical,
                                         c.alignment.wrap_text,c.font.name,c.font.sz,c.font.bold,
                                         c.fill.fgColor.type,c.fill.fgColor.rgb if c.fill.fgColor.type=='rgb' else c.fill.fgColor.indexed,
                                         c.border.left.style,c.border.right.style,c.data_type)
            out[sh.title]=dict(cells=cells,merged=sorted(str(r) for r in sh.merged_cells),freeze=sh.freeze_panes,
                               widths={k:v.width for k,v in sh.column_dimensions.items()},
                               heights={k:v.height for k,v in sh.row_dimensions.items()})
        return out
    finally: book.close()


class Stage3ExportTests(unittest.TestCase):
    def test_real_two_exports_without_codex_node_or_artifact(self):
        daily=daily_fixture();progress,note=progress_fixtures.ProgressExcelTests().data()
        before=deepcopy((daily,progress))
        with tempfile.TemporaryDirectory(prefix='stage3-portable-') as td:
            root=Path(td);(root/'templates').mkdir()
            import shutil
            shutil.copyfile(config.ROOT/'templates/Telegram_Progress_History_Template_v2.xlsx',root/'templates/Telegram_Progress_History_Template_v2.xlsx')
            target=root/'exports';target.mkdir()
            with patch.object(config,'ROOT',root),patch.object(Path,'home',return_value=root/'clean-user'), \
                 patch.object(portable_excel.shutil,'which',return_value=None), \
                 patch.dict(os.environ,DAILY_NODE='',DAILY_ARTIFACT_MODULE=''), \
                 patch('subprocess.run',side_effect=AssertionError('portable export must not spawn Node')):
                self.assertFalse(portable_excel.available_artifact())
                portable_excel.install_if_missing()
                daily_history.write_excel(daily,target/'日报历史记录.xlsx')
                progress_export.write_export(progress,target/'工作进度历史.xlsx')
                portable_excel.write_daily(daily,target/'daily-reference.xlsx')
                portable_progress(progress,target/'progress-reference.xlsx')
            self.assertEqual(workbook_contract(target/'日报历史记录.xlsx'),workbook_contract(target/'daily-reference.xlsx'))
            self.assertEqual(workbook_contract(target/'工作进度历史.xlsx'),workbook_contract(target/'progress-reference.xlsx'))
            book=load_workbook(target/'日报历史记录.xlsx')
            self.assertEqual(book.sheetnames,['每日汇总','员工明细'])
            self.assertEqual(book['每日汇总']['A5'].number_format,'yyyy-mm-dd')
            self.assertEqual(book['每日汇总']['B5'].value,435)
            self.assertEqual(book['每日汇总']['C5'].value,0)
            self.assertEqual(book['员工明细'].freeze_panes,'A3')
            # Reassemble established labelled daily continuation rows; no text loss.
            notes=''.join(c.value or '' for row in book['员工明细'].iter_rows(min_row=6) for c in [row[6]] if c.value)
            self.assertEqual(notes.count('第二行。'),90)
            self.assertTrue(all(c.data_type=='s' for row in book['员工明细'] for c in [row[6]] if isinstance(c.value,str)))
            book.close()
            progress_fixtures.ProgressExcelTests().inspect(target/'工作进度历史.xlsx',note)
            self.assertEqual((daily,progress),before)

    def test_existing_optional_artifact_is_still_selected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'node_modules/@oai/artifact-tool').mkdir(parents=True)
            with patch.dict(os.environ,DAILY_ARTIFACT_MODULE=str(root),DAILY_NODE='node.exe'):
                self.assertEqual(portable_excel.artifact_runtime(),('node.exe',root))
                self.assertFalse(portable_excel.install_if_missing())
            manifest=json.loads((config.ROOT/'scripts/package.json').read_text())
            self.assertNotIn('@oai/artifact-tool',manifest.get('dependencies',{}))
            self.assertNotIn('@oai/artifact-tool',manifest.get('optionalDependencies',{}))


if __name__=='__main__':unittest.main()
