"""Desktop entry: start/reuse the local receiver, then open a standalone app window.

All control decisions remain on PYNQ. Launch never sends an initialize/move POST.
"""
import ctypes,hashlib,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
APP=ROOT/'desktop/PYNQ_Control_V12'
URL='http://127.0.0.1:8800/'
PYTHON=Path(r'D:\Vivado2026.1\2026.1\tps\win64\python-3.13.0\python.exe')
PYTHONW=PYTHON.with_name('pythonw.exe')
EDGE=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
IDENTITY='PYNQ_DESKTOP_CONTROL_V12'


def receiver():
    try:
        with urllib.request.urlopen(URL+'api/desktop',timeout=.6) as r:value=json.load(r)
        if value.get('identity')!=IDENTITY or value.get('project')!=str(ROOT):
            raise RuntimeError('Port 8800 is occupied by another application')
        return value
    except (urllib.error.URLError,TimeoutError):return None


def launch(open_window=True):
    if not all(p.exists() for p in [APP/'index.html',PYTHON,PYTHONW,EDGE]):
        raise RuntimeError('Desktop application files or installed Edge runtime missing')
    tmp=ROOT/'build/tmp';tmp.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env.update(TEMP=str(tmp),TMP=str(tmp),PYTHONDONTWRITEBYTECODE='1')
    # Serialize only launcher startup. The server never owns board hardware.
    kernel=ctypes.windll.kernel32;kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,'Local\\PYNQ_Desktop_Control_V12')
    if not handle:raise RuntimeError('Launcher mutex creation failed')
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    kernel.ReleaseMutex.argtypes=[ctypes.c_void_p];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    acquired=False
    try:
        if kernel.WaitForSingleObject(handle,12000) not in (0,128):raise RuntimeError('Another launcher is still starting')
        acquired=True;state=receiver()
        if state is None:
            logroot=ROOT/'logs/av_desktop_V12';logroot.mkdir(parents=True,exist_ok=True)
            run=logroot/('server_'+time.strftime('%Y%m%d_%H%M%S')+'_'+str(os.getpid()));run.mkdir()
            with (run/'console.log').open('ab') as log:
                child=subprocess.Popen([str(PYTHONW),'-B',str(APP/'server.py'),'--out',str(run)],cwd=ROOT,env=env,
                    stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
            (run/'launch.json').write_text(json.dumps(dict(pid=child.pid,source_sha256=hashlib.sha256((APP/'server.py').read_bytes()).hexdigest(),started=time.time())),encoding='utf-8')
            until=time.monotonic()+8
            while state is None and time.monotonic()<until:
                if child.poll() is not None:raise RuntimeError('Receiver exited; see '+str(run/'console.log'))
                time.sleep(.15);state=receiver()
            if state is None:raise RuntimeError('Receiver startup timeout; do not launch a duplicate')
        # Startup only connects and observes; hardware requests require a UI click.
        if open_window:
            # A dedicated app profile keeps cache on D: and avoids an ordinary browser tab.
            profile=ROOT/'build/desktop_edge_profile_V12'
            subprocess.Popen([str(EDGE),'--app='+URL,'--user-data-dir='+str(profile),
                '--no-first-run','--disable-background-mode','--window-size=1280,900'],cwd=ROOT,env=env,
                creationflags=subprocess.CREATE_NO_WINDOW)
        return dict(url=URL,receiver=state,window_requested=open_window,automatic_hardware_action=False)
    finally:
        if acquired:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


if __name__=='__main__':
    try:
        result=launch('--headless' not in sys.argv)
        log=ROOT/'logs/av_desktop_V12/last_launch.json';log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if sys.stdout:print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        log=ROOT/'logs/av_desktop_V12/launch_error.json';log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(json.dumps(dict(error=repr(exc),time=time.time()),indent=2),encoding='utf-8')
        if '--headless' in sys.argv:raise
        ctypes.windll.user32.MessageBoxW(None,'接收程序启动失败。日志：'+str(log)+'\n'+str(exc),'PYNQ Control',16)
