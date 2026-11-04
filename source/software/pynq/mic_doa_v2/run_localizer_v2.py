"""Explicit, bounded PYNQ-Z2 PL-bearing capture. Import performs no board I/O."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

MANIFEST = 'runtime_manifest_localize_V2.json'
RUNTIME = ('mic_localize.bit', 'mic_localize.hwh', 'mic_capture_fast.py',
           'pl_snapshot.py', 'pl_reference.py', 'baseline_V1.py', 'direction.py',
           'component_coordinates_V20.csv', 'run_localizer_V2.py')


def check_bundle(folder):
    folder = Path(folder).resolve()
    manifest = json.loads((folder / MANIFEST).read_text(encoding='utf-8'))
    if manifest['schema'] != 1 or set(manifest['files']) != set(RUNTIME):
        raise ValueError('Unexpected runtime manifest')
    for name in RUNTIME:
        actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if actual != manifest['files'][name]:
            raise ValueError('Runtime SHA mismatch: ' + name)
    return manifest


def capture(folder, out, seconds):
    if not math.isfinite(seconds) or not 3 <= seconds <= 10:
        raise ValueError('seconds must be finite within 3..10')
    folder = Path(folder).resolve()
    out = Path(out).resolve()
    if out.exists():
        raise FileExistsError('Use a new output directory: ' + str(out))
    manifest = check_bundle(folder)
    if Path(__file__).resolve() != folder / 'run_localizer_V2.py':
        raise ValueError('Run the checked entry point from the bundle directory')
    if os.environ.get('BOARD') != 'Pynq-Z2':
        raise RuntimeError('Expected Pynq-Z2')
    identity = subprocess.check_output(['findmnt', '-no', 'UUID', '/'], text=True).strip()
    if identity != manifest['board_root_uuid']:
        raise RuntimeError('Target SD differs from this tested project')
    from pynq import PL, MMIO
    if PL.bitfile_name:
        active = Path(PL.bitfile_name)
        if not active.is_file() or hashlib.sha256(active.read_bytes()).hexdigest() not in manifest['allowed_active_bit_sha256']:
            raise RuntimeError('A different overlay is active')
        if MMIO(0x43C00000, 0x10000).read(4) != 0 or not MMIO(0x40400000, 0x10000).read(0x34) & 1:
            raise RuntimeError('Existing capture/DMA is active')
    sys.path.insert(0, str(folder))
    import mic_capture_fast as mic
    from pl_snapshot import read_snapshot
    from pl_reference import estimate
    from direction import read_positions
    started = time.perf_counter()
    frames = round(seconds * 31250)
    meta = mic.capture_board(folder / 'mic_localize.bit', out, frames, [0, 4, 6, 7], seconds + 10)
    if meta['status'] != 'CAPTURE_VERIFIED' or not all(meta[k] for k in ('clock_disabled', 'dma_reset_and_halted', 'buffer_freed')):
        raise RuntimeError('Capture/cleanup did not verify')
    snap = read_snapshot(MMIO(0x43C00000, 0x10000))
    coords = read_positions(folder / 'component_coordinates_V20.csv', [1, 5, 7, 8])
    ps_start = time.perf_counter()
    result = estimate(snap, coords)
    snap.update(ps_estimate=result, ps_estimate_ms=(time.perf_counter() - ps_start) * 1000,
                artifacts_sha256=manifest['files'],
                execution_split='PL FIR/full and quarter correlations/energy/peaks/temporal guard; PS interpolation/gates/geometry')
    with (out / 'pl_snapshot.json').open('x', encoding='utf-8') as f:
        json.dump(snap, f, indent=2, allow_nan=False)
    answer = {'status': 'VALID_BEARING' if result['valid'] else 'REJECTED_SIGNAL',
              'selected_channels': [1, 5, 7, 8], 'reasons': result['reasons'],
              'temporal_guard': result['temporal_guard'],
              'candidate_bearing_deg': result['candidate_bearing_deg'],
              'bearing_deg': result['fit']['bearing_deg_from_plus_y_toward_plus_x'] if result['valid'] else None,
              'reference_axis': '+y from MIC05 to MIC01; positive toward MIC08',
              'ps_estimate_ms': snap['ps_estimate_ms'], 'wall_seconds': time.perf_counter() - started,
              'raw_sha256': meta['raw_sha256'], 'frames': frames,
              'physical_bearing_accuracy': 'NOT_VALIDATED', 'range_m': None,
              'clock_disabled': True, 'dma_reset_and_halted': True, 'buffer_freed': True}
    with (out / 'localization.json').open('x', encoding='utf-8') as f:
        json.dump(answer, f, indent=2, allow_nan=False)
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'capture'))
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--seconds', type=float, default=3)
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            checked = check_bundle(args.bundle)
            print(json.dumps({'status': 'RUNTIME_SHA_PASS', 'files': len(checked['files'])}))
        else:
            if args.out is None:
                raise ValueError('capture requires --out with a new directory')
            print(json.dumps(capture(args.bundle, args.out, args.seconds), indent=2))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        mic = sys.modules.get('mic_capture_fast')
        if mic and mic._quarantined_buffers:
            print('DMA halt unconfirmed; keep this process alive until the board is stopped.', file=sys.stderr, flush=True)
            while True:
                time.sleep(1)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
