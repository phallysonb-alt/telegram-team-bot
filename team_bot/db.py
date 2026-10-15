import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def stamp():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employees(uid INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, required INTEGER NOT NULL DEFAULT 1, role TEXT NOT NULL DEFAULT 'employee', approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS personnel_slots(code TEXT PRIMARY KEY, kind TEXT NOT NULL, uid INTEGER UNIQUE);
CREATE TABLE IF NOT EXISTS leave_days(uid INTEGER, day TEXT, excluded INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(uid,day));
CREATE TABLE IF NOT EXISTS attendance(day TEXT PRIMARY KEY, uids TEXT NOT NULL, leave_uids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(uid INTEGER, day TEXT, payload TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, late INTEGER NOT NULL DEFAULT 0, raw TEXT, source TEXT, updated TEXT NOT NULL, PRIMARY KEY(uid,day));
CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, raw TEXT NOT NULL, source TEXT NOT NULL, base_version INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'draft', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, entity_key TEXT NOT NULL, actor INTEGER NOT NULL, reason TEXT NOT NULL, before_value TEXT, after_value TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, completed TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, deadline TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS assignments(task INTEGER, uid INTEGER, status TEXT NOT NULL DEFAULT '待处理', note TEXT NOT NULL DEFAULT '', problem TEXT NOT NULL DEFAULT '', next_step TEXT NOT NULL DEFAULT '', PRIMARY KEY(task,uid), FOREIGN KEY(task) REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, external INTEGER NOT NULL DEFAULT 0, imported TEXT NOT NULL, path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, document INTEGER NOT NULL, location TEXT NOT NULL, text TEXT NOT NULL, FOREIGN KEY(document) REFERENCES documents(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS summaries(day TEXT, version INTEGER, payload TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(day,version));
CREATE TABLE IF NOT EXISTS dirty(day TEXT PRIMARY KEY, due TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, due TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, chat INTEGER NOT NULL, thread INTEGER, text TEXT NOT NULL, file TEXT, markup TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', sent_id INTEGER);
CREATE TABLE IF NOT EXISTS ai_calls(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, purpose TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_cache(key TEXT PRIMARY KEY, text TEXT NOT NULL, expires TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS records_day ON records(day,confirmed);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,due);
CREATE INDEX IF NOT EXISTS outbox_state ON outbox(state,next_try);
CREATE INDEX IF NOT EXISTS history_key ON history(entity,entity_key);
CREATE TABLE IF NOT EXISTS daily_records(uid INTEGER, day TEXT, payload TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL, timing TEXT NOT NULL, confirmed_at TEXT NOT NULL, raw TEXT, source TEXT, PRIMARY KEY(uid,day));
CREATE TABLE IF NOT EXISTS daily_drafts(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, raw TEXT NOT NULL, source TEXT NOT NULL, base_version INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'draft', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily_summaries(day TEXT, version INTEGER, payload TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(day,version));
CREATE TABLE IF NOT EXISTS daily_publications(day TEXT PRIMARY KEY, version INTEGER NOT NULL, chat INTEGER NOT NULL, message_id INTEGER, snapshot TEXT, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS check_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, target INTEGER NOT NULL, uids TEXT NOT NULL, checks TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, simulated INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS check_events(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, task INTEGER NOT NULL, day TEXT NOT NULL, server_time TEXT NOT NULL, delta INTEGER NOT NULL, callback TEXT UNIQUE NOT NULL, undo_of INTEGER UNIQUE);
CREATE TABLE IF NOT EXISTS check_cards(uid INTEGER, task INTEGER, day TEXT, chat INTEGER NOT NULL, message_id INTEGER, state TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(uid,task,day));
CREATE TABLE IF NOT EXISTS wb_config(id INTEGER PRIMARY KEY AUTOINCREMENT, effective TEXT NOT NULL, tasks TEXT NOT NULL, periods TEXT NOT NULL, actor INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wb_days(day TEXT PRIMARY KEY, tasks TEXT NOT NULL, periods TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wb_events(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, task INTEGER NOT NULL, day TEXT NOT NULL, at TEXT NOT NULL, delta INTEGER NOT NULL, action TEXT NOT NULL, period INTEGER, operation_period INTEGER, outside INTEGER NOT NULL, undo_of INTEGER UNIQUE, opkey TEXT UNIQUE NOT NULL, actor INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS wb_event_day ON wb_events(day,uid,task);
CREATE TABLE IF NOT EXISTS wb_help(uid INTEGER, day TEXT, state TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(uid,day));
CREATE TABLE IF NOT EXISTS wb_snapshots(day TEXT, period INTEGER, version INTEGER, payload TEXT NOT NULL, created TEXT NOT NULL, revised INTEGER NOT NULL, PRIMARY KEY(day,period,version));
CREATE TABLE IF NOT EXISTS wb_cards(kind TEXT, viewer INTEGER, day TEXT, chat INTEGER NOT NULL, message_id INTEGER, PRIMARY KEY(kind,viewer,day));
CREATE TABLE IF NOT EXISTS wb_boards(day TEXT PRIMARY KEY, message_id INTEGER, due TEXT, page INTEGER NOT NULL DEFAULT 1, period INTEGER, pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tg_profiles(uid INTEGER PRIMARY KEY,name TEXT NOT NULL,username TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tg_name_history(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER NOT NULL,name TEXT NOT NULL,username TEXT NOT NULL,at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS onboarding(uid INTEGER PRIMARY KEY,state TEXT NOT NULL,reason TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS membership(uid INTEGER,chat INTEGER,status TEXT NOT NULL,verified TEXT NOT NULL,PRIMARY KEY(uid,chat));
CREATE TABLE IF NOT EXISTS wb_event_names(event_id INTEGER PRIMARY KEY,name TEXT NOT NULL,actor_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_names(history_id INTEGER PRIMARY KEY,actor_name TEXT NOT NULL,target_name TEXT NOT NULL);
PRAGMA user_version=5;
'''


class DB:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=20, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA busy_timeout=20000')
        if self.conn.execute('PRAGMA user_version').fetchone()[0] > 5:
            raise ValueError('数据库版本高于当前程序，请升级程序')
        self.conn.executescript(SCHEMA)
        try:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(tokens, chunk UNINDEXED)")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False

    def run(self, sql, args=()):
        return self.conn.execute(sql, args)

    def one(self, sql, args=()):
        row = self.run(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.run(sql, args).fetchall()]

    @contextmanager
    def transaction(self):
        nested = self.conn.in_transaction
        self.run('SAVEPOINT inner_tx' if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            self.run('RELEASE inner_tx' if nested else 'COMMIT')
        except BaseException:
            if nested:
                self.run('ROLLBACK TO inner_tx')
                self.run('RELEASE inner_tx')
            else:
                self.run('ROLLBACK')
            raise

    def setting(self, key, default=None):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(row['value']) if row else default

    def set(self, key, value):
        self.run('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, dumps(value)))

    def audit(self, entity, key, actor, reason, before, after):
        if not reason.strip():
            raise ValueError('必须填写修改原因')
        cur=self.run('INSERT INTO history(entity,entity_key,actor,reason,before_value,after_value,created) VALUES (?,?,?,?,?,?,?)', (entity, str(key), actor, reason, dumps(before), dumps(after), stamp()))
        def name(uid):
            row=self.one('SELECT name FROM tg_profiles WHERE uid=?',(uid,))
            return row['name'] if row else ''
        target=(after or before or {}).get('uid',0) if isinstance(after or before,dict) else 0
        if not target and entity in ('协同工作台','工作台求助'):
            try:target=int(str(key).split(':')[0])
            except ValueError:pass
        self.run('INSERT INTO audit_names VALUES (?,?,?)',(cur.lastrowid,name(actor),name(target)))

    def close(self):
        self.conn.close()
