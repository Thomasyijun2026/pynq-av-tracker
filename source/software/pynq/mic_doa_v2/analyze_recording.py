"""Bounded offline bearing analysis of the V4 record; never connects to PYNQ."""
import argparse
import itertools
import json
from pathlib import Path
import numpy as np
from delay import measure_pairs
from direction import fit_direction, read_positions, sha256


def solve(samples, positions, band=(150, 7000)):
    pairs = list(itertools.combinations([1, 5, 7, 8], 2))
    measured = measure_pairs(samples, band=band)
    lags = [p['lag_samples'] for p in measured]
    weights = [p['coherence'] for p in measured]
    fit = fit_direction(positions, pairs, lags, weights)
    return {'band_hz': list(band), 'pairs': pairs, 'measurements': measured, 'estimate': fit,
            'equal_weight_estimate': fit_direction(positions, pairs, lags, np.ones(6))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--coordinates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    digest = sha256(args.raw)
    if digest != args.expected_sha256:
        raise ValueError('Raw data identity mismatch')
    raw = np.fromfile(args.raw, dtype='<i4')
    if raw.size % 8 or raw.size // 8 < 2 * 31250:
        raise ValueError('Expected at least two seconds of eight-channel int32 data')
    raw = raw.reshape(-1, 8)
    if np.any(raw < -8388608) or np.any(raw > 8388607):
        raise ValueError('Expected sign-extended 24-bit values')
    positions = read_positions(args.coordinates, [1, 5, 7, 8])
    samples = raw[31250:, [0, 4, 6, 7]].astype(float)
    if any(np.array_equal(samples[:, i], samples[:, j]) for i, j in itertools.combinations(range(4), 2)):
        raise ValueError('Selected channels contain exact duplicates')
    primary = solve(samples, positions)
    blocks = [solve(samples[start:start+31250], positions)
              for start in range(0, len(samples) - 31250 + 1, 31250)]
    # Prespecified sensitivity bands; do not select the one that matches the photo.
    sensitivity = [solve(samples, positions, band) for band in ((150, 1500), (300, 3000), (1500, 7000))]
    output = {'version': 'V2', 'status': 'OFFLINE_PROTOTYPE_PHYSICAL_ACCURACY_NOT_VALIDATED',
              'selected': [1, 5, 7, 8], 'fs_hz': 31250, 'discard_first_seconds': 1,
              'analyzed_frames': len(samples), 'assumed_sound_speed_mps': 343,
              'nominal_positions_m': {str(ch): pos.tolist() for ch, pos in positions.items()},
              'axes': 'V20 native TOP: +x toward MIC08; +y from MIC05 to MIC01',
              'photo_convention': 'Cable-left scene: 0deg toward far row/computer; positive to right',
              'algorithm': {'delay': 'Welch GCC-PHAT, conj(X_i)*X_j, j-later positive',
                            'nfft': 2048, 'overlap': 0.5, 'window': 'periodic Hann',
                            'per_channel_relative_power_floor_db': -60,
                            'lag_upsampling': 32, 'lag_search_samples': [-64, 64],
                            'geometry': 'Weighted LS on unconstrained planar direction projection',
                            'weights': 'Per-pair in-band power-weighted coherence'},
              'primary': primary, 'one_second_blocks': blocks, 'band_sensitivity': sensitivity,
              'block_bearings_deg': [b['estimate']['bearing_deg_from_plus_y_toward_plus_x'] for b in blocks],
              'provenance': {'raw': str(args.raw), 'raw_sha256': digest,
                             'coordinates': str(args.coordinates), 'coordinates_sha256': sha256(args.coordinates),
                             'sources_sha256': {n: sha256(Path(__file__).parent / n)
                                                for n in ('analyze_recording.py', 'direction.py', 'delay.py')}},
              'limitations': ['No measured source angle, acoustic centers or channel delay calibration',
                              'Fan may be extended or multiple sources and room reflections',
                              'Stationary repetition is not an accuracy bound',
                              'Projection is not a distance or elevation estimate',
                              'Photo only provides a coarse comparison; vent position is unseen']}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')
    print(json.dumps({'estimate': primary['estimate'], 'block_bearings_deg': output['block_bearings_deg'],
                      'band_sensitivity': [{'band': b['band_hz'], 'bearing': b['estimate']['bearing_deg_from_plus_y_toward_plus_x'],
                                            'residual_samples': b['estimate']['weighted_rms_residual_samples']} for b in sensitivity]}))


if __name__ == '__main__':
    main()
