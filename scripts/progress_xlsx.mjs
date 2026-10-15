import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';
import path from 'node:path';
const require=createRequire(path.join(process.env.DAILY_ARTIFACT_MODULE||path.resolve('scripts'),'runtime.cjs'));
const {Workbook,SpreadsheetFile,FileBlob}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const data=JSON.parse(await fs.readFile(process.argv[2],'utf8')),wb=Workbook.create();
const template=await SpreadsheetFile.importXlsx(await FileBlob.load(fileURLToPath(new URL('../templates/Telegram_Progress_History_Template_v2.xlsx',import.meta.url))));
const definitions=[['查询汇总','L',5,data.summaries],['员工进度历史','S',6,null],['当前问题历史','L',5,data.problems],['任务定义','J',5,data.definitions]];
function excelDate(day){const [y,m,d]=day.split('-').map(Number);return(Date.UTC(y,m-1,d)-Date.UTC(1899,11,30))/86400000;}
function literal(v){if(typeof v!=='string')return v;if(v.length>32767)throw Error('文本超过Excel单元格上限；原记录不变');return /^[=+\-']/.test(v)?"'"+v:v;}
function lines(text,width){return String(text??'').split('\n').reduce((sum,line)=>sum+Math.max(1,Math.ceil([...line].reduce((n,c)=>n+(c.codePointAt(0)>255?2:1),0)/Math.max(7,width-1))),0);}
function fit(values,widths,minimum){return Math.min(180,Math.max(minimum,...values.map((v,i)=>16*lines(v,widths[i])+4)));}
function styles(target,source){target.copyFrom(source,'all');target.clear({applyTo:'contents'});target.format.horizontalAlignment='center';target.format.verticalAlignment='center';target.format.wrapText=true;}
function merged(sh,ref,row,last,text,sourceRow=row,height=24){const r=sh.getRange(`A${row}:${last}${row}`);styles(r,ref.getRange(`A${sourceRow}:${last}${sourceRow}`));r.merge();sh.getRange(`A${row}`).values=[[literal(text)]];r.format.rowHeight=height;}
function base(name,last){const ref=template.worksheets.getItem(name),sh=wb.worksheets.add(name),widths=[];sh.showGridLines=false;sh.freezePanes.freezeRows(5);for(let i=0;i<last.charCodeAt(0)-64;i++){const col=String.fromCharCode(65+i),width=ref.getRange(`${col}:${col}`).format.columnWidth;widths.push(width);sh.getRange(`${col}:${col}`).format.columnWidth=width;}
 for(let row=1;row<=3;row++){let value=ref.getRange(`A${row}`).values[0][0];if(row===1)value=String(value).replace('（修正版式样板）','');if(row===3)value=data.label;merged(sh,ref,row,last,value,row,row===1?28:24);}return {sh,ref,widths};}
function header(sh,ref,row,last,sourceHead){styles(sh.getRange(`A${row}:${last}${row}`),ref.getRange(`A${sourceHead}:${last}${sourceHead}`));sh.getRange(`A${row}:${last}${row}`).values=[ref.getRange(`A${sourceHead}:${last}${sourceHead}`).values[0].map(v=>String(v).replace('身份ID（演示）','身份ID'))];sh.getRange(`A${row}:${last}${row}`).format.rowHeight=ref.getRange(`A${sourceHead}:${last}${sourceHead}`).format.rowHeight;}
function body(sh,ref,start,last,rows,widths,sourceHead,dateCol=0,percent=false){let row=start;const neutral=ref.getRange(`A${sourceHead+1}`),sourceHeight=ref.getRange(`A${sourceHead+1}:${last}${sourceHead+1}`).format.rowHeight;
 for(const original of rows){const values=original.map(literal);if(values[dateCol])values[dateCol]=excelDate(values[dateCol]);const range=sh.getRange(`A${row}:${last}${row}`);styles(range,neutral);range.values=[values];range.format.rowHeight=fit(original,widths,sourceHeight);sh.getRange(`${String.fromCharCode(65+dateCol)}${row}`).setNumberFormat('yyyy-mm-dd');if(percent)sh.getRange(`J${row}`).setNumberFormat('0%');
  // One feedback = one row. Full issue text stays in its original cell.
  for(let i=0;i<values.length;i++){if(last==='S'&&(i>=5&&i<=8||i===18)&&values[i]==='已完成'){const cell=sh.getRange(`${String.fromCharCode(65+i)}${row}`);styles(cell,template.worksheets.getItem('员工进度历史').getRange('F7'));cell.values=[['已完成']];}else if(last==='S'&&values[i]==='未反馈'){const cell=sh.getRange(`${String.fromCharCode(65+i)}${row}`),source=i===4?'E10':i>=5&&i<=8?'F10':null;if(source){styles(cell,template.worksheets.getItem('员工进度历史').getRange(source));cell.values=[['未反馈']];}}}row++;
 }return row;}
for(const [name,last,sourceHead,rows] of definitions){const {sh,ref,widths}=base(name,last);if(rows!==null){header(sh,ref,5,last,sourceHead);body(sh,ref,6,last,rows,widths,sourceHead,name==='任务定义'?1:0,name==='查询汇总');}else{let row=5;for(const group of data.groups){const t=group.task,text=`任务：${t.title}    ｜    规则：${t.kind==='seq'?'按顺序完成':'自由完成'}    ｜    阶段总数：${t.stages.length}`;merged(sh,ref,row,last,text,5,Math.max(24,16*lines(text,widths.reduce((a,b)=>a+b,0))+4));header(sh,ref,row+1,last,sourceHead);row=body(sh,ref,row+2,last,group.rows,widths,sourceHead)+1;}}}
wb.recalculate();await (await SpreadsheetFile.exportXlsx(wb)).save(process.argv[3]);
if(process.env.PROGRESS_RENDER_DIR){await fs.mkdir(process.env.PROGRESS_RENDER_DIR,{recursive:true});for(const [name,last] of definitions){const picture=await wb.render({sheetName:name,range:`A1:${last}${name==='员工进度历史'?12:10}`,scale:1,format:'png'});await fs.writeFile(path.join(process.env.PROGRESS_RENDER_DIR,name+'.png'),new Uint8Array(await picture.arrayBuffer()));}console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#VALUE!|#DIV/0!',options:{useRegex:true,maxResults:20},maxChars:800})).ndjson);}
process.exit(0);
