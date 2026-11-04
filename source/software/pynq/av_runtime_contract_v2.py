"""Exact independent runtime identity. No process, device or network operations."""
import copy
from av_runtime_binding_V1 import digest


CONTRACT = dict(runtime_version=5, visual_io_version=10, coordinator_version=12,
                runner_version=4, initialization_evidence='FULL_TURN_STATIC_AXIS_V1',
                axis_source='CURRENT_TRIGGER_THREE_STATIONARY_VIEWS',
                automatic_initialize=False, decision_owner='PYNQ')
REQUIRED_MODULES = ('av_phase_runtime_V5.py', 'av_phase_io_V10.py',
                    'av_trigger_service_V12.py', 'av_init_proof_V1.py',
                    'av_phase_service_runner_V4.py', 'av_runtime_contract_V2.py')


def validate_config(config):
    if config.get('runtime_contract') != CONTRACT:
        raise ValueError('Exact runtime V5 / IO V10 / Coordinator V12 contract required')
    if not all(name in config.get('modules', {}) for name in REQUIRED_MODULES):
        raise ValueError('Incomplete reviewed runtime V5 source closure')
    if 'axis_file' in config:
        raise ValueError('Historical axis is not a runtime V5 input')
    return copy.deepcopy(CONTRACT)


def ready_identity(config):
    return dict(runtime_contract=validate_config(config), config_sha256=digest(config))
