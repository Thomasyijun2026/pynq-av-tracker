"""ABI2: unchanged V1 features plus PL subwindow peak-consistency guard.

All numbers here are integer operations also implemented by doa_correlator.sv.
No target angle, previous output, or photo-derived direction is used.
"""
import itertools
import numpy as np
from baseline_V1 import estimate as estimate_baseline

CHANNELS = [1, 5, 7, 8]
PAIRS = list(itertools.combinations(CHANNELS, 2))
WINDOW = 32768
CENTER = 19
LAG = 16
SHIFT = 4
SUBWINDOWS = 4
PEAK_TOLERANCE = 1
MIN_SUPPORT = 3


def peak_guard(full_peaks, sub_peaks):
    full = np.asarray(full_peaks, dtype=np.int64)
    sub = np.asarray(sub_peaks, dtype=np.int64)
    if full.shape != (6,) or sub.shape != (4, 6):
        raise ValueError('Expected six full peaks and four groups of six subwindow peaks')
    if np.any(np.abs(full) > 16) or np.any(np.abs(sub) > 16):
        raise ValueError('Out-of-range peak')
    differences = np.abs(sub - full)
    matches = np.all(differences <= PEAK_TOLERANCE, axis=1)
    mask = sum(int(v) << i for i, v in enumerate(matches))
    support = int(matches.sum())
    return {'match_mask': mask, 'support': support, 'stable': support >= MIN_SUPPORT,
            'max_peak_delta_samples': differences.max(axis=1).tolist()}


def features(samples, window=WINDOW):
    x = np.asarray(samples, dtype=np.int64)
    if x.ndim != 2 or x.shape[1] != 4 or len(x) < window + 35:
        raise ValueError('Need four channels and window+35 input frames')
    if window < 4 or window > 32768 or window % 4:
        raise ValueError('Window must be divisible by four within 4..32768')
    if np.any(x < -8388608) or np.any(x > 8388607):
        raise ValueError('Not int24')
    a = x >> SHIFT
    padded = np.pad(a, ((3, 0), (0, 0)))
    y = padded[3:] + padded[2:-1] - padded[1:-2] - padded[:-3]
    sub = np.zeros((4, 202), dtype=np.int64)
    size = window // 4
    for q in range(4):
        start = CENTER + q * size
        for p, (i, j) in enumerate(itertools.combinations(range(4), 2)):
            base = y[start:start+size, i]
            for k in range(-LAG, LAG+1):
                sub[q, p*33+k+LAG] = np.dot(base, y[start+k:start+k+size, j])
        for i in range(4):
            base = y[start:start+size, i]
            sub[q, 198+i] = np.dot(base, base)
    total = sub.sum(axis=0)
    full_peaks = np.argmax(np.abs(total[:198].reshape(6,33)), axis=1) - LAG
    sub_peaks = np.argmax(np.abs(sub[:,:198].reshape(4,6,33)), axis=2) - LAG
    return {'sums_i64': total.tolist(), 'integer_peaks': full_peaks.tolist(),
            'subwindow_peaks': sub_peaks.tolist(), 'guard': peak_guard(full_peaks, sub_peaks)}


def estimate(snapshot, positions):
    sums = np.asarray(snapshot['sums_i64'], dtype=np.int64)
    if sums.shape != (202,):
        raise ValueError('202 signed64 features required')
    peaks = (np.argmax(np.abs(sums[:198].reshape(6,33)), axis=1)-LAG).tolist()
    if peaks != snapshot['integer_peaks']:
        raise ValueError('Full peak/sums mismatch')
    guard = peak_guard(peaks, snapshot['subwindow_peaks'])
    if guard != snapshot['guard']:
        raise ValueError('Guard mismatch')
    result = estimate_baseline(sums, positions)
    result['baseline_valid'] = result['valid']
    result['temporal_guard'] = guard
    if not guard['stable']:
        result['reasons'].append('subwindow_delay_inconsistent')
        result['valid'] = False
    result['bearing_deg'] = (result['fit']['bearing_deg_from_plus_y_toward_plus_x']
                             if result['valid'] else None)
    # Invalid candidates remain visible for diagnosis, never presented as a current fix.
    result['candidate_bearing_deg'] = result.get('fit', {}).get('bearing_deg_from_plus_y_toward_plus_x')
    return result
