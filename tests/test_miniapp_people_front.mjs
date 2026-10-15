import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {performance} from 'node:perf_hooks';
const source=(await fs.readFile(new URL('../miniapp/app.js',import.meta.url),'utf8')).replace(/boot\(\);\s*$/,'');
function fixture(){
 const nodes=new Map(),elements=[],requests=[];let resolve;
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';elements.push(this);}
  set id(v){this._id=v;nodes.set(v,this);}get id(){return this._id;}
  append(...kids){for(const kid of kids){kid.parent=this;this.children.push(kid);}}
  replaceChildren(...kids){this.children=[];this.append(...kids);}
  replaceWith(next){if(this.parent){const parent=this.parent;parent.children[parent.children.indexOf(this)]=next;next.parent=parent;this.parent=null;}}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);if(nodes.get(this.id)===this)nodes.delete(this.id);}
  querySelectorAll(tag){return elements.filter(e=>e.tag===tag);}querySelector(tag){const scan=n=>n.tag===tag?n:n.children?.map(scan).find(Boolean);return this.children.map(scan).find(Boolean)||null;}
  setAttribute(){}
 }
 for(const id of ['title','meta','content','actions','nav','notice','label','back']){const e=new Element('div');e.id=id;}
 const context=vm.createContext({window:{Telegram:{WebApp:{initData:'mock-signature-for-frontend-only'}}},document:{createElement:t=>new Element(t),getElementById:id=>nodes.get(id)},crypto:webcrypto,performance,AbortController,setTimeout,clearTimeout,setInterval:()=>0,requestAnimationFrame:f=>f(),fetch:(path,options)=>{requests.push({path,body:JSON.parse(options.body)});return new Promise(r=>resolve=r);}});
 vm.runInContext(source,context);
 return {context,nodes,elements,requests,reply:data=>resolve({ok:!data.error,status:data.error?503:200,json:async()=>data})};
}

test('retired personnel window is absent from all administrator navigation',()=>{const f=fixture();vm.runInContext("session={admin:true};view='summary';nav();",f.context);assert.ok(!f.elements.some(e=>/业务员管理|添加业务员|待审批|全部名单/.test(e.textContent)));});
function feedback(f,submitted=false){f.context.submitted=submitted;vm.runInContext("session={admin:false,perf:false};roundId=1;submitKey='mocksubmit';selection={'1':1,'2':5};feedback={day:'2026-10-10',time:'2026-10-10T12:00:00+08:00',name:'模拟',username:'',tasks:[{id:1,title:'顺序模拟',kind:'seq',stages:['一','二','三']},{id:2,title:'自由模拟',kind:'free',stages:['一','二','三','四']}],notes:{'1':'','2':''},locked:{'1':1,'2':5},submitted};renderFeedback();",f.context);}
test('cumulative checkboxes lock previously saved stages; free and ordered tasks remain separate',()=>{const f=fixture();feedback(f);const checks=f.elements.filter(e=>e.type==='checkbox');assert.equal(checks[0].disabled,true);assert.equal(checks[1].disabled,false);assert.equal(checks[3].disabled,true);assert.equal(checks[5].disabled,true);checks[1].checked=true;checks[1].onchange();assert.equal(vm.runInContext("selection['1']",f.context),3);assert.equal(vm.runInContext("selection['2']",f.context),5);});
test('submitted panel has disabled stages, issue fields and save; successful response locks immediately',async()=>{const f=fixture();feedback(f);const btn=f.nodes.get('actions').children[0];const pending=btn.onclick({currentTarget:btn});await new Promise(setImmediate);assert.equal(f.requests.length,1);f.reply({ok:true,status:200});await pending;assert.ok(f.elements.filter(e=>e.type==='checkbox').slice(-7).every(e=>e.disabled));assert.ok(f.elements.filter(e=>e.tag==='textarea').slice(-2).every(e=>e.disabled));assert.equal(f.nodes.get('actions').children[0].disabled,true);assert.match(f.nodes.get('notice').textContent,/锁定/);});
test('failed save retains draft and exact retry key; cannot report success',async()=>{const f=fixture();feedback(f);const btn=f.nodes.get('actions').children[0];const pending=btn.onclick({currentTarget:btn});await new Promise(setImmediate);f.reply({error:'模拟保存失败'});await pending;assert.equal(vm.runInContext('feedback.submitted',f.context),false);assert.equal(vm.runInContext("selection['1']",f.context),1);assert.equal(vm.runInContext('submitKey',f.context),'mocksubmit');assert.match(f.nodes.get('notice').textContent,/失败/);});
test('summary refresh keeps old results and suppresses overlapping requests',async()=>{const f=fixture();const data={day:'2026-10-10',time:'2026-10-10T12:00:00+08:00',updated:'2026-10-10T13:00:00+08:00',revision:1,received:0,expected:1,cumulative_completed:{'1':0},tasks:[{id:1,title:'模拟',kind:'seq',stages:['一','二','三']}],rows:[{name:'模拟',username:'',submitted:false,masks:{},cumulative_masks:{},stage_states:{'1':['pending','pending','pending']},notes:{}}]};f.context.data=data;vm.runInContext("session={admin:true,perf:false,label:'模拟'};view='summary';roundId=1;summary=data;renderSummary();",f.context);const old=f.nodes.get('results');const one=vm.runInContext('refreshSummary(true)',f.context);vm.runInContext('refreshSummary(true)',f.context);await new Promise(setImmediate);assert.equal(f.requests.length,1);assert.equal(f.nodes.get('results'),old);f.reply({...data,updated:'2026-10-10T14:00:00+08:00'});await one;assert.equal(f.nodes.get('results'),old);assert.match(f.nodes.get('meta').textContent,/14:00/);});

test('summary preserves historical grey segments and separate round/completed counts',()=>{
 const f=fixture();f.context.data={day:'2026-10-14',time:'2026-10-14T13:00:00+08:00',updated:'2026-10-14T13:01:00+08:00',revision:0,received:0,expected:4,simulated:true,cumulative_completed:{'1':1},tasks:[{id:1,title:'同一任务',kind:'seq',stages:['阶段一','阶段二','阶段三']}],rows:[
 {name:'模拟A',username:'mock_a',submitted:false,cumulative_masks:{'1':7},stage_states:{'1':['history','history','history']},notes:{}},
 {name:'模拟B',username:'',submitted:false,cumulative_masks:{'1':3},stage_states:{'1':['history','history','pending']},notes:{}},
 {name:'模拟C',username:'mock_c',submitted:false,cumulative_masks:{'1':0},stage_states:{'1':['pending','pending','pending']},notes:{}},
 {name:'模拟D',username:'',submitted:false,cumulative_masks:{},stage_states:{'1':['pending','pending','pending']},notes:{}}]};
 vm.runInContext("session={admin:true,label:'验收试用'};summary=data;renderSummary();",f.context);
 const text=f.elements.map(x=>x.textContent).join('\n');
 assert.match(text,/本轮已反馈 0／4 人/);assert.match(text,/累计全部完成 1／4 人/);
 assert.match(text,/未反馈｜历史已完成3\/3/);assert.match(text,/未反馈｜历史累计2\/3/);
 assert.match(text,/未反馈｜历史累计0\/3/);assert.match(text,/未反馈｜暂无记录/);
 assert.equal(f.elements.filter(x=>x.tag==='i'&&x.className==='history').length,5);
 assert.equal(f.elements.filter(x=>x.tag==='details').length,3);
 assert.ok(!text.includes('undefined'));assert.ok(!text.includes('当前问题'));
 f.context.data.rows[0].submitted=true;f.context.data.rows[0].added_masks={'1':4};f.context.data.rows[0].stage_states={'1':['history','history','new']};
 vm.runInContext('renderSummary();',f.context);
 assert.ok(f.elements.some(x=>x.textContent==='本轮新增：阶段三'));
 f.context.data.rows[0].added_masks={'1':0};f.context.data.rows[0].stage_states={'1':['history','history','history']};vm.runInContext('renderSummary();',f.context);
 assert.ok(f.elements.some(x=>x.textContent==='本轮新增：无'));
});
test('thirty simulated rows paginate cumulative values without changing totals',()=>{
 const f=fixture();f.context.data={day:'2026-10-14',time:'2026-10-14T13:00:00+08:00',updated:'2026-10-14T13:01:00+08:00',received:0,expected:30,cumulative_completed:{'1':30},tasks:[{id:1,title:'模拟',kind:'seq',stages:['一','二','三']}],rows:Array.from({length:30},(_,i)=>({name:'模拟'+i,username:'',submitted:false,cumulative_masks:{'1':7},stage_states:{'1':['history','history','history']},notes:{}}))};
 vm.runInContext("session={admin:true,label:'模拟'};summary=data;renderSummary();",f.context);
 assert.equal(f.nodes.get('results').querySelector('tbody').children.length,10);
 const next=f.elements.find(x=>x.tag==='button'&&x.textContent==='下一页');next.onclick();
 assert.equal(vm.runInContext('page',f.context),2);
 assert.equal(f.nodes.get('results').querySelector('tbody').children[0].children[0].textContent,'模拟10');
 assert.ok(f.elements.some(x=>x.textContent==='累计全部完成 30／30 人'));
});

test('three colours and step groups come from backend states; unknown never becomes blue',()=>{
 const f=fixture();f.context.data={day:'2026-10-14',time:'2026-10-14T16:31:00+08:00',updated:'2026-10-14T16:32:00+08:00',received:1,expected:1,cumulative_completed:{'1':0},tasks:[{id:1,title:'四步',kind:'seq',stages:['一','二','三','四']}],rows:[{name:'模拟',username:'',submitted:true,cumulative_masks:{'1':7},stage_states:{'1':['history','history','new','pending']},notes:{}}]};
 vm.runInContext("session={admin:true,label:'模拟'};summary=data;renderSummary();",f.context);
 const states=f.elements.filter(e=>e.tag==='i').map(e=>e.className);
 assert.deepEqual(states,['history','history','new','pending']);
 assert.ok(f.elements.some(e=>e.textContent==='本轮新增 1 项'));
 assert.ok(f.elements.some(e=>e.textContent==='历史完成：一；二'));
 assert.ok(f.elements.some(e=>e.textContent==='本轮新增：三'));assert.ok(f.elements.some(e=>e.textContent==='未完成：四'));
 f.context.data.rows[0].stage_states={'1':['unknown','unknown','unknown','pending']};vm.runInContext('renderSummary();',f.context);
 assert.ok(f.elements.some(e=>e.textContent==='本轮新增：未确认'));
 const css=fs.readFile(new URL('../miniapp/style.css',import.meta.url),'utf8');return css.then(s=>{assert.match(s,/history[^{}]*\{background:#6b7280/);assert.match(s,/pending\{background:#e2e5e9/);});
});
function issueFixture(f){
 const raw='完整问题中文<&>\n'+'长原文'.repeat(200);
 const record={id:'1:456:1',uid:456,task:1,round:1,round_number:1,query_at:'2026-10-14T16:26:00+08:00',submitted_at:'2026-10-14T16:27:00+08:00',name:'模拟',username:'mock',progress:2,total:4,text:raw};
 f.context.data={day:'2026-10-14',time:'2026-10-14T16:31:00+08:00',updated:'2026-10-14T16:32:00+08:00',received:0,expected:0,cumulative_completed:{'1':0},tasks:[{id:1,title:'模拟',kind:'seq',stages:['一','二','三','四']}],rows:[],issues:{'1':{unscoped_count:0,history:{records:[record],count:6,page:1,pages:2},current:{records:[],count:0,page:1,pages:1}}}};
 vm.runInContext("session={admin:true,label:'模拟',perf:false};view='summary';roundId=2;summary=data;renderSummary();",f.context);return raw;
}
test('historical issues precede current, long original is available, paging uses one authenticated summary',async()=>{
 const f=fixture(),raw=issueFixture(f);const headings=f.elements.filter(e=>e.tag==='h3').map(e=>e.textContent);
 assert.deepEqual(headings,['历史问题','本轮问题']);assert.ok(f.elements.some(e=>e.textContent==='本轮暂无新增问题'));
 assert.ok(f.elements.some(e=>e.textContent===raw));assert.ok(f.elements.some(e=>e.textContent==='模拟（@mock）'));
 assert.ok(f.elements.some(e=>e.textContent.includes('当轮进度：2/4')));
 const next=f.elements.find(e=>e.tag==='button'&&e.textContent==='下一页问题');next.onclick();await new Promise(setImmediate);
 assert.equal(f.requests.length,1);assert.equal(f.requests[0].body.body.issue_pages['1'].history,2);
 const reply=structuredClone(f.context.data);reply.issues['1'].history.page=2;reply.issues['1'].history.records=[];f.reply(reply);await new Promise(setImmediate);
 assert.equal(vm.runInContext("summary.issues['1'].history.page",f.context),2);
});
test('queued issue page change retains old table and coalesces instead of overlapping',async()=>{
 const f=fixture();issueFixture(f);const old=f.nodes.get('results');const first=vm.runInContext('refreshSummary()',f.context);await new Promise(setImmediate);
 vm.runInContext("changeIssuePage('1','history',2)",f.context);assert.equal(f.requests.length,1);assert.equal(f.nodes.get('results'),old);
 f.reply(f.context.data);await first;await new Promise(setImmediate);assert.equal(f.requests.length,2);
 const reply=structuredClone(f.context.data);reply.issues['1'].history.page=2;f.reply(reply);await new Promise(setImmediate);
 assert.equal(vm.runInContext("summary.issues['1'].history.page",f.context),2);
});
test('only a successful authenticated refresh clears the connection failure banner',async()=>{
 const f=fixture();issueFixture(f);vm.runInContext("notice('连接中断，可点击刷新重试')",f.context);
 const pending=vm.runInContext('refreshSummary()',f.context);await new Promise(setImmediate);
 assert.equal(f.nodes.get('notice').textContent,'连接中断，可点击刷新重试');f.reply(f.context.data);await pending;
 assert.equal(f.nodes.get('notice').textContent,'');
});
