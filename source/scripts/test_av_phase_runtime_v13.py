"""Run the complete V12 service lifecycle on D: with explicit synthetic devices.

Actual V12 Coordinator, Runner V4 and request handlers execute. A fake listener
never binds a socket; dependencies never import PYNQ/cv2/numpy or touch Linux.
Saved full-turn evidence is synthetic and is not a real initialization pass.
"""
import contextlib
import copy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import types
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'software/pynq'), str(ROOT/'scripts')]
from av_phase_runtime_V13 import run, Host as RuntimeHost
from av_runtime_contract_V10 import CONTRACT, REQUIRED_MODULES
def readiness(receipt, observed, now):
    ready=observed['ready_record']; brief=observed['brief']
    return dict(ready=(ready['runtime_contract']==CONTRACT and brief['runtime_contract']==CONTRACT
        and ready['pid']==observed['pid']==receipt['pid']
        and ready['start_ticks']==observed['actual_start_ticks']==receipt['start_ticks']
        and ready['config_sha256']==receipt['config_sha256']))
from test_reference_repair_V1 import fixture as full_turn_fixture


def request(handler, path, body=None, client='127.0.0.1', headers=None):
    obj = object.__new__(handler)
    obj.client_address, obj.path = (client, 10000), path
    raw = json.dumps(body).encode() if body is not None else b''
    obj.headers = {'Content-Type':'application/json', 'X-AV-Request':'1', 'Host':'127.0.0.1:8765',
                   'Content-Length':str(len(raw))}
    obj.headers.update(headers or {})
    obj.rfile = io.BytesIO(raw)
    result = []
    obj.answer = lambda code, value: result.append((code, copy.deepcopy(value)))
    (handler.do_GET if body is None else handler.do_POST)(obj)
    return result[-1]


class Listener:
    def __init__(self, handler, host):
        self.handler, self.host, self.stop = handler, host, threading.Event()
    def serve_forever(self): self.stop.wait(3)
    def shutdown(self): self.host.events.append('listener_shutdown'); self.stop.set()
    def server_close(self): self.host.events.append('listener_closed')


class Host(RuntimeHost):
    def __init__(self, base, scenario, checks):
        self.base, self.scenario, self.check = base, scenario, checks
        self.t, self.round, self.calls, self.quiet_count = 99.9, 0, 0, 0
        self.events, self.http = [], []
        self.procs = {40:400, 30:300, 20:None}
        self.fixture_code = base/'code'; self.fixture_code.mkdir()
        for name in REQUIRED_MODULES:
            (self.fixture_code/name).write_bytes((ROOT/'software/pynq'/name).read_bytes())
        (self.fixture_code/'known_source.npy').write_bytes(b'SYNTHETIC_REFERENCE_NOT_A_NUMPY_ARRAY')
        self.stop_at = 130.
    def boot(self): return 'TEST_ONLY'
    def now(self): return self.t
    def pid(self): return __import__('os').getpid()
    def ticks(self, pid): return 400 if pid == self.pid() else self.procs.get(pid)
    def source(self): return self.fixture_code
    @contextlib.contextmanager
    def lock(self, path):
        self.events.append('flock_acquired')
        if self.scenario == 'occupied_lock': raise BlockingIOError('SYNTHETIC_OCCUPIED_FLOCK')
        try: yield
        finally: self.events.append('flock_released')
    def signals(self, stop): self.events.append('signal_handlers_double')
    def server(self, handler):
        self.listener = Listener(handler, self)
        return self.listener
    def dependencies(self, bundle):
        self.events.append('dependencies_double')
        host = self
        class Check:
            def __init__(self, *args): pass
            def quiet(self):
                host.quiet_count += 1
                host.events.append('quiet_'+str(host.quiet_count))
                clean = not (host.scenario == 'failed_postflight' and host.t >= host.stop_at)
                return dict(cleanup_verified=clean, audio_control=0, dma_halted=True,
                            buffers_released=True, camera_released=True, uart_closed=True, torque=0)
        class Engine:
            selected = selected_verdict = None
            def __init__(self, *args, **kwargs): pass
            def clock(self): return host.now()
            def initialize(self, folder, trigger):
                host.calls += 1
                host.events.append('operation_begin')
                camera = Path(folder)/'camera'; camera.mkdir(parents=True)
                raw = full_turn_fixture()
                for n, row in enumerate(raw['visual']['measured_axis']['measurements'], 1):
                    payload = ('SYNTHETIC_STATIC_VIEW_%d'%n).encode()
                    (camera/row['frame']['file']).write_bytes(payload)
                    row['frame']['sha256'] = hashlib.sha256(payload).hexdigest()
                third = raw['visual']['measured_axis']['measurements'][-1]['frame']
                raw['confirmation_frame'] = copy.deepcopy(third)
                raw['visual']['confirmation_frame'] = copy.deepcopy(third)
                raw["stationary_audio"]["confirmed_view_sha256"]=third["sha256"]
                raw.update(triggered=trigger, elapsed_seconds=8.5,
                           source_anchor=dict(audio_bearing_deg=42.,servo_view_position=1500,reference_sha256="b"*64))
                if host.scenario == 'incomplete_full_turn': raw['full_sweep_this_trigger'] = False
                elif host.scenario == 'stale_stationary_view':
                    raw['visual']['measured_axis']['measurements'][0]['frame']['read_begin'] = 99.
                elif host.scenario == 'corrupt_confirmation':
                    (camera/third['file']).write_bytes(b'CHANGED_SYNTHETIC_BYTES')
                host.t = 129.01 if host.scenario == 'long_operation_no_total_limit' else 119.5
                host.events.append('operation_returned')
                return raw
        return dict(check=Check, engine=Engine, visual=lambda *a, **k:None, capture=lambda *a:None,
                    gate=lambda *a:None, source=lambda *a:None,
                    buttons=lambda: types.SimpleNamespace(read=lambda address:15 if address==4 else 0))
    def event(self):
        host = self
        class Stop:
            done = False
            def is_set(self): return self.done
            def set(self): self.done = True
            def wait(self, duration):
                if host.round: host.t = host.stop_at; return
                host.round += 1
                h = host.listener.handler
                ready = json.loads((host.base/'run/ready.json').read_text())
                code, brief = request(h, '/brief')
                host.check(code==200 and brief['jobs']==[] and host.calls==0,
                           host.scenario+'_ready_and_reads_do_not_initialize')
                receipt = dict(boot_id=host.boot(),pid=host.pid(),start_ticks=400,started_monotonic=99.9,
                               config_sha256=ready['config_sha256'])
                observed = dict(clock='BOARD_MONOTONIC',boot_id=host.boot(),pid=host.pid(),start_ticks=400,
                    process_observed=True,actual_start_ticks=400,observed_monotonic=host.t,
                    ready_record=ready,brief=brief)
                host.check(readiness(receipt,observed,host.t)['ready'],host.scenario+'_actual_ready_and_brief_exact_v7_contract')
                if host.scenario == 'no_initialize': host.t=host.stop_at; return
                host.t = 100.
                code, job = request(h, '/trigger', dict(request_id='explicit_'+host.scenario))
                host.check(code==202,host.scenario+'_explicit_http_trigger_accepted')
                # Only fixture-owned operation threads exist in this serial test.
                for thread in threading.enumerate():
                    if thread.name=='av_board_owner': thread.join(3)
                code, public = request(h, '/status')
                host.http.append(public)
                host.check(public['active'] is None and public['pending'] is None,
                           host.scenario+'_operation_returns_and_no_pending_owner')
                host.check(host.calls==1,host.scenario+'_exactly_one_operation')
                before = host.calls
                request(h, '/trigger', dict(request_id='explicit_'+host.scenario))
                host.check(host.calls==before,host.scenario+'_same_id_reads_existing_result_without_replay')
                accepted = host.scenario in ('explicit_success','explicit_idle_finish','failed_postflight','long_operation_no_total_limit')
                host.check((public['calibration']=='RELATIVE_SINGLE_SOURCE')==accepted,
                           host.scenario+'_mapping_requires_new_full_turn_evidence')
                if host.scenario=='explicit_idle_finish':
                    code,_ = request(h,'/service/finish',dict(boot_id=host.boot(),runtime_start_ticks=399))
                    host.check(code==400 and not self.done,'wrong_finish_identity_keeps_service')
                    code,_ = request(h,'/service/finish',dict(boot_id=host.boot(),runtime_start_ticks=400))
                    host.check(code==202 and self.done,'only_explicit_correct_idle_finish_ends_service')
                host.t = host.stop_at
        return Stop()


def main():
    out=ROOT/'logs/av_phase_runtime_V13'/('offline_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True)
    checks, cases=[],[]
    def check(value,name):
        checks.append(dict(name=name,passed=bool(value)))
        if not value: raise AssertionError(name)
    sources=['software/pynq/'+n for n in REQUIRED_MODULES]+[
        'software/pynq/av_runtime_binding_V2.py','software/pynq/av_runtime_backend_V2.py',
        'scripts/test_av_phase_runtime_V13.py','scripts/test_av_init_proof_V1.py']
    def save(status,error=None):
        value=dict(status=status,checks=len(checks),assertions=checks,cases=cases,error=error,
            scope='PC_COMPLETE_SERVICE_WITH_SYNTHETIC_DEVICES_AND_IN_MEMORY_HTTP',
            actual_sockets=0,actual_pynq_imports=0,actual_hardware_actions=0,deployed=False,
            physical_initialization_pass=False,
            source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sources})
        (out/'result.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
        return value
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('No subprocess allowed')))
            stack.enter_context(patch.object(urllib.request,'urlopen',side_effect=AssertionError('No network allowed')))
            for scenario in ('no_initialize','explicit_success','long_operation_no_total_limit','explicit_idle_finish','wrong_contract','failed_postflight'):
                base=out/scenario; base.mkdir()
                host=Host(base,scenario,check)
                parent=base/'session'; parent.mkdir()
                observer=base/'observer'; observer.mkdir()
                config=dict(folder=str(base/'run'),session_root=str(parent),hardware_root=str(base/'hardware'),
                    hardware_files={'unused.bit':'0'*64},boot_id=host.boot(),stop_monotonic=host.stop_at,
                    no_new_operation_monotonic=108.5,installation_id='SYNTHETIC_ONLY',
                    observer=dict(remote=str(observer),boot_id=host.boot(),pid=30,start_ticks=300),
                    modules={n:hashlib.sha256((host.source()/n).read_bytes()).hexdigest() for n in REQUIRED_MODULES},
                    assets={'known_source.npy':hashlib.sha256((host.source()/'known_source.npy').read_bytes()).hexdigest()},
                    known_wav_sha256='0'*64,runtime_contract=copy.deepcopy(CONTRACT))
                if scenario=='observer_changed': host.procs[30]=301
                if scenario=='live_binding':
                    (observer/'runtime_binding.json').write_text(json.dumps(dict(boot_id=host.boot(),pid=40,start_ticks=400)))
                if scenario=='source_changed': (host.source()/REQUIRED_MODULES[0]).write_bytes(b'CHANGED_SOURCE')
                if scenario=='wrong_contract': config['runtime_contract']['visual_io_version']=9
                if scenario=='boot_changed': config['boot_id']='OLD_BOOT'
                if scenario=='asset_changed': (host.source()/'known_source.npy').write_bytes(b'CHANGED_ASSET')
                # Existing Runner uses board monotonic time, replaced only inside this PC test.
                with patch('av_phase_service_runner_V9.time.monotonic',side_effect=host.now):
                    try: result=run(config,host)
                    except (ValueError,RuntimeError,BlockingIOError) as exc:
                        check(scenario in ('occupied_lock','source_changed','wrong_contract','boot_changed'),
                              scenario+'_only_expected_preflight_exception')
                        check(host.calls==0 and 'dependencies_double' not in host.events,
                              scenario+'_rejected_before_device_dependencies')
                        result=dict(status='EXPECTED_PREFLIGHT_REJECTION',error=repr(exc))
                    else:
                        check(scenario not in ('occupied_lock','source_changed','wrong_contract','boot_changed'),
                              scenario+'_preflight_not_silently_accepted')
                        check(host.events[-1]=='flock_released',scenario+'_lock_released_last')
                        check((base/'run/summary.json').is_file(),scenario+'_final_summary_persisted')
                        check('listener_closed' not in host.events or
                              host.events.index('listener_closed') < len(host.events)-2,
                              scenario+'_listener_and_postflight_before_unlock')
                        if scenario in ('observer_changed','live_binding','asset_changed'):
                            check(result['status']=='FAILED' and not (base/'run/ready.json').exists() and host.calls==0,
                                  scenario+'_startup_failure_never_publishes_ready')
                        elif scenario=='failed_postflight':
                            check(not result['cleanup_verified'] and result['postflight']['cleanup_verified'] is False,
                                  'failed_postflight_preserved_not_called_success')
                        else:
                            check(result['cleanup_verified'] and result['http_closed'],scenario+'_bounded_cleanup_confirmed_by_doubles')
                            check(result['status']==('REQUESTED_IDLE_FINISH' if scenario=='explicit_idle_finish' else 'NATURAL_LIFETIME_END'),
                                  scenario+'_ordinary_lifecycle_result')
                if scenario=='no_initialize': check(host.calls==0,'all_ready_polls_without_explicit_command_remain_idle')
                cases.append(dict(scenario=scenario,scope='SYNTHETIC_TEST_ONLY',result=result,
                                  events=host.events,engine_calls=host.calls,http_states=host.http))
        save('PASS_PC_COMPLETE_RUNTIME_V13_LIFECYCLE')
    except BaseException as exc:
        save('FAILED',repr(exc))
        raise
    print(json.dumps(dict(status='PASS_PC_COMPLETE_RUNTIME_V13_LIFECYCLE',checks=len(checks),cases=len(cases),
                          evidence=str(out.relative_to(ROOT)).replace('\\','/'))))


if __name__=='__main__': main()
