"""Integer reference and geometry for positive, physical PL peak selection.

The FIR, 202 sums, window, geometry gates and temporal tolerance are unchanged.
This candidate selects measured maxima, never peaks fitted to an expected angle.
No hardware access. ABI3 is required for production snapshots.
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
VERSION = 'POSITIVE_PHYSICAL_PEAKS_V4'


def select_peaks(curves):
    r = np.asarray(curves, dtype=np.int64)
    if r.shape[-2:] != (6, 33):
        raise ValueError('Six signed64 curves with 33 bins required')
    allowed = np.abs(np.arange(-16, 17))[None, :] <= LIMITS[:, None]
    scores = np.where(allowed & (r > 0), r, 0)
    peaks = np.argmax(scores, axis=-1) - LAG
    return np.where(scores.max(axis=-1) > 0, peaks, INVALID)


def peak_guard(full, sub):
    full, sub = np.asarray(full, dtype=np.int64), np.asarray(sub, dtype=np.int64)
    if full.shape != (6,) or sub.shape != (4, 6):
        raise ValueError('Expected six full and four groups of six sub peaks')
    for row in [full, *sub]:
        if np.any((row != INVALID) & (np.abs(row) > LIMITS)):
            raise ValueError('Peak outside pair search domain')
    delta = np.abs(sub-full)
    matches = np.all((delta <= 1) & (full != INVALID) & (sub != INVALID), axis=1)
    support = int(matches.sum())
    return dict(match_mask=sum(int(x) << q for q, x in enumerate(matches)),
                support=support, stable=support >= 3,
                max_peak_delta_samples=delta.max(axis=1).tolist())


def features(samples, window=WINDOW):
    x = np.asarray(samples, dtype=np.int64)
    if x.ndim != 2 or x.shape[1] != 4 or len(x) < window+35:
        raise ValueError('Need four channels and window+35 frames')
    if window < 4 or window > WINDOW or window % 4:
        raise ValueError('Window must divide by four within 4..32768')
    if np.any(x < -8388608) or np.any(x > 8388607):
        raise ValueError('Not int24')
    a = np.pad(x >> 4, ((3, 0), (0, 0)))
    y = a[3:] + a[2:-1] - a[1:-2] - a[:-3]
    sub = np.zeros((4, 202), dtype=np.int64)
    size = window//4
    for q in range(4):
        start = CENTER+q*size
        for p, (i, j) in enumerate(itertools.combinations(range(4), 2)):
            for lag in range(-16, 17):
                sub[q, p*33+lag+16] = np.dot(y[start:start+size, i], y[start+lag:start+lag+size, j])
        for i in range(4):
            sub[q, 198+i] = np.dot(y[start:start+size, i], y[start:start+size, i])
    sums = sub.sum(axis=0)
    full = select_peaks(sums[:198].reshape(6, 33))
    small = select_peaks(sub[:, :198].reshape(4, 6, 33))
    return dict(sums_i64=sums.tolist(), integer_peaks=full.tolist(),
                subwindow_peaks=small.tolist(), guard=peak_guard(full, small))


def estimate(snapshot, positions):
    sums = np.asarray(snapshot['sums_i64'], dtype=np.int64)
    if sums.shape != (202,):
        raise ValueError('202 signed64 sums required')
    curves = sums[:198].reshape(6, 33)
    peaks = select_peaks(curves).tolist()
    if peaks != snapshot['integer_peaks']:
        raise ValueError('Positive physical PL peak/sums mismatch')
    guard = peak_guard(peaks, snapshot['subwindow_peaks'])
    if guard != snapshot['guard']:
        raise ValueError('PL temporal guard mismatch')
    limits = [float(np.linalg.norm(positions[j]-positions[i])*31250/343) for i, j in PAIRS]
    if np.ceil(limits).astype(int).tolist() != LIMITS.tolist():
        raise ValueError('Geometry does not match immutable PL pair domains')
    result = dict(valid=False, reasons=[], integer_peaks=peaks, temporal_guard=guard,
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
    if not guard['stable']: reasons.append('subwindow_delay_inconsistent')
    candidate = fit['bearing_deg_from_plus_y_toward_plus_x']
    result.update(valid=not reasons, reasons=reasons, lags=lags, strengths=strengths,
                  peak_contrast=contrasts, tone_shape_residuals=tone, fit=fit,
                  peak_policy_details=details, candidate_bearing_deg=candidate,
                  bearing_deg=candidate if not reasons else None)
    return result
