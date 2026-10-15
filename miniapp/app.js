/* Identity and permissions always come from the signed backend session. */
const tg={get initData(){return window.Telegram?.WebApp?.initData||new URLSearchParams(location.hash.slice(1)).get('tgWebAppData')||'';},ready(){window.Telegram?.WebApp?.ready();},expand(){window.Telegram?.WebApp?.expand();},close(){window.Telegram?.WebApp?.close();},openTelegramLink(url){if(window.Telegram?.WebApp)window.Telegram.WebApp.openTelegramLink(url);else location.href=url;}};
document.querySelector?.('script[src*="telegram-web-app"]')?.addEventListener('load',()=>{tg.ready();tg.expand();});
const $=id=>document.getElementById(id);
let session,view='summary',roundId,selection,feedback,summary,taskIndex=0,page=1,publishKey,submitKey;
let measureCalls=[],savingFeedback=false,issuePages={},summaryPending=false,activeIssuePages='';
const uuid=()=>crypto.randomUUID().replaceAll('-','');
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,action,cls=''){const b=el('button',text,cls);b.type='button';b.onclick=action;return b;}
function notice(text){$('notice').textContent=text;}
function feedbackChanged(){if(savingFeedback||feedback?.submitted)return;submitKey=uuid();const btn=$('actions').querySelector('button');if(btn)btn.textContent='提交反馈';notice('');}
function human(value){return value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'—';}
function fetchTiming(path,started){const list=performance.getEntriesByType('resource').filter(x=>new URL(x.name).pathname==='/api/'+path),r=list.at(-1);if(!r)return {};return {before_request_ms:Math.max(0,r.requestStart-started),ttfb_ms:r.responseStart-r.requestStart,download_ms:r.responseEnd-r.responseStart,dns_ms:r.domainLookupEnd-r.domainLookupStart,connect_ms:r.connectEnd-r.connectStart};}
let apiSocket,apiConnecting,apiSequence=0;const socketPending=new Map();
function connectApi(){if(apiSocket?.readyState===1)return Promise.resolve(apiSocket);if(apiConnecting)return apiConnecting;apiConnecting=new Promise((resolve,reject)=>{const ws=new WebSocket(location.origin.replace('https:','wss:').replace('http:','ws:')+'/api/client');const timeout=setTimeout(()=>{ws.close();reject(Error('socket unavailable'));},5000);ws.onopen=()=>{clearTimeout(timeout);apiSocket=ws;resolve(ws);};ws.onmessage=e=>{let data;try{data=JSON.parse(e.data);}catch{return;}const pending=socketPending.get(data.id);if(pending){clearTimeout(pending.timer);socketPending.delete(data.id);pending.resolve(data);}};ws.onerror=()=>{clearTimeout(timeout);reject(Error('socket unavailable'));};ws.onclose=()=>{if(apiSocket===ws)apiSocket=null;for(const [id,p] of socketPending){clearTimeout(p.timer);p.reject(Error('连接中断，填写内容已保留；重试不会重复保存'));}socketPending.clear();};}).finally(()=>{apiConnecting=null;});return apiConnecting;}
async function api(path,body={}){const callStart=performance.now();let data,status,transport='WebSocket';const envelope={initData:tg?.initData||'',body};let ws;try{ws=await connectApi();}catch{}
if(ws){const id=String(++apiSequence);const result=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{socketPending.delete(id);reject(Error('连接超时，填写内容已保留；请重试'));},30000);socketPending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,path:'/api/'+path,envelope}));});data=result.body;status=result.status;}
else{transport='HTTP兼容';const c=new AbortController(),timer=setTimeout(()=>c.abort(),30000);try{const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(envelope),signal:c.signal});status=r.status;data=await r.json();}catch(e){throw Error('网络连接失败，填写内容已保留，请重试');}finally{clearTimeout(timer);}}
measureCalls.push({path,ms:performance.now()-callStart,browser:transport==='WebSocket'?{transport}:fetchTiming(path,callStart),timing:data._timing||{},status});if(status<200||status>=300)throw Error(data.error||'无法完成，请重试');return data;}
function shell(title,date){$('title').textContent=title;$('meta').textContent=date;$('content').replaceChildren();$('actions').replaceChildren();$('nav').replaceChildren();notice('');}
function field(container,title,value='',placeholder=''){const label=el('label',title,'field'),input=el('input');input.value=value;input.placeholder=placeholder;label.append(input);container.append(label);return input;}
function bar(mask,n){const wrap=el('div',undefined,'progress'),b=el('div',undefined,'bar');for(let i=0;i<n;i++)b.append(el('i',undefined,mask&(1<<i)?'on':''));const count=Array.from({length:n},(_,i)=>!!(mask&(1<<i))).filter(Boolean).length;wrap.append(b,el('span',`${count}／${n}${count===n?' · 已完成':''}`));return wrap;}
function nav(){if(session.admin){if(view!=='publish')$('nav').append(button('发布任务',publishPage,'quiet'));if(view!=='summary')$('nav').append(button('查看总表',()=>summaryPage(),'quiet'));}}
function emptyTask(){return {title:'',kind:'seq',stages:['','','']};}
let draft=[emptyTask()],draftDate;
function publishPage(){view='publish';shell('发布今日任务',session.today);nav();publishKey=publishKey||uuid();draftDate=draftDate||session.today;const date=field($('content'),'工作日期',draftDate);date.type='date';date.oninput=()=>{draftDate=date.value;publishKey=uuid();};
 draft.forEach((task,index)=>{const card=el('section',undefined,'card');card.append(el('h2',`任务 ${index+1}`));const title=field(card,'任务主题',task.title,'微信养号强制执行');title.maxLength=100;title.oninput=()=>{task.title=title.value;publishKey=uuid();};const examples=['检查账户是否正常','指定内容发布','指定数量评论、点赞','第四步'];card.append(el('h3','完成步骤'));task.stages.forEach((value,i)=>{const line=el('label',undefined,'step');line.append(el('b',`${i+1}`,'number'));const input=el('input');input.value=value;input.placeholder=examples[i];input.maxLength=80;input.oninput=()=>{task.stages[i]=input.value;publishKey=uuid();};line.append(input);card.append(line);});if(task.stages.length===3)card.append(button('＋第四步',()=>{task.stages.push('');publishKey=uuid();publishPage();},'quiet'));const mode=el('label',undefined,'switch'),check=el('input');check.type='checkbox';check.checked=task.kind==='seq';check.onchange=()=>{task.kind=check.checked?'seq':'free';publishKey=uuid();};mode.append(check,el('span','按顺序完成'));card.append(mode);$('content').append(card);});
 $('actions').append(button('＋任务',()=>{draft.push(emptyTask());publishKey=uuid();publishPage();},'quiet'),button('发布任务',async e=>{const btn=e.currentTarget;btn.disabled=true;try{await api('publish',{key:publishKey,day:draftDate,tasks:draft});draft=[emptyTask()];publishKey=null;publishPage();notice('任务已发布');}catch(err){notice(err.message);btn.disabled=false;}}));}
async function feedbackPage(id){view='feedback';roundId=id;try{feedback=session.initial_feedback?.round===id?session.initial_feedback:await api('feedback',{round:id});session.initial_feedback=null;selection={...feedback.masks};submitKey=uuid();renderFeedback();}catch(e){shell('反馈工作进度','');notice(e.message);$('actions').append(button('重试',()=>feedbackPage(id)));}}
function renderFeedback(){shell('反馈工作进度',feedback.day);$('meta').textContent=`${feedback.day} · 查询 ${human(feedback.time)}`;$('content').append(el('h2',`${feedback.name}${feedback.username?'（@'+feedback.username.replace(/^@/,'')+'）':''}`));if(feedback.prefilled&&!feedback.submitted)$('content').append(el('p','此前已提交阶段已锁定，本轮可新增完成阶段及填写当前问题。','muted'));
 feedback.tasks.forEach(task=>{const key=String(task.id),card=el('section',undefined,'card');card.append(el('h2',task.title),el('p',task.kind==='seq'?'按顺序完成':'自由完成','muted'));const checks=[];let progress=bar(selection[key],task.stages.length);
 task.stages.forEach((stage,i)=>{const row=el('label',undefined,'step'),check=el('input');check.type='checkbox';check.checked=!!(selection[key]&(1<<i));check.disabled=feedback.submitted||!!((feedback.locked?.[key]||0)&(1<<i));check.onchange=()=>{if(feedback.submitted||savingFeedback)return;if(task.kind==='seq')selection[key]=check.checked?(1<<(i+1))-1:selection[key]&((1<<i)-1);else selection[key]^=1<<i;selection[key]|=feedback.locked?.[key]||0;checks.forEach((c,j)=>c.checked=!!(selection[key]&(1<<j)));const next=bar(selection[key],task.stages.length);progress.replaceWith(next);progress=next;feedbackChanged();};checks.push(check);row.append(check,el('b',`${i+1}`,'number'),el('span',stage));card.append(row);});card.append(progress);const label=el('label','当前问题（选填）','field'),note=el('textarea');note.value=feedback.notes[key]||'';note.disabled=feedback.submitted;note.maxLength=3500;note.placeholder='填写当前遇到的问题，没有可留空';note.oninput=()=>{if(feedback.submitted||savingFeedback)return;feedback.notes[key]=note.value;feedbackChanged();};label.append(note);card.append(label);$('content').append(card);});
 if(feedback.legacyNote)$('content').append(el('p','历史当前问题：'+feedback.legacyNote,'note'));
 if(feedback.submitted)notice('已提交，本轮反馈已锁定');const save=button(feedback.submitted?'已提交':'提交反馈',async e=>{const actionStart=performance.now();measureCalls=[];const btn=e.currentTarget;btn.disabled=true;savingFeedback=true;for(const input of $('content').querySelectorAll('input, textarea'))input.disabled=true;try{const result=await api('submit',{key:submitKey,round:roundId,masks:{...selection},notes:{...feedback.notes}});feedback.submitted=true;feedback.locked={...selection};savingFeedback=false;renderFeedback();reportMetric('submit',actionStart,result._timing?.revision);}catch(err){savingFeedback=false;renderFeedback();notice(err.message);}});save.disabled=feedback.submitted;$('actions').append(save);}
async function summaryPage(id){issuePages={};summaryPending=false;view='summary';summaryLoading=true;shell('团队工作进度',session.today);nav();notice('正在读取任务与查询记录…');try{
 const initial=session.initial;session.initial=null;let history;
 if(initial&&(!id||id===initial.round)){history={rounds:initial.rounds};roundId=id||initial.round;summary=initial.summary;}
 else{history=await api('history',{day:session.today});roundId=id||history.rounds[0]?.id;summary=null;}
 notice('');const tools=el('div',undefined,'tools'),historySelect=el('select');
 if(history.rounds.length){for(const r of history.rounds){const o=el('option',`${r.day} ${human(r.at)}`);o.value=r.id;historySelect.append(o);}if(roundId)historySelect.value=roundId;historySelect.onchange=()=>{taskIndex=0;page=1;summaryPage(Number(historySelect.value));};tools.append(historySelect);}
 tools.append(button('刷新',()=>refreshSummary(true),'quiet'),button('查询任务',async()=>{try{const r=await api('query',{key:uuid()});summaryPage(r.round);}catch(e){notice(e.message==='填写或入口无效，请检查后重试'?'今日尚未发布任务':e.message);}},'quiet'));$('content').append(tools);
 if(!roundId){$('content').append(el('p','今日尚未发布任务或发起查询。请先发布任务，再查询进度。'));return;}
 if(initial&&initial.tasks===0)$('content').append(el('p','今日暂无有效任务；下方为已保存的查询历史。','muted'));
 if(!summary){notice('正在读取团队反馈…');summary=await api('summary',{round:roundId});notice('');}renderSummary();
 }catch(e){notice(e.message);$('actions').replaceChildren(button('重试',()=>summaryPage(id),'quiet'));}finally{summaryLoading=false;}}
function adminProgress(states,n,label){
 const wrap=el('div',undefined,'progress admin-progress'),segments=el('div',undefined,'bar');
 const labels={history:'历史完成',new:'本轮新增',pending:'未完成',unknown:'已完成，所属轮次未确认'};
 for(let i=0;i<n;i++){const state=states?.[i]||'pending',segment=el('i',undefined,state);segment.title=`阶段${i+1}：${labels[state]}`;segment.setAttribute('aria-label',segment.title);segments.append(segment);}
 wrap.append(segments,el('span',label));return wrap;
}
function issueTime(value){return value&&Number.isFinite(new Date(value).getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)).replaceAll('/','-'):'未确认';}
function changeIssuePage(task,part,next){issuePages[task]={...(issuePages[task]||{}),[part]:next};refreshSummary(true);}
function renderIssues(out,key){
 const bundle=summary.issues?.[key];if(!bundle)return;
 for(const [part,title] of [['history','历史问题'],['current','本轮问题']]){
  const data=bundle[part],section=el('section',undefined,'issue-section');section.append(el('h3',title));
  if(part==='history'&&data.count)section.append(el('p','历史问题仅表示曾经反馈，不代表当前仍未解决。','muted'));
  if(!data.count)section.append(el('p',part==='history'?'暂无历史问题':'本轮暂无新增问题','muted'));
  for(const issue of data.records){
   const card=el('div',undefined,'note issue-card');card.append(el('strong',`第${issue.round_number}轮｜查询 ${issueTime(issue.query_at)}`),el('strong',`${issue.name}（${issue.username?'@'+issue.username.replace(/^@/,''):'—'}）`));
   card.append(el('p','任务：'+(issue.task_title||summary.tasks.find(t=>String(t.id)===key)?.title||'名称未留存'),'muted'));
   card.append(el('p',`实际提交：${issueTime(issue.submitted_at)} · 当轮进度：${issue.progress===null?'未确认':issue.progress+'/'+issue.total}`,'muted'));
   if(issue.text.length>500){card.append(el('div','问题：'+issue.text.slice(0,250)+'…'));const full=el('details');full.append(el('summary','展开完整问题'),el('div',issue.text));card.append(full);}
   else card.append(el('div','问题：'+issue.text));
   section.append(card);
  }
  if(data.pages>1){const controls=el('div',undefined,'tools');if(data.page>1)controls.append(button('上一页问题',()=>changeIssuePage(key,part,data.page-1),'quiet'));controls.append(el('span',`第 ${data.page}／${data.pages} 页 · ${data.count} 条`));if(data.page<data.pages)controls.append(button('下一页问题',()=>changeIssuePage(key,part,data.page+1),'quiet'));section.append(controls);}
  out.append(section);
 }
 if(bundle.unscoped_count)out.append(el('p',`${bundle.unscoped_count}条旧问题缺少任务关联，未猜测归入本任务。`,'muted'));
 if(bundle.time_unknown_count)out.append(el('p',`${bundle.time_unknown_count}条旧反馈的提交时间晚于本查询或未确认，未当作当时的历史。`,'muted'));
}
function renderSummary(){
 const old=$('results');if(old)old.remove();const out=el('div');out.id='results';$('content').append(out);
 $('meta').textContent=`${summary.day} · 查询 ${human(summary.time)} · 更新 ${human(summary.updated)}`;
 if(summary.simulated&&!session.label.includes('模拟'))out.append(el('p','模拟展示','trial'));
 const tabs=el('div',undefined,'tabs');summary.tasks.forEach((t,i)=>tabs.append(button(t.title,()=>{taskIndex=i;page=1;renderSummary();},i===taskIndex?'selected':'quiet')));out.append(tabs);
 const task=summary.tasks[taskIndex],key=String(task.id),full=(1<<task.stages.length)-1;
 out.append(el('p',`本轮已反馈 ${summary.received}／${summary.expected} 人`,'badge'),el('p',`累计全部完成 ${summary.cumulative_completed[key]}／${summary.expected} 人`,'badge'));
 out.append(el('h2',task.title),el('p',task.kind==='seq'?'按顺序完成':'自由完成','muted'));
 const legend=el('div',undefined,'progress-legend');legend.append(el('span','实心灰＝历史完成','history'),el('span','蓝色＝本轮新增','new'),el('span','浅灰＝未完成','pending'));out.append(legend);
 const table=el('table'),head=el('tr');['员工名称','Telegram 用户名','累计进度 / 本轮反馈'].forEach(x=>head.append(el('th',x)));const thead=el('thead');thead.append(head);table.append(thead);
 const body=el('tbody'),rows=summary.rows.slice((page-1)*10,page*10);
 for(const row of rows){
  const tr=el('tr');tr.append(el('td',row.name),el('td',row.username?'@'+row.username.replace(/^@/,''):'—'));const cell=el('td');
  const recorded=Object.prototype.hasOwnProperty.call(row.cumulative_masks,key),mask=row.cumulative_masks[key]||0,states=row.stage_states?.[key];
  if(!Array.isArray(states)||states.length!==task.stages.length){cell.append(el('span','阶段状态待刷新','muted'));tr.append(cell);body.append(tr);continue;}
  const count=states.filter(x=>x!=='pending').length;
  cell.append(adminProgress(states,task.stages.length,recorded?`累计 ${count}／${task.stages.length}${mask===full?' · 已完成':''}`:'暂无记录'));
  if(recorded){
   cell.append(el('span',row.submitted?'本轮已反馈':mask===full?`未反馈｜历史已完成${count}/${task.stages.length}`:`未反馈｜历史累计${count}/${task.stages.length}`,'muted'));
   const uncertain=states.includes('unknown');if(row.submitted)cell.append(el('div',uncertain?'本轮新增：未确认':`本轮新增 ${states.filter(x=>x==='new').length} 项`,'muted'));
   if(uncertain)cell.append(el('div','部分完成阶段所属轮次未确认','muted'));
   const detail=el('details');detail.append(el('summary','具体步骤'));
   for(const [state,label] of [['history','历史完成'],['new','本轮新增'],['pending','未完成'],['unknown','完成轮次未确认']]){const selected=task.stages.filter((_,i)=>states[i]===state);if(state!=='unknown'||selected.length)detail.append(el('p',label+'：'+(selected.join('；')||'无'),'stage-detail'));}
   cell.append(detail);
  }else cell.append(el('span','未反馈｜暂无记录','muted'));
  tr.append(cell);body.append(tr);
 }
 table.append(body);out.append(table);const pages=Math.max(1,Math.ceil(summary.rows.length/10)),paging=el('div',undefined,'tools');
 if(page>1)paging.append(button('上一页',()=>{page--;renderSummary();},'quiet'));paging.append(el('span',`第 ${page}／${pages} 页`));if(page<pages)paging.append(button('下一页',()=>{page++;renderSummary();},'quiet'));out.append(paging);
 renderIssues(out,key);
}
async function boot(){tg?.ready();tg?.expand();$('back').onclick=()=>{if(session?.group)tg?.openTelegramLink(session.group);tg?.close();};shell('工作进度','');notice('正在加载，校验 Telegram 身份并连接后台…');const slow=setTimeout(()=>notice('后台正在响应，请稍候；连接失败会显示重试入口。'),4000);
 try{session=await api('session');clearTimeout(slow);$('label').textContent=session.label;const start=session.start||'';
 if(start.startsWith('feedback_'))await feedbackPage(Number(start.slice(9)));
 else if(start==='people'){if(!session.admin)throw Error('此操作仅管理员可用');await summaryPage();}
 else if(start==='publish'){if(!session.admin)throw Error('此操作仅管理员可用');publishPage();}
 else{if(!session.admin)throw Error('此入口仅管理员可查看；员工请从查询消息点击“填写进度”。');await summaryPage(start.startsWith('summary_')?Number(start.slice(8)):undefined);}
 if(start.startsWith('feedback_')&&feedback?.round===roundId)reportMetric('form_open',0);
 else if(view==='summary'&&$('results'))reportMetric('summary_open',0);
 }catch(e){clearTimeout(slow);notice(e.message);$('actions').replaceChildren(button('重试加载',boot,'quiet'));}}
let refreshing=false,summaryLoading=false;
async function refreshSummary(manual=false){
 if(refreshing||summaryLoading||!roundId){if(manual&&roundId&&JSON.stringify(issuePages)!==activeIssuePages)summaryPending=true;return;}refreshing=true;const requested=roundId,wanted=JSON.stringify(issuePages);activeIssuePages=wanted;
 try{const next=await api('summary',{round:requested,issue_pages:JSON.parse(wanted)});
  if(view==='summary'&&roundId===requested){
   if(wanted!==JSON.stringify(issuePages)){summaryPending=true;return;}
   const changed=next.revision!==summary?.revision||JSON.stringify(next.rows)!==JSON.stringify(summary?.rows)||JSON.stringify(next.issues)!==JSON.stringify(summary?.issues);summary=next;
   if(changed){renderSummary();reportMetric('summary_visible',performance.now(),next.revision);}else $('meta').textContent=[summary.day,'查询 '+human(summary.time),'更新 '+human(summary.updated)].join(' · ');
   if(manual)notice('已刷新');else if($('notice').textContent==='连接中断，可点击刷新重试')notice('');
  }
 }catch(e){if(manual)notice(e.message);else notice('连接中断，可点击刷新重试');}
 finally{refreshing=false;if(summaryPending&&!summaryLoading){summaryPending=false;refreshSummary(true);}}
}
setInterval(()=>{if(view==='summary'&&!document.hidden)refreshSummary();},1000);
async function reportMetric(kind,start,revision){if(!session?.perf)return;const calls=measureCalls.slice(-10);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const body={kind,phase:session.perf_phase,start_epoch:performance.timeOrigin+start,ready_epoch:performance.timeOrigin+performance.now(),duration_ms:performance.now()-start,calls,round:roundId||0,revision:revision||summary?.revision||0,client_id:uuid(),resources:performance.getEntriesByType('resource').filter(x=>/app.js|style.css|telegram-web-app/.test(x.name)).map(x=>({name:x.name.includes('telegram-web-app')?'Telegram SDK':x.name.includes('style.css')?'style.css':'app.js',ms:x.duration,bytes:x.transferSize}))};fetch('/api/metrics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg?.initData||'',body}),keepalive:true}).catch(()=>{});}
boot();
