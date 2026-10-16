"""Small local HTTP origin; expose only behind a permanent HTTPS reverse proxy."""
import asyncio
import json
import os
from concurrent.futures import TimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from .bridge_diagnostics import MemberCheckUnavailable

ROOT=Path(__file__).resolve().parents[1]/'miniapp'

def start_server(api,loop,host='127.0.0.1',port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass # Never log initData or credentials.
        def answer(self,status,body,kind='application/json; charset=utf-8'):
            data=body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors https://web.telegram.org https://*.telegram.org")
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            path=self.path.split('?',1)[0]
            if path=='/health':return self.answer(200,dict(ok=True))
            assets={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            if path not in assets:return self.answer(404,dict(error='页面不存在'))
            file,kind=assets[path];self.answer(200,(ROOT/file).read_bytes(),kind)
        def do_POST(self):
            if not self.path.startswith('/api/') or '?' in self.path:return self.answer(404,dict(error='入口不存在'))
            future=None
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=131072:raise ValueError('请求过长或为空')
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('请求格式错误')
                envelope=json.loads(self.rfile.read(length))
                future=asyncio.run_coroutine_threadsafe(api.dispatch(self.path,envelope),loop)
                result=future.result(40);self.answer(200,result)
            except MemberCheckUnavailable as e:self.answer(503,dict(error=str(e),code='membership_unavailable',retryable=True))
            except PermissionError as e:self.answer(403,dict(error=str(e)))
            except ValueError as e:self.answer(400,dict(error=str(e) if str(e) in ('任务已调整，请进入新的查询','顺序任务不能跳过前置步骤','请填写本次全部任务','每项备注最多3500字','这是旧轮查询，请进入最新查询反馈','该日没有所选有效任务','请求内容已变化，请重新提交') else '填写或入口无效，请检查后重试'))
            except (KeyError,TypeError):self.answer(400,dict(error='填写或入口无效，请检查后重试'))
            except TimeoutError:self.answer(503,dict(error='连接较慢，请重试；已保存的内容不会重复计数'))
            except Exception:self.answer(503,dict(error='服务暂时不可用，请保留填写内容并重试'))
    server=ThreadingHTTPServer((host,port),Handler);server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True,name='miniapp-http');thread.start()
    return server

async def start(app,service):
    if os.environ.get('PROGRESS_MINIAPP_SERVER','').lower()!='true':return
    from .progress_ui import controller
    from .progress_miniapp import MiniAPI
    api=MiniAPI(controller(service),app.bot)
    app.bot_data['miniapp_server']=start_server(api,asyncio.get_running_loop(),port=int(os.environ.get('PROGRESS_MINIAPP_PORT','8765')))

async def stop(app):
    server=app.bot_data.pop('miniapp_server',None)
    if server:
        await asyncio.to_thread(server.shutdown);server.server_close()
