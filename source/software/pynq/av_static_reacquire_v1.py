"""Bounded stationary search and probe plans; never an alignment certificate."""
import math
from av_screen_sweep_V1 import LOW, HIGH


def nearby(origin):
    if type(origin) is not int or not LOW <= origin <= HIGH:
        raise ValueError('stationary_search_origin_invalid')
    return [origin+d for d in (0,-128,128,-256,256,-384,384) if LOW <= origin+d <= HIGH]


def dither_targets(origin):
    direction=1 if HIGH-origin >= origin-LOW else -1
    return [origin+direction*d for d in (64,-64) if LOW <= origin+direction*d <= HIGH]


def center_hint(first, second):
    q0,q1=first['position'],second['position']
    if not all(type(q) is int and LOW <= q <= HIGH for q in (q0,q1)) or abs(q1-q0)<48:
        raise ValueError('stationary_probe_baseline_invalid')
    u0,u1=first['u'],second['u']
    if not all(type(u) in (int,float) and math.isfinite(u) and 0<=u<=639 for u in (u0,u1)):
        raise ValueError('stationary_probe_pixels_invalid')
    slope=(u1-u0)/(q1-q0)
    if abs(slope)<.1:raise ValueError('stationary_probe_response_too_small')
    delta=(319.5-u0)/slope
    if abs(delta)>512:raise ValueError('stationary_probe_center_outside_search_bound')
    unwrapped=round(q0+delta)
    equivalents=[unwrapped+k*4096 for k in (-1,0,1) if LOW<=unwrapped+k*4096<=HIGH]
    if not equivalents:raise ValueError('stationary_probe_no_fixed_range_return')
    target=min(equivalents,key=lambda q:abs(q-q1))
    return dict(status='STATIONARY_PROBE_SEARCH_HINT_ONLY',goal=target,
        unwrapped_goal=unwrapped,equivalent_branch_return=target!=unwrapped,
        slope_hint=slope,usable_for_alignment=False,search_radius_counts=512,
        source_frames=[first['frame']['sha256'],second['frame']['sha256']])


def axis_targets(center):
    if type(center) is not int or not LOW<=center<=HIGH:raise ValueError('axis_center_outside_fixed_range')
    low=max(LOW,min(center-128,HIGH-256))
    # End at the middle pose; all three are acquired anew, after settling.
    return [low,low+256,low+128]
