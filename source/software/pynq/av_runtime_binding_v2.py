"""Exact runtime V5 readiness and immutable launch-plan binding.

V1 remains a V4-only contract. This version accepts no V4 READY or partial
full-turn capability, and never requests initialization during connection.
"""
import copy
from pathlib import PurePosixPath
from av_runtime_binding_V1 import (digest, _number, _integer, _inside, _hashes,
    _identity, admission, Launcher as PreviousLauncher)
from av_runtime_contract_V2 import CONTRACT, REQUIRED_MODULES, validate_config


def configuration(requirements, placement, clock):
    if clock.get('clock') != 'BOARD_MONOTONIC' or not clock.get('boot_id'):
        raise ValueError('Current board clock required')
    now = _number(clock['observed_monotonic'])
    root = _inside(placement['session_root'])
    code, folder = _inside(placement['code_root'], root), _inside(placement['folder'], root)
    hardware = _inside(placement['hardware_root'], root)
    if folder == code or PurePosixPath(code) in PurePosixPath(folder).parents:
        raise ValueError('Run must not be inside frozen code')
    if (requirements.get('entry') != 'software/pynq/av_phase_runtime_V5.py'
            or requirements.get('axis_file_required') is not False
            or requirements.get('runtime_contract') != CONTRACT):
        raise ValueError('Reviewed runtime V5 full-turn proof contract required')
    modules, assets = _hashes(requirements['modules']), _hashes(requirements['assets'])
    files = _hashes(requirements['hardware_files'])
    if not all(name in modules for name in REQUIRED_MODULES) or set(assets) != {'known_source.npy'}:
        raise ValueError('Missing runtime V5 closure or unexpected reference assets')
    wav = _hashes({'known.wav': requirements['known_wav_sha256']})['known.wav']
    stop, cutoff = _number(placement['stop_monotonic']), _number(placement['no_new_operation_monotonic'])
    if not 90 <= stop-now <= 25200 or not now < cutoff <= stop-11.5:
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
        known_wav_sha256=wav, runtime_contract=copy.deepcopy(CONTRACT))
    plan = dict(entry=code+'/av_phase_runtime_V5.py', config=cfg, config_sha256=digest(cfg),
        automatic_initialize=False, required_runtime_flock=root+'/standalone_runtime.lock')
    plan['plan_sha256'] = digest(plan)
    return plan


def validate_plan(plan):
    validate_config(plan['config'])
    if (PurePosixPath(plan['entry']).name != 'av_phase_runtime_V5.py'
            or plan.get('automatic_initialize') is not False
            or digest(plan['config']) != plan.get('config_sha256')
            or digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) != plan.get('plan_sha256')):
        raise ValueError('Runtime V5 plan identity changed')
    return True


class Launcher(PreviousLauncher):
    def start(self, plan):
        validate_plan(plan)
        return super().start(plan)


def readiness(receipt, observed, now, timeout=60.):
    base = dict(ready=False, automatic_initialize=False)
    try: age = _number(now)-_number(observed['observed_monotonic'])
    except (KeyError, ValueError): return dict(base, status='START_OBSERVATION_UNCONFIRMED')
    if observed.get('clock') != 'BOARD_MONOTONIC' or not 0 <= age <= 2.:
        return dict(base, status='START_OBSERVATION_UNCONFIRMED')
    if observed.get('outcome_unknown') or not _integer(receipt.get('pid')) or not _integer(receipt.get('start_ticks')):
        return dict(base, status='START_OUTCOME_UNKNOWN')
    if _identity(receipt) != _identity(observed): return dict(base, status='START_IDENTITY_CHANGED')
    if observed.get('process_observed') is not True: return dict(base, status='START_OUTCOME_UNKNOWN')
    actual = observed.get('actual_start_ticks')
    if actual is not None and actual != receipt['start_ticks']: return dict(base, status='START_PID_REUSED')
    if actual == receipt['start_ticks']:
        ready, brief = observed.get('ready_record', {}), observed.get('brief', {})
        try: ready_time = _number(ready['ready_monotonic'])
        except (KeyError, ValueError): ready_time = None
        if (_identity(ready) == _identity(receipt) and ready.get('status') == 'READY'
                and type(ready.get('version')) is int and ready['version'] == 5
                and ready.get('runtime_contract') == CONTRACT
                and ready.get('config_sha256') == receipt.get('config_sha256')
                and isinstance(receipt.get('config_sha256'), str) and len(receipt['config_sha256']) == 64
                and ready_time is not None and receipt['started_monotonic'] <= ready_time <= observed['observed_monotonic']
                and brief.get('boot_id') == receipt['boot_id'] and brief.get('pid') == receipt['pid']
                and brief.get('runtime_start_ticks') == receipt['start_ticks']
                and brief.get('closed') is False and brief.get('full_turn_initialization_supported') is True
                and brief.get('version') == 12 and brief.get('runtime_version') == 5
                and brief.get('initialization_evidence_contract') == CONTRACT['initialization_evidence']
                and brief.get('runtime_contract') == CONTRACT
                and brief.get('config_sha256') == receipt['config_sha256']):
            return dict(base, status='READY', ready=True)
        return dict(base, status='STARTUP_TIMEOUT_OBSERVING' if now-receipt['started_monotonic'] > timeout else 'STARTING')
    completion = observed.get('completion', {})
    if _identity(completion) != _identity(receipt) or completion.get('cleanup_verified') is not True:
        return dict(base, status='EXIT_CLEANUP_UNCONFIRMED')
    normal = completion.get('status') in ('NATURAL_LIFETIME_END', 'REQUESTED_IDLE_FINISH')
    return dict(base, status='ENDED' if normal else 'START_FAILED', error=completion.get('error'))


def explicit_finish(receipt, observed, now, operator_requested=False):
    if not operator_requested: return dict(status='NO_OPERATOR_REQUEST', request=None)
    if readiness(receipt, observed, now)['status'] != 'READY':
        return dict(status='NOT_READY_FOR_IDLE_FINISH', request=None)
    brief = observed['brief']
    if ('active' not in brief or 'pending' not in brief or brief['active'] is not None
            or brief['pending'] is not None or observed.get('unknown_requests') != []):
        return dict(status='WAITING_FOR_OPERATION_COMPLETION', request=None)
    return dict(status='EXPLICIT_IDLE_FINISH_ONLY', request=dict(method='POST', path='/service/finish',
        headers={'Content-Type':'application/json', 'X-AV-Request':'1'},
        body=dict(boot_id=receipt['boot_id'], runtime_start_ticks=receipt['start_ticks'])))
