"""Explicit tested commit/push workflow. No business imports, deploys or polling."""
import argparse,datetime,json,os,shutil,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
from repo_guard import GuardError,git,scan,scan_worktree,known_values,scan_blob,policy

ROOT=Path(__file__).resolve().parents[1]

def text(p):return p.stdout.decode('utf8',errors='replace').strip()
def gitdir(root):return Path(text(git(root,'rev-parse','--absolute-git-dir')))
def tree(root):return text(git(root,'write-tree'))
def head(root):return text(git(root,'rev-parse','--verify','HEAD',check=False))
def receipt_path(root,tid):return gitdir(root)/'sync-validations'/(tid+'.json')

def receipt(root,tid,checks):
    if not checks or any(x.get('exit_code')!=0 for x in checks):raise GuardError('没有有效的已通过测试')
    value=dict(tree=tid,checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),checks=checks)
    dest=receipt_path(root,tid);dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8');os.replace(temp,dest)

def require_receipt(root,tid):
    try:v=json.loads(receipt_path(root,tid).read_text(encoding='utf8'))
    except (OSError,ValueError):raise GuardError('当前版本没有测试凭证；使用sync-github.cmd sync')
    if v.get('tree')!=tid or not v.get('checks') or any(x.get('exit_code')!=0 for x in v['checks']):raise GuardError('测试凭证与版本不匹配')
    return v

def gh_executable():
    found=shutil.which('gh')
    if found:return found
    # Official winget user install; resolve from the current user, never a fixed account.
    base=Path(os.getenv('LOCALAPPDATA',''))/'Microsoft/WinGet/Packages'
    candidates=list(base.glob('GitHub.cli_*/bin/gh.exe')) if base.is_dir() else []
    if len(candidates)==1:return str(candidates[0])
    raise GuardError('未找到gh，请安装并完成GitHub授权')

def repo_slug(url):
    if url.startswith('git@github.com:'):slug=url.split(':',1)[1]
    else:
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname!='github.com' or p.username or p.password or p.query or p.fragment:raise GuardError('origin必须是无内嵌凭据的GitHub地址')
        slug=p.path.lstrip('/')
    slug=slug.removesuffix('.git')
    if len(slug.split('/'))!=2 or any(not s or not all(c.isalnum() or c in '-_.' for c in s) for s in slug.split('/')):raise GuardError('origin仓库地址无效')
    return slug

def authorized_remote(root):
    fetch=text(git(root,'remote','get-url','origin'));slug=repo_slug(fetch)
    rule=policy(root).get('github_sync',{})
    if rule.get('repository','').lower()!=slug.lower():raise GuardError('origin与已授权仓库不符；拒绝向其他仓库同步')
    push_urls=text(git(root,'remote','get-url','--push','--all','origin')).splitlines()
    if not push_urls or any(repo_slug(u)!=slug for u in push_urls):raise GuardError('推送地址与origin不同，需先检查')
    p=subprocess.run([gh_executable(),'repo','view',slug,'--json','nameWithOwner,isPrivate,viewerPermission'],capture_output=True)
    if p.returncode:raise GuardError('GitHub暂不可访问或无仓库权限；本地提交保留')
    data=json.loads(p.stdout)
    if data.get('nameWithOwner','').lower()!=slug.lower() or not isinstance(data.get('isPrivate'),bool):raise GuardError('目标仓库身份或可见性无法核实')
    if not data['isPrivate'] and rule.get('allow_public') is not True:raise GuardError('目标为公开仓库，但尚未获得公开同步授权')
    if data.get('viewerPermission') not in ('ADMIN','MAINTAIN','WRITE'):raise GuardError('当前GitHub账户没有已核实的推送权限')
    return slug

def ahead_commits(root):
    return text(git(root,'rev-list','HEAD','--not','--remotes=origin')).splitlines()

def validate_outgoing(root,commits):
    for commit in commits:
        tid=text(git(root,'rev-parse',commit+'^{tree}'))
        scan(root,commit);require_receipt(root,tid)
        scan_blob('README.md',git(root,'log','-1','--format=%B',commit).stdout,policy(root),known_values(root))

def push(root):
    authorized_remote(root)
    branch=text(git(root,'symbolic-ref','--short','HEAD'))
    if git(root,'fetch','origin','--prune',check=False).returncode:raise GuardError('网络或远程读取失败；本地提交已保留，稍后retry')
    remote='refs/remotes/origin/'+branch
    if not git(root,'rev-parse','--verify',remote,check=False).returncode:
        if git(root,'merge-base','--is-ancestor',remote,'HEAD',check=False).returncode:raise GuardError('远程存在其他修改；需检查冲突，本工具不强推或覆盖')
    validate_outgoing(root,ahead_commits(root))
    if git(root,'push','--set-upstream','origin','HEAD:refs/heads/'+branch,check=False).returncode:raise GuardError('推送失败；本地提交保留，恢复后retry')
    local=head(root);remote_oid=text(git(root,'ls-remote','origin','refs/heads/'+branch)).split()[0]
    if local!=remote_oid:raise GuardError('推送后远端版本不一致，需检查')
    print('同步成功；本地与GitHub一致：'+local)

def checks_for(root,args):
    if args.checks_json:
        checks=json.loads(Path(args.checks_json).read_text(encoding='utf8'))
        if not isinstance(checks,list) or not checks or any(not isinstance(x,list) or not x or any(not isinstance(a,str) or '\0' in a for a in x) for x in checks):raise GuardError('相关测试必须是非空argv数组列表')
        return checks
    if args.profile=='docs':
        changed=text(git(root,'diff','--cached','--name-only',*(['HEAD'] if head(root) else []))).splitlines()
        if not changed or any(not x.endswith('.md') for x in changed):raise GuardError('docs配置仅适用于纯Markdown修改')
        return [[sys.executable,'scripts/github_sync.py','check-docs'],[sys.executable,'-m','unittest','discover','-s','tests','-p','test_github_sync.py','-q']]
    return [['{python}','-m','team_bot','test'],['{node}','--test','tests/test_miniapp_people_front.mjs','tests/test_miniapp_edge.mjs','tests/test_worker_stability.mjs'],['{python}','-m','unittest','discover','-s','migration','-p','test_migration_tools.py','-q']]

def node_executable():
    found=shutil.which('node')
    if found:return found
    candidate=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    if candidate.is_file():return str(candidate)
    raise GuardError('缺少Node.js，无法完成前端测试')

def run_checks(root,checks):
    results=[];folder=gitdir(root)/'sync-test-logs';folder.mkdir(exist_ok=True)
    for i,argv in enumerate(checks):
        # Receipts/commit messages use portable names, never local account paths.
        recorded=['{python}' if a==sys.executable else a for a in argv]
        scan_blob('README.md',json.dumps(recorded).encode(),policy(root),known_values(root))
        actual=[sys.executable if a=='{python}' else node_executable() if a=='{node}' else a for a in recorded]
        print('运行相关检查 '+str(i+1)+'/'+str(len(checks)),flush=True)
        try:p=subprocess.run(actual,cwd=root,capture_output=True,timeout=900)
        except (OSError,subprocess.TimeoutExpired):raise GuardError('检查无法执行或超时；未创建提交')
        raw=p.stdout+p.stderr
        # Local test evidence stays in .git. Redact known credentials even on errors.
        for value in known_values(root):raw=raw.replace(value,b'[REDACTED]')
        (folder/('latest-'+str(i+1)+'.txt')).write_bytes(raw)
        results.append(dict(command=recorded,exit_code=p.returncode))
        if p.returncode:raise GuardError('相关检查失败；详情在.git/sync-test-logs，未提交未推送')
    return results

def check_docs(root):
    files=[Path(root)/name for name in ['README.md','AGENTS.md','docs/GitHub托管与自动同步.md']]
    for f in files:
        if not f.is_file() or not f.read_text(encoding='utf8').strip():raise GuardError('必要文档缺失')
    scan_worktree(root);print('必要文档与保密检查通过')

def install(root):
    old=text(git(root,'config','--local','--get','core.hooksPath',check=False))
    if old and old!='.githooks':raise GuardError('已有其他钩子配置，不能覆盖')
    git(root,'config','--local','core.hooksPath','.githooks')
    print('本仓库提交/推送检查已启用')

def sync(root,args):
    install(root);print('工作区扫描文件：'+str(scan_worktree(root)))
    git(root,'add','--all');count=scan(root);tid=tree(root)
    if head(root) and text(git(root,'rev-parse','HEAD^{tree}'))==tid:
        print('无新的源码修改；检查已提交版本后补推');push(root);return
    checks=checks_for(root,args);results=run_checks(root,checks)
    if tree(root)!=tid or git(root,'diff','--quiet',check=False).returncode:raise GuardError('测试期间源码发生变化，请检查后重新测试')
    if args.profile=='docs':check_docs(root)
    scan(root);receipt(root,tid,results)
    body='Validation-Tree: '+tid+'\nValidation-Checks: '+json.dumps(results,ensure_ascii=False)+'\nValidation: passed; '+str(count)+' files scanned'
    scan_blob('README.md',(args.message+'\n'+body).encode(),policy(root),known_values(root))
    if git(root,'commit','-m',args.message,'-m',body,check=False).returncode:raise GuardError('提交未完成；请检查钩子或Git身份配置')
    print('本地已提交：'+head(root),flush=True);push(root)

def main():
    parser=argparse.ArgumentParser(description='经过扫描、测试的GitHub同步；不触碰业务运行')
    parser.add_argument('action',choices=['sync','retry','status','install-hooks','scan','hook-commit','hook-push','check-docs'])
    parser.add_argument('--message',default='chore: validated project update');parser.add_argument('--profile',choices=['full','docs'],default='full');parser.add_argument('--checks-json');parser.add_argument('--remote-name');parser.add_argument('--remote-url')
    args=parser.parse_args();root=ROOT
    if args.action=='install-hooks':install(root)
    elif args.action=='scan':print('暂存扫描通过：'+str(scan(root))+'个文件')
    elif args.action=='check-docs':check_docs(root)
    elif args.action=='sync':sync(root,args)
    elif args.action=='retry':push(root)
    elif args.action=='status':
        print('本地版本：'+(head(root) or '尚未提交'));print('未同步本地提交：'+str(len(ahead_commits(root)) if head(root) else 0));print('工作区变更数：'+str(len(text(git(root,'status','--porcelain')).splitlines())))
    elif args.action=='hook-commit':scan(root);require_receipt(root,tree(root))
    elif args.action=='hook-push':
        slug=authorized_remote(root);commits=[]
        if args.remote_name!='origin' or not args.remote_url or repo_slug(args.remote_url)!=slug:raise GuardError('拒绝向未授权的其他远程推送')
        for line in sys.stdin:
            local_ref,local_oid,remote_ref,remote_oid=line.split()
            if set(local_oid)=={'0'}:raise GuardError('自动同步不允许删除远程分支')
            if set(remote_oid)!={'0'}:
                if git(root,'merge-base','--is-ancestor',remote_oid,local_oid,check=False).returncode:raise GuardError('拒绝覆盖远程历史')
                commits+=text(git(root,'rev-list',remote_oid+'..'+local_oid)).splitlines()
            else:commits+=text(git(root,'rev-list',local_oid,'--not','--remotes=origin')).splitlines()
        validate_outgoing(root,list(dict.fromkeys(commits)))

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf8',errors='replace')
    try:main()
    except (GuardError,ValueError,OSError) as e:print('未同步：'+str(e),file=sys.stderr);sys.exit(1)
