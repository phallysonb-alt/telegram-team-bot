"""Owner-only real-client verification with a separate database, disabled by default."""
import os
from dataclasses import replace
from .progress_rounds import Progress
from .service import Service
def enabled(p):return p.c.run_mode=='trial' and os.getenv('FINAL_VERIFY_ENABLED','false').lower()=='true'
def isolated(api):
 if hasattr(api,'verification_api'):return api.verification_api
 c=replace(api.p.c,data=api.p.c.data/'isolated-final',test_run='finalverify',schedule_allowed=False)
 s=Service(c);owner=api.p.s.db.one('SELECT * FROM employees WHERE uid=?',(c.owner,))
 if owner and not s.db.one('SELECT * FROM employees WHERE uid=?',(c.owner,)):
  values=dict(owner,role='admin',required=0);keys=list(values);s.db.run('INSERT INTO employees('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',tuple(values[k] for k in keys))
 profile=api.p.s.db.one('SELECT * FROM tg_profiles WHERE uid=?',(c.owner,))
 if profile:s.db.run('INSERT OR IGNORE INTO tg_profiles VALUES (?,?,?,?)',tuple(profile[k] for k in ('uid','name','username','updated')))
 from .progress_miniapp import MiniAPI
 child=MiniAPI(Progress(s),api.bot,api.membership);child.is_verification=True;child.p._verification=True;api.verification_api=child
 return child
