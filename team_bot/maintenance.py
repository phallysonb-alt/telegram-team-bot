import sqlite3
import zipfile
import os
from contextlib import closing
from pathlib import Path
from datetime import timedelta


class InstanceLock:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'instance.lock'
        self.file = None

    def __enter__(self):
        self.file = self.path.open('a+b')
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('已有实例正在运行，请先停止') from None
        if self.path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        return self

    def __exit__(self, *args):
        if os.name == 'nt':
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()


def backup(service):
    c = service.c
    c.backups.mkdir(parents=True, exist_ok=True)
    name = c.now().strftime('%Y%m%d-%H%M%S-%f')
    temp = c.backups / (name + '.sqlite3')
    try:
        with closing(sqlite3.connect(temp)) as target:
            service.db.conn.backup(target)
        dest = c.backups / (name + '.zip')
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(temp, 'team.sqlite3')
            for pg_name in ('progress.sqlite3','progress-acceptance.sqlite3'):
                pg_source=c.data/pg_name
                if pg_source.exists():
                    pg_stage=c.backups/(name+'-'+pg_name)
                    try:
                        with closing(sqlite3.connect(pg_source)) as source, closing(sqlite3.connect(pg_stage)) as target:source.backup(target)
                        z.write(pg_stage,pg_name)
                    finally:pg_stage.unlink(missing_ok=True)
            for folder in ('materials', 'exports'):
                for p in (c.data / folder).rglob('*'):
                    if p.is_file():
                        z.write(p, p.relative_to(c.data))
    finally:
        temp.unlink(missing_ok=True)
    cutoff = c.now().timestamp() - timedelta(days=c.retention).total_seconds()
    for p in c.backups.glob('*.zip'):
        if p.stat().st_mtime < cutoff and p != dest:
            p.unlink()
    return dest


def restore(config, archive):
    from .db import DB
    config.data.mkdir(parents=True, exist_ok=True)
    with InstanceLock(config.data):
        if (config.data / 'team.sqlite3').exists():
            old = DB(config.data / 'team.sqlite3')
            from .service import Service
            try:
                backup(Service(config, old))
            finally:
                old.close()
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if 'team.sqlite3' not in names:
                raise ValueError('备份缺少数据库')
            for name in names:
                path = (config.data / name).resolve()
                if not path.is_relative_to(config.data.resolve()) or name.startswith(('/', '\\')):
                    raise ValueError('备份含不安全路径')
                if name not in ('team.sqlite3','progress.sqlite3','progress-acceptance.sqlite3') and not name.startswith(('materials/', 'exports/')):
                    raise ValueError('备份含非项目数据')
            # 先验证再替换，避免损坏备份覆盖当前数据库。
            staged = config.data / 'restore-check.sqlite3'
            staged.write_bytes(z.read('team.sqlite3'))
            try:
                with closing(sqlite3.connect(staged)) as check:
                    if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('备份数据库完整性检查失败')
                for pg_name in ('progress.sqlite3','progress-acceptance.sqlite3'):
                    if pg_name in names:
                        checkfile=config.data/('restore-check-'+pg_name);checkfile.write_bytes(z.read(pg_name))
                        try:
                            with closing(sqlite3.connect(checkfile)) as check:
                                if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('进度备份完整性检查失败')
                        finally:checkfile.unlink(missing_ok=True)
                        for suffix in ('-wal','-shm'):Path(str(config.data/pg_name)+suffix).unlink(missing_ok=True)
                for suffix in ('-wal', '-shm'):
                    Path(str(config.data / 'team.sqlite3') + suffix).unlink(missing_ok=True)
                for name in names:
                    target = config.data / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(name))
                # 备份数据库中的资料绝对路径适配恢复位置。
                db = DB(config.data / 'team.sqlite3')
                for r in db.all('SELECT id,path FROM documents'):
                    db.run('UPDATE documents SET path=? WHERE id=?', (str(config.data / 'materials' / Path(r['path']).name), r['id']))
                db.close()
            finally:
                staged.unlink(missing_ok=True)
    return '恢复完成；发件箱保留原状态，可能需要管理员检查未确认发送记录'
