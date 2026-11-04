"""Preserve genuine operation rejection instead of replacing it with ABI errors."""
from av_trigger_service_V10 import Coordinator as Previous, ButtonEdges, make_handler, atomic_json


class Coordinator(Previous):
    def _state(self):
        state = super()._state()
        state.update(version=11, initialization_mode='FULL_TURN_AUDIOVISUAL',
                     full_turn_initialization_supported=True)
        return state

    def _accept(self, job, result, now):
        if (isinstance(result, dict) and result.get('local_cleanup_verified') is True and
                result.get('navigation_ready') is False and job['action'] in ('initialize', 'select')):
            allowed = {'INITIALIZATION_FAILED', 'INITIALIZATION_DEADLINE_OR_CLEANUP_FAILED'} if job['action'] == 'initialize' else {
                'AUDIO_REJECTED', 'FOLLOW_DEADLINE_OR_CLEANUP_FAILED'}
            if result.get('status') in allowed:
                specific = (result.get('visual') or {}).get('error') or result.get('error') or result['status']
                job.update(status='INITIALIZATION_REJECTED' if job['action'] == 'initialize' else 'FOLLOW_REJECTED',
                           navigation_ready=False, reason=specific, error=result.get('error'),
                           operation_status=result['status'])
                return
        return super()._accept(job, result, now)
