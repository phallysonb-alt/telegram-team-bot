"""Read-only full-object audit. Reports locations/rules, never matched values.

Remote releases/Actions/assets must be inventoried separately; an offline Git
audit cannot prove they are absent. Existing history findings do not authorize
history rewriting, credential rotation or changing repository visibility.
"""
import argparse
import collections
import json
from pathlib import Path

from repo_guard import GuardError, git, known_values, policy, scan_blob


def audit(root):
    root = Path(root)
    rules, known = policy(root), known_values(root)
    commits = git(root, 'rev-list', '--all').stdout.decode().splitlines()
    paths = collections.defaultdict(set)
    head_blobs = set()
    findings = []
    head = git(root, 'rev-parse', 'HEAD', check=False).stdout.strip().decode()
    for commit in commits:
        for entry in git(root, 'ls-tree', '-rz', '--full-tree', commit).stdout.split(b'\0'):
            if not entry:
                continue
            meta, raw_name = entry.split(b'\t', 1)
            mode, kind, raw_oid = meta.split()
            oid, name = raw_oid.decode(), raw_name.decode('utf8')
            if mode not in (b'100644', b'100755') or kind != b'blob':
                findings.append(dict(object=oid, path=name, rule='符号链接或子模块需专项审计', scope='history'))
                continue
            paths[oid].add(name)
            if commit == head:
                head_blobs.add((oid, name))
    inventory = git(root, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname) %(objecttype)').stdout.decode().splitlines()
    counts = collections.Counter()
    for entry in inventory:
        oid, kind = entry.split()
        counts[kind] += 1
        if kind not in ('blob', 'commit', 'tag'):
            continue
        raw = git(root, 'cat-file', kind, oid).stdout
        # Use only messages, not Git's internal headers, for metadata checks.
        if kind != 'blob':
            raw = raw.partition(b'\n\n')[2]
        names = sorted(paths[oid]) if kind == 'blob' else []
        for name in names or ['README.md']:
            try:
                scan_blob(name, raw, rules, known)
                if raw.startswith(b'version https://git-lfs.github.com/spec/v1'):
                    raise GuardError(name + '：LFS外部内容未在对象库内审计')
            except (GuardError, ValueError) as exc:
                # GuardError contains rule/path only. Never include raw content.
                reason = str(exc).partition('：')[2] or '对象格式未通过'
                findings.append(dict(object=oid, path=name if names else '(' + kind + ' message/orphan)',
                                     rule=reason, scope='HEAD' if (oid, name) in head_blobs else 'history/local-object'))
    refs = git(root, 'for-each-ref', '--format=%(refname)').stdout.decode().splitlines()
    return dict(commits=len(commits), objects=dict(counts), refs=refs,
                remote_assets_audited=False, findings=findings,
                conclusion='需核查发现项' if findings else '本地Git对象检查通过；仍需远程附件与人工复核')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True, help='必须存放在仓库外的私有审计目录')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error('报告必须在仓库外，不得混入源码')
    result = audit(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(dict(commits=result['commits'], objects=result['objects'], findings=len(result['findings'])), ensure_ascii=False))
    return int(bool(result['findings']))


if __name__ == '__main__':
    raise SystemExit(main())
