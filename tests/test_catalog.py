import unittest
from unittest.mock import patch,AsyncMock
from types import SimpleNamespace
from team_bot.catalog import Catalog
from team_bot.bot import Adapter
from team_bot.knowledge import Knowledge
from test_acceptance import Base
import test_acceptance as fixtures


class CatalogTests(Base,unittest.IsolatedAsyncioTestCase):
    make_update=fixtures.TelegramAdapterTests.make_update

    def test_catalog_preserves_bytes_without_chunks_or_decoding(self):
        self.c.training_mode='directory'
        p=self.root/'模拟资料.txt'
        p.write_bytes(b'\xff\xfe\x2d\x4e\x87\x65')
        doc,_=Catalog(self.s).add(99999,p)
        self.assertEqual(self.s.db.one('SELECT COUNT(*) AS n FROM chunks')['n'],0)
        self.assertEqual(self.s.db.one('SELECT COUNT(*) AS n FROM search_index')['n'],0)
        self.emp()
        name,saved=Catalog(self.s).download(10001,doc)
        self.assertEqual(name,p.name)
        self.assertEqual(saved.read_bytes(),p.read_bytes())
        with self.assertRaises(ValueError): Knowledge(self.s).import_file(99999,p)
        with self.assertRaises(ValueError): Knowledge(self.s).reindex(99999)
        with self.assertRaises(ValueError): Knowledge(self.s).change(99999,doc,'外发允许')

    async def test_directory_ask_never_searches_or_calls_models(self):
        self.emp(); self.c.training_mode='directory'
        a=Adapter(self.s)
        with patch.object(a.kb,'search') as search,patch.object(a.ai,'ask',new=AsyncMock()) as ai:
            await a.handle(self.make_update(1,'/ask 不读取正文',thread=22),SimpleNamespace(bot=SimpleNamespace(id=123)))
        search.assert_not_called(); ai.assert_not_awaited()
        self.assertIn('正文问答已停用',a.db.one('SELECT text FROM outbox')['text'])
        with patch('team_bot.knowledge.Knowledge.local_answer') as local:
            result=await a.ai.ask('模拟查询',[])
        local.assert_not_called()
        self.assertIn('正文问答已停用',result)

    async def test_directory_and_original_filename_download_permissions(self):
        uid=self.emp(); self.c.training_mode='directory'
        p=self.root/'独立模拟.txt'; p.write_bytes(b'simulation only')
        doc,_=Catalog(self.s).add(99999,p)
        a=Adapter(self.s); a.db.set('private_started:10001',True)
        ctx=SimpleNamespace(bot=SimpleNamespace(id=123))
        await a.handle(self.make_update(1,'/materials',thread=22),ctx)
        await a.handle(self.make_update(2,f'/material_get {doc}',thread=22),ctx)
        row=a.db.one("SELECT * FROM outbox WHERE file IS NOT NULL")
        self.assertEqual((row['chat'],row['thread']),(uid,None))
        self.assertEqual(a.db.setting('file_name:'+row['key']),p.name)
        with self.assertRaises(PermissionError): Catalog(self.s).download(10002,doc)
        a.db.run('UPDATE documents SET active=0 WHERE id=?',(doc,))
        with self.assertRaises(ValueError): Catalog(self.s).download(uid,doc)

    def test_excluded_name_is_rejected_before_any_source_read(self):
        self.s.db.set('training_excluded_names',['excluded.txt'])
        with patch('pathlib.Path.read_bytes') as read:
            with self.assertRaises(ValueError): Catalog(self.s).add(99999,self.root/'excluded.txt')
        read.assert_not_called()
