"""UART feedback has a bounded observation interval, not a sample timestamp.

The servo does not send a sample clock. Its register read happened somewhere
between the request and the complete response. Scheduling after reception must
not compress the motion interval. This does not repair or discard any position.
"""
import math


def interval(feedback):
    if feedback.get('time_basis') != 'REQUEST_RESPONSE_BRACKET':
        raise ValueError('Feedback observation bracket missing')
    a = feedback['observation_begin_monotonic']
    b = feedback['observation_end_monotonic']
    if (isinstance(a, bool) or isinstance(b, bool) or
            not math.isfinite(a) or not math.isfinite(b) or
            not 0 <= a <= b or b-a > .150):
        raise ValueError('Invalid or stale feedback observation bracket')
    return a, b


def motion_check(first, previous, current, target, speed):
    """Original position/speed/reversal limits; interval-correct displacement."""
    pa, pb = interval(previous)
    ca, cb = interval(current)
    if ca < pb:
        raise ValueError('Overlapping or reordered feedback transactions')
    direction = 1 if target >= first['position'] else -1
    delta = current['position'] - previous['position']
    # Maximum possible elapsed sample time, without pretending we know the
    # position's sensor acquisition time inside either UART transaction.
    elapsed_max = cb-pa
    elapsed_min = ca-pb
    limits = dict(outside_travel=not min(first['position'], target)-4 <= current['position'] <= max(first['position'], target)+4,
                  speed=abs(current['speed']) > speed*1.5,
                  reversed=delta*direction < -4,
                  discontinuity=abs(delta) > max(128, speed*2*elapsed_max))
    evidence = dict(delta_counts=delta, elapsed_min=elapsed_min, elapsed_max=elapsed_max,
                    max_displacement_counts=max(128, speed*2*elapsed_max), predicates=limits)
    if any(limits.values()):
        raise ValueError('Motion guard: '+','.join(k for k, v in limits.items() if v))
    return evidence


def frame_pose(frame, feedback, speed_bound=2700., max_gap=.150):
    """Estimated pose at camera read completion plus a conservative bound.

    Camera read timestamps do not establish exposure timestamps. The returned
    interval applies to read_end; callers must retain this scope explicitly.
    """
    t = frame['read_end']
    if not math.isfinite(t) or frame['read_begin'] > t:
        return None
    rows = []
    for f in feedback:
        if not f.get('full_angle'):
            continue
        try:
            a, b = interval(f)
        except (ValueError, KeyError):
            continue
        rows.append(((a+b)/2, a, b, f['position']))
    for left, right in zip(rows, rows[1:]):
        m0, a0, b0, p0 = left
        m1, a1, b1, p1 = right
        if not (m0 <= t <= m1 and b0 <= a1 and 0 < m1-m0 <= max_gap):
            continue
        estimate = p0+(p1-p0)*(t-m0)/(m1-m0)
        # Each observation alone gives a reachable position interval at t.
        # Their intersection retains both timing uncertainties.
        radius0 = speed_bound*max(abs(t-a0), abs(t-b0))
        radius1 = speed_bound*max(abs(t-a1), abs(t-b1))
        lo, hi = max(p0-radius0, p1-radius1), min(p0+radius0, p1+radius1)
        if lo > hi or not lo <= estimate <= hi:
            return None
        return dict(position=estimate, position_interval=[lo, hi],
                    maximum_error_counts=max(estimate-lo, hi-estimate),
                    time_scope='CAMERA_READ_END_NOT_EXPOSURE',
                    telemetry_scope='REQUEST_RESPONSE_BRACKET_ESTIMATE')
    return None
