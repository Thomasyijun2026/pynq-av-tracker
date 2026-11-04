"""First-trigger axis measurement: moving frames guide probes, never alignment.

The three stationary views and their original 200-count/6px/10px model gates
remain mandatory. No earlier boot or pre-existing axis file is an input.
"""
import math
from av_screen_sweep_V1 import LOW, HIGH, measure_axis
from av_pan_view_probe_V1 import stable

CONVENTION = 'UPRIGHT_IMAGE_ARRAY_Z_AND_SERVO_AXIS_UP'
SCOPE = 'THIS_INITIALIZATION_THREE_STATIONARY_MARKER_VIEWS'


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def probe_plan(first, records):
    q, u = first['position'], first['u']
    if type(q) is not int or not LOW <= q <= HIGH or not finite(u) or not 0 <= u <= 639:
        raise ValueError('invalid_stationary_probe_origin')
    hints = []
    for row in records:
        v, pose = row.get('vision', {}), row.get('pose', {})
        b = v.get('best') or {}
        p = pose.get('pose')
        uv = b.get('center_px')
        if (v.get('status') == 'KNOWN_SCREEN_MATCHED' and v.get('image_size') == [1280, 960]
                and pose.get('status') == 'SEARCH_HINT_ONLY' and pose.get('stationary_anchor') is False
                and finite(p) and LOW <= p <= HIGH and isinstance(uv, (list, tuple)) and len(uv) == 2
                and all(finite(x) for x in uv) and 0 <= uv[0] <= 1278):
            hints.append((p, uv[0] / 2))
    hints.sort()
    slope = None
    if len(hints) >= 2 and hints[-1][0] - hints[0][0] >= 64:
        slope = (hints[-1][1] - hints[0][1]) / (hints[-1][0] - hints[0][0])
        if abs(slope) < .1:
            slope = None
    if slope is None:
        direction = 1 if HIGH - q >= q - LOW else -1
        basis = 'MECHANICAL_MARGIN_ONLY'
    else:
        direction = 1 if (319.5 - u) / slope >= 0 else -1
        basis = 'CURRENT_SWEEP_SEARCH_HINT_ONLY'
    if not LOW <= q + direction * 256 <= HIGH:
        direction = -direction
    if not LOW <= q + direction * 256 <= HIGH:
        raise ValueError('insufficient_fixed_envelope_for_axis_probe')
    return dict(status='PROBE_PLAN_ONLY', direction=direction,
                targets=[q + direction * n for n in (128, 256)], basis=basis,
                slope_hint=slope, usable_for_alignment=False,
                requires_three_fresh_stationary_views=True)


def validate_fresh_axis(axis, boot, triggered, completed):
    if (axis.get('status') != 'VISUAL_PAN_RESPONSE_MEASURED' or axis.get('boot_id') != boot
            or axis.get('clock_domain') != 'BOARD_MONOTONIC' or axis.get('evidence_scope') != SCOPE
            or axis.get('yaw_convention') != CONVENTION):
        raise ValueError('fresh_current_trigger_axis_required')
    rows = axis.get('measurements')
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError('three_fresh_stationary_views_required')
    previous = triggered
    identifiers = set()
    for row in rows:
        q, frame = row['position'], row['frame']
        if (row.get('boot_id') != boot or row.get('clock_domain') != 'BOARD_MONOTONIC'
                or type(q) is not int or not LOW <= q <= HIGH
                or not finite(row['u']) or not 0 <= row['u'] <= 639):
            raise ValueError('axis_measurement_identity_or_geometry')
        vision = row.get('vision') or {}
        uv = (vision.get('best') or {}).get('center_px')
        if (vision.get('status') != 'KNOWN_SCREEN_MATCHED' or vision.get('image_size') != [1280, 960]
                or not isinstance(uv, (list, tuple)) or len(uv) != 2 or not all(finite(x) for x in uv)
                or abs(uv[0] / 2 - row['u']) > 1e-9):
            raise ValueError('axis_stationary_marker_geometry_mismatch')
        stable(row['before'], row['after'], q)
        times = [frame.get(k) for k in ('read_begin', 'read_end', 'write_end')]
        if (not all(finite(t) for t in times) or not previous <= times[0] <= times[1] <= times[2] <= completed
                or frame.get('image_rotation') != 180 or frame.get('time_basis') != 'DRAINED_READ_NOT_EXPOSURE'):
            raise ValueError('axis_measurement_not_fresh_stationary_read')
        sha = frame.get('sha256')
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('axis_frame_sha_required')
        number = frame.get('frame_number')
        if type(number) is not int or number <= 0 or number in identifiers:
            raise ValueError('axis_frame_identity_repeated')
        identifiers.add(number)
        previous = times[2]
    fit = measure_axis(rows)
    best = axis.get('best') or {}
    for key, value in fit.items():
        if not finite(best.get(key)) or abs(best[key] - value) > 1e-9:
            raise ValueError('axis_fit_not_from_saved_fresh_measurements')
    return fit
