"""Full-turn search policy. No device access and no caller-supplied target angle.

Moving-frame poses are search hints, not calibrated optical observations.
Only a fresh stationary confirmation can become the alignment anchor.
"""
import math

LOW, HIGH, COUNTS_PER_TURN = -1321, 2775, 4096


def plan(position):
    if type(position) is not int or not LOW <= position <= HIGH:
        raise ValueError('sweep_start_outside_fixed_envelope')
    first = LOW if position - LOW <= HIGH - position else HIGH
    last = HIGH if first == LOW else LOW
    return dict(start=position, entry=first, exit=last, direction=1 if last > first else -1,
                low=LOW, high=HIGH, counts_per_turn=COUNTS_PER_TURN,
                required_travel=COUNTS_PER_TURN, full_sweep_required=True)


def observation_interval(row):
    t=row['monotonic']
    if row.get('time_basis')!='REQUEST_RESPONSE_BRACKET':
        raise ValueError('sweep_observation_bracket_required')
    a,b=row.get('observation_begin_monotonic'),row.get('observation_end_monotonic')
    if (not all(type(v) in (int,float) and math.isfinite(v) for v in (a,b,t))
            or not a<=b<=t or b-a>.1 or t-b>.1):
        raise ValueError('sweep_observation_bracket_invalid')
    return a,b


def verify_travel(route, entry, sweep):
    """Verify arrived endpoints and the ordered, guarded telemetry, not an intention."""
    for record, goal in ((entry, route['entry']), (sweep, route['exit'])):
        samples = record.get('samples', [])
        if record.get('status') != 'ARRIVED' or record.get('target') != goal or len(samples) < 3:
            raise ValueError('sweep_endpoint_not_confirmed')
        if any(abs(v['position'] - goal) > 4 or v['moving'] or v['speed'] for v in samples[-3:]):
            raise ValueError('sweep_endpoint_not_confirmed')
    samples = sweep['samples']
    if abs(sweep['start'] - route['entry']) > 4:
        raise ValueError('sweep_did_not_start_at_endpoint')
    previous = dict(position=sweep['start'], monotonic=sweep['begin'])
    previous_interval=(sweep['begin'],sweep['begin'])
    for row in samples:
        if not LOW - 4 <= row['position'] <= HIGH + 4:
            raise ValueError('sweep_telemetry_outside_envelope')
        dt = row['monotonic'] - previous['monotonic']
        if dt <= 0 or dt > .5 or (row['position'] - previous['position']) * route['direction'] < -4:
            raise ValueError('sweep_telemetry_gap_or_reversal')
        interval=observation_interval(row)
        if interval[0]<previous_interval[1] or interval[1]>sweep['end']:
            raise ValueError('sweep_observation_order_invalid')
        # Feedback acquisition is within the request/response bracket. Using
        # response completion as an instantaneous sample falsely rejects a
        # delayed reply followed by a fast reply. Numeric guards are unchanged.
        possible_dt=interval[1]-previous_interval[0]
        if possible_dt<=0 or possible_dt>.5:
            raise ValueError('sweep_telemetry_gap_or_reversal')
        if abs(row['position'] - previous['position']) > max(128, 1800 * 2 * possible_dt):
            raise ValueError('sweep_telemetry_discontinuity')
        previous = row
        previous_interval=interval
    travel = abs(samples[-1]['position'] - sweep['start'])
    if abs(travel - COUNTS_PER_TURN) > 8:
        raise ValueError('full_turn_not_measured')
    return dict(status='FULL_TURN_ENCODER_VERIFIED', measured_travel=travel,
                endpoint_tolerance_counts=4, optical_coverage='SEPARATE_FRAME_EVIDENCE')


def associate(frame, samples):
    """Bracket a read with telemetry; do not manufacture an exposure timestamp."""
    a, b = frame['read_begin'], frame['read_end']
    before = [s for s in samples if s['monotonic'] <= a]
    after = [s for s in samples if s['monotonic'] >= b]
    if not before or not after:
        return dict(status='POSE_UNBRACKETED', pose=None)
    left, right = before[-1], after[0]
    span = right['monotonic'] - left['monotonic']
    if span <= 0 or span > .3:
        return dict(status='POSE_BRACKET_TOO_WIDE', pose=None)
    midpoint = (a + b) / 2
    value = left['position'] + (right['position'] - left['position']) * (midpoint - left['monotonic']) / span
    return dict(status='SEARCH_HINT_ONLY', pose=int(round(value)),
                read_interval=[a, b], telemetry_interval=[left['monotonic'], right['monotonic']],
                position_bracket=[left['position'], right['position']],
                time_basis='READ_AND_TELEMETRY_BRACKETS_NOT_EXPOSURE', stationary_anchor=False)


def choose(records, travel):
    if travel.get('status') != 'FULL_TURN_ENCODER_VERIFIED':
        raise ValueError('full_turn_required_before_selection')
    accepted = []
    for row in records:
        result, pose = row['vision'], row['pose']
        if result.get('status') != 'KNOWN_SCREEN_MATCHED' or pose.get('status') != 'SEARCH_HINT_ONLY':
            continue
        best = result.get('best') or {}
        score = best.get('score')
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            continue
        if not LOW <= pose['pose'] <= HIGH:
            continue
        accepted.append(row)
    if not accepted:
        raise ValueError('known_screen_not_found_in_full_sweep')
    winner = max(accepted, key=lambda row: (row['vision']['best']['score'],
                  -abs(row['vision']['best']['center_px'][0] - row['vision']['image_size'][0] / 2)))
    return dict(status='SCREEN_SEARCH_HINT_SELECTED', goal=winner['pose']['pose'],
                frame=winner['frame'], pattern=winner['vision']['best'],
                search_quality=winner['vision']['best']['score'], quality_kind='SCREEN_PATTERN_AGREEMENT',
                matched_frames=len(accepted), anchor_requires_fresh_stationary_confirmation=True)


def measure_axis(rows):
    """Same three-view / 200-count / 6px residual / 10px holdout gates as V1."""
    points = sorted(rows, key=lambda r: r['position'])
    if len(points) != 3 or points[-1]['position'] - points[0]['position'] < 200:
        raise ValueError('insufficient_fresh_axis_baseline')
    slopes = [(b['u']-a['u'])/(b['position']-a['position']) for a,b in zip(points, points[1:])]
    qbar = sum(p['position'] for p in points)/3
    ubar = sum(p['u'] for p in points)/3
    slope = sum((p['position']-qbar)*(p['u']-ubar) for p in points)/sum((p['position']-qbar)**2 for p in points)
    intercept = ubar - slope*qbar
    residual = max(abs(p['u']-slope*p['position']-intercept) for p in points)
    a,b,c = points
    holdout = abs(c['u']-(a['u']+slopes[0]*(c['position']-a['position'])))
    if abs(slope)<.1 or any(s*slope<=0 for s in slopes) or residual>6 or holdout>10:
        raise ValueError('fresh_axis_response_model_rejected')
    return dict(pixel_u_per_count=slope, upright_yaw_sign=1 if slope<0 else -1,
                holdout_error_px=holdout, max_residual_px=residual,
                center_count_local_linear=(319.5-intercept)/slope)
