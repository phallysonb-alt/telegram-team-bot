"""Additive migration and transactional monotonic task completion."""
import json
from .db import dumps
SCHEMA='''
CREATE TABLE IF NOT EXISTS pg_migrations(version INTEGER PRIMARY KEY,at TEXT,description TEXT);
CREATE TABLE IF NOT EXISTS pg_cumulative(task INTEGER,version INTEGER,uid INTEGER,mask INTEGER,at TEXT,source TEXT,PRIMARY KEY(task,version,uid));
CREATE TABLE IF NOT EXISTS pg_feedback_delta(round INTEGER,uid INTEGER,task INTEGER,baseline INTEGER,added INTEGER,PRIMARY KEY(round,uid,task));
CREATE TABLE IF NOT EXISTS pg_roster_names(round INTEGER,uid INTEGER,name TEXT,username TEXT,PRIMARY KEY(round,uid));
'''
def migrate(p):
 p.db.conn.executescript(SCHEMA)
 if p.db.one('SELECT version FROM pg_migrations WHERE version=1'):return
 with p.db.transaction():
  # The last saved effective value, not a union of historically cancelled stages.
  for f in p.db.all('SELECT * FROM pg_feedback ORDER BY at,round'):
   r=p.round(f['round']);masks=json.loads(f['payload'])
   for t in r['tasks']:
    if str(t['id']) not in masks:continue
    mask=masks[str(t['id'])]
    if type(mask) is not int or not 0<=mask<(1<<len(t['stages'])):continue
    p.db.run('INSERT OR REPLACE INTO pg_cumulative VALUES (?,?,?,?,?,?)',(t['id'],t['version'],f['uid'],mask,f['at'],f"升级基线：原反馈{f['round']}；未新增完成事件"))
  for rr in p.db.all('SELECT id FROM pg_rounds'):
   r=p.round(rr['id'])
   for uid in r['roster']:
    f=p.feedback(r['id'],uid);name,username=(f['name'],f['username']) if f else p.identity(uid)
    p.db.run('INSERT OR IGNORE INTO pg_roster_names VALUES (?,?,?,?)',(r['id'],uid,name,username))
  p.db.run('INSERT INTO pg_migrations VALUES (1,?,?)',(p.now(),'累计锁定；最后有效旧反馈基线；旧历史不改写'))
  dirty(p)
def mask(p,t,uid):
 row=p.db.one('SELECT mask FROM pg_cumulative WHERE task=? AND version=? AND uid=?',(t['id'],t['version'],uid))
 return row['mask'] if row else 0
def dirty(p):
 p.db.set('progress_excel_revision',p.db.setting('progress_excel_revision',0)+1)
 p.db.set('progress_excel_dirty',True)
def lock(p,rid,uid,t,value):
 old=mask(p,t,uid)
 if value&old!=old:raise ValueError('已提交阶段已锁定，不能取消')
 p.db.run('INSERT OR REPLACE INTO pg_cumulative VALUES (?,?,?,?,?,?)',(t['id'],t['version'],uid,value,p.now(),f'查询{rid}本人提交'))
 p.db.run('INSERT INTO pg_feedback_delta VALUES (?,?,?,?,?)',(rid,uid,t['id'],old,value&~old))
 return value&~old
