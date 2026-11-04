"""Finite Linux manager and desktop protocol for the reviewed runtime V4.

Candidate only: importing this file neither starts a process nor opens a device.
Connection/READY never submits initialization. One durable launch attempt is
owned by Backend V1; an unresolved launch or command is never transmitted again.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from av_runtime_backend_V1 import Backend, identity
from av_runtime_binding_V1 import Launcher, digest, readiness


class UnknownCommand(RuntimeError):
    """The request may have reached the worker. Keep observing the same ID."""


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Finite board time required')
    return value


def manager_admission(backend, stop):
    """No HTTP listener/manager is started alongside the previous manager."""
    h, cfg = backend.host, backend.cfg
    now = number(h.now())
    if h.boot() != cfg['boot_id']:
        raise ValueError('Manager boot changed')
    if not now < number(stop) <= number(cfg['stop_monotonic']) + 30:
        raise ValueError('Manager lifetime exhausted or outside worker bound')
    if stop - now > 25230:
        raise ValueError('Manager lifetime too long')
    previous = backend._predecessor('manager')
    completion = previous.get('completion') or {}
    if (previous.get('process_observed') is not True or
            previous.get('actual_start_ticks') is not None or
            identity(completion) != identity(previous) or
            completion.get('exit_confirmed') is not True or
            completion.get('cleanup_verified') is not True):
        raise ValueError('Previous manager has not exited and released its HTTP service')
    return dict(boot_id=cfg['boot_id'], observed_monotonic=now,
                previous_manager=previous, automatic_initialize=False)


def message(status):
    known = {
        'READY': '板端连接成功，整圈定位服务已就绪；点击开始初始化。',
        'STARTING': '板端正在启动定位服务；启动过程独立于11秒初始化计时。',
        'STARTUP_TIMEOUT_OBSERVING': '定位服务启动超过60秒；继续核对原进程，不重复启动。',
        'WAITING_FOR_MANAGER_NATURAL_EXIT': '等待上一管理程序正常结束；不会中断现有程序。',
        'WAITING_FOR_WORKER_NATURAL_EXIT': '等待上一定位程序正常结束；不会中断现有任务。',
        'ENDED': '本次定位服务已正常结束；本轮不会自动重复启动。',
        'MANAGER_ENDED': '本次管理程序已结束；保留正在执行的板端任务。',
        'OPERATION_CUTOFF': '已到本轮新动作截止时间；保留当前任务直至结束。',
    }
    return known.get(status, '定位服务尚未就绪；原因：' + str(status))


class ManagedSession:
    """Launcher ticks may start a service. HTTP reads/connection never do so."""
    def __init__(self, backend, stop, observer_factory):
        self.backend, self.stop = backend, number(stop)
        self.clock, self.factory = backend.host.now, observer_factory
        self.launcher = Launcher(backend, self.clock)
        self.lock = threading.RLock()
        self.closed = False
        self.observer = None
        self.current_identity = None
        self.persistence_error = None
        self.last = dict(status='CHECKING', ready=False, automatic_initialize=False)

    def _view(self):
        b, now = self.backend, self.clock()
        if self.closed or now >= self.stop:
            return None, None, dict(status='MANAGER_ENDED', ready=False, automatic_initialize=False)
        if b.host.boot() != b.cfg['boot_id']:
            return None, None, dict(status='BOOT_CHANGED', ready=False, automatic_initialize=False)
        record = b.intent()
        if record is None:
            return None, None, copy.deepcopy(self.last)
        if (record.get('status') != 'SPAWNED' or
                record.get('plan_sha256') != b.plan['plan_sha256'] or
                record.get('config_sha256') != b.plan['config_sha256']):
            return record, None, b.observe(record, now)
        seen = b.observation(record)
        life = readiness(record, seen, self.clock())
        if self.clock() >= b.cfg['no_new_operation_monotonic']:
            life = dict(life, status='OPERATION_CUTOFF', ready=False)
        return record, seen, life

    def tick(self):
        with self.lock:
            if self.closed or self.clock() >= self.stop:
                self.closed = True
                self.last = dict(status='MANAGER_ENDED', ready=False, automatic_initialize=False)
            elif self.backend.host.boot() != self.backend.cfg['boot_id']:
                self.last = dict(status='BOOT_CHANGED', ready=False, automatic_initialize=False)
            else:
                try:
                    # Launcher observes existing receipts, including UNKNOWN, without retry.
                    self.last = self.launcher.start(self.backend.plan)
                    _, _, self.last = self._view()
                except Exception as exc:
                    self.last = dict(status='MANAGER_CHECK_FAILED', ready=False,
                                     automatic_initialize=False, error=repr(exc))
            self.last = self._decorate(self.last)
            try:
                self.backend.write(self.backend.folder/'lifecycle.json', self.last)
                self.persistence_error = None
            except Exception as exc:
                self.persistence_error = repr(exc)
                self.last = self._decorate(dict(status='MANAGER_PERSISTENCE_FAILED', ready=False,
                    automatic_initialize=False, error=repr(exc)))
            return copy.deepcopy(self.last)

    def _decorate(self, life):
        now = self.clock()
        life = dict(life, observed_monotonic=now, message=message(life['status']),
                    startup_budget_seconds=60, initialization_budget_seconds=11,
                    automatic_initialize=False)
        try:
            receipt = self.backend.intent()
        except Exception as exc:
            receipt = None
            life['intent_read_error'] = repr(exc)
        if receipt and type(receipt.get('started_monotonic')) in (int, float):
            life['elapsed_seconds'] = max(0., now-receipt['started_monotonic'])
        return life

    def _observer(self, record):
        who = identity(record)
        if who != self.current_identity:
            self.observer = self.factory(record['run']+'/control', record['pid'], record['boot_id'])
            self.current_identity = who
        return self.observer

    def _unresolved(self, record):
        """A lost POST reply blocks later actions until its actual result is saved.

        A matching public job label or an idle HTTP response is insufficient.
        The original worker control record, bounded times, result SHA, action
        and cleanup must agree. The request is never sent during reconciliation.
        """
        b = self.backend
        paths = sorted(b.folder.glob('command_*.json'))
        if len(paths) > 256:
            raise ValueError('Command history exceeds this finite session')
        state = None
        unresolved = []
        for path in paths:
            item = b.read(path)
            if item.get('status') in ('CONFIRMED', 'RESOLVED_RESULT'):
                continue
            rid = item.get('body', {}).get('request_id')
            matched = None
            if record is not None and item.get('identity') == identity(record):
                if state is None:
                    state = b.read(b.host.path(record['run']+'/control/state.json'), optional=True) or {}
                if (state.get('boot_id') == record['boot_id'] and state.get('pid') == record['pid']
                        and isinstance(state.get('jobs'), list)):
                    found = [j for j in state['jobs'] if isinstance(j, dict) and j.get('id') == rid]
                    if len(found) == 1:
                        job = found[0]
                        if (rid not in (state.get('active'), state.get('pending')) and
                                job.get('action') == item['action'] and
                                type(job.get('trigger_monotonic')) in (int, float) and
                                job['trigger_monotonic'] >= item['requested_monotonic'] and
                                not b._unconfirmed_jobs({'jobs':[job]}, record['run']+'/control')):
                            matched = job
            if matched is None:
                unresolved.append(rid)
            else:
                b.write(path, dict(item, status='RESOLVED_RESULT', observed_monotonic=self.clock(),
                    result_reference=matched['result_reference'], response=dict(id=rid,
                        status=matched['status'], outcome_observed_from_saved_result=True)))
        return unresolved

    def _state(self):
        record, seen, life = self._view()
        if life.get('ready'):
            state = self._observer(record).state()
            # Do not trust the generic legacy observer's "not closed" readiness.
            if (state.get('boot_id') != record['boot_id'] or state.get('pid') != record['pid'] or
                    state.get('closed') is not False or
                    state.get('full_turn_initialization_supported') is not True):
                raise ValueError('Observer identity or full-turn capability mismatch')
            try:
                preview = self.observer.completed_preview()
                state['preview'] = {k:v for k,v in preview.items() if k!='path'} if preview else None
            except Exception as exc:
                state.update(preview=None, preview_error=repr(exc))
        else:
            state = dict(pid=None, boot_id=self.backend.cfg['boot_id'], decision_owner='PYNQ',
                active=None, active_action=None, pending=None, can_request=False, sources=[],
                max_sources=3, calibration='PENDING', jobs=[], latched_block=None,
                closed=self.closed, full_turn_initialization_supported=False, preview=None)
        life = self._decorate(life)
        delivery_hold = self._unresolved(record)
        if delivery_hold:
            life.update(status='WAITING_FOR_COMMAND_RESULT', ready=False,
                        message='上一请求的接收或结束结果待核对；保留原请求，不再发送新动作。')
        if self.persistence_error:
            life.update(status='MANAGER_PERSISTENCE_FAILED', ready=False,
                        message='管理状态保存失败，等待核对；不会重复启动。', error=self.persistence_error)
        state.update(board_connected=True, observer_monotonic=self.clock(), observer_pid=os.getpid(),
            runtime_ready=life['ready'], runtime_status=life['status'], service_lifecycle=life,
            status_message=life['message'], initialization_automatic=False, unresolved_requests=delivery_hold)
        state['can_request'] = state.get('can_request') is True and life['ready']
        return state

    def state(self):
        with self.lock:
            try:
                return self._state()
            except Exception as exc:
                life = self._decorate(dict(status='READINESS_UNCONFIRMED', ready=False,
                                           automatic_initialize=False, error=repr(exc)))
                return dict(board_connected=True, boot_id=self.backend.cfg['boot_id'],
                    runtime_ready=False, can_request=False, runtime_status=life['status'],
                    full_turn_initialization_supported=False, service_lifecycle=life,
                    status_message=life['message'], jobs=[], sources=[], active=None,
                    pending=None, active_action=None, calibration='PENDING', closed=self.closed,
                    observer_monotonic=self.clock(), preview=None)

    def command(self, action, body):
        expected = {'request_id', 'source_id'} if action == 'select' else {'request_id'}
        if action not in ('initialize', 'select') or not isinstance(body, dict) or set(body) != expected:
            raise ValueError('Only explicit initialize/select with exact fields is supported')
        rid = body['request_id']
        if (not isinstance(rid, str) or not 1 <= len(rid) <= 80 or not rid.isascii() or
                any(c not in '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-' for c in rid)):
            raise ValueError('ASCII request ID required')
        if action == 'select' and (not isinstance(body['source_id'], str) or not 1 <= len(body['source_id']) <= 80):
            raise ValueError('Source ID required; PC angles are not accepted')
        with self.lock, self.backend.launch_guard():
            path = self.backend.folder/('command_'+rid+'.json')
            previous = self.backend.read(path, optional=True)
            content = dict(action=action, body=body)
            sha = digest(content)
            if previous is not None:
                if previous.get('content_sha256') != sha:
                    raise ValueError('Request ID already bound to different content')
                if previous.get('status') in ('CONFIRMED', 'RESOLVED_RESULT'):
                    return copy.deepcopy(previous['response'])  # Read receipt, no second POST.
                raise UnknownCommand('Request outcome unknown; observe request_id='+rid)
            if len(list(self.backend.folder.glob('command_*.json'))) >= 256:
                raise ValueError('Finite command history full; no new request accepted')
            record, _, life = self._view()
            if not life.get('ready'):
                raise ValueError(message(life['status']))
            state = self._state()
            if state.get('can_request') is not True:
                raise ValueError('Current worker operation has not finished')
            intent = dict(content, content_sha256=sha, identity=identity(record), status='INTENT_DURABLE',
                          requested_monotonic=self.clock(), automatic_initialize=False)
            self.backend.write(path, intent, exclusive=True)
            try:
                response = self._observer(record).command(action, body)
                if not isinstance(response, dict) or response.get('id') != rid:
                    raise ValueError('Worker acknowledgement does not identify this request')
                self.backend.write(path, dict(intent, status='CONFIRMED', response=response,
                                              acknowledged_monotonic=self.clock()))
                return response
            except Exception as exc:
                # A file-write failure after POST is ambiguous too. Never resend it.
                try:
                    self.backend.write(path, dict(intent, status='OUTCOME_UNKNOWN', error=repr(exc)))
                except Exception:
                    pass  # Original durable intent still prohibits retransmission.
                raise UnknownCommand('Worker request result unknown; observe request_id='+rid) from exc

    def image(self, sid, sequence):
        with self.lock:
            record, _, life = self._view()
            if not life.get('ready'):
                raise ValueError('No current ready worker image')
            return self._observer(record).image(sid, sequence)

    def close(self):
        with self.lock:
            self.closed = True
            return self.tick()  # No finish request, signal, terminate or hardware access.


def command_response(session, action, body):
    try:
        return 202, session.command(action, body)
    except UnknownCommand as exc:
        return 503, dict(error=str(exc), outcome='UNKNOWN_DO_NOT_RESEND', request_repeated=False)
    except Exception as exc:
        return 409, dict(error=str(exc), request_repeated=False)


def handler(session, page, allowed=('127.0.0.1', '192.168.2.1')):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def send(self, status, payload, mime='application/json'):
            if mime == 'application/json':
                payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            try:
                p = urlparse(self.path)
                if p.path == '/': return self.send(200, page, 'text/html; charset=utf-8')
                if p.path == '/api/state': return self.send(200, session.state())
                if p.path == '/api/image':
                    args = parse_qs(p.query, strict_parsing=True)
                    if set(args) != {'source', 'sequence'} or any(len(v)!=1 for v in args.values()):
                        raise ValueError('Exact source and sequence required')
                    return self.send(200, session.image(args['source'][0], args['sequence'][0]), 'image/jpeg')
                self.send(404, dict(error='Unknown read endpoint'))
            except Exception as exc:
                self.send(409, dict(error=str(exc)))

        def do_POST(self):
            try:
                p = urlparse(self.path)
                actions = {'/api/initialize': 'initialize', '/api/select': 'select'}
                if p.path not in actions or p.query: raise ValueError('Explicit action endpoint required')
                if self.client_address[0] not in allowed: raise ValueError('Unreviewed client')
                if self.headers.get('X-AV-Request') != '1': raise ValueError('Request header required')
                if self.headers.get_content_type() != 'application/json': raise ValueError('JSON required')
                origin = self.headers.get('Origin')
                if origin and urlparse(origin).netloc != self.headers.get('Host'):
                    raise ValueError('Origin mismatch')
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 1024: raise ValueError('Bounded request body required')
                body = json.loads(self.rfile.read(length))
                code, result = command_response(session, actions[p.path], body)
                self.send(code, result)
            except Exception as exc:
                self.send(409, dict(error=str(exc), request_repeated=False))
    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    args = p.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if set(config) != {'plan', 'predecessors', 'backend_folder', 'manager_stop_monotonic',
                       'manager_sources', 'page_sha256'}:
        raise ValueError('Exact reviewed manager configuration required')
    backend = Backend(config['plan'], config['backend_folder'], config['predecessors'])
    source = Path(__file__).resolve().parent
    if 'av_managed_observer_V2.py' not in config['manager_sources']:
        raise ValueError('Manager source identity missing')
    for name, sha in config['manager_sources'].items():
        if Path(name).name != name or hashlib.sha256((source/name).read_bytes()).hexdigest() != sha:
            raise ValueError('Manager source SHA mismatch: '+name)
    page = (source/'av_observer_ui_V7.html').read_bytes()
    if hashlib.sha256(page).hexdigest() != config['page_sha256']:
        raise ValueError('Page SHA mismatch')
    from av_observer_result_V1 import Observer
    manager_lock = backend.host.path(backend.cfg['session_root']+'/runtime_manager.lock')
    with backend.host.lock(manager_lock):
        admission = manager_admission(backend, config['manager_stop_monotonic'])
        session = ManagedSession(backend, config['manager_stop_monotonic'], Observer)
        server = ThreadingHTTPServer(('0.0.0.0', 8764), handler(session, page))
        server.daemon_threads = False
        who = dict(boot_id=backend.host.boot(), pid=os.getpid(),
                   start_ticks=backend.host.process(os.getpid())['actual_start_ticks'])
        owner = dict(who, admission=admission, port=8764, hardware_opened=False,
                     started_monotonic=backend.host.now(), stop_monotonic=session.stop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            backend.write(backend.folder/'manager_owner.json', owner, exclusive=True)
            thread.start()
            while backend.host.now() < session.stop:
                session.tick()
                time.sleep(min(.8, max(0., session.stop-backend.host.now())))
        finally:
            session.close()
            if thread.is_alive(): server.shutdown()
            server.server_close()
            if thread.ident is not None: thread.join(3)
            backend.write(backend.folder/'closed.json', dict(owner, http_closed=not thread.is_alive(),
                runtime_interrupted=False, hardware_opened=False, ended_monotonic=backend.host.now()))


if __name__ == '__main__':
    main()
