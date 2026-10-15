import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const require = createRequire(process.env.DAILY_ARTIFACT_MODULE ? path.join(process.env.DAILY_ARTIFACT_MODULE,'runtime.cjs') : import.meta.url);
const { Workbook,SpreadsheetFile }=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const data=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const wb=Workbook.create();
const summary=wb.worksheets.add('每日汇总'),detail=wb.worksheets.add('员工明细');
const blue='#16324F',light='#DEEAF5';
function setup(sh,last,widths,freezeRows){
 sh.showGridLines=false;sh.tabColor=blue;
 sh.getRange(`A1:${last}${Math.max(10,data.days.length*35+10)}`).format.font={name:'Noto Sans CJK SC',size:11,color:blue};
 for(let i=0;i<widths.length;i++)sh.getRange(`${String.fromCharCode(65+i)}:${String.fromCharCode(65+i)}`).format.columnWidth=widths[i];
 sh.freezePanes.freezeRows(freezeRows);
}
function title(sh,last,text){sh.getRange(`A1:${last}1`).merge();sh.getRange('A1').values=[[text]];sh.getRange(`A1:${last}1`).format.font={name:'Noto Sans CJK SC',size:17,bold:true,color:blue};sh.getRange('A1').format.rowHeight=32;sh.getRange(`A2:${last}2`).merge();sh.getRange('A2').values=[[data.label||'数据库生成 · 只读历史报表']];sh.getRange('A2').format.rowHeight=26;}
function columns(sh,row,last){
 const r=sh.getRange(`A${row}:${last}${row}`);
 r.format.horizontalAlignment='center';r.format.verticalAlignment='center';
 r.format.borders={insideVertical:{style:'thin',color:'#D9D9D9'},left:{style:'thin',color:'#D9D9D9'},right:{style:'thin',color:'#D9D9D9'}};
 if(sh.name==='员工明细')for(const col of ['A','B','G'])sh.getRange(`${col}${row}`).format.horizontalAlignment='left';
}
function head(sh,row,headers){let last=String.fromCharCode(64+headers.length);let r=sh.getRange(`A${row}:${last}${row}`);r.values=[headers];r.format.fill=blue;r.format.font={name:'Noto Sans CJK SC',size:11,bold:true,color:'#FFFFFF'};r.format.rowHeight=26;columns(sh,row,last);r.format.horizontalAlignment='center';}
function dateSerial(day){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(day))throw new Error('Invalid business date');
 const [y,m,d]=day.split('-').map(Number);
 const ms=Date.UTC(y,m-1,d);
 if(new Date(ms).toISOString().slice(0,10)!==day)throw new Error('Invalid business date');
 return (ms-Date.UTC(1899,11,30))/86400000;
}
function literal(v){return typeof v==='string'&&/^[=+\-@]/.test(v)?"'"+v:v;}
setup(summary,'H',[17,13,13,13,13,11,11,18],4);setup(detail,'H',[20,28,11,11,11,11,46,20],2);
title(summary,'H','日报历史记录 · 每日汇总');title(detail,'H','日报历史记录 · 员工明细');
head(summary,4,['日期','新增','维聊','有效','精聊','已交人数','应交人数','状态']);
let row=5;
for(const day of data.days){
 summary.getRange(`A${row}:H${row}`).values=[[dateSerial(day.day),...['新增','维聊','有效','精聊'].map(m=>day.submitted?day.totals[m]:null),day.submitted,day.expected,day.status]];
 summary.getRange(`A${row}`).setNumberFormat('yyyy-mm-dd');summary.getRange(`B${row}:G${row}`).setNumberFormat('#,##0');summary.getRange(`A${row}:H${row}`).format.rowHeight=26;
 columns(summary,row,'H');
 if(row%2)summary.getRange(`A${row}:H${row}`).format.fill='#F2F6FA';row++;
}
row=4;
for(const day of data.days){
 const band=detail.getRange(`A${row}:H${row}`);band.merge();detail.getRange(`A${row}`).values=[[dateSerial(day.day)]];band.setNumberFormat('yyyy-mm-dd');band.format.fill=light;band.format.font={name:'Noto Sans CJK SC',size:15,bold:true,color:blue};band.format.horizontalAlignment='center';band.format.verticalAlignment='center';band.format.rowHeight=30;
 head(detail,++row,['员工名称','Telegram 用户名','新增','维聊','有效','精聊','备注','提交状态']);row++;
 for(const values of day.rows){
  // Excel has a 409.5-point row-height limit. Long notes use labelled continuation
  // rows, with no duplicated metrics; concatenating note cells restores all text.
  const notes=String(values[6]);const chunks=[];let rest=notes;
  while(rest.length){let end=Math.min(250,rest.length);const breaks=[...rest.slice(0,end).matchAll(/\n/g)];if(breaks.length>9)end=breaks[8].index+1;else if(end<rest.length){const n=rest.lastIndexOf('\n',end);if(n>100)end=n+1;}chunks.push(rest.slice(0,end));rest=rest.slice(end);}
  if(!chunks.length)chunks.push('');
  for(let j=0;j<chunks.length;j++){
   const cells=j===0?[...values.slice(0,6),chunks[j],values[7]]:[values[0]+'（备注续）',values[1],null,null,null,null,chunks[j],'备注续'];
   detail.getRange(`A${row}:H${row}`).values=[cells.map((v,i)=>i===1?v:literal(v))];detail.getRange(`C${row}:F${row}`).setNumberFormat('#,##0');
   const r=detail.getRange(`A${row}:H${row}`);r.format.font={name:'Noto Sans CJK SC',size:11,color:blue};r.format.wrapText=true;r.format.rowHeight=Math.min(409,Math.max(27,18*Math.ceil(cells[0].length/9),18*Math.ceil(String(cells[1]).length/22),18*Math.ceil(String(cells[7]).length/10),18*(chunks[j].split('\n').reduce((n,l)=>n+Math.max(1,Math.ceil(l.length/21)),0))));
   columns(detail,row,'H');
   if(row%2)r.format.fill='#F2F6FA';row++;
  }
 }
 row++;
}
wb.recalculate();
const output=await SpreadsheetFile.exportXlsx(wb);await output.save(process.argv[3]);
if(process.argv[4]){
 for(const [sheetName,range,name] of [['每日汇总','A1:H10','汇总'],['员工明细','A1:H18','明细']]){
  const image=await wb.render({sheetName,range,scale:1.5});await fs.writeFile(path.join(process.argv[4],name+'.png'),new Uint8Array(await image.arrayBuffer()));
 }
}
