"""Generate a credential-free Worker artifact from the Mini App assets."""
from pathlib import Path
import json,shutil
p=Path(__file__).resolve().parents[1];out=p/'build/miniapp-site'
(out/'server').mkdir(parents=True,exist_ok=True);(out/'.openai/drizzle/meta').mkdir(parents=True,exist_ok=True)
assets={}
for path,file,kind in [('/','index.html','text/html; charset=utf-8'),('/app.js','app.js','text/javascript; charset=utf-8'),('/style.css','style.css','text/css; charset=utf-8')]:assets[path]=dict(body=(p/'miniapp'/file).read_text(encoding='utf-8'),type=kind)
(out/'server/index.js').write_text((p/'deploy/worker.js').read_text(encoding='utf-8').replace('__ASSETS__',json.dumps(assets,ensure_ascii=False)),encoding='utf-8')
(out/'.openai/hosting.json').write_text(json.dumps(dict(d1='DB',r2=None),indent=2))
shutil.copy2(p/'deploy/0000_miniapp_bridge.sql',out/'.openai/drizzle/0000_miniapp_bridge.sql')
(out/'.openai/drizzle/meta/_journal.json').write_text(json.dumps(dict(version='7',dialect='sqlite',entries=[dict(idx=0,version='6',when=1791800000000,tag='0000_miniapp_bridge',breakpoints=True)])))
print('Generated credential-free Worker artifact. Set the intended Site project_id before deploying.')
