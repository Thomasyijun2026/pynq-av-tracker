"""Fractional-window stability policy for ABI5; no hardware or reference angle.

PL computes the 4 x 202 integer features. PS interpolates each measured peak
and tests geometry. The 10-degree temporal diameter is an engineering output
stability gate, not a physical accuracy specification. No energy-weighted
selection or preferred scene direction is used.
"""
import itertools
import math
import numpy as np
from audio_peak_reference_V4 import select_peaks, peak_guard
from audio_tolerance_V1 import apply_tolerance, estimate as full_estimate
from audio_curve_geometry_V1 import estimate_window
from direction import fit_direction

VERSION = 'FRACTIONAL_SUBWINDOW_CONSENSUS_V1'
MAX_DIAMETER_DEG = 10.0
MIN_SUPPORT = 3
CHANNELS = [1, 5, 7, 8]
PAIRS = list(itertools.combinations(CHANNELS, 2))


def separation(a, b):
    return abs((float(a)-float(b)+180.0) % 360.0-180.0)


def consensus(rows):
    eligible = [i for i, r in enumerate(rows) if r['valid']]
    candidates = []
    for count in range(len(eligible), MIN_SUPPORT-1, -1):
        for group in itertools.combinations(eligible, count):
            diameter = max(separation(rows[i]['bearing_deg'], rows[j]['bearing_deg'])
                           for i, j in itertools.combinations(group, 2))
            if diameter <= MAX_DIAMETER_DEG:
                candidates.append((diameter, group))
        if candidates:
            break
    return min(candidates)[1] if candidates else ()


def estimate(snapshot, positions):
    if snapshot.get('extension_abi') != '0x444f4105':
        raise ValueError('ABI5 immutable subwindow evidence required')
    raw = snapshot['subwindow_sums_i64']
    if len(raw) != 4 or any(len(row) != 202 for row in raw):
        raise ValueError('Four banks of 202 signed64 values required')
    if any(isinstance(x, bool) or not isinstance(x, (int, np.integer)) or
           not -(1 << 63) <= int(x) < (1 << 63) for row in raw for x in row):
        raise ValueError('Invalid signed64 subwindow values')
    # Python integer sum avoids hiding overflow or a torn read in np.int64.
    totals = [sum(int(row[k]) for row in raw) for k in range(202)]
    if totals != list(snapshot['sums_i64']):
        raise ValueError('Subwindow sum conservation failed')
    sub = np.asarray(raw, dtype=np.int64)
    peaks = select_peaks(sub[:, :198].reshape(4, 6, 33)).tolist()
    if peaks != snapshot['subwindow_peaks']:
        raise ValueError('Subwindow peak readback mismatch')
    if peak_guard(snapshot['integer_peaks'], peaks) != snapshot['guard']:
        raise ValueError('Temporal guard readback mismatch')
    whole = full_estimate(snapshot, positions)
    rows = [apply_tolerance(estimate_window(row, positions)) for row in raw]
    group = consensus(rows)
    reasons = list(whole['reasons'])
    if sum(r['valid'] for r in rows) < MIN_SUPPORT:
        reasons.append('insufficient_fractional_windows')
    elif not group:
        reasons.append('fractional_direction_unstable')
    result = dict(valid=False, bearing_deg=None, reasons=reasons,
                  coordinate_frame='ARRAY_FIXED', policy_version=VERSION,
                  physical_bearing_accuracy='NOT_VALIDATED',
                  temporal_diameter_limit_deg=MAX_DIAMETER_DEG,
                  required_support=MIN_SUPPORT, selected_windows=list(group),
                  full_window_assessment=whole, subwindow_assessments=rows,
                  temporal_guard=snapshot['guard'],
                  candidate_bearing_deg=None, fit=None,
                  tolerance_fraction=whole['tolerance_fraction'],
                  strict_assessment=whole['strict_assessment'],
                  geometry_candidate=None, quality_grade='REJECTED')
    if not group:
        return result
    diameter = max(separation(rows[i]['bearing_deg'], rows[j]['bearing_deg'])
                   for i, j in itertools.combinations(group, 2))
    # Equal temporal weight: a short high-energy transient cannot dominate.
    lags = np.median([rows[i]['lags'] for i in group], axis=0)
    strengths = np.mean([rows[i]['strengths'] for i in group], axis=0)
    fit = fit_direction(positions, PAIRS, lags, np.maximum(strengths, 1e-6))
    candidate = float(fit['bearing_deg_from_plus_y_toward_plus_x'])
    if not fit['all_measured_delays_within_physical_limits']:
        reasons.append('consensus_nonphysical_delay')
    if fit['weighted_rms_residual_samples'] > .6:
        reasons.append('consensus_inconsistent_geometry')
    if max(abs(c['closure_error_samples']) for c in fit['cycle_closures']) > 1.2:
        reasons.append('consensus_inconsistent_delay_cycle')
    if not .12 < fit['projected_norm'] < 1.1:
        reasons.append('consensus_projection_ill_defined')
    if any(separation(candidate, rows[i]['bearing_deg']) > MAX_DIAMETER_DEG for i in group):
        reasons.append('consensus_direction_disagreement')
    if not math.isfinite(candidate):
        raise ValueError('Non-finite consensus bearing')
    result.update(valid=not reasons, reasons=reasons,
                  bearing_deg=candidate if not reasons else None,
                  geometry_candidate=fit, temporal_diameter_deg=diameter,
                  candidate_bearing_deg=candidate, fit=fit,
                  median_lags=lags.tolist(), strengths=strengths.tolist(),
                  quality_grade='FRACTIONAL_CONSENSUS_PASS' if not reasons else 'REJECTED')
    return result
