"""Healthy, current-operation phase rejection is silence/ambiguity, not an angle."""
import math
import re

REASONS=frozenset(('phase_model_mismatch','alternative_not_separated','subwindow_vector_unstable',
    'insufficient_effective_bins','insufficient_frequency_span','model_radius_too_large',
    'insufficient_valid_subwindows'))


def healthy_phase_rejection(result, boot, trigger, now, anchor):
    try:
        phase=result['phase'];proof=result['rejected_capture'];e=phase['capture_evidence'];h=proof['health']
        reasons=phase['reasons'];m=proof['metadata']
        if (result['status']!='AUDIO_REJECTED' or result.get('error')!="ValueError('audio_phase_rejected')"
                or result.get('motion_started') is not False or result.get('navigation_ready') is not False
                or result.get('source_event') is not None or result.get('target_count') is not None
                or result.get('local_cleanup_verified') is not True or result['boot_id']!=boot
                or result['triggered']!=trigger or phase['status']!='REJECTED'
                or phase.get('global_bearing') is not None or phase.get('servo_command') is not None
                or phase.get('delta_vector') is not None or not reasons or not set(reasons)<=REASONS):return False
        if (phase['identity']!=anchor['identity'] or phase['reference_sha256']!=anchor['reference_sha256']
                or e['boot_id']!=boot or phase['identity']!=proof['reference_identity']
                or phase['identity']['boot_id']!=boot or phase['reference_sha256']!=proof['reference_sha256']
                or not re.fullmatch('[0-9a-f]{64}',phase['reference_sha256'])
                or e['installation_id']!=phase['identity']['installation_id'] or e['epoch']!=phase['identity']['epoch']
                or e['origin']!='BOARD_CAPTURE' or e['clock_domain']!='BOARD_MONOTONIC'
                or e['sample_time_basis']!='TRANSACTION_ENVELOPE_ONLY' or e['cleanup_verified'] is not True
                or e['abi']!=0x444f4105 or e['fir_id']!=0x08040210 or e['raw_frames']!=65536
                or e['window_first']!=31269 or m['status']!='CAPTURE_VERIFIED' or m['valid_frames']!=65536
                or any(m.get(k) is not True for k in ('clock_disabled','dma_reset_and_halted','buffer_freed'))):return False
        sha=e['raw_sha256']
        if not re.fullmatch('[0-9a-f]{64}',sha) or sha!=phase['raw_sha256'] or sha!=m['raw_sha256']:return False
        if (h['changed']!=[True]*4 or h['clipped']!=[False]*4 or h['duplicate_pairs']!=[]
                or len(h['ac_rms'])!=4 or any(type(x) not in (float,int) or not math.isfinite(x) or x<=1 for x in h['ac_rms'])):return False
        times=[trigger,e['begin'],e['end'],result['completed_monotonic'],now]
        if not all(type(x) in (int,float) and math.isfinite(x) for x in times) or times!=sorted(times) or trigger<0:return False
        if (e['begin']!=m['transaction_begin_monotonic'] or e['end']!=m['transaction_end_monotonic']):return False
        # No output-age relaxation: a rejection never enters the source catalogue.
        return True
    except (KeyError,TypeError,ValueError):return False
