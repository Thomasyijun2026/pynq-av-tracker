"""Board observer survives finite runtime sessions; never opens hardware.

A deployment must explicitly publish a boot/PID/start-ticks binding. Missing or
dead runtime is reported as preparing, never as an initialized localizer.
"""
import argparse, hashlib, json, os, signal, threading, time
from pathlib import Path
from http.server import ThreadingHTTPServer
from av_observer_ui_V6 import Observer
from av_observer_ui_V3 import handler


def process_ticks(pid):
    try:
        return int(Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


class Bridge:
    def __init__(self, folder, boot, session_root, factory=Observer,
                 ticks=process_ticks, clock=time.monotonic):
        self.folder, self.boot = Path(folder).resolve(), boot
        self.root = Path(session_root).resolve()
        self.factory, self.ticks, self.clock = factory, ticks, clock
        self.current = None
        self.lock = threading.RLock()

    def runtime(self):
        path = self.folder/'runtime_binding.json'
        if not path.exists():
            return None
        b = json.loads(path.read_text())
        control = Path(b['control']).resolve()
        if b['boot_id'] != self.boot or self.root not in control.parents:
            raise ValueError('Runtime binding boot or control mismatch')
        if type(b['pid']) is not int or b['pid'] <= 0 or type(b['start_ticks']) is not int:
            raise ValueError('Invalid runtime identity')
        if self.ticks(b['pid']) != b['start_ticks']:
            return None
        with self.lock:
            if self.current and self.current[0] != b:
                previous = self.current[0]
                if self.ticks(previous['pid']) == previous['start_ticks']:
                    raise ValueError('Previous runtime still running; binding switch refused')
            if self.current is None or self.current[0] != b:
                self.current = b, self.factory(str(control), b['pid'], self.boot)
            return self.current[1]

    def preview(self):
        p = self.folder/'preview.json'
        if not p.exists():
            return None
        r = json.loads(p.read_text())
        if r['boot_id'] != self.boot or not r['camera_released']:
            raise ValueError('Preview source not completed in this boot')
        age = self.clock() - r['read_end']
        if age < 0:
            raise ValueError('Preview is from the future')
        return dict(image_url='/api/image?source=board_preview&sequence='+str(r['sequence']),
                    age_seconds=age, live=False, sha256=r['sha256'], boot_id=self.boot)

    def state(self):
        reason = None
        try:
            runtime = self.runtime()
            state = runtime.state() if runtime is not None else None
        except Exception as exc:
            state, reason = None, str(exc)
        if state is None:
            state = dict(pid=None, boot_id=self.boot, decision_owner='PYNQ', active=None,
                         active_action=None, pending=None, can_request=False, sources=[],
                         max_sources=3, calibration='PENDING_AFTER_REBOOT', input_hold=None,
                         discovery_hold=None, jobs=[], latched_block=None, closed=False,
                         runtime_ready=False, runtime_status='PREPARING',
                         status_message='板端已连接，定位程序正在准备；当前未开始定位。',
                         runtime_error=reason, physical_multi_source_validation=False,
                         simultaneous_source_separation=False)
        else:
            state.update(runtime_ready=not state['closed'], runtime_status='RUNNING' if not state['closed'] else 'ENDED')
        state.update(observer_pid=os.getpid(), observer_monotonic=self.clock(), board_connected=True)
        try:
            state['preview'] = self.preview()
        except Exception as exc:
            state.update(preview=None, preview_error=str(exc))
        return state

    def command(self, action, body):
        runtime = self.runtime()
        if runtime is None:
            raise ValueError('定位程序正在准备，尚未执行动作。')
        return runtime.command(action, body)

    def image(self, sid, sequence):
        if sid == 'board_preview':
            r = json.loads((self.folder/'preview.json').read_text())
            self.preview()
            if str(r['sequence']) != sequence or Path(r['file']).name != r['file']:
                raise ValueError('Preview identity changed')
            data = (self.folder/r['file']).read_bytes()
            if hashlib.sha256(data).hexdigest() != r['sha256']:
                raise ValueError('Preview SHA mismatch')
            return data
        runtime = self.runtime()
        if runtime is None:
            raise ValueError('No active runtime image')
        return runtime.image(sid, sequence)


def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--boot',required=True)
    p.add_argument('--session-root',required=True);p.add_argument('--duration',type=float,required=True)
    args=p.parse_args()
    if not 1 <= args.duration <= 43200: raise ValueError('Bounded lifetime required')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=args.boot: raise ValueError('Boot changed')
    folder=Path(args.folder); stop=threading.Event()
    bridge=Bridge(folder,args.boot,args.session_root)
    server=ThreadingHTTPServer(('0.0.0.0',8770),handler(bridge,(folder/'av_observer_ui_V7.html').read_bytes()))
    server.daemon_threads=True
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:stop.set())
    owner=dict(pid=os.getpid(),start_ticks=process_ticks(os.getpid()),boot_id=args.boot,port=8770,
               started_monotonic=time.monotonic(),duration=args.duration,hardware_opened=False)
    (folder/'bridge_owner.json').write_text(json.dumps(owner))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try: stop.wait(args.duration)
    finally:
        server.shutdown();server.server_close();worker.join(3)
        (folder/'bridge_closed.json').write_text(json.dumps(dict(owner,closed=True,worker_closed=not worker.is_alive(),hardware_opened=False)))


if __name__=='__main__': main()
