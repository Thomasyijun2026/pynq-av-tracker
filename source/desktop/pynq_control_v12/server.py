"""Automatic board connection and service startup. Initialization is explicit."""
import argparse
import copy
import hashlib
import json
import os
import signal
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from serve_av_receiver_V3 import Relay as PriorRelay, handler as prior_handler

from stimulus import Stimulus

IDENTITY = 'PYNQ_DESKTOP_CONTROL_V12'
BOARD = '192.168.2.99'
MANAGER = 'http://' + BOARD + ':8764'
SYSTEM = 'http://' + BOARD + ':9090/api'
PORT = 8800


def get_json(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/json'}, method='GET')
    with urllib.request.urlopen(request, timeout=1.2) as response:
        data = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(data) > 1024 * 1024:
            raise ValueError('Invalid status response')
        value = json.loads(data)
        if type(value) is not dict:
            raise ValueError('Status must be an object')
        return value


class ConnectionMonitor:
    """Two independent probes: a missing localization service cannot hide the board."""
    def __init__(self, out, fetch=get_json, interval=1.0, max_age=4.0, clock=time.monotonic):
        self.out, self.fetch = Path(out), fetch
        self.interval, self.max_age, self.clock = interval, max_age, clock
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.samples = {'system': None, 'service': None}
        self.sequence = 0
        self.threads = []
        self.last_transition = None
        self.start_reply = None
        self.stimulus = None

    def start(self):
        if self.threads:
            raise RuntimeError('Monitor already started')
        for name, url in [('system', SYSTEM), ('service', MANAGER + '/api/state')]:
            worker = threading.Thread(target=self._loop, args=(name, url), daemon=True,
                                      name='pynq-' + name + '-monitor')
            self.threads.append(worker)
            worker.start()
        worker=threading.Thread(target=self._start_loop,daemon=True,name='pynq-service-start')
        self.threads.append(worker); worker.start()

    def _start_loop(self):
        while not self.stop.wait(1):
            try: self.ensure_service()
            except Exception as exc:
                self.start_reply=dict(status='ERROR',error=repr(exc),automatic_initialization=False)

    def ensure_service(self):
        with self.lock: item=copy.deepcopy(self.samples['service'])
        if not item or not item['ok'] or not 0<=self.clock()-item['monotonic']<=self.max_age: return
        state=item['value']; life=state.get('service_lifecycle') or {}
        if state.get('service_identity')!='PYNQ_SERVICE_BOOTSTRAP_V1' or not state.get('service_start_supported'): return
        if life.get('status') not in ('WAITING_FOR_DESKTOP','ENDED_CLEAN'): return
        boot=state.get('boot_id')
        if not isinstance(boot,str) or not boot: return
        # Stable request identity survives a lost response and a desktop restart.
        key=hashlib.sha256((boot+'|'+str(state.get('manager_start_ticks'))+'|'+str(life.get('attempt_id','first'))).encode()).hexdigest()[:32]
        folder=ROOT/'logs/av_desktop_V12/service_requests'; folder.mkdir(parents=True,exist_ok=True)
        intent=folder/(key+'_intent.json'); reply=folder/(key+'_reply.json')
        body=dict(request_id='desktop_service_'+key)
        if intent.exists():
            self.start_reply=json.loads(reply.read_text(encoding='utf-8')) if reply.exists() else dict(status='UNKNOWN_DO_NOT_RESEND',request_id=body['request_id'])
            return
        with intent.open('x',encoding='utf-8') as f:
            json.dump(dict(body=body,boot_id=boot,pc_unix=time.time(),automatic_initialization=False),f);f.flush();os.fsync(f.fileno())
        req=urllib.request.Request(MANAGER+'/api/service/start',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-AV-Request':'1'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=3) as response:
                value=dict(http=response.status,body=json.load(response),status='ACKNOWLEDGED',request_id=body['request_id'])
        except Exception as exc:
            value=dict(status='UNKNOWN_DO_NOT_RESEND',error=repr(exc),request_id=body['request_id'])
        with reply.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        self.start_reply=value

    def _loop(self, name, url):
        while not self.stop.is_set():
            self.probe(name, url)
            self.stop.wait(self.interval)

    def probe(self, name, url):
        try:
            value = self.fetch(url)
            if name == 'system' and not isinstance(value.get('version'), str):
                raise ValueError('System version missing')
            if name == 'service' and (not isinstance(value.get('boot_id'), str) or not value['boot_id']):
                raise ValueError('Service boot identity missing')
            sample = dict(ok=True, value=value, error=None)
        except Exception as exc:
            sample = dict(ok=False, value=None, error=type(exc).__name__ + ': ' + str(exc)[:200])
        sample.update(monotonic=self.clock(), pc_unix=time.time())
        with self.lock:
            self.samples[name] = sample
            self.sequence += 1

    def snapshot(self):
        with self.lock:
            samples, sequence = copy.deepcopy(self.samples), self.sequence
        now = self.clock()
        def current(name):
            item = samples[name]
            return bool(item and item['ok'] and 0 <= now - item['monotonic'] <= self.max_age)
        system_ok, service_ok = current('system'), current('service')
        s = copy.deepcopy(samples['service']['value']) if service_ok else {}
        board_ok = system_ok or service_ok
        ready = (service_ok and s.get('runtime_ready') is True and not s.get('closed')
                 and type(s.get('pid')) is int and s['pid'] > 0
                 and (s.get('service_lifecycle') or {}).get('ready') is not False)
        full_turn = s.get('full_turn_initialization_supported') is True
        if not board_ok:
            phase = 'CONNECTING' if all(v is None for v in samples.values()) else 'RECONNECTING'
            message = '正在连接板子' if phase == 'CONNECTING' else '板子连接中断，正在自动重连'
        elif not service_ok:
            phase, message = 'UNAVAILABLE', '板子已连接，定位服务尚未响应'
        elif s.get('closed'):
            phase, message = 'ENDED', '板子已连接，定位服务已结束'
        elif not ready:
            phase = 'FAILED' if (s.get('service_lifecycle') or {}).get('status') in ('FAILED','UNKNOWN_DO_NOT_RESEND') else 'STARTING'
            message = (s.get('service_lifecycle') or {}).get('message') or '板子已连接，正在等待定位服务就绪'
        elif not full_turn:
            phase, message = 'INCOMPATIBLE', '板子已连接，当前服务尚不支持整圈初始化'
        elif s.get('can_request') is not True:
            phase, message = 'BUSY', '定位服务已连接，正在执行任务'
        else:
            phase, message = 'READY', '板子和定位服务已连接，可以点击开始初始化'
        s.update(board_connected=board_ok, runtime_ready=bool(ready),
                 can_request=bool(ready and full_turn and s.get('can_request') is True))
        if not service_ok:
            s.update(boot_id=None, pid=None, active=None, active_action=None, jobs=[], sources=[],
                     calibration='PENDING', full_turn_initialization_supported=False)
        status = dict(sequence=sequence, pc_unix=time.time(), automatic_connection=True,
                      board_address=BOARD, service_status=phase, message=message,
                      service_available=service_ok, system_available=system_ok,
                      system_version=samples['system']['value']['version'] if system_ok else None,
                      retry_seconds=self.interval, stale_after_seconds=self.max_age,
                      probes={name: None if item is None else dict(ok=current(name),
                          age_seconds=max(0, now-item['monotonic']), observed_pc_unix=item['pc_unix'],
                          error=item['error']) for name, item in samples.items()})
        s['connection_monitor'] = status
        s['pc_audio'] = self.stimulus.snapshot() if self.stimulus else dict(status='STARTING', ready=False, playing=False)
        status.update(automatic_service_start=True,service_start_reply=copy.deepcopy(self.start_reply))
        return s

    def log_transition(self):
        state = self.snapshot()
        monitor = state['connection_monitor']
        key = (state['board_connected'], monitor['service_status'], state.get('boot_id'), state.get('pid'))
        with self.lock:
            if key == self.last_transition:
                return
            self.last_transition = key
            with (self.out / 'connection_transitions.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(dict(pc_unix=monitor['pc_unix'], board_connected=state['board_connected'],
                    runtime_ready=state['runtime_ready'], boot_id=state.get('boot_id'), pid=state.get('pid'),
                    connection_monitor=monitor), ensure_ascii=False) + '\n')

    def close(self):
        self.stop.set()
        for worker in self.threads:
            worker.join(2)
        return all(not worker.is_alive() for worker in self.threads)


class Relay(PriorRelay):
    def __init__(self, out, monitor):
        super().__init__('http://' + BOARD + ':8770', out)
        self.upstream, self.monitor = MANAGER, monitor

    def command(self, path, body):
        # Never turn connection/reconnection/READY into an initialization request.
        state = self.monitor.snapshot()
        if state['can_request'] is not True:
            raise ValueError(state['connection_monitor']['message'])
        if path == '/api/initialize' and not state.get('pc_audio', {}).get('ready'):
            raise ValueError(state.get('pc_audio', {}).get('message', 'First test audio is not ready'))
        return super().command(path, body)


def handler(relay, page):
    base = prior_handler(relay, page)
    assets = {'/' + n: (Path(__file__).parent / n).read_bytes() for n in ('app.js', 'control-model.js')}
    class Handler(base):
        def do_GET(self):
            if self.path in assets:
                return self.send(200, 'text/javascript; charset=utf-8', assets[self.path])
            if self.path == '/api/desktop':
                return self.send(200, 'application/json', json.dumps(dict(identity=IDENTITY,
                    project=str(ROOT), pid=os.getpid(), role='DISPLAY_AND_ID_RELAY_ONLY',
                    page_sha256=hashlib.sha256(page).hexdigest(), automatic_connection=True, automatic_service_start=True,
                    state_push='SSE_WITH_POLL_FALLBACK', automatic_hardware_action=False,
                    stimulus='FIRST_WAV_ON_BOARD_REFERENCE_PHASE', pc_audio=relay.monitor.stimulus.snapshot())).encode())
            if self.path == '/api/state':
                return self.send(200, 'application/json', json.dumps(relay.monitor.snapshot()).encode())
            if self.path == '/api/events':
                self.connection.settimeout(3)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                try:
                    while not relay.monitor.stop.is_set():
                        payload = json.dumps(relay.monitor.snapshot(), ensure_ascii=False)
                        self.wfile.write(('retry: 1000\nevent: state\ndata: ' + payload + '\n\n').encode())
                        self.wfile.flush()
                        relay.monitor.stop.wait(1)
                except (OSError, ConnectionError):
                    pass
                return
            return super().do_GET()
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--seconds', type=int, default=43200)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.drive.upper() != 'D:' or not 1 <= args.seconds <= 43200:
        raise ValueError('D drive and bounded lifetime required')
    out.mkdir(parents=True, exist_ok=True)
    page = (Path(__file__).parent / 'index.html').read_bytes()
    monitor = ConnectionMonitor(out)
    stimulus = Stimulus(out / 'stimulus')
    monitor.stimulus = stimulus
    commands = ROOT / 'logs/av_desktop_V12/requests'
    commands.mkdir(parents=True, exist_ok=True)
    relay = Relay(commands, monitor)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), handler(relay, page))
    server.daemon_threads = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: monitor.stop.set())
    owner = dict(identity=IDENTITY, pid=os.getpid(), url='http://127.0.0.1:' + str(PORT) + '/',
                 started_pc_unix=time.time(), duration=args.seconds,
                 page_sha256=hashlib.sha256(page).hexdigest(), board_processes_touched=False)
    (out / 'owner.json').write_text(json.dumps(owner, indent=2), encoding='utf-8')
    (ROOT / 'logs/av_desktop_V12/active.json').write_text(json.dumps(dict(owner, run=str(out)), indent=2), encoding='utf-8')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monitor.start()
    stimulus.start()
    deadline = time.monotonic() + args.seconds
    try:
        while not monitor.stop.wait(1) and time.monotonic() < deadline:
            monitor.log_transition()
    finally:
        stimulus_closed = stimulus.close()
        probes_closed = monitor.close()
        server.shutdown()
        server.server_close()
        worker.join(3)
        (out / 'closed.json').write_text(json.dumps(dict(closed=True,
            probes_closed=probes_closed, stimulus_closed=stimulus_closed, worker_closed=not worker.is_alive(), pc_unix=time.time())), encoding='utf-8')


if __name__ == '__main__':
    main()
