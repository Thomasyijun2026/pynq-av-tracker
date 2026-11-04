"""Offline, uncalibrated planar-array bearing from independently measured TDOAs.

Coordinates: native V20 TOP, +x toward MIC08, +y from MIC05 to MIC01.
For the 2026-10-28 cable-left photo, 0 degrees faces the computer; positive
angles turn right. This is a plane-wave projection, not a range estimator.
"""
import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_positions(path, selected):
    positions = {}
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        rows = {r['reference']: r for r in csv.DictReader(f)}
    for ch in selected:
        row = rows['J' + str(ch + 1)]
        positions[ch] = np.array([float(row['native_x_mm']), float(row['native_y_mm'])]) / 1000
    return positions


def fit_direction(positions, pairs, delays_samples, weights, fs=31250, sound_speed=343):
    """Positive delay means j arrives after i; tau_ji=-(r_j-r_i).u/c."""
    if not np.isfinite(fs) or fs <= 0 or not np.isfinite(sound_speed) or sound_speed <= 0:
        raise ValueError('Positive finite sample rate and sound speed required')
    a = np.array([-(positions[j] - positions[i]) * fs / sound_speed for i, j in pairs])
    d, w = np.asarray(delays_samples, dtype=float), np.asarray(weights, dtype=float)
    if a.shape != (len(d), 2) or w.shape != d.shape or not len(d):
        raise ValueError('Expected planar coordinates and one delay/weight per pair')
    if not all(np.isfinite(x).all() for x in (a, d, w)) or np.any(w <= 0):
        raise ValueError('Inputs must be finite and weights strictly positive')
    aw = a * np.sqrt(w[:, None])
    if np.linalg.matrix_rank(aw) < 2:
        raise ValueError('Collinear geometry cannot determine both in-plane components')
    u = np.linalg.lstsq(aw, d * np.sqrt(w), rcond=None)[0]
    predicted = a @ u
    residual = d - predicted
    norm = float(np.linalg.norm(u))
    # Do not normalize before fitting or silently force an in-plane sound source.
    angle = float(np.degrees(np.arctan2(u[0], u[1]))) if norm > 1e-8 else None
    limit = np.linalg.norm(a, axis=1)
    lag_map = {(i, j): float(v) for (i, j), v in zip(pairs, d)}
    lag_map.update({(j, i): -v for (i, j), v in list(lag_map.items())})
    cycles = []
    for i, j, k in itertools.combinations(sorted(positions), 3):
        if all(p in lag_map for p in ((i, j), (j, k), (i, k))):
            cycles.append({'channels': [i, j, k],
                           'closure_error_samples': lag_map[i, j] + lag_map[j, k] - lag_map[i, k]})
    return {'bearing_deg_from_plus_y_toward_plus_x': angle,
            'projected_direction_xy': u.tolist(), 'projected_norm': norm,
            'plane_wave_projection_feasible': norm <= 1.0,
            'weighted_rms_residual_samples': float(np.sqrt(np.sum(w * residual**2) / np.sum(w))),
            'max_abs_residual_samples': float(np.max(np.abs(residual))),
            'weighted_geometry_condition': float(np.linalg.cond(aw)),
            'pair_prediction_samples': predicted.tolist(), 'pair_residual_samples': residual.tolist(),
            'physical_delay_limits_samples': limit.tolist(),
            'all_measured_delays_within_physical_limits': bool(np.all(np.abs(d) <= limit)),
            'cycle_closures': cycles,
            'physical_bearing_accuracy': 'NOT_VALIDATED', 'range_m': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--coordinates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.analysis.read_text(encoding='utf-8'))
    selected = source['selected']
    if selected != [1, 5, 7, 8]:
        raise ValueError('This V1 experiment is authorized for MIC01/05/07/08 only')
    positions = read_positions(args.coordinates, selected)
    measured = source['pairs']
    pairs = [tuple(p['channels']) for p in measured]
    if set(pairs) != set(itertools.combinations(selected, 2)) or len(pairs) != 6:
        raise ValueError('Six unique ordered pairs required')
    delays = [p['gcc_phat_lag_j_after_i_samples_search_pm64'] for p in measured]
    weights = [p['power_weighted_coherence_150_7000'] for p in measured]
    blocks = [p['one_second_block_lags_samples'] for p in measured]
    if len(set(map(len, blocks))) != 1 or len(blocks[0]) < 2:
        raise ValueError('Aligned time blocks required')
    estimate = fit_direction(positions, pairs, delays, weights)
    block_results = [fit_direction(positions, pairs, values, weights) for values in zip(*blocks)]
    angles = [r['bearing_deg_from_plus_y_toward_plus_x'] for r in block_results]
    center = estimate['bearing_deg_from_plus_y_toward_plus_x']
    offsets = [float((v - center + 180) % 360 - 180) for v in angles] if center is not None and None not in angles else None
    result = {'selected': selected, 'coordinate_axes': '+x toward MIC08; +y from MIC05 to MIC01',
              'photo_orientation': '2026-10-28 cable-left: +x right, +y toward far row/computer',
              'fs_hz': 31250, 'assumed_sound_speed_mps': 343,
              'nominal_positions_m': {str(k): v.tolist() for k, v in positions.items()},
              'pairs': pairs, 'weights': weights, 'delays_samples': delays,
              'model': 'weighted least squares of tau_ji=-(r_j-r_i).u/c; u is an unconstrained 2D projection',
              'estimate': estimate, 'one_second_blocks': block_results,
              'block_bearings_deg': angles, 'block_offsets_from_full_estimate_deg': offsets,
              'equal_weight_sensitivity': fit_direction(positions, pairs, delays, np.ones(6)),
              'provenance': {'analysis': str(args.analysis), 'analysis_sha256': sha256(args.analysis),
                             'coordinates': str(args.coordinates), 'coordinates_sha256': sha256(args.coordinates),
                             'script_sha256': sha256(__file__), 'raw_sha256': source['fan']['raw_sha256']},
              'limitations': ['Static fan and photo are not a surveyed source position or on/off test',
                              'Geometry, acoustic centers and channel timing are uncalibrated',
                              'A planar array cannot distinguish reflection across its plane',
                              'Low residual or repeatability does not establish physical accuracy',
                              'No range or elevation result is reported']}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
    print(json.dumps({'estimate': estimate, 'block_bearings_deg': angles,
                      'equal_weight_bearing': result['equal_weight_sensitivity']['bearing_deg_from_plus_y_toward_plus_x']}))


if __name__ == '__main__':
    main()
