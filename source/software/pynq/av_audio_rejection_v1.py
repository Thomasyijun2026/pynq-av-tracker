"""Classify healthy but inconclusive sound, without accepting a bearing.

This changes listener scheduling only. No signal threshold, geometry, or source
catalogue admission is changed. Unknown reasons and damaged inputs remain faults.
"""
import math
import re

SOFT_REASONS = frozenset((
    'weak_pair_correlation', 'ambiguous_peak', 'near_single_tone',
    'nonphysical_delay', 'inconsistent_geometry', 'inconsistent_delay_cycle',
    'projection_ill_defined', 'subwindow_delay_inconsistent',
    'no_positive_physical_peak'))


def healthy_inconclusive(source, boot_id, now):
    try:
        e=source['audio_evidence'];h=e['health'];t=e['capture_envelope']
        reasons=e['reasons']
        if (source.get('audio_valid') is not False or source.get('audio_bearing_deg') is not None
                or source.get('boot_id')!=boot_id or source.get('coordinate_frame')!='ARRAY_FIXED'
                or e.get('origin')!='BOARD_CAPTURE' or e.get('status')!='REJECTED_SIGNAL'
                or e.get('selected_channels')!=[1,5,7,8] or e.get('frames')!=65536
                or type(reasons) is not list or not reasons or not set(reasons)<=SOFT_REASONS):
            return False
        if not re.fullmatch('[0-9a-f]{64}',e.get('raw_sha256','')):return False
        if any(e.get(k) is not True for k in ('clock_disabled','dma_reset_and_halted','buffer_freed')):return False
        if h.get('changed')!=[True]*4 or h.get('clipped')!=[False]*4 or h.get('duplicate_pairs')!=[]:return False
        rms=h.get('ac_rms',[])
        if len(rms)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 for v in rms):return False
        if (t.get('clock_domain')!='BOARD_MONOTONIC' or t.get('boot_id')!=boot_id
                or t.get('sample_time_basis')!='TRANSACTION_ENVELOPE_ONLY'):return False
        begin,end,published=t['begin_monotonic'],t['end_monotonic'],e['published_monotonic']
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (begin,end,published,now)):return False
        return 0<=begin<=end<=published<=now and now-begin<=5.0
    except (KeyError,TypeError,ValueError):
        return False
