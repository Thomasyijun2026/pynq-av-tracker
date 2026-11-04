"""Installed board service: an idempotent service-start request never initializes.

The HTTP manager owns no peripheral while waiting. The reviewed finite runtime
owns the canonical hardware lock and makes every localization/motion decision.
"""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from http.server import ThreadingHTTPServer
from av_observer_bridge_V2 import Bridge, process_ticks
from av_observer_result_V1 import Observer
from av_observer_ui_V3 import handler as observer_handler


def atomic(path, value):
    path=Path(path); temp=path.with_name(path.name+'.new')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False); f.flush(); os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(str(path.parent),os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InstalledService(Bridge):
    def __init__(self, config, *, boot=None, clock=time.monotonic, ticks=process_ticks):
        self.cfg=copy.deepcopy(config)
        boot=boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        folder=Path(config['install_root'])/'state'/boot
        folder.mkdir(parents=True,exist_ok=True)
        super().__init__(folder,boot,config['base']['session_root'],factory=Observer,ticks=ticks,clock=clock)
        self.guard=threading.RLock(); self.launch_thread=None; self.children={}
        self.receipt_path=folder/'service_start.json'
        self.receipt=load(self.receipt_path) if self.receipt_path.exists() else None
        self.last_request=None

    def lifecycle(self):
        with self.guard: receipt=copy.deepcopy(self.receipt)
        if receipt is None:
            return dict(status='WAITING_FOR_DESKTOP',ready=False,message='板子已连接，等待软件请求启动定位服务。')
        value=dict(receipt,ready=False)
        if receipt['status']=='FAILED': return value
        pid=receipt.get('pid'); ticks=receipt.get('start_ticks')
        child=self.children.get(pid)
        if child: child.poll()  # Reap our naturally exited child only.
        if pid is None:
            if self.launch_thread and self.launch_thread.is_alive():
                value.update(status='STARTING',message='板端正在准备定位服务，请稍候。')
            else:
                value.update(status='UNKNOWN_DO_NOT_RESEND',message='启动回执未完整保存，保留原请求，请查看服务记录。')
            return value
        alive=self.ticks(pid)==ticks
        run=Path(receipt['run'])
        if not alive:
            result=load(run/'summary.json') if (run/'summary.json').exists() else {}
            verified=(result.get('boot_id')==self.boot and result.get('pid')==pid and result.get('start_ticks')==ticks)
            normal=verified and result.get('cleanup_verified') is True and result.get('status') in ('NATURAL_LIFETIME_END','REQUESTED_IDLE_FINISH')
            value.update(status='ENDED_CLEAN' if normal else 'FAILED',message=result.get('error') or ('定位服务已正常结束，可以重新启动。' if normal else '定位服务未就绪，启动日志已保存。'),cleanup_verified=normal,result=result)
            return value
        ready=load(run/'ready.json') if (run/'ready.json').exists() else {}
        expected=self.cfg['contract']
        if ready.get('pid')==pid and ready.get('start_ticks')==ticks and ready.get('boot_id')==self.boot and ready.get('runtime_contract')==expected:
            try:
                s=Observer(str(run/'control'),pid,self.boot).call('/brief')
                if (s.get('boot_id')==self.boot and s.get('pid')==pid and s.get('runtime_start_ticks')==ticks
                        and s.get('runtime_contract')==expected and s.get('full_turn_initialization_supported') is True
                        and not s.get('closed')):
                    value.update(status='READY',ready=True,message='定位服务已就绪，请手动点击开始初始化。')
                    return value
            except Exception as exc: value['readiness_error']=repr(exc)
        elapsed=self.clock()-receipt['requested_monotonic']
        value.update(status='STARTING',message='板端正在启动定位服务（已等待%.0f秒）。'%elapsed,elapsed_seconds=elapsed)
        return value

    def state(self):
        s=super().state(); life=self.lifecycle()
        s.update(service_lifecycle=life,service_start_supported=True,service_identity='PYNQ_SERVICE_BOOTSTRAP_V1',manager_pid=os.getpid(),manager_start_ticks=self.ticks(os.getpid()))
        s['runtime_ready']=life['ready']
        s['can_request']=bool(life['ready'] and s.get('can_request') is True)
        s['runtime_status']=life['status']; s['status_message']=life['message']
        s['initialization_budget_seconds']=self.cfg['contract']['initialization_budget_seconds']
        s['selection_budget_seconds']=self.cfg['contract']['selection_budget_seconds']
        return s

    def ensure_started(self, request):
        if type(request) is not dict or set(request)!={'request_id'}:
            raise ValueError('Service start accepts request_id only')
        rid=request['request_id']
        if not isinstance(rid,str) or not 1<=len(rid)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in rid):
            raise ValueError('Invalid request ID')
        with self.guard:
            life=self.lifecycle()
            if self.receipt and life['status']!='ENDED_CLEAN':
                return dict(accepted=life['status'] in ('STARTING','READY'),reused=True,initialization_submitted=False,boot_id=self.boot,service=life)
            # An intent survives lost HTTP responses and manager restarts.
            attempt=uuid.uuid4().hex
            self.receipt=dict(status='STARTING',message='已收到软件启动请求，正在启动定位服务。',attempt_id=attempt,request_id=rid,
                boot_id=self.boot,requested_monotonic=self.clock(),run=str(self.folder/('runtime_'+attempt)),initialization_submitted=False)
            atomic(self.folder/('request_'+attempt+'.json'),self.receipt)
            atomic(self.receipt_path,self.receipt)
            self.launch_thread=threading.Thread(target=self._launch,name='service-start',daemon=True)
            self.launch_thread.start()
            return dict(accepted=True,reused=False,initialization_submitted=False,boot_id=self.boot,service=copy.deepcopy(self.receipt))

    def _launch(self):
        # PYNQ device discovery expects a current asyncio loop, including in a
        # background service-start thread (Jupyter normally supplies one).
        import asyncio
        loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
        try:
            self.launch_runtime()
        except BaseException as exc:
            import traceback
            (self.folder/('startup_error_'+self.receipt['attempt_id']+'.txt')).write_text(traceback.format_exc())
            with self.guard:
                self.receipt.update(status='FAILED',message='定位服务启动失败：'+str(exc),error=repr(exc))
                atomic(self.receipt_path,self.receipt)
        finally:
            loop.close();asyncio.set_event_loop(None)

    def command(self, action, body):
        if not self.lifecycle()['ready']: raise ValueError('定位服务尚未就绪，初始化没有开始。')
        return super().command(action,body)

    def launch_runtime(self):
        import fcntl
        code=Path(self.cfg['code']); bundle=Path(self.cfg['base']['hardware_root'])
        for name,digest in self.cfg['manifest'].items():
            if Path(name).name!=name or sha(code/name)!=digest: raise ValueError('Installed source changed: '+name)
        for name,digest in self.cfg['base']['hardware_files'].items():
            if sha(bundle/name)!=digest: raise ValueError('Hardware file changed: '+name)
        for p in Path('/proc').iterdir():
            if not p.name.isdigit() or int(p.name)==os.getpid(): continue
            try: cmd=(p/'cmdline').read_bytes().replace(bytes([0]),b' ').decode(errors='replace')
            except OSError: continue
            if any(x in cmd for x in ('av_phase_runtime','av_managed_observer','av_observer_bridge_V1.py')):
                raise RuntimeError('Existing AV process: '+p.name)
        with (self.root/'standalone_runtime.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            sys.path.append(str(bundle))
            from av_pmod_check_V4 import Check, holders
            if holders(): raise RuntimeError('Camera or serial is owned by another process')
            import pynq, mic_capture_fast
            loaded=bool(pynq.PL.bitfile_name and Path(pynq.PL.bitfile_name).exists() and sha(pynq.PL.bitfile_name)==self.cfg['base']['hardware_files']['av_pmod.bit'])
            if not loaded:
                atomic(self.folder/'overlay_intent.json',dict(boot_id=self.boot,bit_sha256=self.cfg['base']['hardware_files']['av_pmod.bit']))
                pynq.Overlay(str(bundle/'av_pmod.bit'))
                mic_capture_fast.reset_dma(pynq.MMIO(0x40400000,65536))
            class ReadinessCheck(Check):
                def save(_,name,value): atomic(self.folder/('hardware_'+name),value)
            quiet=ReadinessCheck(bundle,self.cfg['base']['hardware_files'],self.boot).quiet()
            if not quiet['cleanup_verified']: raise RuntimeError('Hardware not idle: '+json.dumps(quiet))
            atomic(self.folder/'hardware_admission.json',dict(quiet=quiet,overlay_loaded=not loaded))
        stop=self.clock()+21600.0
        config=copy.deepcopy(self.cfg['base'])
        config.pop('axis_file',None)
        config.update(boot_id=self.boot,folder=self.receipt['run'],modules={n:d for n,d in self.cfg['manifest'].items() if n.endswith('.py')},
            assets={'known_source.npy':self.cfg['manifest']['known_source.npy']},installation_id=self.cfg['installation_id'],
            observer=dict(pid=os.getpid(),start_ticks=self.ticks(os.getpid()),boot_id=self.boot,remote=str(self.folder)),
            runtime_contract=self.cfg['contract'],stop_monotonic=stop,no_new_operation_monotonic=stop)
        config_path=self.folder/('config_'+self.receipt['attempt_id']+'.json'); atomic(config_path,config)
        with self.guard:
            self.receipt.update(status='SPAWNING',config=str(config_path)); atomic(self.receipt_path,self.receipt)
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        with (self.folder/('runtime_'+self.receipt['attempt_id']+'.log')).open('ab') as log:
            child=subprocess.Popen([sys.executable,'-B',str(code/'av_phase_runtime_V13.py'),'--config',str(config_path)],cwd=str(code),env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        ticks=self.ticks(child.pid)
        if ticks is None: raise RuntimeError('Spawned process identity unknown; no retry')
        with self.guard:
            self.children[child.pid]=child
            self.receipt.update(status='STARTING',pid=child.pid,start_ticks=ticks,stop_monotonic=stop)
            atomic(self.receipt_path,self.receipt)


def handler(service,page):
    parent=observer_handler(service,page)
    class Handler(parent):
        def do_POST(self):
            if self.path!='/api/service/start': return super().do_POST()
            try:
                if self.client_address[0] not in ('127.0.0.1','192.168.2.1'): raise ValueError('Client address')
                if self.headers.get('X-AV-Request')!='1': raise ValueError('Request header')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024: raise ValueError('Request length')
                result=service.ensure_started(json.loads(self.rfile.read(size)))
                self.send(202 if result['accepted'] else 409,result)
            except Exception as exc:
                self.send(400,dict(error=str(exc)))
    return Handler


def main():
    import fcntl
    parser=argparse.ArgumentParser(); parser.add_argument('--config',required=True)
    cfg=load(parser.parse_args().config); service=InstalledService(cfg)
    lock=(service.root/'runtime_manager.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    page=(Path(cfg['code'])/'av_observer_ui_V7.html').read_bytes()
    servers=[]
    for port in (8764,8770):
        server=ThreadingHTTPServer(('0.0.0.0',port),handler(service,page)); server.daemon_threads=True
        threading.Thread(target=server.serve_forever,name='av-http-'+str(port),daemon=True).start(); servers.append(server)
    atomic(service.folder/'manager_owner.json',dict(boot_id=service.boot,pid=os.getpid(),start_ticks=process_ticks(os.getpid()),ports=[8764,8770],automatic_initialization=False))
    while True: time.sleep(1)


if __name__=='__main__': main()
