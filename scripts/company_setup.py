"""Windows company installer. No business changes, polling, bootstrap or SaaS.

prepare is standard-library only, so it runs before installing project dependencies.
All mutable operations require our marker in a separate installation directory.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.company-install.json'


class SetupError(RuntimeError):
    """Only explicitly sanitized operator messages may leave the installer."""


def atomic(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.install-tmp')
    tmp.write_text(value, encoding='utf-8')
    os.replace(tmp, path)


@contextlib.contextmanager
def install_lock(root, name='.company-install.lock'):
    # One installer; separate from the bot's existing process/instance lock.
    f = (root / name).open('a+b')
    try:
        if not f.seek(0, 2):
            f.write(b'0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise SetupError('另一个安装程序正在运行；请等待其结束。') from None
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        # Closing the handle releases the OS lock, including after interruption.
        f.close()


def state(root):
    try: s = json.loads((root / MARKER).read_text(encoding='utf-8'))
    except (OSError, ValueError): raise SetupError('非独立安装目录；请从下载的源码运行 setup-company.cmd。') from None
    if s.get('schema') != 1 or not re.fullmatch('[a-f0-9]{24}', s.get('installation', '')):
        raise SetupError('安装标识无效；不要在已有公司项目上初始化新公司。')
    return s


def save_state(root, s):
    atomic(root / MARKER, json.dumps(s, ensure_ascii=False, indent=2))


def prepare(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise SetupError('运行目录必须与下载源码分离，不能互相包含。')
    if target.is_symlink() or (target.exists() and getattr(target, 'is_junction', lambda: False)()):
        raise SetupError('运行目录不能是链接或目录联接。')
    if target.exists() and any(target.iterdir()) and not (target / MARKER).is_file():
        raise SetupError('目标目录已有文件且不属于本安装器；请选择空目录，禁止覆盖。')
    target.mkdir(parents=True, exist_ok=True)
    with install_lock(target):
        if (target / MARKER).exists():
            s = state(target)
        else:
            s = dict(schema=1, installation=secrets.token_hex(12), steps={}, manifest={})
            save_state(target, s)
        # Manifest allowlist, not a recursive company folder copy. No Git needed.
        sys.path.insert(0, str(source / 'scripts'))
        from repo_guard import permitted, scan_blob
        policy = json.loads((source / 'repo-policy.json').read_text(encoding='utf-8'))
        names = set(policy['files']) | set(policy['templates'])
        for folder, suffixes in policy['source_roots'].items():
            names.update(p.relative_to(source).as_posix() for p in (source / folder).rglob('*')
                         if p.is_file() and p.suffix in suffixes and permitted(p.relative_to(source).as_posix(), policy))
        for name in sorted(names):
            src, dest = source / name, target / name
            if src.is_symlink() or not src.is_file():
                raise SetupError('源码包不完整或存在链接：' + name)
            raw = src.read_bytes()
            scan_blob(name, raw, policy, [])
            digest = hashlib.sha256(raw).hexdigest()
            if name in s['manifest'] and s['manifest'][name] != digest:
                raise SetupError('下载源码版本与已安装版本不同；请使用原安装包继续，升级另按文档执行。')
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                raise SetupError('目标源码已有修改，未覆盖：' + name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists(): dest.write_bytes(raw)
            s['manifest'][name] = digest
        s['steps']['source'] = True
        save_state(target, s)
    return target


def read_env(root):
    # Installer-generated dotenv uses JSON double-quoted strings (no expansion).
    result = {}
    for line in (root / '.env').read_text(encoding='utf-8-sig').splitlines():
        if not line or line.startswith('#') or '=' not in line: continue
        k, v = line.split('=', 1)
        v = v.strip()
        result[k.strip()] = json.loads(v) if v.startswith('"') else v.strip("'")
    return result


def write_env(root, values):
    atomic(root / '.env', '\n'.join(k + '=' + json.dumps(v, ensure_ascii=False) for k, v in values.items()) + '\n')


def validate_config(v):
    if not re.fullmatch(r'\d{6,14}:[A-Za-z0-9_-]{30,}', v.get('TELEGRAM_BOT_TOKEN', '')):
        raise SetupError('Bot Token格式无效；在本公司BotFather核对，密钥不会显示。')
    if not v.get('OWNER_USER_ID', '').isdigit() or int(v['OWNER_USER_ID']) < 1:
        raise SetupError('所有者必须是本人Telegram数字user_id，不是用户名。')
    if not re.fullmatch(r'-100\d+', v.get('ALLOWED_CHAT_ID', '')):
        raise SetupError('工作群应为启用话题的超级群，数字ID通常以-100开头。')
    topics = [v.get(k + '_THREAD_ID', '') for k in ('REPORT', 'PROGRESS')]
    if any(not x.isdigit() or int(x) <= 0 for x in topics) or topics[0] == topics[1]:
        raise SetupError('日报和进度必须填写两个不同的实际话题ID；0不代表General。')
    if v.get('RUN_MODE') != 'trial' or v.get('SCHEDULE_ENABLED') != 'false':
        raise SetupError('快速安装只支持独立验收模式且正式调度关闭；不修改已有运营配置。')
    if v.get('DATA_DIR') != 'data/company-trial' or v.get('BACKUP_DIR') != 'data/company-trial/backups':
        raise SetupError('安装期数据目录必须留在本公司运行目录内。')
    if len(v.get('PROGRESS_BRIDGE_SECRET', '')) < 40:
        raise SetupError('缺少独立桥接密钥，请恢复本公司配置备份，不复用其他公司密钥。')


def configure(root, ask=input, ask_secret=None):
    s = state(root)
    if (root / '.env').exists():
        values = read_env(root)
        validate_config(values)
        return values
    if ask_secret is None:
        from getpass import getpass
        ask_secret = getpass
    values = {}
    for line in (root / '.env.example').read_text(encoding='utf-8-sig').splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1); values[k] = v
    values.update(TELEGRAM_BOT_TOKEN=ask_secret('本公司的Bot Token（隐藏输入）：').strip(),
                  OWNER_USER_ID=ask('所有者Telegram数字user_id：').strip(),
                  ALLOWED_CHAT_ID=ask('工作群数字ID（-100开头）：').strip(),
                  REPORT_THREAD_ID=ask('每日报表实际话题ID：').strip(),
                  PROGRESS_THREAD_ID=ask('工作进度实际话题ID：').strip(),
                  PROGRESS_BRIDGE_SECRET=secrets.token_urlsafe(48),
                  DATA_DIR='data/company-trial', BACKUP_DIR='data/company-trial/backups',
                  RUN_MODE='trial', SCHEDULE_ENABLED='false', AI_ENABLED='false',
                  FINAL_VERIFY_ENABLED='false', TRAINING_MODE='directory', DESKTOP_HISTORY_DIR='')
    validate_config(values)
    write_env(root, values)
    s['steps']['config'] = True; save_state(root, s)
    return values


def initialize(root, values):
    state(root); validate_config(values)
    # Reuse real schema/migrations. Config is explicit; never load source .env.
    sys.path.insert(0, str(root))
    from team_bot.config import Config
    from team_bot.service import Service
    from team_bot.progress_rounds import Progress
    c = Config(data=root / values['DATA_DIR'], backups=root / values['BACKUP_DIR'],
               owner=int(values['OWNER_USER_ID']), run_mode='trial', test_run='company-trial',
               schedule_allowed=False, identity_mode='telegram', report_version=2)
    with install_lock(root):
        s = Service(c)
        try:
            p = Progress(s)
            try:
                for db in (s.db, p.db):
                    if db.one('PRAGMA integrity_check')['integrity_check'] != 'ok':
                        raise SetupError('数据库完整性检查失败；保留原数据，先恢复私有备份。')
            finally: p.db.close()
        finally: s.db.close()
        checkpoint = state(root); checkpoint['steps']['database'] = True; save_state(root, checkpoint)


def runtime_env():
    # Do not inherit another company's dotenv values into subprocesses.
    from dotenv import dotenv_values
    private = set(dotenv_values(ROOT / '.env.example'))
    return {k: v for k, v in os.environ.items() if k not in private}


class Runner:
    def __init__(self, root):
        self.root = root
        self.env = runtime_env()
        self.env.update(WRANGLER_SEND_METRICS='false', NO_COLOR='1')

    def __call__(self, argv, *, input=None, interactive=False, timeout=300):
        try:
            p = subprocess.run(argv, cwd=self.root / 'deploy', env=self.env,
                               input=input, text=True, encoding='utf-8', errors='replace',
                               capture_output=not interactive, timeout=timeout,
                               **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' and not interactive else {}))
        except (OSError, subprocess.TimeoutExpired):
            raise SetupError('外部安装/部署步骤无法执行或超时；检查网络、Node和授权后重新运行。') from None
        if p.returncode:
            # Raw CLI output may include credentials, never log or relay it.
            raise SetupError('Cloudflare命令未成功（退出码' + str(p.returncode) + '）；检查登录、账户权限、网络或额度，重试可恢复。')
        return p.stdout or ''


def wrangler(root, node=None):
    node = node or shutil.which('node')
    cli = root / 'deploy/node_modules/wrangler/bin/wrangler.js'
    if not node or not cli.is_file():
        raise SetupError('缺少Node/Wrangler依赖；重新运行 setup-company.cmd。')
    return [str(node), str(cli)]


def json_output(raw):
    try: return json.loads(raw)
    except (ValueError, TypeError): raise SetupError('Cloudflare未返回预期JSON；请核对锁定的Wrangler版本。') from None


def choose_account(root, run, cli, ask=input):
    try:
        info = json_output(run(cli + ['whoami', '--json']))
        if not info.get('loggedIn'): raise SetupError('尚未登录')
    except SetupError:
        print('Cloudflare需要本人浏览器授权；不读取或输出授权凭据。')
        run(cli + ['login'], interactive=True)
        info = json_output(run(cli + ['whoami', '--json']))
    accounts = info.get('accounts', [])
    accounts = [a for a in accounts if re.fullmatch('[a-fA-F0-9]{32}', a.get('id', ''))]
    bound = state(root).get('account')
    if bound:
        if bound not in [a['id'] for a in accounts]:
            raise SetupError('当前授权不包含本安装已绑定账户；请用本公司原账户登录后重试。')
        return bound
    if len(accounts) == 1: return accounts[0]['id']
    if not accounts:
        raise SetupError('无法读取可用Cloudflare账户；请核对账户成员及Worker/D1权限后重试。')
    print('请选择本公司的Cloudflare账户，不使用其他公司账户：')
    for i, a in enumerate(accounts, 1):
        name = ''.join(c for c in str(a.get('name', '未命名')) if c.isprintable())[:60]
        print(str(i) + '. ' + name)
    answer = ask('账户序号：').strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(accounts): raise SetupError('账户序号无效，未创建资源。')
    return accounts[int(answer) - 1]['id']


def deploy_cloud(root, v, account, run=None, cli=None):
    s = state(root); validate_config(v)
    if not re.fullmatch('[a-fA-F0-9]{32}', account):
        raise SetupError('Cloudflare账户ID应为32位十六进制，在本人控制台首页可复制。')
    if s.get('account') and s['account'] != account:
        raise SetupError('本安装已绑定另一Cloudflare账户；未切换账户或覆盖云端资源。')
    run, cli = run or Runner(root), cli or wrangler(root)
    bot_id = v['TELEGRAM_BOT_TOKEN'].split(':')[0]
    if s.get('bot_id') and s['bot_id'] != bot_id:
        raise SetupError('本安装已绑定另一个Bot ID；请使用新目录部署新机器人，禁止复用其身份和历史。')
    s['account'] = account
    name = 'team-progress-' + s['installation'][:12]
    dbname = name + '-relay'
    s.update(worker=name, database_name=dbname)
    save_state(root, s)  # durable intent before resource creation
    cfg = root / 'deploy/wrangler.toml'
    def config(dbid=None):
        text = f'name = "{name}"\nmain = "dist/worker.js"\ncompatibility_date = "2026-10-01"\nworkers_dev = true\naccount_id = "{account}"\n[vars]\nPROGRESS_BOT_ID = "{v["TELEGRAM_BOT_TOKEN"].split(":")[0]}"\nBOOTSTRAP_ALLOWED = "false"\n'
        if dbid: text += f'[[d1_databases]]\nbinding = "DB"\ndatabase_name = "{dbname}"\ndatabase_id = "{dbid}"\n'
        atomic(cfg, text)
    config(s.get('database_id'))
    run.env['CLOUDFLARE_ACCOUNT_ID'] = account
    def step(label, argv, **kwargs):
        s['pending_step'] = label; save_state(root, s)
        try: return run(argv, **kwargs)
        except SetupError as e: raise SetupError(label + '未完成：' + str(e)) from None
    databases = json_output(step('读取本公司D1列表', cli + ['d1', 'list', '--json']))
    matches = [d for d in databases if d.get('name') == dbname]
    if not matches:
        step('创建独立D1', cli + ['d1', 'create', dbname])
        databases = json_output(step('核对独立D1', cli + ['d1', 'list', '--json']))
        matches = [d for d in databases if d.get('name') == dbname]
    if len(matches) != 1 or not re.fullmatch('[a-fA-F0-9-]{36}', matches[0].get('uuid', '')):
        raise SetupError('无法唯一确认本安装专属D1；未部署Worker，请在本公司账户核对。')
    dbid = matches[0]['uuid']
    if s.get('database_id') and s['database_id'] != dbid:
        raise SetupError('D1标识发生变化；未覆盖绑定，请检查原数据库。')
    s['database_id'] = dbid; save_state(root, s); config(dbid)
    schema = (root / 'deploy/0000_miniapp_bridge.sql').read_text(encoding='utf-8')
    schema = schema.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ').replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS ')
    sql = root / 'deploy/company-init.sql'
    atomic(sql, schema)
    step('初始化中转数据库', cli + ['d1', 'execute', dbname, '--remote', '--file', str(sql), '--yes'])
    step('构建Mini App前端', [cli[0], str(root / 'deploy/build-worker.mjs')])
    # D1 validation before the upload; only the installation-owned relay is read.
    step('验证D1表结构', cli + ['d1', 'execute', dbname, '--remote', '--command', 'SELECT key FROM bridge_state LIMIT 1', '--json'])
    output = step('上传Worker及前端', cli + ['deploy', '--config', str(cfg)])
    urls = re.findall(r'https://' + re.escape(name) + r'\.[a-zA-Z0-9-]+\.workers\.dev\b', output)
    origin = urls[-1] if urls else s.get('origin')
    if not origin: raise SetupError('Worker已上传但无法确认HTTPS地址；在控制台启用workers.dev后重试。')
    # Secret only through stdin. It never enters frontend, URL, state or argv.
    step('设置独立桥接密钥', cli + ['secret', 'put', 'PROGRESS_BRIDGE_SECRET', '--config', str(cfg)], input=v['PROGRESS_BRIDGE_SECRET'] + '\n')
    v.update(PROGRESS_MINIAPP_ORIGIN=origin, PROGRESS_MINIAPP_BRIDGE='true')
    write_env(root, v)
    s['origin'] = origin; s['bot_id'] = bot_id
    s['steps']['cloud'] = True; s['pending_step'] = ''; save_state(root, s)
    return origin


def request_json(url, payload=None, headers=None):
    req = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        # Only status, never echo API descriptions/URL with token.
        return e.code, {}
    except (OSError, ValueError): raise SetupError('网络超时或响应无效；检查联网后重试，未改变业务数据。') from None


def telegram(v, method, payload=None, transport=request_json):
    code, body = transport('https://api.telegram.org/bot' + v['TELEGRAM_BOT_TOKEN'] + '/' + method, payload or {})
    if code != 200 or not body.get('ok'):
        raise SetupError('Telegram ' + method + '失败（HTTP ' + str(code) + '）；核对Token、群成员/管理员权限及网络。')
    return body['result']


def verify_telegram(v, transport=request_json):
    bot = telegram(v, 'getMe', transport=transport)
    if not bot.get('is_bot') or str(bot.get('id')) != v['TELEGRAM_BOT_TOKEN'].split(':')[0]:
        raise SetupError('Bot ID与Token不匹配；未启用服务。')
    chat = telegram(v, 'getChat', {'chat_id': v['ALLOWED_CHAT_ID']}, transport)
    if str(chat.get('id')) != v['ALLOWED_CHAT_ID'] or not chat.get('is_forum'):
        raise SetupError('工作群ID不匹配或尚未开启话题。')
    me = telegram(v, 'getChatMember', dict(chat_id=v['ALLOWED_CHAT_ID'], user_id=bot['id']), transport)
    if me.get('status') != 'administrator' or not me.get('can_invite_users') or not me.get('can_pin_messages'):
        raise SetupError('请在本公司工作群授予机器人管理员、邀请用户及置顶权限；不要授予无关权限。')
    owner = telegram(v, 'getChatMember', dict(chat_id=v['ALLOWED_CHAT_ID'], user_id=int(v['OWNER_USER_ID'])), transport)
    if int(v['OWNER_USER_ID']) == bot['id'] or owner.get('user', {}).get('is_bot'):
        raise SetupError('所有者必须填写本人数字user_id，不能填写机器人ID。')
    if owner.get('status') not in ('creator', 'administrator', 'member'):
        raise SetupError('配置的业务所有者不在工作群；不会自动把首位使用者设为管理员。')
    if not bot.get('can_read_all_group_messages'):
        raise SetupError('BotFather /setprivacy 选择本机器人并Disable；按Telegram提示重新加入群后重试。')
    webhook = telegram(v, 'getWebhookInfo', transport=transport)
    if webhook.get('url'):
        raise SetupError('本机器人已启用Webhook，与polling不兼容；先确认旧服务停止，再由本人处理，安装器不自动删除Webhook。')
    return bot


def verify_topics(root, v, transport=request_json):
    s = state(root)
    for kind, title in [('REPORT', '每日报表'), ('PROGRESS', '工作进度')]:
        expected = int(v[kind + '_THREAD_ID'])
        old = s.get('topic_receipts', {}).get(kind, {})
        if old.get('topic') == expected and old.get('chat') == v['ALLOWED_CHAT_ID'] and old.get('bot') == v['TELEGRAM_BOT_TOKEN'].split(':')[0]:
            continue
        receipt = telegram(v, 'sendMessage', dict(chat_id=v['ALLOWED_CHAT_ID'], message_thread_id=expected,
                           text='【安装验收，不计正式数据】' + title + '路由检查'), transport)
        if receipt.get('message_thread_id') != expected or str(receipt.get('chat', {}).get('id')) != v['ALLOWED_CHAT_ID']:
            raise SetupError(title + '话题回执不匹配，未启用启动。')
        s.setdefault('topic_receipts', {})[kind] = dict(topic=expected, message=receipt['message_id'],
                    chat=v['ALLOWED_CHAT_ID'], bot=v['TELEGRAM_BOT_TOKEN'].split(':')[0])
        save_state(root, s)


def topics_verified(s, v):
    return all(s.get('topic_receipts', {}).get(k, {}).get('topic') == int(v[k + '_THREAD_ID'])
               and s['topic_receipts'][k].get('chat') == v['ALLOWED_CHAT_ID']
               and s['topic_receipts'][k].get('bot') == v['TELEGRAM_BOT_TOKEN'].split(':')[0]
               for k in ('REPORT', 'PROGRESS'))


def attach_miniapp(root, v, shortname):
    state(root)
    if not re.fullmatch('[A-Za-z0-9_]{3,64}', shortname):
        raise SetupError('Mini App短名格式无效；应使用BotFather /newapp返回的短名。')
    if not v.get('BOT_USERNAME') or not v.get('PROGRESS_MINIAPP_ORIGIN'):
        raise SetupError('先完成Telegram核验及Cloudflare部署。')
    v['PROGRESS_MINIAPP_LINK'] = 'https://t.me/' + v['BOT_USERNAME'] + '/' + shortname
    v['PROGRESS_MINIAPP_ENABLED'] = 'true'
    write_env(root, v)
    s = state(root); s['steps']['botfather_attested'] = True
    s['miniapp_binding'] = dict(bot=v['BOT_USERNAME'], link=v['PROGRESS_MINIAPP_LINK'], origin=v['PROGRESS_MINIAPP_ORIGIN'])
    save_state(root, s)


def db_check(root, v):
    folder = (root / v['DATA_DIR']).resolve()
    if root.resolve() not in folder.parents: raise SetupError('数据库目录越出本公司目录。')
    count = 0
    for name, table in [('team.sqlite3', 'employees'), ('progress-acceptance.sqlite3', 'pg_rounds')]:
        path = folder / name
        if not path.is_file(): raise SetupError('数据库尚未初始化；重新运行安装入口。')
        with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise SetupError('数据库完整性失败。')
            db.execute('SELECT COUNT(*) FROM ' + table).fetchone()
            if table == 'employees':
                if db.execute("SELECT COUNT(*) FROM employees WHERE role NOT IN ('admin','employee') OR approved NOT IN (0,1) OR active NOT IN (0,1)").fetchone()[0]:
                    raise SetupError('员工权限字段异常；保留数据，请核实人员状态。')
                count = db.execute("SELECT COUNT(*) FROM employees WHERE approved=1 AND active=1 AND role='employee' AND uid<>?", (int(v['OWNER_USER_ID']),)).fetchone()[0]
    return count


def processes(root):
    if os.name != 'nt': raise SetupError('进程核验仅支持Windows。')
    # No secrets/command lines leave PowerShell; count launcher/interpreter as one tree.
    script = "$p=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'team_bot\\s+run' }); $ids=@($p.ProcessId); @($p | Where-Object { $_.ParentProcessId -notin $ids } | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress"
    try:
        p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                           capture_output=True, text=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if p.returncode: raise ValueError()
        result = json.loads(p.stdout or '[]')
    except (OSError, ValueError, subprocess.TimeoutExpired): raise SetupError('无法读取Windows进程；未确认实例数量，请用相同用户重试。') from None
    if isinstance(result, dict): result = [result]
    local = [p for p in result if Path(p.get('ExecutablePath') or '').resolve() == (root / '.venv/Scripts/pythonw.exe').resolve() or Path(p.get('ExecutablePath') or '').resolve() == (root / '.venv/Scripts/python.exe').resolve()]
    return len(local), len(result)


def diagnose(root, online=False, transport=request_json, process_probe=processes, cloud_runner=None):
    state(root)
    results = []
    def check(name, fn):
        try:
            detail = fn(); results.append(dict(item=name, status='通过', detail=detail or '检查完成'))
        except SetupError as e: results.append(dict(item=name, status='失败', detail=str(e)))
        except Exception: results.append(dict(item=name, status='失败', detail='检查未完成；请确认依赖和配置，未输出敏感内容。'))
    try: v = read_env(root); validate_config(v)
    except (OSError, ValueError, SetupError):
        return [dict(item='配置', status='失败', detail='配置缺失或无效；重新运行安装入口，原配置不会被覆盖。')]
    check('隔离配置', lambda: '验收模式；正式调度关闭；独立桥接密钥已配置')
    check('数据库及人员', lambda: str(db_check(root, v)) + '名有效业务员；所有者来自明确数字ID配置')
    def process_check():
        local, total = process_probe(root)
        if local > 1: raise SetupError('本公司目录存在重复polling进程树；先用本公司stop.cmd核查，勿终止其他公司。')
        return f'本目录{local}个polling实例；电脑共{total}个进程树。跨电脑同Token运行无法本地证明，启动前须核对。'
    check('单实例', process_check)
    s = state(root)
    if not s.get('steps', {}).get('cloud'):
        results.append(dict(item='云部署', status='待处理', detail='Cloudflare尚未完成；重新运行安装入口继续。'))
    if not s.get('steps', {}).get('botfather_attested'):
        results.append(dict(item='Mini App入口', status='待处理', detail='需本人在BotFather /newapp配置HTTPS地址并填写短名。'))
    else:
        def binding():
            bound = s.get('miniapp_binding', {})
            if (bound != dict(bot=v.get('BOT_USERNAME'), link=v.get('PROGRESS_MINIAPP_LINK'), origin=v.get('PROGRESS_MINIAPP_ORIGIN'))
                or s.get('origin') != v.get('PROGRESS_MINIAPP_ORIGIN')
                or s.get('bot_id') != v['TELEGRAM_BOT_TOKEN'].split(':')[0]
                or v.get('PROGRESS_MINIAPP_ENABLED') != 'true' or v.get('PROGRESS_MINIAPP_BRIDGE') != 'true'):
                raise SetupError('Mini App地址/机器人绑定与安装记录不一致；核对本公司.env及BotFather配置。')
            return '本地入口绑定一致；BotFather仍需真实客户端最终验收'
        check('Mini App入口', binding)
    if not topics_verified(s, v):
        results.append(dict(item='话题路由', status='待处理', detail='仅配置数值，尚未真实验证；安装器可发送两条明确标记的路由检查消息。'))
    if online:
        def telegram_check():
            current = verify_telegram(v, transport)
            if current.get('username') != v.get('BOT_USERNAME'):
                raise SetupError('机器人用户名已变化；重新运行安装入口同步用户名并核对BotFather链接。')
            return 'Token、Bot ID、所有者及群权限核验完成'
        check('Telegram身份/群/权限', telegram_check)
        origin = v.get('PROGRESS_MINIAPP_ORIGIN', '')
        def https():
            url = urllib.parse.urlsplit(origin)
            if url.scheme != 'https' or not url.hostname or url.username or url.query or url.path not in ('', '/'):
                raise SetupError('Mini App需固定HTTPS源站；先部署本人Worker。')
            code, body = transport(origin + '/health')
            if code != 200 or not body.get('ok'): raise SetupError('Worker HTTPS健康检查失败；核对云部署及网络。')
            code, _ = transport(origin + '/bridge/socket', headers={'Authorization': 'Bearer ' + v['PROGRESS_BRIDGE_SECRET']})
            if code != 426: raise SetupError('桥接认证检查失败（HTTP ' + str(code) + '）；核对本公司Secret与Worker版本。')
            return 'HTTPS及桥接密钥有效；不等同于本地后台在线'
        check('HTTPS与桥接认证', https)
        heartbeats = []
        def d1():
            runner = cloud_runner or Runner(root)
            cli = wrangler(root)
            output = json_output(runner(cli + ['d1', 'execute', s['database_name'], '--remote', '--json', '--command', "SELECT value FROM bridge_state WHERE key='heartbeat'"]))
            if not isinstance(output, list) or not output or not isinstance(output[0].get('results'), list) or output[0].get('success') is False:
                raise SetupError('D1查询没有成功；核对本公司数据库绑定和Cloudflare授权。')
            heartbeats.extend(output[0]['results'])
            return '本公司D1已连接，心跳表可读'
        def heartbeat():
            if not heartbeats or not -5000 <= time.time() * 1000 - float(heartbeats[0]['value']) <= 30000:
                raise SetupError('D1可访问，但桥接未在线/心跳过期；安装完成启动后再次检查。')
            return '桥接最近30秒内在线'
        if s.get('steps', {}).get('cloud'):
            check('D1连接', d1)
            if results[-1]['status'] == '通过': check('本地桥接', heartbeat)
    return results


def wizard(root, offline=False):
    state(root)
    v = configure(root)
    initialize(root, v)
    print('独立数据库已初始化/检查；未添加模拟员工，未启动polling。')
    if offline:
        print('离线阶段完成。联网后再次运行安装器继续Cloudflare/BotFather及真实检查。')
        return 0
    bot = verify_telegram(v)
    v['BOT_USERNAME'] = bot['username']; write_env(root, v)
    s = state(root)
    if not s.get('steps', {}).get('cloud'):
        print('以下只部署本次独立安装自己的Worker和D1，不迁移任何既有Sites。Cloudflare可能要求本人浏览器授权。')
        run = Runner(root); cli = wrangler(root)
        account = choose_account(root, run, cli)
        origin = deploy_cloud(root, v, account, run, cli)
        print('本公司固定HTTPS地址：' + origin)
    else:
        print('已部署，复用本次安装的HTTPS：' + s['origin'])
    if not state(root).get('steps', {}).get('botfather_attested'):
        print('请本人在BotFather发送 /newapp，选择本机器人，按提示填写名称/图标/简介及上面的HTTPS地址。完成后输入返回的短名；暂不能完成可直接关闭，下次继续。')
        attach_miniapp(root, v, input('BotFather Mini App短名：').strip())
    if not topics_verified(state(root), v):
        if input('允许向两个配置话题各发一条安装验收消息以核对路由？y/其他跳过：').lower() == 'y':
            verify_topics(root, v)
    print_results(diagnose(root, online=True))
    print('电脑关机或休眠时本地业务服务停止；云端页面仍可打开，但无法保存。')
    if input('如确认本Bot未在其他电脑polling，输入 START 启动此新公司唯一验收实例（其余保留停止）：') == 'START':
        check = diagnose(root, online=True)
        # Bridge-offline is expected before first start; other failures block startup.
        if any(x['status'] != '通过' for x in check if x['item'] != '本地桥接'):
            raise SetupError('启动前检查尚未通过；按失败项目处理后重新运行，不会启动半配置实例。')
        local, _ = processes(root)
        if not local:
            subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'scripts/start.ps1')], cwd=root, env=runtime_env(), check=True)
        if input('是否安装当前Windows用户登录自启动？y/其他跳过：').lower() == 'y':
            subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'scripts/autostart.ps1'), 'install'], cwd=root, env=runtime_env(), check=True)
        time.sleep(3)
        print_results(diagnose(root, online=True))
    return 0


def print_results(results):
    for r in results: print('[' + r['status'] + '] ' + r['item'] + '：' + r['detail'])


def main():
    parser = argparse.ArgumentParser(description='Windows新公司独立安装/诊断；不会自动发布正式业务')
    parser.add_argument('action', choices=['prepare', 'wizard', 'diagnose', 'cloud'])
    parser.add_argument('--target', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--online', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt': raise SetupError('仅支持Windows部署；其他系统可运行隔离测试但不是部署目标。')
    if args.action == 'prepare':
        if not args.target: parser.error('prepare需要--target')
        print('独立源码目录已准备：' + str(prepare(ROOT, args.target))); return 0
    if args.action == 'wizard':
        with install_lock(ROOT, '.company-wizard.lock'):
            return wizard(ROOT, args.offline)
    if args.action == 'cloud':
        v = read_env(ROOT); verify_telegram(v)
        runner = Runner(ROOT); cli = wrangler(ROOT)
        deploy_cloud(ROOT, v, choose_account(ROOT, runner, cli), runner, cli); return 0
    results = diagnose(ROOT, args.online); print_results(results)
    return 0 if all(r['status'] == '通过' for r in results) else 2


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (SetupError, KeyboardInterrupt) as e:
        print('未完成：' + (str(e) if isinstance(e, SetupError) else '已中断；再次运行可继续。'), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('未完成：本地步骤异常；已有配置和数据保留。检查依赖、磁盘权限后重试；不输出凭据。', file=sys.stderr)
        raise SystemExit(1)
