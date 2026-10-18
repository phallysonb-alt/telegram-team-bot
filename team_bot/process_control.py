"""Local Windows process identity and graceful run_polling stop requests.

Not a remote API. Requests target a PID AND its kernel creation time; a stale
file cannot stop a later process that reuses the same PID.
"""
import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


class ProcessRef:
    def __init__(self, pid, terminate=False):
        if os.name != 'nt': raise RuntimeError('Windows process identity required')
        k = self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.pid = int(pid)
        self.handle = k.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, self.pid)
        if not self.handle: raise ProcessLookupError('进程已退出或无法核实身份')
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times]): raise OSError('无法读取进程创建时间')
            self.birth = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
            buffer = ctypes.create_unicode_buffer(32768); length = wintypes.DWORD(len(buffer))
            if not k.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(length)): raise OSError('无法读取进程路径')
            self.image = Path(buffer.value).resolve()
        except BaseException:
            self.close(); raise

    def alive(self):
        result = self.k.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258): raise OSError('进程状态核实失败')
        return result == 258

    def kill(self):
        if self.alive() and not self.k.TerminateProcess(self.handle, 1): raise OSError('停止已核实进程失败')

    def identity(self): return dict(pid=self.pid, birth=self.birth)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle); self.handle = None

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def stop_requested(path, pid, birth):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 8192: return False
        targets = json.loads(raw).get('targets', [])
        return dict(pid=pid, birth=birth) in targets
    except (OSError, ValueError, TypeError, AttributeError): return False


async def watch_stop(app, data):
    if os.name != 'nt': return
    with ProcessRef(os.getpid()) as me:
        while True:
            if app.running and stop_requested(Path(data) / 'stop-request.json', me.pid, me.birth):
                # PTB drains queued handlers/persistence and invokes post_stop.
                app.stop_running()
                return
            await asyncio.sleep(.25)
