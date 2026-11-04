"""Linux adapter for the reviewed V4 launch contract.

Importing this file performs no I/O. Constructing an adapter does not launch a
process. The caller must explicitly invoke Launcher.start; even READY never
sends initialization. Existing managers and workers are never interrupted.
"""
import contextlib
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import urllib.request
import uuid

from av_runtime_binding_V1 import digest, readiness, explicit_finish, _inside


def identity(value):
    return {k: value.get(k) for k in ('boot_id', 'pid', 'start_ticks')}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_proc_stat(text):
    # comm can contain spaces and parentheses. starttime is field 22, after comm.
    parts = text.rsplit(')', 1)[1].split()
    return dict(process_observed=True, actual_start_ticks=int(parts[19]), state=parts[0])


class LinuxHost:
    """Real proc, filesystem, flock and localhost GET operations; injectable in tests."""
    def __init__(self, root):
        if sys.platform != 'linux':
            raise RuntimeError('Real backend requires Linux; PC tests must inject a host')
        self.root = Path(_inside(root))
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('Session root must be a real canonical directory')

    def path(self, value):
        value = _inside(value)
        p = Path(value)
        if p != self.root and self.root not in p.parents:
            raise ValueError('Path outside session')
        for q in (p, *p.parents):
            if q == self.root:
                break
            if q.is_symlink():
                raise ValueError('Symlink in session path')
        return p

    def boot(self):
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def now(self):
        return time.monotonic()

    def process(self, pid):
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return dict(process_observed=False, actual_start_ticks=None, error='INVALID_PID')
        try:
            # A zombie is still present: do not pretend it has exited/released ownership.
            return parse_proc_stat(Path('/proc/%d/stat' % pid).read_text())
        except FileNotFoundError:
            return dict(process_observed=True, actual_start_ticks=None)
        except (OSError, ValueError, IndexError) as exc:
            return dict(process_observed=False, actual_start_ticks=None, error=repr(exc))

    def sync_directory(self, directory):
        fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def lock(self, path):
        import fcntl
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def holders(self):
        rows = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit() or int(proc.name) == os.getpid():
                continue
            try:
                for fd in (proc / 'fd').iterdir():
                    try:
                        target = os.readlink(str(fd))
                        if target.startswith(('/dev/video', '/dev/ttyUSB', '/dev/ttyACM',
                                              '/dev/mem', '/dev/uio', '/dev/xlnk')):
                            rows.append([int(proc.name), target])
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass
        return sorted(rows)

    def quiet(self, plan):
        """Read-only MMIO audit, called only after predecessor/lock admission.

        No overlay load, audio DMA, camera, UART writes, or source-tree reports.
        A child repeats the standard audit after taking its own canonical lock.
        """
        code = self.path(str(PurePosixPath(plan['entry']).parent))
        cfg = plan['config']
        bundle = self.path(cfg['hardware_root'])
        spec = importlib.util.spec_from_file_location('_runtime_backend_quiet', code / 'av_pmod_check_V4.py')
        module = importlib.util.module_from_spec(spec)
        previous = list(sys.path)
        try:
            sys.path[:0] = [str(code), str(bundle)]
            spec.loader.exec_module(module)
            class ReadOnlyCheck(module.Check):
                def save(self, name, value):
                    pass
            return ReadOnlyCheck(bundle, cfg['hardware_files'], cfg['boot_id']).quiet()
        finally:
            sys.path[:] = previous

    def spawn(self, argv, cwd, log):
        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True)

    def brief(self):
        # GET only; no retry and no /initialize, /select or /service/finish here.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise RuntimeError('Runtime brief redirects are not accepted')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request('http://127.0.0.1:8765/brief', method='GET')
        with opener.open(request, timeout=1.0) as response:
            if response.status != 200:
                raise RuntimeError('Runtime brief HTTP status')
            return json.loads(response.read(1024 * 1024 + 1))


class Backend:
    """One immutable plan, one durable attempt, no automatic retirement/retry.

    `predecessors` identifies the existing manager/worker and their evidence
    directories. Current observer binding must still identify that worker.
    A successor created by the existing manager requires new explicit review;
    it is never silently adopted or displaced by this adapter.
    """
    def __init__(self, plan, folder, predecessors, host=None, executable=None):
        self.plan = copy.deepcopy(plan)
        self.cfg = self.plan['config']
        self.host = host or LinuxHost(self.cfg['session_root'])
        self.folder_name = _inside(folder, self.cfg['session_root'])
        self.folder = self.host.path(self.folder_name)
        # Installation creates this dedicated directory. Construction creates nothing.
        if not self.folder.is_dir():
            raise ValueError('Dedicated backend directory must already exist')
        self.predecessors = copy.deepcopy(predecessors)
        if set(self.predecessors) != {'manager', 'worker'}:
            raise ValueError('Both predecessor identities required')
        for item in self.predecessors.values():
            _inside(item['folder'], self.cfg['session_root'])
            if (item.get('boot_id') != self.cfg['boot_id'] or
                    any(not isinstance(item.get(k), int) or isinstance(item.get(k), bool) or item[k] <= 0
                        for k in ('pid', 'start_ticks'))):
                raise ValueError('Same-boot predecessor PID/start ticks required')
        state_path = PurePosixPath(self.folder_name)
        reserved = [str(PurePosixPath(plan['entry']).parent), self.cfg['hardware_root'],
                    self.cfg['folder'], self.cfg['observer']['remote']]
        reserved.extend(item['folder'] for item in self.predecessors.values())
        if any(state_path == PurePosixPath(p) or state_path in PurePosixPath(p).parents
               or PurePosixPath(p) in state_path.parents for p in reserved):
            raise ValueError('Backend state must be disjoint from sources, runs and observers')
        self.executable = executable or sys.executable
        self.children = {}

    def read(self, path, optional=False):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            if optional:
                return None
            raise

    def write(self, path, value, exclusive=False):
        """File fsync + atomic publication + parent fsync, before any spawn.

        Exclusive publication uses a hard link, so an existing intent/claim is
        never overwritten. A failed persistence step prevents Popen entirely.
        """
        payload = json.dumps(value, sort_keys=True, allow_nan=False, indent=2).encode('utf-8')
        tmp = path.with_name(path.name + '.tmp_' + uuid.uuid4().hex)
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                os.link(str(tmp), str(path))
            else:
                if path.is_symlink():
                    raise ValueError('Refuse symlink publication')
                os.replace(str(tmp), str(path))
            self.host.sync_directory(path.parent)
        finally:
            if tmp.exists():
                tmp.unlink()  # Only our uniquely named unpublished scratch file.

    def launch_guard(self):
        return self.host.lock(self.host.path(self.cfg['session_root'] + '/runtime_binding_launch.lock'))

    def intent(self):
        value = self.read(self.folder / 'intent.json', optional=True)
        if value is None and any((self.folder / name).exists() for name in
                                ('admission_first.json', 'runtime_config.json', 'spawn_claim.json')):
            return dict(status='PERSISTENCE_INCOMPLETE', automatic_initialize=False)
        if value is not None and not isinstance(value, dict):
            raise ValueError('Invalid durable intent')
        return value

    def _predecessor(self, name):
        item = self.predecessors[name]
        record = identity(item)
        record.update(self.host.process(item['pid']))
        report = self.read(self.host.path(item['folder'] + ('/closed.json' if name == 'manager'
                                                           else '/summary.json')), optional=True)
        if report is not None:
            complete = identity(report)
            clean = (report.get('http_closed') is True and report.get('hardware_opened') is False
                     and report.get('runtime_interrupted') is False) if name == 'manager' else report.get('cleanup_verified') is True
            complete.update(exit_confirmed=record.get('process_observed') is True and
                record.get('actual_start_ticks') is None, cleanup_verified=clean,
                normal_end=report.get('status') in ('NATURAL_LIFETIME_END', 'REQUESTED_IDLE_FINISH')
                           if name == 'worker' else clean)
            record['completion'] = complete
        # Volatile /proc state is useful evidence, not part of stable admission identity.
        record.pop('state', None)
        return record

    def _requests(self, worker):
        state = self.read(self.host.path(self.predecessors['worker']['folder'] + '/control/state.json'), optional=True)
        if (not isinstance(state, dict) or state.get('boot_id') != self.cfg['boot_id']
                or state.get('pid') != worker['pid'] or 'active' not in state or 'pending' not in state
                or not isinstance(state.get('jobs'), list)):
            return ['REQUEST_STATE_UNCONFIRMED']
        unknown = []
        if state['active'] is not None or state['pending'] is not None:
            unknown.append('ACTIVE_OR_PENDING_REQUEST')
        unknown.extend(self._unconfirmed_jobs(state, self.predecessors['worker']['folder'] + '/control'))
        binding = self.read(self.host.path(self.cfg['observer']['remote'] + '/runtime_binding.json'), optional=True)
        if (not isinstance(binding, dict) or identity(binding) != identity(worker)
                or binding.get('control') != self.predecessors['worker']['folder'] + '/control'):
            unknown.append('OBSERVER_RUNTIME_BINDING_CHANGED')
        return unknown

    def _unconfirmed_jobs(self, state, control):
        unknown = []
        for job in state['jobs']:
            try:
                if (not isinstance(job, dict) or job.get('boot_id') != self.cfg['boot_id']
                        or not finite(job.get('trigger_monotonic')) or not finite(job.get('finished_monotonic'))
                        or not 0 <= job['trigger_monotonic'] <= job['finished_monotonic'] <= self.host.now()
                        or job.get('cleanup_verified') is not True):
                    raise ValueError('Unconfirmed job lifecycle')
                ref = job['result_reference']
                relative = PurePosixPath(ref['path'])
                if relative.is_absolute() or '..' in relative.parts or relative.name != 'service_result.json':
                    raise ValueError('Result path outside completed operation')
                payload = self.host.path(control + '/' + str(relative)).read_bytes()
                if hashlib.sha256(payload).hexdigest() != ref['sha256']:
                    raise ValueError('Completed result SHA differs')
                result = json.loads(payload)
                if (result.get('boot_id') != self.cfg['boot_id'] or result.get('local_cleanup_verified') is not True
                        or result.get('operation_action') != job.get('action')):
                    raise ValueError('Completed result identity/cleanup differs')
            except (OSError, ValueError, TypeError, KeyError) as exc:
                unknown.append('UNCONFIRMED_JOB_' + str(job.get('id') if isinstance(job, dict) else '?') + ':' + str(exc))
        return unknown

    def verify_payload(self, plan):
        if digest(plan) != digest(self.plan):
            return False
        cfg = plan['config']
        code = str(PurePosixPath(plan['entry']).parent)
        for directory, rows in ((code, cfg['modules']), (code, cfg['assets']),
                                (cfg['hardware_root'], cfg['hardware_files'])):
            for name, sha in rows.items():
                if PurePosixPath(name).name != name or not name or name in ('.', '..'):
                    return False
                p = self.host.path(directory + '/' + name)
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                    return False
        return True

    def facts(self):
        h = self.host
        boot = h.boot()
        records = {name: self._predecessor(name) for name in ('manager', 'worker')}
        observer = identity(self.cfg['observer'])
        observer.update(h.process(observer['pid']))
        observer.pop('state', None)
        requests = self._requests(records['worker'])
        owners = dict(runtime_flock_available=False, task_owner_resolved=False,
                      holders=None, isolated_buffers=None)
        hardware = {}
        ended = all(r.get('process_observed') is True and r.get('actual_start_ticks') is None
                    and identity(r.get('completion', {})) == identity(r)
                    and r['completion'].get('cleanup_verified') is True for r in records.values())
        observer_ok = observer.get('process_observed') is True and observer.get('actual_start_ticks') == observer['start_ticks']
        if boot == self.cfg['boot_id'] and ended and observer_ok and not requests:
            # Never import PYNQ or read MMIO while a known predecessor is alive/unknown.
            # The flock also excludes cooperating runtimes during this quiet audit.
            try:
                with h.lock(h.path(self.plan['required_runtime_flock'])):
                    owners['runtime_flock_available'] = True
                    owners['holders'] = h.holders()
                    if owners['holders'] == [] and self.verify_payload(self.plan):
                        q = h.quiet(self.plan)
                        if q.get('boot_id') == boot and q.get('cleanup_verified') is True:
                            owners.update(task_owner_resolved=True,
                                isolated_buffers=[] if q.get('quarantined_buffers') == 0 else ['UNRELEASED_BUFFER'])
                            hardware = dict(abi=int(q['audio_abi'][1], 16), fir_id=int(q['fir_id'], 16),
                                bundle_manifest_sha256=digest(self.cfg['hardware_files']))
            except BlockingIOError:
                owners['runtime_flock_available'] = False
        result = dict(clock='BOARD_MONOTONIC', boot_id=h.boot(), observed_monotonic=h.now(),
                    manager=records['manager'], worker=records['worker'], observer=observer,
                    ownership=owners, unknown_requests=requests, hardware=hardware,
                    run_folder_absent=not h.path(self.cfg['folder']).exists())
        self.last_facts = copy.deepcopy(result)
        return result

    def persist_intent(self, plan, first):
        if digest(plan) != digest(self.plan):
            raise ValueError('Unexpected plan')
        config_name = self.folder_name + '/runtime_config.json'
        self.write(self.folder / 'admission_first.json', first, exclusive=True)
        self.write(self.host.path(config_name), plan['config'], exclusive=True)
        record = dict(attempt_id=uuid.uuid4().hex, boot_id=self.cfg['boot_id'],
            started_monotonic=self.host.now(), status='INTENT_DURABLE', entry=plan['entry'],
            config=config_name, run=self.cfg['folder'], plan_sha256=plan['plan_sha256'],
            config_sha256=plan['config_sha256'], admitted_facts_sha256=digest(first),
            automatic_initialize=False)
        self.write(self.folder / 'intent.json', record, exclusive=True)
        return record

    def _update(self, record, **changes):
        current = self.intent()
        if current is None or current.get('attempt_id') != record.get('attempt_id'):
            raise RuntimeError('Attempt identity changed')
        current.update(changes)
        self.write(self.folder / 'intent.json', current)
        return current

    def abort_before_spawn(self, record, reason):
        self.write(self.folder / 'admission_second.json', self.last_facts, exclusive=True)
        self._update(record, status='ABORTED_BEFORE_SPAWN', reason=reason)

    def record_unknown_outcome(self, record, error):
        self._update(record, status='SPAWN_OUTCOME_UNKNOWN', error=error)

    def spawn_once(self, record):
        current = self.intent()
        if (current.get('attempt_id') != record['attempt_id'] or current.get('status') != 'INTENT_DURABLE'
                or current.get('plan_sha256') != self.plan['plan_sha256']):
            raise RuntimeError('Attempt is not eligible for a unique spawn')
        if digest(self.read(self.host.path(current['config']))) != current['config_sha256']:
            raise RuntimeError('Durable configuration changed')
        if self.host.boot() != self.cfg['boot_id'] or not self.verify_payload(self.plan):
            raise RuntimeError('Boot/payload changed before spawn')
        now = self.host.now()
        if now >= self.cfg['no_new_operation_monotonic'] or self.cfg['stop_monotonic'] - now < 90:
            raise RuntimeError('Launch lifetime expired before spawn')
        self.write(self.folder / 'admission_second.json', self.last_facts, exclusive=True)
        # Survives a crash after Popen but before receipt update. No path retries a claim.
        self.write(self.folder / 'spawn_claim.json', dict(attempt_id=current['attempt_id'],
            boot_id=self.host.boot(), claimed_monotonic=self.host.now()), exclusive=True)
        current = self._update(current, status='SPAWN_CLAIMED')
        if self.host.boot() != self.cfg['boot_id'] or self.host.now() >= self.cfg['no_new_operation_monotonic']:
            raise RuntimeError('Boot/cutoff changed after durable claim')
        with (self.folder / 'runtime.log').open('ab') as log:
            child = self.host.spawn([self.executable, '-B', current['entry'], '--config', current['config']],
                cwd=str(PurePosixPath(current['entry']).parent), log=log)
        self.children[child.pid] = child
        process = self.host.process(child.pid)
        self._update(current, status='SPAWNED', pid=child.pid,
            start_ticks=process.get('actual_start_ticks') if process.get('process_observed') else None)

    def observation(self, record):
        h = self.host
        seen = dict(clock='BOARD_MONOTONIC', boot_id=h.boot(), pid=record.get('pid'),
            start_ticks=record.get('start_ticks'), unknown_requests=['UNCONFIRMED'])
        child = self.children.get(record.get('pid'))
        if child is not None:
            child.poll()  # Reap only this adapter's child after its natural exit.
        seen.update(h.process(record.get('pid')))
        run = h.path(record['run'])
        seen['ready_record'] = self.read(run / 'ready.json', optional=True) or {}
        if not finite(seen['ready_record'].get('ready_monotonic')):
            seen['ready_record'] = {}
        seen['completion'] = self.read(run / 'summary.json', optional=True) or {}
        if seen.get('actual_start_ticks') == record.get('start_ticks') and record.get('start_ticks') is not None:
            try:
                brief = h.brief()
                seen['brief'] = brief
                state = self.read(run / 'control/state.json', optional=True) or {}
                # Unknown jobs prevent explicit finish even when the HTTP summary is idle.
                if (state.get('boot_id') == record['boot_id'] and state.get('pid') == record['pid']
                        and isinstance(state.get('jobs'), list) and 'active' in state and 'pending' in state):
                    seen['unknown_requests'] = self._unconfirmed_jobs(state, record['run'] + '/control')
                    if state['active'] is not None or state['pending'] is not None:
                        seen['unknown_requests'].append('ACTIVE_OR_PENDING_REQUEST')
            except Exception as exc:
                seen['readiness_error'] = repr(exc)
        seen['observed_monotonic'] = h.now()
        return seen

    def observe(self, record, now):
        if record.get('status') == 'ABORTED_BEFORE_SPAWN':
            return dict(status='START_ABORTED_BEFORE_SPAWN', ready=False, automatic_initialize=False,
                        reason=record.get('reason'))
        if (record.get('status') != 'SPAWNED' or record.get('plan_sha256') != self.plan['plan_sha256']
                or record.get('config_sha256') != self.plan['config_sha256']):
            return dict(status='START_OUTCOME_UNKNOWN', ready=False, automatic_initialize=False)
        seen = self.observation(record)
        return readiness(record, seen, self.host.now())

    def finish_description(self, operator_requested=False):
        """Return a description only. No request is transmitted by the backend."""
        record = self.intent()
        if record is None:
            return dict(status='NO_ATTEMPT', request=None)
        return explicit_finish(record, self.observation(record), self.host.now(), operator_requested)
