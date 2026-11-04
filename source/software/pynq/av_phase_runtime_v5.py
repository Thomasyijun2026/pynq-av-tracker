"""Independent complete runtime: IO V10, Coordinator V12, explicit UI/BTN0.

The default Host is Linux/PYNQ. An injected host permits complete lifecycle
regression with saved synthetic evidence, without sockets or device access.
No code in this module runs a service merely by being imported.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from av_runtime_contract_V2 import ready_identity
from av_trigger_service_V12 import Coordinator, ButtonEdges, make_handler, atomic_json
from av_phase_service_runner_V4 import ServiceEngine, Runner


class Host:
    def now(self): return time.monotonic()
    def boot(self): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    def pid(self): return os.getpid()
    def ticks(self, pid):
        try: return int(Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[19])
        except (OSError, ValueError, IndexError): return None
    def source(self): return Path(__file__).resolve().parent
    def event(self): return threading.Event()
    @contextlib.contextmanager
    def lock(self, path):
        import fcntl
        with path.open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try: yield
            finally: fcntl.flock(stream, fcntl.LOCK_UN)
    def dependencies(self, bundle):
        sys.path.append(str(bundle))
        import cv2
        import numpy as np
        import pynq
        from av_pmod_check_V4 import Check
        from av_phase_io_V10 import Visual
        from av_background_capture_V1 import Capture
        from audio_stimulus_gate_V2 import StimulusGate
        cv2.setNumThreads(1)
        return dict(check=Check, visual=Visual, capture=Capture, gate=StimulusGate,
                    source=lambda path: np.load(str(path), allow_pickle=False),
                    buttons=lambda: pynq.MMIO(0x43c20000, 65536), engine=ServiceEngine)
    def server(self, handler): return ThreadingHTTPServer(('0.0.0.0', 8765), handler)
    def signals(self, stop):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())


def runtime_handler(coordinator, stop, result, host):
    base = make_handler(coordinator)
    class Handler(base):
        def do_POST(self):
            if self.path != '/service/finish': return super().do_POST()
            if self.client_address[0] not in ('127.0.0.1', '192.168.2.1'):
                return self.answer(403, dict(error='CLIENT_NOT_ALLOWED'))
            if self.headers.get('Content-Type') != 'application/json' or self.headers.get('X-AV-Request') != '1':
                return self.answer(400, dict(error='EXPLICIT_JSON_REQUIRED'))
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.answer(403, dict(error='ORIGIN_MISMATCH'))
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 512: raise ValueError('size')
                body = json.loads(self.rfile.read(size))
                if body != dict(boot_id=result['boot_id'], runtime_start_ticks=result['start_ticks']):
                    raise ValueError('identity')
                with coordinator.lock:
                    if coordinator.active or coordinator.pending:
                        return self.answer(409, dict(error='ACTIVE_OPERATION_MUST_FINISH_NATURALLY'))
                    coordinator.closed = True
                    result['requested_idle_finish'] = True
                    stop.set()
                return self.answer(202, dict(status='IDLE_FINISH_ACCEPTED', worker_interrupted=False))
            except (ValueError, TypeError):
                return self.answer(400, dict(error='INVALID_FINISH_IDENTITY'))
        def do_GET(self):
            if self.path not in ('/brief', '/display'): return super().do_GET()
            if self.client_address[0] not in ('127.0.0.1', '192.168.2.1'):
                return self.answer(403, dict(error='CLIENT_NOT_ALLOWED'))
            if self.path == '/brief':
                value = coordinator.brief()
                value.update(runtime_start_ticks=result['start_ticks'], runtime_version=5,
                             runtime_contract=result['runtime_contract'], config_sha256=result['config_sha256'])
            else:
                with coordinator.lock:
                    value = dict(coordinator.display_phase, runtime_pid=host.pid(),
                        runtime_start_ticks=result['start_ticks'], closed=coordinator.closed,
                        active=coordinator.active, boot_id=result['boot_id'], server_monotonic=host.now(),
                        stimulus='BROADBAND_FIRST_AUDIO_ONLY', maximum_play_seconds=35,
                        stop_monotonic=result['stop_monotonic'])
            return self.answer(200, value)
    return Handler


def run(config, host=None):
    h = host or Host()
    identity = ready_identity(config)  # Reject wrong closure/contract before devices or listener.
    boot, now = h.boot(), h.now()
    if boot != config['boot_id']: raise RuntimeError('Boot changed; old mapping is invalid')
    stop_at = config['stop_monotonic']
    cutoff = min(stop_at - 11.5, config['no_new_operation_monotonic'])
    if not 15 <= stop_at-now <= 25200 or not now < cutoff <= stop_at-11.5:
        raise ValueError('Finite same-clock runtime and operation cutoff required')
    folder, parent = Path(config['folder']), Path(config['session_root'])
    code, bundle = h.source(), Path(config['hardware_root'])
    for name, sha in config['modules'].items():
        if Path(name).name != name or hashlib.sha256((code/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Module SHA '+name)
    folder.mkdir(exist_ok=False)
    with h.lock(parent/'standalone_runtime.lock'):
        stop = h.event()
        coordinator = server = thread = check = None
        result = dict(identity, status='PREPARING', boot_id=boot, pid=h.pid(), start_ticks=h.ticks(h.pid()),
            started_monotonic=now, stop_monotonic=stop_at, version=5, decision_owner='PYNQ',
            buzzer_driven=False, external_agent_needed=False, automatic_start=False,
            initialization_mode='FULL_TURN_AUDIOVISUAL')
        atomic_json(folder/'owner.json', result)
        try:
            if not isinstance(result['start_ticks'], int) or result['start_ticks'] <= 0:
                raise RuntimeError('Current Linux process identity unconfirmed')
            deps = h.dependencies(bundle)
            class ServiceCheck(deps['check']):
                def save(self, name, value): atomic_json(folder/('hardware_'+name), value)
            check = ServiceCheck(bundle, config['hardware_files'], boot)
            if not check.quiet()['cleanup_verified']: raise RuntimeError('Hardware not idle')
            for name, sha in config['assets'].items():
                if Path(name).name != name or hashlib.sha256((code/name).read_bytes()).hexdigest() != sha:
                    raise ValueError('Asset SHA '+name)
            gate = deps['gate'](deps['source'](code/'known_source.npy'), config['known_wav_sha256'])
            def publish(value):
                atomic_json(folder/'progress.json', dict(phase=value['phase'], boot_id=boot,
                    published_monotonic=h.now(), triggered=value.get('triggered')))
                if coordinator is not None: coordinator.publish_phase(value)
            engine = deps['engine'](boot, config['installation_id'], None, gate,
                deps['capture'](check, bundle, config['hardware_files']),
                lambda out, deadline: deps['visual'](out, deadline, boot), check.quiet, publish=publish)
            runner = Runner(engine, follow=config.get('automatic_follow', True))
            def admission():
                if stop_at-h.now() < 11.5 or h.now() >= cutoff:
                    return dict(ready=False, reason='RUNTIME_DEADLINE')
                quiet = check.quiet()
                return dict(ready=quiet['cleanup_verified'],
                            reason=None if quiet['cleanup_verified'] else 'HARDWARE_NOT_IDLE')
            coordinator = Coordinator(folder/'control', boot, admission, runner, check.quiet,
                                      budget=11., discovery_interval=1., clock=h.now)
            server = h.server(runtime_handler(coordinator, stop, result, h))
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, name='phase_http', daemon=True)
            thread.start()
            h.signals(stop)
            buttons = deps['buttons']()
            if buttons.read(4)&15 != 15: raise RuntimeError('Buttons are not inputs')
            bridge = config['observer']
            binding = Path(bridge['remote'])/'runtime_binding.json'
            if bridge.get('boot_id') != boot or h.ticks(bridge['pid']) != bridge['start_ticks']:
                raise RuntimeError('Observer changed')
            if binding.exists():
                old = json.loads(binding.read_text())
                if old.get('boot_id') != boot: raise RuntimeError('Existing observer binding boot differs')
                if old.get('start_ticks') is None or h.ticks(old['pid']) == old['start_ticks']:
                    raise RuntimeError('Existing live or unknown runtime binding')
            record = dict(boot_id=boot, pid=h.pid(), start_ticks=result['start_ticks'], control=str(folder/'control'))
            atomic_json(binding, record)
            atomic_json(folder/'binding.json', record)
            result.update(status='READY', ready_monotonic=h.now())
            atomic_json(folder/'ready.json', result)
            edges, presses = ButtonEdges(), []
            while not stop.is_set() and h.now() < stop_at:
                current = h.now()
                for index in edges.update(buttons.read(0)&15, current):
                    presses.append(dict(index=index, monotonic=current))
                    if index == 0: coordinator.trigger('BUTTON0', 'btn0_'+str(len(presses)))
                    elif index == 1: coordinator.request_cancel()
                coordinator.maintain()
                if current < cutoff: coordinator.schedule_discovery()
                stop.wait(.05)
            result['button_events'] = presses
            result['status'] = 'REQUESTED_IDLE_FINISH' if result.get('requested_idle_finish') else 'NATURAL_LIFETIME_END'
        except BaseException as exc:
            result.update(status='FAILED', error=repr(exc))
        finally:
            if coordinator is not None: result['coordinator_close'] = coordinator.close(timeout=None)
            if server is not None:
                server.shutdown()
                server.server_close()
                if thread is not None: thread.join(3)
                result['http_closed'] = thread is None or not thread.is_alive()
            if check is not None:
                try: result['postflight'] = check.quiet()
                except Exception as exc: result['postflight'] = dict(cleanup_verified=False, error=repr(exc))
            result['cleanup_verified'] = bool(result.get('postflight', {}).get('cleanup_verified') and
                result.get('coordinator_close', {}).get('worker_closed', coordinator is None) and
                result.get('http_closed', server is None))
            result['ended_monotonic'] = h.now()
            atomic_json(folder/'summary.json', result)
            # Canonical flock remains held until worker return and final evidence persistence.
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    result = run(json.loads(Path(args.config).read_text(encoding='utf-8')))
    print(json.dumps(result), flush=True)


if __name__ == '__main__': main()
