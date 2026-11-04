"""Exact independent runtime identity. No process, device or network operations."""
import copy
from av_runtime_binding_V1 import digest


CONTRACT = dict(runtime_version=13, visual_io_version=16, coordinator_version=18,
                runner_version=9, initialization_budget_seconds=None, selection_budget_seconds=None, initialization_evidence='FULL_TURN_STATIC_AXIS_V1',
                axis_source='CURRENT_TRIGGER_THREE_STATIONARY_VIEWS',
                audio_anchor_source='CURRENT_TRIGGER_POST_RETURN_STATIONARY_CAPTURE',
                automatic_initialize=False, decision_owner='PYNQ')
REQUIRED_MODULES = ('av_phase_runtime_V13.py', 'av_phase_io_V16.py',
                    'av_trigger_service_V18.py', 'av_init_proof_V5.py',
                    'av_phase_service_runner_V9.py', 'av_runtime_contract_V10.py', 'av_phase_follow_V15.py', 'audio_phase_session_V7.py', 'av_scan_queue_V1.py', 'av_screen_detector_V6.py', 'audio_stimulus_gate_V3.py', 'av_screen_sweep_V2.py', 'av_phase_rejection_V1.py', 'av_static_reacquire_V1.py', 'av_search_endpoint_V1.py', 'av_pl_follow_source_V1.py', 'av_audio_rejection_V2.py', 'av_direction_catalogue_V2.py', 'servo_coordinates_V1.py', 'servo_pan_session_V8.py')


def validate_config(config):
    if config.get('runtime_contract') != CONTRACT:
        raise ValueError('Exact runtime V13 / IO V16 / Coordinator V18 contract required')
    if not all(name in config.get('modules', {}) for name in REQUIRED_MODULES):
        raise ValueError('Incomplete reviewed runtime V13 source closure')
    if 'axis_file' in config:
        raise ValueError('Historical axis is not a runtime V13 input')
    return copy.deepcopy(CONTRACT)


def ready_identity(config):
    return dict(runtime_contract=validate_config(config), config_sha256=digest(config))
