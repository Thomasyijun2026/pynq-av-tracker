"""Stationary endpoint search hints and independently measured axis provenance.

Endpoint images supplement one verified complete sweep. No moving frame or
endpoint hint is itself the optical/audio alignment anchor.
"""
import math
from av_screen_sweep_V2 import LOW, HIGH, associate
from av_pan_view_probe_V1 import stable
from av_static_reacquire_V1 import center_hint, axis_targets


def stationary(row, boot, begin, end):
    frame = row['frame']
    q = row['position']
    if (row.get('boot_id') != boot or row.get('clock_domain') != 'BOARD_MONOTONIC'
            or type(q) is not int or not LOW <= q <= HIGH):
        raise ValueError('static_search_identity_or_position')
    stable(row['before'], row['after'], q)
    times = [row['before'].get('monotonic'), frame.get('read_begin'),
             frame.get('read_end'), row['after'].get('monotonic'), frame.get('write_end')]
    if (not all(type(t) in (int, float) and math.isfinite(t) for t in times)
            or not begin <= times[0] <= times[1] <= times[2] <= times[3] <= times[4] <= end
            or frame.get('image_rotation') != 180
            or frame.get('time_basis') != 'DRAINED_READ_NOT_EXPOSURE'):
        raise ValueError('static_search_not_fresh_stationary')
    sha = frame.get('sha256', '')
    if (len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
            or type(frame.get('frame_number')) is not int or frame['frame_number'] <= 0):
        raise ValueError('static_search_frame_identity')
    vision = row['vision']
    if vision.get('status') == 'KNOWN_SCREEN_MATCHED':
        best = vision.get('best') or {}
        uv = best.get('center_px', [])
        if (vision.get('image_size') != [1280, 960] or len(uv) != 2
                or not all(type(x) in (int, float) and math.isfinite(x) for x in uv)
                or not 0 <= uv[0] <= 1278 or not 0 <= uv[1] <= 959
                or not best.get('matched') or best.get('pattern_id') != 'PYNQ_SOUND_MARK_V1'):
            raise ValueError('static_search_marker_identity')
        if 'u' in row and abs(row['u'] - uv[0] / 2) > 1e-9:
            raise ValueError('static_search_marker_geometry')
    return q


def pose_for_endpoint(row, route, entry, sweep, boot, triggered, completed):
    role = row.get('endpoint_role')
    if role == 'ENTRY':
        begin, end, target = entry['end'], sweep['begin'], route['entry']
    elif role == 'EXIT':
        begin, end, target = sweep['end'], completed, route['exit']
    else:
        raise ValueError('static_search_endpoint_role')
    if not triggered <= begin <= end <= completed:
        raise ValueError('static_search_endpoint_interval')
    q = stationary(row, boot, begin, end)
    if abs(q - target) > 4:
        raise ValueError('static_search_not_at_endpoint')
    return dict(status='SEARCH_HINT_ONLY', pose=q,
                time_basis='STATIONARY_ENDPOINT_READ_BRACKET_NOT_EXPOSURE',
                endpoint_role=role, stationary_anchor=False,
                read_interval=[row['frame']['read_begin'], row['frame']['read_end']],
                anchor_requires_fresh_stationary_confirmation=True)


def expected_rows(evidence, boot, triggered, completed):
    entry, sweep, route = evidence['entry'], evidence['sweep'], evidence['route']
    samples = entry['samples'] + sweep['samples']
    result = []
    for row in evidence['visual_decisions']:
        frame = row['frame']
        eligible = sweep['begin'] <= frame['read_begin'] <= frame['read_end'] <= sweep['end']
        pose = associate(frame, samples) if eligible else dict(status='OUTSIDE_FULL_SWEEP', pose=None)
        result.append((row, pose))
    roles = set()
    for row in evidence.get('endpoint_views', []):
        if row.get('endpoint_role') in roles:
            raise ValueError('duplicate_search_endpoint')
        roles.add(row.get('endpoint_role'))
        result.append((row, pose_for_endpoint(row, route, entry, sweep, boot, triggered, completed)))
    return result


def validate_reacquisition(evidence, axis, winner, boot, triggered, completed):
    """The selected angle is a return hint; the final three probes can recenter."""
    motion = evidence.get('return_motion') or {}
    samples = motion.get('samples', [])
    if (motion.get('status') != 'ARRIVED' or motion.get('target') != winner['goal']
            or len(samples) < 3
            or not evidence['sweep']['end'] <= motion['begin'] <= motion['end'] <= completed):
        raise ValueError('selected_search_hint_return_not_confirmed')
    for sample in samples[-3:]:
        stable(sample, sample, winner['goal'])
    attempts = evidence.get('stationary_search_attempts') or []
    if not attempts:
        raise ValueError('stationary_reacquisition_evidence_missing')
    prior = motion['end']
    ids = set()
    for row in attempts:
        stationary(row, boot, prior, completed)
        number = row['frame']['frame_number']
        if number in ids:
            raise ValueError('stationary_search_frame_reused')
        ids.add(number)
        prior = row['frame']['write_end']
    if abs(attempts[0]['position'] - winner['goal']) > 4:
        raise ValueError('stationary_search_did_not_start_at_selected_hint')
    hint = evidence.get('stationary_center_hint') or {}
    sources = hint.get('source_frames', [])
    if len(sources) != 2 or sources[0] == sources[1]:
        raise ValueError('stationary_center_hint_sources')
    pair = []
    for sha in sources:
        found = [r for r in attempts if r['frame']['sha256'] == sha]
        if len(found) != 1 or found[0]['vision'].get('status') != 'KNOWN_SCREEN_MATCHED':
            raise ValueError('stationary_center_hint_source_not_matched')
        item=dict(found[0])
        item['u']=item['vision']['best']['center_px'][0]/2
        pair.append(item)
    if (pair[0]['frame']['write_end'] > pair[1]['frame']['read_begin']
            or center_hint(*pair) != hint):
        raise ValueError('stationary_center_hint_not_from_saved_views')
    targets = axis_targets(hint['goal'])
    plan = dict(status='PROBE_PLAN_ONLY', targets=targets,
                basis='CURRENT_TRIGGER_STATIONARY_DITHER', usable_for_alignment=False,
                requires_three_fresh_stationary_views=True)
    if evidence.get('axis_probe_plan') != plan:
        raise ValueError('static_axis_plan_not_from_center_hint')
    rows = axis['measurements']
    if len(rows) != 3:
        raise ValueError('three_new_axis_views_required')
    for target, row in zip(targets, rows):
        recorded = [a for a in attempts if a['frame']['frame_number']==row['frame']['frame_number']]
        matching = len(recorded)==1 and all(row.get(k)==v for k,v in recorded[0].items())
        if (abs(row['position'] - target) > 4 or not matching
                or row['frame']['read_begin'] <= pair[1]['frame']['write_end']):
            raise ValueError('axis_view_not_from_new_static_probe_plan')
    return dict(status='REACQUISITION_AND_NEW_STATIC_AXIS_VERIFIED',
                return_goal=winner['goal'], center_hint=hint['goal'], axis_targets=targets,
                search_hints_are_alignment=False)
