"""Single-window geometry extracted unchanged from the measured-peak policy.
No invented temporal samples or guard evidence.
"""
import itertools
import numpy as np
from direction import fit_direction

CHANNELS = [1, 5, 7, 8]
PAIRS = list(itertools.combinations(CHANNELS, 2))
LIMITS = np.array([4, 7, 10, 6, 9, 3], dtype=np.int64)
LAG = 16
WINDOW = 32768
CENTER = 19
INVALID = -128
VERSION = 'SINGLE_WINDOW_GEOMETRY_V1'


from audio_peak_reference_V4 import select_peaks

def estimate_window(values, positions):
    sums = np.asarray(values, dtype=np.int64)
    if sums.shape != (202,):
        raise ValueError("202 signed64 sums required")
    curves = sums[:198].reshape(6, 33)
    peaks = select_peaks(curves).tolist()
    limits = [float(np.linalg.norm(positions[j]-positions[i])*31250/343) for i, j in PAIRS]
    if np.ceil(limits).astype(int).tolist() != LIMITS.tolist():
        raise ValueError('Geometry does not match immutable PL pair domains')
    result = dict(valid=False, reasons=[], integer_peaks=peaks, temporal_assessment="NOT_APPLICABLE_SINGLE_WINDOW",
                  policy_version=VERSION, coordinate_frame='ARRAY_FIXED',
                  bearing_deg=None, candidate_bearing_deg=None, physical_bearing_accuracy='NOT_VALIDATED')
    energy = sums[198:].astype(float)
    if np.any(energy <= 0):
        result['reasons'] = ['zero_filtered_energy']
        return result
    if INVALID in peaks:
        result['reasons'] = ['no_positive_physical_peak']
        return result
    lags, strengths, contrasts, tone, details = [], [], [], [], []
    for p, (i, j) in enumerate(itertools.combinations(range(4), 2)):
        r = curves[p].astype(float)
        pk = peaks[p]+16
        left, mid, right = r[pk-1:pk+2]
        den = left-2*mid+right
        fraction = float(np.clip(.5*(left-right)/den, -.5, .5)) if den else 0.
        lags.append(peaks[p]+fraction)
        strengths.append(float(min(1., mid/np.sqrt(energy[i]*energy[j]))))
        bins = np.arange(-16, 17)
        allowed = (abs(bins) <= LIMITS[p]) & (abs(bins-peaks[p]) > 4) & (r > 0)
        alt = float(r[allowed].max()) if allowed.any() else 0.
        contrasts.append(float(1-alt/mid))
        sequence, neighbours = r[1:-1], r[:-2]+r[2:]
        divisor = float(sequence@sequence)
        factor = float(sequence@neighbours)/divisor if divisor else 0.
        tone.append(float(np.linalg.norm(neighbours-factor*sequence)/max(np.linalg.norm(r), 1.)))
        details.append(dict(physical_limit_samples=limits[p], search_limit_integer=int(LIMITS[p]),
                            peak_signed_height=float(mid), alternative_signed_height=alt))
    fit = fit_direction(positions, PAIRS, lags, np.maximum(strengths, 1e-6))
    reasons = []
    if min(strengths) < .2: reasons.append('weak_pair_correlation')
    if min(contrasts) < .08: reasons.append('ambiguous_peak')
    if max(tone) < .001: reasons.append('near_single_tone')
    if not fit['all_measured_delays_within_physical_limits']: reasons.append('nonphysical_delay')
    if fit['weighted_rms_residual_samples'] > .5: reasons.append('inconsistent_geometry')
    if max(abs(c['closure_error_samples']) for c in fit['cycle_closures']) > 1.: reasons.append('inconsistent_delay_cycle')
    if not .12 < fit['projected_norm'] < 1.1: reasons.append('projection_ill_defined')
    candidate = fit['bearing_deg_from_plus_y_toward_plus_x']
    result.update(valid=not reasons, reasons=reasons, lags=lags, strengths=strengths,
                  peak_contrast=contrasts, tone_shape_residuals=tone, fit=fit,
                  peak_policy_details=details, candidate_bearing_deg=candidate,
                  bearing_deg=candidate if not reasons else None)
    return result
