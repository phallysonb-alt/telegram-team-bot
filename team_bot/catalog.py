"""Metadata-only catalog. No decoding, chunk extraction, search, or model calls."""
from pathlib import Path
import hashlib
from .db import stamp


class Catalog:
    def __init__(self, service):
        self.s=service
        self.db=service.db

    def add(self, actor, path):
        self.s.require_admin(actor)
        path=Path(path)
        if path.name in self.db.setting('training_excluded_names',[]):
            raise ValueError('该文件未获准接入资料分发；原文件保留不变')
        if path.stat().st_size>self.s.c.max_file_mb*1024*1024:
            raise ValueError('资料超过文件大小限制')
        data=path.read_bytes()
        digest=hashlib.sha256(data).hexdigest()
        if digest in self.db.setting('training_excluded_hashes',[]):
            raise ValueError('该文件未获准接入资料分发；原文件保留不变')
        target=self.s.c.data/'materials'/(digest+path.suffix.lower())
        target.write_bytes(data)
        with self.db.transaction():
            previous=self.db.all('SELECT id FROM documents WHERE name=? AND active=1',(path.name,))
            self.db.run('UPDATE documents SET active=0 WHERE name=?',(path.name,))
            version=self.db.one('SELECT COALESCE(MAX(version),0)+1 AS v FROM documents WHERE name=?',(path.name,))['v']
            doc=self.db.run('INSERT INTO documents(name,version,imported,path,external) VALUES (?,?,?,?,0)',(path.name,version,stamp(),str(target))).lastrowid
            self.db.set('catalog:'+str(doc),dict(bytes=len(data),sha256=digest))
            self.db.audit('资料目录',doc,actor,'仅保存文件与目录元数据；不解析正文、不索引、不外发模型',previous,dict(name=path.name,version=version))
        return doc,version

    def entries(self):
        return [dict(row,**self.db.setting('catalog:'+str(row['id']))) for row in self.db.all('SELECT id,name,version,imported FROM documents WHERE active=1 ORDER BY name,id') if self.db.setting('catalog:'+str(row['id']))]

    def download(self, uid, doc):
        self.s.employee(uid)
        row=self.db.one('SELECT * FROM documents WHERE id=? AND active=1',(doc,))
        if not row or not self.db.setting('catalog:'+str(doc)):
            raise ValueError('资料不存在、已停用或未获准目录下载')
        path=Path(row['path']).resolve()
        if path.parent!=(self.s.c.data/'materials').resolve() or not path.is_file():
            raise ValueError('本地资料文件不可用')
        return row['name'],path
