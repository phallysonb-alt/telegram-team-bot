"""Operator handover acknowledgement; does not start, schedule or change business records."""
import argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--old-pc-stopped',action='store_true');p.add_argument('--authorized-handover',action='store_true');a=p.parse_args()
if not (a.old_pc_stopped and a.authorized_handover):raise SystemExit('先取得迁移切换授权并核实旧电脑机器人及登录守护均已停止；禁止双机polling。')
f=Path(__file__).resolve().parent/'HANDOVER_PENDING'
f.unlink(missing_ok=True)
print('本机启动保护已解除；未启动服务，仍为验收。此确认不能自动核实远端电脑状态。')
