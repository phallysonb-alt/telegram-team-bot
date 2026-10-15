import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),assets={};
for(const [url,name,type] of [['/','index.html','text/html; charset=utf-8'],['/app.js','app.js','text/javascript; charset=utf-8'],['/style.css','style.css','text/css; charset=utf-8']])assets[url]={body:await fs.readFile(path.join(root,'miniapp',name),'utf8'),type};
for(const name of ['app.js','style.css'])assets['/'].body=assets['/'].body.replace('/'+name,'/'+name+'?v='+createHash('sha256').update(assets['/'+name].body).digest('hex').slice(0,12));
await fs.mkdir(path.join(root,'deploy','dist'),{recursive:true});await fs.writeFile(path.join(root,'deploy','dist','worker.js'),(await fs.readFile(path.join(root,'deploy','worker.js'),'utf8')).replace('__ASSETS__',JSON.stringify(assets)));console.log('Worker built without company identity, credentials or business data.');
