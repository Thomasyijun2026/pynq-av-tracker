"""Check full-turn initialization evidence before publishing an aligned state.

These are software evidence checks, not a sensor-exposure synchronization or
physical accuracy measurement. Transaction-envelope overlap keeps its label.
"""
import math
from av_axis_bootstrap_V1 import validate_fresh_axis
from av_screen_sweep_V2 import plan, verify_travel, choose
from av_search_endpoint_V1 import expected_rows, validate_reacquisition


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def validate(result, boot, triggered, completed):
    if not (finite(triggered) and finite(completed) and triggered <= completed):
        raise ValueError('initialization_publication_chronology')
    if (result.get('boot_id')!=boot or result.get('initialization_mode')!='FULL_TURN_AUDIOVISUAL_SEARCH'
            or result.get('full_sweep_this_trigger') is not True):
        raise ValueError('full_turn_current_trigger_result_required')
    visual=result.get('visual') or {}
    if visual.get('status')!='SCREEN_CENTER_CONFIRMED' or visual.get('full_sweep_this_trigger') is not True:
        raise ValueError('full_turn_stationary_visual_result_required')
    evidence=visual['sweep']
    route,entry,sweep=evidence['route'],evidence['entry'],evidence['sweep']
    if route!=plan(route['start']):
        raise ValueError('fixed_full_turn_route_required')
    times=[entry.get('begin'),entry.get('end'),sweep.get('begin'),sweep.get('end')]
    if not all(finite(t) for t in times) or not triggered<=times[0]<=times[1]<=times[2]<=times[3]<=completed:
        raise ValueError('full_turn_timing_not_in_current_trigger')
    travel=verify_travel(route,entry,sweep)
    if evidence.get('travel')!=travel:
        raise ValueError('full_turn_travel_not_from_saved_feedback')
    for record in (entry,sweep):
        if any(not finite(row.get('monotonic')) or not record['begin']<=row['monotonic']<=record['end']
               for row in record['samples']):
            raise ValueError('full_turn_feedback_outside_motion_interval')
    rows=expected_rows(evidence,boot,triggered,completed)
    for row,expected in rows:
        if row.get('pose')!=expected:
            raise ValueError('search_pose_not_from_saved_frame_feedback')
    winner=choose([row for row,expected in rows],travel)
    if evidence.get('selected')!=winner:
        raise ValueError('highest_valid_screen_match_not_selected')
    axis=visual['measured_axis']
    validate_fresh_axis(axis,boot,triggered,completed)
    measurements=axis['measurements']
    if measurements[0]['frame']['read_begin']<sweep['end']:
        raise ValueError('stationary_axis_view_precedes_full_turn_completion')
    if 'stationary_search_attempts' in evidence:
        validate_reacquisition(evidence,axis,winner,boot,triggered,completed)
    elif abs(measurements[0]['position']-winner['goal'])>4:
        raise ValueError('axis_probe_not_returned_to_selected_screen_pose')
    confirmation=result['confirmation_frame']
    original=visual['confirmation_frame']
    if confirmation.get('sha256')!=original.get('sha256') or confirmation.get('frame_number')!=original.get('frame_number'):
        raise ValueError('published_confirmation_not_the_confirmed_static_frame')
    ts=[confirmation.get(k) for k in ('read_begin','read_end','write_end')]
    if (not all(finite(t) for t in ts) or not measurements[-1]['frame']['read_begin']<=ts[0]<=ts[1]<=ts[2]<=completed
            or confirmation.get('image_rotation')!=180 or confirmation.get('time_basis')!='DRAINED_READ_NOT_EXPOSURE'):
        raise ValueError('confirmation_not_fresh_stationary_read')
    if (not finite(visual.get('center_residual_px')) or abs(visual['center_residual_px'])>12):
        raise ValueError('confirmed_center_residual_exceeded')
    overlap=result.get('audio_sweep_overlap') or {}
    if (not finite(overlap.get('seconds')) or overlap['seconds']<=0 or
            overlap.get('basis')!='AUDIO_TRANSACTION_AND_MOTION_ENVELOPES_NOT_SAMPLE_EXPOSURE_SYNC'):
        raise ValueError('audio_motion_transaction_overlap_required')
    from av_pan_view_probe_V1 import stable
    reference=result.get('stationary_audio') or {}
    scan=result.get('scan_audio') or {}
    ev=reference.get('evidence') or {}
    prior=scan.get('evidence') or {}
    before=reference.get('before') or []
    after=reference.get('after') or {}
    if (reference.get('status')!='STATIONARY_REFERENCE_CAPTURED' or len(before)!=3
            or reference.get('source_phase')!='INITIALIZING_STATIC_REFERENCE'
            or reference.get('confirmed_view_sha256')!=confirmation['sha256']):
        raise ValueError('stationary_audio_reference_required')
    for pose in before:stable(pose,after,int(visual['position']))
    pose_times=[p.get('monotonic') for p in before]+[after.get('monotonic')]
    if (not all(finite(t) for t in pose_times) or pose_times!=sorted(pose_times)
            or not confirmation['write_end']<=pose_times[0]<=pose_times[2]<=ev['begin']<ev['end']<=pose_times[3]<=completed):
        raise ValueError('stationary_audio_pose_chronology')
    for audio in (ev,prior):
        if (audio.get('boot_id')!=boot or audio.get('origin')!='BOARD_CAPTURE'
                or audio.get('clock_domain')!='BOARD_MONOTONIC' or audio.get('cleanup_verified') is not True
                or not triggered<=audio['begin']<audio['end']<=completed):
            raise ValueError('current_trigger_audio_evidence_required')
    anchor=result.get('source_anchor') or {}
    if (prior['end']>=ev['begin'] or prior.get('raw_sha256')==ev.get('raw_sha256')
            or anchor.get('reference_sha256')!=ev.get('raw_sha256')):
        raise ValueError('independent_stationary_audio_anchor_required')
    actual_overlap=max(0.,min(sweep['end'],prior['end'])-max(sweep['begin'],prior['begin']))
    if abs(actual_overlap-overlap['seconds'])>1e-9:
        raise ValueError('scan_audio_overlap_not_from_evidence')
    return dict(status='FULL_TURN_STATIC_AXIS_HANDOFF_CHECKED',same_boot=True,
                current_trigger=True,physical_accuracy_verified=False,
                overlap_basis=overlap['basis'])
