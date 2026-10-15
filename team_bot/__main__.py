import argparse
import asyncio
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from .config import load, ROOT
from .service import Service
from .maintenance import InstanceLock, backup, restore

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def setup_logging(config):
    folder = config.data / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / 'bot.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    class Redact(logging.Filter):
        def filter(self, record):
            import re
            message = record.getMessage()
            for secret in (config.token, config.key):
                if secret:
                    message = message.replace(secret, '[密钥已隐藏]')
            record.msg = re.sub(r'https?://\S+|\b\d{6,}:[A-Za-z0-9_-]+', '[地址已隐藏]', message)
            record.args = ()
            record.exc_info = None
            return True
    handler.addFilter(Redact())
    # 第三方 HTTP 日志可能包含 Token URL，直接关闭。
    for name in ('httpx', 'httpcore', 'telegram', 'google_genai', 'google.auth'):
        logging.getLogger(name).disabled = True
    logging.basicConfig(level=logging.WARNING, handlers=[handler])


def configure():
    from dotenv import dotenv_values
    from getpass import getpass
    target = ROOT / '.env'
    values = dict(dotenv_values(ROOT / '.env.example'))
    if target.exists():
        values.update(dotenv_values(target))
    print('交互配置；输入为空时保留旧值。定时任务默认关闭，之后在 Telegram 明确确认。')
    secret_fields = {key for key in values if any(part in key.upper() for part in ('TOKEN','KEY','SECRET','AUTH'))}
    for key in values:
        if key == 'SCHEDULE_ENABLED':
            values[key] = 'false'
            continue
        prompt = key + ('（密钥，不显示）' if key in secret_fields else f' [{values[key] or "未填写"}]') + ': '
        result = getpass(prompt) if key in secret_fields else input(prompt)
        if result.strip():
            values[key] = result.strip()
    if values.get('AI_ENABLED') == 'true' and values.get('FREE_PROJECT_CONFIRMED') != 'true':
        values['AI_ENABLED'] = 'false'
        print('未核实免费项目，已保持 AI 关闭。')
    # dotenv 使用双引号支持转义；不通过 shell 插值。
    target.write_text('\n'.join(k + '=' + json.dumps(v or '', ensure_ascii=False) for k, v in values.items()) + '\n', encoding='utf-8')
    print('配置已保存至项目 .env，请勿公开。')


def diagnose(service):
    import importlib.metadata
    c = service.c
    print('Python：' + sys.version.split()[0])
    for name in ('python-telegram-bot', 'google-genai', 'python-docx', 'pypdf', 'python-dotenv', 'tzdata'):
        print(name + '：' + importlib.metadata.version(name))
    print('SQLite：' + __import__('sqlite3').sqlite_version)
    print('FTS5：' + ('可用' if service.db.fts else '不可用，启用中文关键词回退'))
    print('数据库完整性：' + service.db.run('PRAGMA integrity_check').fetchone()[0])
    print('时区：' + c.timezone + '；本地时间：' + c.now().isoformat())
    print('数据目录：' + str(c.data))
    from .scheduler import Scheduler
    scheduler = Scheduler(service)
    print('定时任务已确认：' + str(scheduler.schedule_enabled()))
    print('Token 已配置：' + str(bool(c.token)) + '；管理员 ID 已配置：' + str(bool(c.owner)))
    print('Gemini Key/模型已配置：' + str(bool(c.key and c.model)))
    print('待发送消息：' + str(service.db.one("SELECT COUNT(*) AS n FROM outbox WHERE state='pending'")['n']))
    print('本诊断不调用真实接口，不验证群权限或免费额度。Telegram 运行后管理员使用 /diagnose。')


async def ai_probe(service):
    if not (service.c.ai_enabled and service.c.free_confirmed):
        raise ValueError('需明确启用 AI 并核实免费项目后再探测；探测本身可能消耗实际账户额度')
    from .ai import AI
    answer, status = await AI(service).generate('管理员模型探测', '请用简体中文回复：连接成功')
    print(status)
    print(answer or '探测未成功，继续使用本地功能')
    print('调用成功只能证明当前接口可用，不能单凭响应证明账单为零。请在 AI Studio 核对免费层与项目账单。')


def main():
    parser = argparse.ArgumentParser(description='Telegram 团队工作机器人')
    parser.add_argument('command', choices=['run', 'configure', 'diagnose', 'demo', 'backup', 'restore', 'import-materials', 'ai-probe', 'paths', 'test'])
    parser.add_argument('path', nargs='?')
    args = parser.parse_args()
    if args.command == 'configure':
        configure()
        return
    if args.command == 'test':
        import unittest
        from .portable_excel import install_if_missing
        install_if_missing()
        from .regression import suite as current_suite
        suite = current_suite()
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    c = load()
    if args.command == 'paths':
        print(str(c.data))
        return
    if args.command == 'restore':
        if not args.path:
            raise ValueError('请指定备份 ZIP 路径；恢复前先停止机器人')
        print(restore(c, Path(args.path)))
        return
    if args.command == 'demo':
        # 永不写入真实 team.sqlite3。
        c.data = c.data / 'demo'
        c.backups = c.data / 'backups'
        c.owner = 99999
    from .portable_excel import install_if_missing
    install_if_missing() # No effect on existing artifact-tool installations or daily rules.
    s = Service(c)
    try:
        setup_logging(c)
        if args.command == 'run':
            from .bot import run
            run(s)
        elif args.command == 'diagnose':
            diagnose(s)
        else:
            with InstanceLock(c.data):
                if args.command == 'demo':
                    from .demo import seed, EXPECTED
                    r = seed(s)
                    if r['totals'] != EXPECTED:
                        raise AssertionError('模拟合计与固定期望不一致')
                    print(s.render_summary(r))
                    print('固定期望合计：' + json.dumps(EXPECTED, ensure_ascii=False))
                elif args.command == 'backup':
                    print('备份已保存：' + str(backup(s)))
                elif args.command == 'import-materials':
                    from .knowledge import Knowledge
                    if not c.owner:
                        raise ValueError('请先配置本地 OWNER_USER_ID')
                    folder = Path(args.path or str(c.data / 'inbox'))
                    if not folder.is_dir():
                        raise ValueError('请指定本地资料文件夹')
                    for path in folder.iterdir():
                        if path.suffix.lower() in ('.txt', '.md', '.docx', '.pdf'):
                            print(path.name, Knowledge(s).import_file(c.owner, path))
                elif args.command == 'ai-probe':
                    asyncio.run(ai_probe(s))
    finally:
        s.db.close()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, PermissionError) as e:
        print('操作未完成：' + str(e), file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        logging.getLogger(__name__).error('启动或运行失败：%s', type(e).__name__)
        print('启动或运行失败：' + type(e).__name__ + '；请运行诊断并检查脱敏日志。', file=sys.stderr)
        raise SystemExit(1)
