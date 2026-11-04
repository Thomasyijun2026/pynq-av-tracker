"""Check full-turn initialization evidence before publishing an aligned state.

These are software evidence checks, not a sensor-exposure synchronization or
physical accuracy measurement. Transaction-envelope overlap keeps its label.
"""
import math
from av_axis_bootstrap_V1 import validate_fresh_axis
from av_screen_sweep_V1 import plan, verify_travel, associate, choose


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def validate(result, boot, triggered, completed):
    if not (finite(triggered) and finite(completed) and triggered <= completed <= triggered+11.):
        raise ValueError('initialization_complete_publication_deadline')
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
    rows=evidence['visual_decisions']
    samples=entry['samples']+sweep['samples']
    for row in rows:
        frame=row['frame']
        eligible=(sweep['begin']<=frame['read_begin']<=frame['read_end']<=sweep['end'])
        expected=associate(frame,samples) if eligible else dict(status='OUTSIDE_FULL_SWEEP',pose=None)
        if row.get('pose')!=expected:
            raise ValueError('search_pose_not_from_saved_frame_feedback')
    winner=choose(rows,travel)
    if evidence.get('selected')!=winner:
        raise ValueError('highest_valid_screen_match_not_selected')
    axis=visual['measured_axis']
    validate_fresh_axis(axis,boot,triggered,completed)
    measurements=axis['measurements']
    if measurements[0]['frame']['read_begin']<sweep['end']:
        raise ValueError('stationary_axis_view_precedes_full_turn_completion')
    if abs(measurements[0]['position']-winner['goal'])>4:
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
    return dict(status='FULL_TURN_STATIC_AXIS_HANDOFF_CHECKED',same_boot=True,
                current_trigger=True,physical_accuracy_verified=False,
                overlap_basis=overlap['basis'])
