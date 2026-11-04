"""Pure runtime-V4 launch contract; no network, process or peripheral operations.

The supplied backend implements an exclusive launch guard, durable intent and
read-only Linux process/lock observations. Existing runtime/manager processes
always take precedence. A connection or READY never requests initialization.
"""
import copy
import hashlib
import json
import math
from pathlib import PurePosixPath


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite board-monotonic number required')
    return float(value)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _inside(value, root=None):
    if not isinstance(value, str) or not value.isascii() or '\\' in value or ' ' in value:
        raise ValueError('ASCII absolute Linux path required')
    p = PurePosixPath(value)
    if not p.is_absolute() or '..' in p.parts or (root is not None and PurePosixPath(root) not in p.parents):
        raise ValueError('Path outside isolated session')
    return str(p)


def _hashes(rows):
    if not isinstance(rows, dict) or not rows:
        raise ValueError('Nonempty SHA manifest required')
    for name, sha in rows.items():
        if (not isinstance(name, str) or not name or not name.isascii() or PurePosixPath(name).name != name
                or '/' in name or '\\' in name or name in ('.', '..') or not isinstance(sha, str)
                or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)):
            raise ValueError('Invalid manifest member')
    return copy.deepcopy(rows)


def configuration(requirements, placement, clock):
    """Bind a frozen source closure to a NEW run and current board clock.

    This only creates a configuration value. It does not admit or start a run.
    Runtime V4 still takes its own canonical flock before accessing hardware.
    """
    if clock.get('clock') != 'BOARD_MONOTONIC' or not clock.get('boot_id'):
        raise ValueError('Current board clock required')
    now = _number(clock['observed_monotonic'])
    root = _inside(placement['session_root'])
    code = _inside(placement['code_root'], root)
    folder = _inside(placement['folder'], root)
    hardware = _inside(placement['hardware_root'], root)
    if folder == code or PurePosixPath(code) in PurePosixPath(folder).parents:
        raise ValueError('Run must not be inside frozen code')
    if requirements.get('entry') != 'software/pynq/av_phase_runtime_V4.py' or requirements.get('axis_file_required') is not False:
        raise ValueError('Reviewed first-trigger runtime V4 required')
    modules = _hashes(requirements['modules'])
    assets = _hashes(requirements['assets'])
    files = _hashes(requirements['hardware_files'])
    if 'av_phase_runtime_V4.py' not in modules or set(assets) != {'known_source.npy'}:
        raise ValueError('Missing runtime or unexpected reference assets')
    wav = _hashes({'known.wav': requirements['known_wav_sha256']})['known.wav']
    stop = _number(placement['stop_monotonic'])
    cutoff = _number(placement['no_new_operation_monotonic'])
    if not 90 <= stop - now <= 25200 or not now < cutoff <= stop - 11.5:
        raise ValueError('Finite lifetime/cutoff insufficient')
    observer = copy.deepcopy(placement['observer'])
    observer['remote'] = _inside(observer['remote'], root)
    if (observer.get('boot_id') != clock['boot_id'] or not _integer(observer.get('pid'))
            or not _integer(observer.get('start_ticks'))):
        raise ValueError('Current observer identity required')
    installation = placement['installation_id']
    if not isinstance(installation, str) or not installation or not installation.isascii() or '/' in installation or '\\' in installation:
        raise ValueError('Installation identity required')
    cfg = dict(folder=folder, session_root=root, hardware_root=hardware, hardware_files=files,
               boot_id=clock['boot_id'], stop_monotonic=stop, no_new_operation_monotonic=cutoff,
               installation_id=installation, observer=observer, modules=modules, assets=assets,
               known_wav_sha256=wav)
    plan = dict(entry=code + '/av_phase_runtime_V4.py', config=cfg, config_sha256=digest(cfg),
                automatic_initialize=False, required_runtime_flock=root + '/standalone_runtime.lock')
    plan['plan_sha256'] = digest(plan)
    return plan


def _identity(record):
    return {k: record.get(k) for k in ('boot_id', 'pid', 'start_ticks')}


def admission(plan, facts, now):
    """Facts must come from same-boot read-only probes, not cached UI status."""
    def result(status):
        return dict(ready=status == 'ADMITTED', status=status, automatic_initialize=False)
    cfg = plan['config']
    if digest({k: v for k, v in plan.items() if k != 'plan_sha256'}) != plan.get('plan_sha256'):
        return result('PLAN_CHANGED')
    if digest(cfg) != plan['config_sha256']:
        return result('CONFIG_CHANGED')
    try:
        age = _number(now) - _number(facts['observed_monotonic'])
    except (KeyError, ValueError):
        return result('CLOCK_UNCONFIRMED')
    if facts.get('clock') != 'BOARD_MONOTONIC' or facts.get('boot_id') != cfg['boot_id']:
        return result('BOOT_OR_CLOCK_CHANGED')
    if not 0 <= age <= 2.0:
        return result('FACTS_EXPIRED_OR_FUTURE')
    if cfg['stop_monotonic'] - now < 90 or now >= cfg['no_new_operation_monotonic']:
        return result('DEADLINE')
    for name in ('manager', 'worker'):
        record = facts.get(name, {})
        if record.get('process_observed') is not True or record.get('boot_id') != cfg['boot_id']:
            return result(name.upper() + '_UNKNOWN')
        if not _integer(record.get('pid')) or not _integer(record.get('start_ticks')):
            return result(name.upper() + '_IDENTITY_MISSING')
        actual = record.get('actual_start_ticks')
        if actual == record['start_ticks']:
            return result('WAITING_FOR_' + name.upper() + '_NATURAL_EXIT')
        if actual is not None:
            return result(name.upper() + '_PID_REUSED')
        cleanup = record.get('completion', {})
        if (_identity(cleanup) != _identity(record) or cleanup.get('exit_confirmed') is not True
                or cleanup.get('cleanup_verified') is not True):
            return result(name.upper() + '_END_UNCONFIRMED')
    obs = facts.get('observer', {})
    if (_identity(obs) != _identity(cfg['observer']) or obs.get('process_observed') is not True
            or obs.get('actual_start_ticks') != obs.get('start_ticks')):
        return result('OBSERVER_CHANGED')
    owners = facts.get('ownership', {})
    if owners.get('runtime_flock_available') is not True or owners.get('task_owner_resolved') is not True:
        return result('OWNERSHIP_UNCONFIRMED')
    if owners.get('holders') != [] or owners.get('isolated_buffers') != []:
        return result('RESOURCES_UNCONFIRMED')
    if facts.get('unknown_requests') != []:
        return result('REQUEST_OUTCOME_UNKNOWN')
    hardware = facts.get('hardware', {})
    if (hardware.get('abi') != 0x444f4105 or hardware.get('fir_id') != 0x08040210
            or hardware.get('bundle_manifest_sha256') != digest(cfg['hardware_files'])):
        return result('HARDWARE_IDENTITY_UNCONFIRMED')
    if facts.get('run_folder_absent') is not True:
        return result('RUN_DIRECTORY_ALREADY_EXISTS_OR_UNKNOWN')
    return result('ADMITTED')


def _facts_identity(facts):
    return {k: facts.get(k) for k in ('boot_id', 'manager', 'worker', 'observer', 'ownership',
                                     'unknown_requests', 'hardware', 'run_folder_absent')}


class Launcher:
    """Backend operations are explicit and replaceable in offline regressions.

    launch_guard must be one shared, exclusive nonblocking interprocess lock.
    persist_intent must atomically create and fsync a unique receipt BEFORE
    spawn. Existing/unknown receipts are observed, never submitted again.
    No backend method is permitted to kill a worker or send initialization.
    """
    def __init__(self, backend, clock):
        self.backend, self.clock = backend, clock

    def start(self, plan):
        b = self.backend
        with b.launch_guard():
            old = b.intent()
            if old is not None:
                return b.observe(old, self.clock())
            first = b.facts()
            check = admission(plan, first, self.clock())
            if not check['ready']:
                return check
            if b.verify_payload(plan) is not True:
                return dict(status='PAYLOAD_SHA_MISMATCH', ready=False, automatic_initialize=False)
            intent = b.persist_intent(copy.deepcopy(plan), first)
            second = b.facts()
            check = admission(plan, second, self.clock())
            if not check['ready'] or _facts_identity(first) != _facts_identity(second):
                b.abort_before_spawn(intent, check['status'] if not check['ready'] else 'FACTS_CHANGED')
                return dict(status='START_ABORTED_FACTS_CHANGED', ready=False, automatic_initialize=False)
            try:
                b.spawn_once(intent)
            except Exception as exc:
                b.record_unknown_outcome(intent, repr(exc))
                return dict(status='START_OUTCOME_UNKNOWN', ready=False, automatic_initialize=False)
            return dict(status='STARTING', ready=False, automatic_initialize=False)


def readiness(receipt, observed, now, timeout=60.0):
    """Do not turn HTTP reachability, an old ready file or elapsed time into READY."""
    base = dict(ready=False, automatic_initialize=False)
    try:
        age = _number(now) - _number(observed['observed_monotonic'])
    except (KeyError, ValueError):
        return dict(base, status='START_OBSERVATION_UNCONFIRMED')
    if observed.get('clock') != 'BOARD_MONOTONIC' or not 0 <= age <= 2.0:
        return dict(base, status='START_OBSERVATION_UNCONFIRMED')
    if observed.get('outcome_unknown') or not _integer(receipt.get('pid')) or not _integer(receipt.get('start_ticks')):
        return dict(base, status='START_OUTCOME_UNKNOWN')
    if _identity(receipt) != _identity(observed):
        return dict(base, status='START_IDENTITY_CHANGED')
    if observed.get('process_observed') is not True:
        return dict(base, status='START_OUTCOME_UNKNOWN')
    actual = observed.get('actual_start_ticks')
    if actual is not None and actual != receipt['start_ticks']:
        return dict(base, status='START_PID_REUSED')
    if actual == receipt['start_ticks']:
        ready = observed.get('ready_record', {})
        brief = observed.get('brief', {})
        if (_identity(ready) == _identity(receipt)
                and brief.get('boot_id') == receipt['boot_id'] and brief.get('pid') == receipt['pid']
                and ready.get('status') == 'READY' and ready.get('version') == 4
                and isinstance(ready.get('ready_monotonic'), (int, float))
                and receipt['started_monotonic'] <= ready['ready_monotonic'] <= observed['observed_monotonic']
                and brief.get('closed') is False and brief.get('full_turn_initialization_supported') is True):
            return dict(base, status='READY', ready=True)
        elapsed = _number(now) - _number(receipt['started_monotonic'])
        return dict(base, status='STARTUP_TIMEOUT_OBSERVING' if elapsed > timeout else 'STARTING')
    completion = observed.get('completion', {})
    if _identity(completion) != _identity(receipt) or completion.get('cleanup_verified') is not True:
        return dict(base, status='EXIT_CLEANUP_UNCONFIRMED')
    normal = completion.get('status') in ('NATURAL_LIFETIME_END', 'REQUESTED_IDLE_FINISH')
    return dict(base, status='ENDED' if normal else 'START_FAILED', error=completion.get('error'))


def explicit_finish(receipt, observed, now, operator_requested=False):
    """Describe an ordinary operator-requested idle finish; never send it here."""
    if not operator_requested:
        return dict(status='NO_OPERATOR_REQUEST', request=None)
    if readiness(receipt, observed, now)['status'] != 'READY':
        return dict(status='NOT_READY_FOR_IDLE_FINISH', request=None)
    brief = observed['brief']
    if ('active' not in brief or 'pending' not in brief or brief.get('active') is not None
            or brief.get('pending') is not None or observed.get('unknown_requests') != []):
        return dict(status='WAITING_FOR_OPERATION_COMPLETION', request=None)
    return dict(status='EXPLICIT_IDLE_FINISH_ONLY', request=dict(method='POST', path='/service/finish',
        headers={'Content-Type': 'application/json', 'X-AV-Request': '1'},
        body=dict(boot_id=receipt['boot_id'], runtime_start_ticks=receipt['start_ticks'])))
