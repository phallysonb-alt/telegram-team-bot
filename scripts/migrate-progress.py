"""Run only with the bot stopped; additive and repeatable."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from team_bot.config import load
from team_bot.service import Service
from team_bot.progress_rounds import Progress
p=Progress(Service(load()))
print('进度兼容迁移完成；旧查询、反馈、当前问题和审计保留。')
p.db.close();p.s.db.close()
