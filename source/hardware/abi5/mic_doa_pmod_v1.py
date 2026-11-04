"""PMOD candidate V1: 65536 frames; original V2 audio logic plus independent UART.

No Overlay constructor or bitstream download. Original release helpers remain
unchanged; DMA storage stays alive if cleanup cannot establish a halted engine.
"""
import hashlib
import json
from pathlib import Path
import time


def fingerprint(hardware, manifest):
    active = Path(hardware.PL.bitfile_name)
    digest = hashlib.sha256(active.read_bytes()).hexdigest()
    if digest != manifest['files']['av_pmod.bit']:
        raise RuntimeError('Active PL is not the reviewed PMOD candidate bitstream')
    clock = float(hardware.Clocks.fclk0_mhz)
    if abs(clock - 100) > .001:
        raise RuntimeError('FCLK0 must be 100 MHz')
    return {'bitfile_name': str(active), 'bit_sha256': digest,
            'loaded_timestamp': str(hardware.PL.timestamp), 'fclk0_mhz': clock}


def capture_active(mic, bundle, manifest, out, frames=65536, hardware=None):
    if frames != 65536:
        raise ValueError('This entry is verified for 65536 frames per cycle')
    if hardware is None:
        import pynq as hardware
    before = fingerprint(hardware, manifest)
    ctrl = hardware.MMIO(mic.CTRL_BASE, 0x10000)
    dma = hardware.MMIO(mic.DMA_BASE, 0x10000)
    if mic._quarantined_buffers:
        raise RuntimeError('Prior DMA buffer remains quarantined')
    if ctrl.read(0) != mic.ABI or ctrl.read(0x40) != 0x444f4102:
        raise RuntimeError('Capture/DOA ABI mismatch')
    if (ctrl.read(4) != 0 or ctrl.read(8) & 5 or dma.read(0x30) & 1
            or not dma.read(0x34) & 1 or dma.read(0x34) & 0x770):
        raise RuntimeError('Capture/DMA is not idle before this cycle')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    meta = mic.base_metadata(frames, [0, 4, 6, 7])
    meta.update(status='FAILED', valid_frames=0, board_capture='STARTED',
                bitstream=str(Path(bundle)/'av_pmod.bit'),
                bit_sha256=manifest['files']['av_pmod.bit'],
                hwh_sha256=manifest['files']['av_pmod.hwh'],
                fclk0_mhz_readback=before['fclk0_mhz'], active_pl_before=before,
                overlay_downloaded=False)
    mic.write_json(out/'metadata.json', meta)
    buf = None
    raw = None
    failure = None
    completed = False
    halted = False
    try:
        phase_begin = time.monotonic()
        buf = hardware.allocate(shape=(frames*8,), dtype='int32')
        meta['allocate_seconds'] = time.monotonic()-phase_begin
        meta['transaction_begin_monotonic'] = time.monotonic()
        meta.update(mic.capture_transaction(ctrl, dma, buf, frames, 13))
        meta['transaction_end_monotonic'] = time.monotonic()
        completed = True
    except BaseException as exc:
        failure = exc
        meta['error'] = repr(exc)
    finally:
        cleanup_begin = time.monotonic()
        try:
            meta['ctrl_before_cleanup'] = {hex(r): ctrl.read(r) for r in (8, 0x10, 0x14, 0x18, 0x28)}
        except Exception as exc:
            meta['control_snapshot_error'] = repr(exc)
            failure = failure or exc
        try:
            ctrl.write(4, 0)
            mic.wait_until(lambda: not(ctrl.read(8) & 5), 1, 'Clock/capture disable failed')
            meta['clock_disabled'] = True
        except Exception as exc:
            meta['control_cleanup_error'] = repr(exc)
            failure = failure or exc
        try:
            meta['dma_before_cleanup'] = {hex(r): dma.read(r) for r in (0x30, 0x34, 0x58)}
        except Exception as exc:
            meta['dma_snapshot_error'] = repr(exc)
            failure = failure or exc
        try:
            mic.reset_dma(dma)
            halted = True
            meta['dma_reset_and_halted'] = True
        except Exception as exc:
            meta['dma_cleanup_error'] = repr(exc)
            failure = failure or exc
        if buf is not None and halted:
            try:
                buf.invalidate()
                raw = buf.astype('<i4', copy=False).tobytes()
            except Exception as exc:
                meta['buffer_read_error'] = repr(exc)
                failure = failure or exc
            finally:
                try:
                    buf.freebuffer()
                    meta['buffer_freed'] = True
                except Exception as exc:
                    meta['buffer_free_error'] = repr(exc)
                    failure = failure or exc
        elif buf is not None:
            mic._quarantined_buffers.append(buf)
            meta['buffer_freed'] = False
            meta['operator_action'] = 'Keep this kernel alive until DMA halt is independently established.'
        meta['cleanup_seconds'] = time.monotonic()-cleanup_begin
        try:
            identity_begin = time.monotonic()
            meta['active_pl_after'] = fingerprint(hardware, manifest)
            meta['identity_after_seconds'] = time.monotonic()-identity_begin
            if meta['active_pl_after'] != before:
                raise RuntimeError('Active PL/clock identity changed during capture')
            if raw is not None:
                name = 'raw_i32le.bin' if completed else 'raw_unverified_i32le.bin'
                (out/name).write_bytes(raw)
                meta.update(raw_file=name, raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
                if completed:
                    mic.decode_raw(raw)  # Validate signed sample representation now.
                    meta.update(valid_frames=frames, duration_seconds=frames/mic.FS,
                                reporting_state='DEFERRED', reporting_outputs_verified=False)
        except Exception as exc:
            meta['export_or_identity_error'] = repr(exc)
            failure = failure or exc
        if completed and failure is None:
            meta.update(status='CAPTURE_VERIFIED', board_capture='COMPLETED')
        else:
            meta.update(status='FAILED', board_capture='FAILED')
        mic.write_json(out/'metadata.json', meta)
    if failure is not None:
        raise RuntimeError('Active capture failed; inspect '+str(out/'metadata.json')) from failure
    return meta


def localize(mic, bundle, manifest, out):
    from pynq import MMIO
    from pl_snapshot import read_snapshot
    from pl_reference import estimate
    from direction import read_positions
    started = time.monotonic()
    out = Path(out)
    meta = capture_active(mic, bundle, manifest, out)
    snapshot_begin = time.monotonic()
    snap = read_snapshot(MMIO(mic.CTRL_BASE, 0x10000))
    snapshot_seconds = time.monotonic()-snapshot_begin
    coords = read_positions(Path(bundle)/'component_coordinates_V20.csv', [1, 5, 7, 8])
    ps_start = time.monotonic()
    result = estimate(snap, coords)
    ms = (time.monotonic()-ps_start)*1000
    snap.update(ps_estimate=result, ps_estimate_ms=ms, artifacts_sha256=manifest['files'],
                execution_split='PL FIR/correlations/energy/peaks/guard; PS interpolation/gates/geometry')
    answer = {'status': 'VALID_BEARING' if result['valid'] else 'REJECTED_SIGNAL',
              'selected_channels': [1, 5, 7, 8], 'reasons': result['reasons'],
              'temporal_guard': result['temporal_guard'],
              'candidate_bearing_deg': result['candidate_bearing_deg'],
              'bearing_deg': result['fit']['bearing_deg_from_plus_y_toward_plus_x'] if result['valid'] else None,
              'reference_axis': '+y from MIC05 to MIC01; positive toward MIC08',
              'reporting_state_at_output': meta['reporting_state'],
              'snapshot_seconds': snapshot_seconds, 'ps_estimate_ms': ms, 'wall_seconds': time.monotonic()-started,
              'raw_sha256': meta['raw_sha256'], 'frames': 65536, 'coordinate_frame':'ARRAY_FIXED',
              'physical_bearing_accuracy': 'NOT_VALIDATED', 'range_m': None,
              'clock_disabled': True, 'dma_reset_and_halted': True, 'buffer_freed': True,
              'output_monotonic': time.monotonic(), 'overlay_downloaded': False}
    for name, value in [('pl_snapshot.json', snap), ('localization.json', answer)]:
        with (out/name).open('x') as f:
            json.dump(value, f, indent=2, allow_nan=False)
    return answer


def finalize_reporting(mic, out):
    """Finish non-live artifacts from immutable PCM, without accessing hardware."""
    out = Path(out)
    path = out/'metadata.json'
    meta = json.loads(path.read_text())
    if meta.get('reporting_state') != 'DEFERRED':
        raise RuntimeError('Reporting was not pending; refuse duplicate finalization')
    if (meta['status'] != 'CAPTURE_VERIFIED' or not meta['clock_disabled']
            or not meta['dma_reset_and_halted'] or not meta['buffer_freed']):
        raise RuntimeError('Capture has not completed and released its resources')
    begin = time.monotonic()
    try:
        raw = (out/meta['raw_file']).read_bytes()
        if len(raw) != 65536*32 or hashlib.sha256(raw).hexdigest() != meta['raw_sha256']:
            raise RuntimeError('Saved PCM changed before reporting')
        samples = mic.decode_raw(raw)
        if (out/'selected.wav').exists():
            raise RuntimeError('Unexpected existing WAV; inspect before repeating')
        mic.write_wav(out/'selected.wav', samples, [0, 4, 6, 7])
        meta['statistics'] = mic.statistics(samples)
        meta.update(reporting_state='COMPLETE', reporting_outputs_verified=True,
                    reporting_seconds=time.monotonic()-begin)
    except BaseException as exc:
        meta.update(reporting_state='FAILED', reporting_outputs_verified=False,
                    reporting_error=repr(exc))
        mic.write_json(path, meta)
        raise
    mic.write_json(path, meta)
    return {'directory': out.name, 'seconds': meta['reporting_seconds'],
            'raw_sha256': meta['raw_sha256'], 'reporting_state': meta['reporting_state']}
