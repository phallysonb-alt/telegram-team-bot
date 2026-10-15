"""Fail-closed Git content checks. Never print matched secret values."""
import base64,hashlib,html,io,json,os,re,sqlite3,subprocess,zipfile
from contextlib import closing
from pathlib import Path,PurePosixPath
from urllib.parse import unquote,urlsplit

class GuardError(RuntimeError):pass

def git(root,*args,check=True,input=None):
    p=subprocess.run(['git','-C',str(root),*args],input=input,capture_output=True)
    if check and p.returncode:raise GuardError('Git操作失败：'+args[0])
    return p

def known_values(root):
    values=[]
    path=Path(root)/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            m=re.match(r'^([A-Z_0-9]+)\s*=\s*(.*)$',line)
            if not m:continue
            key,value=m.groups();value=value.strip().strip('\"\'')
            private=any(x in key for x in ('TOKEN','KEY','SECRET','AUTH')) or key in ('OWNER_USER_ID','ALLOWED_CHAT_ID','INTERNAL_CHAT_ID','BOT_USERNAME','PROGRESS_MINIAPP_LINK','PROGRESS_MINIAPP_ORIGIN')
            if private and len(value)>=5:
                values.append(value.encode())
                if key=='PROGRESS_MINIAPP_LINK' and urlsplit(value).hostname=='t.me':
                    bot=urlsplit(value).path.strip('/').split('/')[0]
                    if len(bot)>=5:values.append(bot.encode())
                if 'TOKEN' in key and re.fullmatch(r'\d{6,14}:[A-Za-z0-9_-]{30,}',value):values.append(value.split(':')[0].encode())
                if any(x in key for x in ('TOKEN','KEY','SECRET','AUTH')) and len(value)>=16:
                    values.extend([base64.b64encode(value.encode()),json.dumps(value,ensure_ascii=True)[1:-1].encode(),value.encode('utf-16le')])
        # Read-only identity comparison. Never open a new database or dump values.
        fields={m.group(1):m.group(2).strip().strip('\"\'') for line in path.read_text(encoding='utf-8-sig').splitlines() if (m:=re.match(r'^([A-Z_0-9]+)\s*=\s*(.*)$',line))}
        db=(Path(root)/fields.get('DATA_DIR','data')/'team.sqlite3').resolve()
        if db.is_file():
            try:
                with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=2)) as conn:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='tg_profiles'").fetchone():
                        for uid,name,username in conn.execute('SELECT uid,name,username FROM tg_profiles'):
                            if len(str(uid))>=7:values.append(str(uid).encode())
                            if username and len(username)>=4:values.append(username.lstrip('@').encode())
                            if name and len(name)>=2 and re.search(r'[\u4e00-\u9fff]',name):values.append(name.encode())
            except sqlite3.Error:raise GuardError('本地身份比对不可用；未扫描或输出数据库内容')
    return list(dict.fromkeys(v for v in values if v))

PATTERNS=[
 ('Telegram凭据',rb'\b[0-9]{6,14}:[A-Za-z0-9_-]{30,}\b'),
 ('GitHub凭据',rb'\bgh[pousr]_[A-Za-z0-9_]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
 ('Google凭据',rb'\bAIza[A-Za-z0-9_-]{30,}\b'),
 ('私钥',rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
 ('云端凭据',rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}\b'),
 ('高风险凭据常量',rb'''(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|bridge[_-]?secret|password)\s*[=:]\s*["'][A-Za-z0-9_+/=-]{24,}["']'''),
 ('本机用户目录',rb'''(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!Public[\\/]|Default[\\/])[^\\/\s"'<>]+'''),
]
DENIED_PARTS={'.env','.git','.venv','venv','node_modules','__pycache__','.wrangler','.aws','.ssh','data','exports','logs','backups','backup','tmp','temp','screenshots','credentials'}
DENIED_SUFFIX={'.log','.ndjson','.db','.db-wal','.db-shm','.sqlite','.sqlite3','.zip','.7z','.bak','.tmp','.pem','.key','.p12','.pfx','.credentials'}

def policy(root):return json.loads((Path(root)/'repo-policy.json').read_text(encoding='utf8'))

def permitted(name,p):
    path=PurePosixPath(name);parts=path.parts
    if name.startswith('/') or '..' in parts or '\\' in name:return False
    if name=='.env.example':return True
    if any(x.lower() in DENIED_PARTS or x.lower().startswith('.env') for x in parts):return False
    if path.suffix.lower() in DENIED_SUFFIX or '.sqlite' in path.name.lower() or path.name.startswith('~$'):return False
    if path.name.lower() in ('hosts.yml','wrangler.toml','wrangler.json','wrangler.jsonc','id_rsa','id_ed25519'):return False
    if name in p['files'] or name in p['templates']:return True
    return any(name.startswith(prefix) and path.suffix.lower() in suffixes for prefix,suffixes in p['source_roots'].items())

def scan_blob(name,raw,p,known=()):
    if not permitted(name,p):raise GuardError(name+'：文件类型或路径未获允许')
    if len(raw)>15_000_000:raise GuardError(name+'：文件过大，需专项审核')
    if name in p['templates']:
        if hashlib.sha256(raw).hexdigest()!=p['templates'][name]:raise GuardError(name+'：格式模板校验值变更，需审核')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(n.file_size for n in z.infolist())>64_000_000:raise GuardError(name+'：模板展开过大')
            if z.testzip():raise GuardError(name+'：模板损坏')
            if any('/embeddings/' in n or n.lower().endswith('vbaproject.bin') for n in z.namelist()):raise GuardError(name+'：模板包含嵌入文件或宏')
            payload=b'\n'.join(z.read(n) for n in z.namelist() if n.endswith(('.xml','.rels','.txt','.json')))
    else:
        if b'\0' in raw:raise GuardError(name+'：不允许的二进制内容')
        payload=raw
    decoded=unquote(html.unescape(payload.decode('utf8',errors='replace'))).encode('utf8')
    if any(v in payload or v in decoded for v in known):raise GuardError(name+'：匹配本地敏感配置或人员标识实值')
    for label,pattern in PATTERNS:
        if re.search(pattern,payload) or re.search(pattern,decoded):raise GuardError(name+'：'+label+'风险（值已隐藏）')

def entries(root,tree=None):
    args=('ls-tree','-rz','--full-tree',tree) if tree else ('ls-files','--stage','-z')
    for item in git(root,*args).stdout.split(b'\0'):
        if not item:continue
        meta,name=item.split(b'\t',1)
        if tree:mode,kind,oid=meta.split(b' ');extra=b'0'
        else:mode,oid,extra=meta.split(b' ')
        if mode not in (b'100644',b'100755') or extra!=b'0':raise GuardError('存在符号链接、子模块或未解决冲突')
        yield name.decode('utf8'),oid.decode()

def scan(root,tree=None):
    p=policy(root);known=known_values(root);count=0
    for name,oid in entries(root,tree):
        scan_blob(name,git(root,'cat-file','blob',oid).stdout,p,known);count+=1
    return count

def candidate_names(root):
    return [x.decode('utf8') for x in git(root,'ls-files','-z','--cached','--others','--exclude-standard').stdout.split(b'\0') if x]

def scan_worktree(root):
    p=policy(root);known=known_values(root);count=0
    for name in set(candidate_names(root)):
        f=Path(root)/name
        if f.is_symlink():raise GuardError(name+'：不允许符号链接')
        if f.is_file():scan_blob(name,f.read_bytes(),p,known);count+=1
    return count
