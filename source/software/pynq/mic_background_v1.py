"""Raw-only background acquisition; never reads an incomplete DOA snapshot.

The accepted ABI5 correlator needs raw input index 64052. Its unchanged
65536-frame localization entry remains in mic_doa_pmod_V5. This separate entry
captures 32768 post-warmup frames solely for the noise reference. A background
record is never a valid direction or a successful PL correlation result.
"""
import hashlib
from pathlib import Path
import time
from mic_doa_pmod_V5 import fingerprint

FRAMES = 32768


def capture_background(mic, bundle, manifest, out, hardware=None):
    if hardware is None:
        import pynq as hardware
    before = fingerprint(hardware, manifest)
    ctrl = hardware.MMIO(mic.CTRL_BASE, 0x10000)
    dma = hardware.MMIO(mic.DMA_BASE, 0x10000)
    if mic._quarantined_buffers:
        raise RuntimeError('Prior DMA buffer remains quarantined')
    if ctrl.read(0) != mic.ABI or ctrl.read(0x40) != 0x444f4105 or ctrl.read(0xa4) != 0x08040210:
        raise RuntimeError('Capture/DOA/FIR identity mismatch')
    if (ctrl.read(4) != 0 or ctrl.read(8) & 5 or dma.read(0x30) & 1
            or not dma.read(0x34) & 1 or dma.read(0x34) & 0x770):
        raise RuntimeError('Capture/DMA is not idle before background')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    meta = mic.base_metadata(FRAMES, [0, 4, 6, 7])
    meta.update(status='FAILED', valid_frames=0, board_capture='STARTED',
                analysis_usage='BACKGROUND_RAW_ONLY', direction_result_available=False,
                pl_snapshot_read=False, window_first_raw_index=0,
                bitstream=str(Path(bundle) / 'av_pmod.bit'),
                bit_sha256=manifest['files']['av_pmod.bit'],
                hwh_sha256=manifest['files']['av_pmod.hwh'],
                fclk0_mhz_readback=before['fclk0_mhz'], active_pl_before=before,
                overlay_downloaded=False, fir_id='0x08040210')
    mic.write_json(out / 'metadata.json', meta)
    buf = raw = failure = None
    completed = halted = False
    try:
        begin = time.monotonic()
        buf = hardware.allocate(shape=(FRAMES * 8,), dtype='int32')
        meta['allocate_seconds'] = time.monotonic() - begin
        meta['transaction_begin_monotonic'] = time.monotonic()
        transaction = mic.capture_transaction(ctrl, dma, buf, FRAMES, 13)
        meta['transaction_end_monotonic'] = time.monotonic()
        meta.update(transaction)
        if any(transaction.get(k) != v for k, v in
               [('captured_frames', FRAMES), ('sent_frames', FRAMES), ('dma_actual_bytes', FRAMES * 32)]):
            raise RuntimeError('Background transaction counts disagree')
        completed = True
    except BaseException as exc:
        failure = exc
        meta['error'] = repr(exc)
    finally:
        cleanup_begin = time.monotonic()
        try:
            ctrl.write(4, 0)
            mic.wait_until(lambda: not (ctrl.read(8) & 5), 1, 'Clock/capture disable failed')
            meta['clock_disabled'] = True
        except Exception as exc:
            meta['control_cleanup_error'] = repr(exc)
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
            meta['operator_action'] = 'Keep owner alive until DMA halt is independently established.'
        meta['cleanup_seconds'] = time.monotonic() - cleanup_begin
        try:
            meta['active_pl_after'] = fingerprint(hardware, manifest)
            if meta['active_pl_after'] != before:
                raise RuntimeError('Active PL/clock identity changed during background')
            if raw is not None:
                if completed and len(raw) != FRAMES * 32:
                    raise RuntimeError('Background buffer length mismatch')
                name = 'raw_i32le.bin' if completed else 'raw_unverified_i32le.bin'
                (out / name).write_bytes(raw)
                meta.update(raw_file=name, raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
                if completed:
                    mic.decode_raw(raw)
                    meta.update(valid_frames=FRAMES, duration_seconds=FRAMES / mic.FS,
                                reporting_state='RAW_ONLY', reporting_outputs_verified=False)
        except Exception as exc:
            meta['export_or_identity_error'] = repr(exc)
            failure = failure or exc
        meta.update(status='CAPTURE_VERIFIED' if completed and failure is None else 'FAILED',
                    board_capture='COMPLETED' if completed and failure is None else 'FAILED')
        mic.write_json(out / 'metadata.json', meta)
    if failure is not None:
        raise RuntimeError('Background failed; inspect ' + str(out / 'metadata.json')) from failure
    return meta
