import unittest
from datetime import date
from team_bot.reports import parse_progress


class ProgressCopyTests(unittest.TestCase):
    def test_flattened_template_preserves_numbers(self):
        raw = '日期：2026-10-10 阶段：上半场 精聊新增：1 引流新增：2 维聊新增：3'
        day, payload = parse_progress(raw, date(2026, 10, 11))
        self.assertEqual(day, '2026-10-10')
        self.assertEqual([payload['上半场' + metric + '新增'] for metric in ('精聊','引流','维聊')], [1,2,3])

    def test_invalid_compact_input_is_not_guessed(self):
        for raw in ('日期：2026-10-10 阶段：上半场 精聊新增：1 引流新增：2',
                    '日期：2026-10-10 阶段：上半场 精聊新增：-1 引流新增：2 维聊新增：3',
                    '日期：2026-10-12 阶段：上半场 精聊新增：1 引流新增：2 维聊新增：3'):
            with self.assertRaises(ValueError):
                parse_progress(raw, date(2026,10,11))
