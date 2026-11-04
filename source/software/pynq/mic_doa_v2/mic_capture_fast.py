"""PYNQ-Z2 microphone capture ABI1. Import and offline commands never load PL.

Derived exporter: mic_capture_fast V1; capture/DMA/cleanup functions preserved.
Raw: frame-major, channels0..7, little-endian signed int32, sign-extended int24.
WAV: selected channels, PCM24 (no normalization), actual sample rate31250Hz.
"""
from __future__ import annotations
import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import wave
import numpy as np

FS = 31250
BCLK = 2000000
MAX_FRAMES = 0x1FFFFF
CTRL_BASE = 0x43C00000
DMA_BASE = 0x40400000
ABI = 0x4D494301
CHANNEL_MAP = [dict(channel=i, data=i//2, slot='left' if i%2==0 else 'right',
                    connector='J'+str(i+2), module='MIC%02d'%(i+1)) for i in range(8)]

def channels_arg(value):
    result = [int(s) for s in value.split(',')]
    if not result or len(set(result)) != len(result) or any(i not in range(8) for i in result):
        raise ValueError('channels must be unique indices0..7, e.g.0 or0,1 or0,1,2,3,4,5,6,7')
    return result

def decode_raw(raw):
    if not raw or len(raw)%32:
        raise ValueError('raw length must be a positive multiple of32bytes/frame')
    samples=np.frombuffer(raw,dtype='<i4')
    if np.any(samples < -8388608) or np.any(samples > 8388607):
        raise ValueError('invalid int24-to-int32 sign extension')
    return samples

def write_wav(path, samples, channels, sample_rate=FS):
    if not channels or len(set(channels))!=len(channels) or any(c not in range(8) for c in channels):
        raise ValueError('invalid channels')
    matrix=np.asarray(samples,dtype='<i4').reshape(-1,8)
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(len(channels));wav.setsampwidth(3);wav.setframerate(sample_rate)
        for start in range(0,len(matrix),8192):
            block=np.ascontiguousarray(matrix[start:start+8192,channels],dtype='<i4')
            packed=block.view(np.uint8).reshape(-1,4)[:,:3].copy()
            wav.writeframesraw(packed.tobytes())
        wav.writeframes(b'')

def statistics(samples):
    matrix=np.asarray(samples,dtype=np.int64).reshape(-1,8)
    n=len(matrix)
    result=[]
    for c in range(8):
        values=matrix[:,c]
        mean=int(values.sum(dtype=np.int64))/n
        squares=values*values
        # Exact arbitrary-width total without int64 overflow at MAX_FRAMES.
        total=(int((squares>>32).sum(dtype=np.int64))<<32)+int((squares&0xffffffff).sum(dtype=np.int64))
        square=total/n
        rms=math.sqrt(square)
        result.append(dict(channel=c,min=int(values.min()),max=int(values.max()),mean=mean,rms=rms,
                           ac_rms=math.sqrt(max(0,square-mean*mean)),
                           rms_dbfs=20*math.log10(rms/8388608) if rms else None,
                           zeros=int(np.count_nonzero(values==0)),
                           clipped=int(np.count_nonzero((values==-8388608)|(values==8388607)))))
    return result

def base_metadata(frames,channels):
    return dict(schema='mic_debug_V1',created_utc=datetime.now(timezone.utc).isoformat(),
        sample_rate_hz=FS,bclk_hz=BCLK,slot_width_bits=32,sample_width_bits=24,
        storage_width_bits=32,raw_endian='little',raw_layout='frame_major_8_channels',
        channel_map=CHANNEL_MAP,wav_channels=channels,wav_format='PCM24_no_normalization',
        requested_frames=frames,requested_bytes=frames*32,warmup_frames=4096,
        warmup_min_seconds=262144/BCLK,physical_module_compatibility='UNVERIFIED')

def write_json(path,data):
    path.write_text(json.dumps(data,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')

def export_raw(raw_path,out_dir,channels,expected_frames=None):
    raw_path=Path(raw_path);out_dir=Path(out_dir)
    raw=raw_path.read_bytes();samples=decode_raw(raw);frames=len(samples)//8
    if expected_frames is not None and frames!=expected_frames:
        raise ValueError('raw frame count differs from expected count')
    out_dir.mkdir(parents=True,exist_ok=False)
    meta=base_metadata(frames,channels)
    meta.update(status='OFFLINE_FORMAT_VERIFIED',raw_source=str(raw_path.resolve()),
                raw_sha256=hashlib.sha256(raw).hexdigest(),valid_frames=frames,
                duration_seconds=frames/FS,statistics=statistics(samples),board_capture='NOT_RUN')
    write_wav(out_dir/'selected.wav',samples,channels)
    write_json(out_dir/'metadata.json',meta)
    return meta

def wait_until(predicate,timeout,description):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>=deadline: raise TimeoutError(description)
        time.sleep(0.001)

def reset_dma(dma):
    dma.write(0x30,4)
    wait_until(lambda: not (dma.read(0x30)&4),1.0,'DMA reset bit did not clear')
    wait_until(lambda: bool(dma.read(0x34)&1),1.0,'DMA did not halt after reset')

def capture_transaction(ctrl,dma,buffer,frames,timeout):
    """Device-level transaction, factored for offline fault-injection tests.

    Caller owns cleanup. No returned data is valid until both stream and DMA
    completed and the DMA reports the exact byte count.
    """
    if not 1<=frames<=MAX_FRAMES: raise ValueError('frames out of DMA range')
    if not math.isfinite(timeout) or timeout<=0: raise ValueError('timeout must be finite and positive')
    if ctrl.read(0)!=ABI: raise RuntimeError('overlay ABI mismatch')
    expected={0x1C:FS,0x20:BCLK,0x24:0x08201820,0x2C:4096,0x30:0x76543210}
    for address,value in expected.items():
        if ctrl.read(address)!=value: raise RuntimeError('overlay format mismatch at0x%02x'%address)
    physical=int(buffer.physical_address)
    if physical<0 or physical%8 or physical+frames*32>0x20000000:
        raise RuntimeError('DMA buffer must be8-byte aligned in the512MiB DDR aperture')
    ctrl.write(4,0)
    reset_dma(dma)
    buffer[:]=0
    buffer.flush()
    dma.write(0x34,0x7000)
    dma.write(0x30,1)
    wait_until(lambda:not(dma.read(0x34)&1),1.0,'DMA did not start')
    dma.write(0x48,physical)
    dma.write(0x58,frames*32)
    ctrl.write(0x0C,frames)
    ctrl.write(4,3)
    start=time.monotonic()
    while True:
        status=ctrl.read(8);dstatus=dma.read(0x34)
        if status&0x70: raise RuntimeError('capture overflow/abort/config error0x%08x'%status)
        if dstatus&0x770: raise RuntimeError('DMA error0x%08x'%dstatus)
        if status&8 and dstatus&2 and dstatus&0x1000:
            break
        if time.monotonic()-start>=timeout:
            raise TimeoutError('capture timeout: ctrl0x%08x dma0x%08x'%(status,dstatus))
        time.sleep(0.002)
    result=dict(ctrl_status=status,dma_status=dstatus,captured_frames=ctrl.read(0x10),
                sent_frames=ctrl.read(0x14),dma_actual_bytes=dma.read(0x58),
                elapsed_seconds=time.monotonic()-start)
    if status&4 or result['captured_frames']!=frames or result['sent_frames']!=frames or result['dma_actual_bytes']!=frames*32:
        raise RuntimeError('completed capture length/count mismatch: '+str(result))
    buffer.invalidate()
    return result

def capture_board(bit_path,out_dir,frames,channels,timeout):
    # All board-only imports and PL changes are inside this explicit command.
    from pynq import Overlay,MMIO,allocate
    import numpy as np
    bit_path=Path(bit_path).resolve();out_dir=Path(out_dir)
    if not bit_path.is_file() or not bit_path.with_suffix('.hwh').is_file():
        raise FileNotFoundError('matching .bit and .hwh are required')
    if not 1<=frames<=MAX_FRAMES: raise ValueError('frames outside1..2097151')
    if not math.isfinite(timeout) or timeout<=0: raise ValueError('timeout must be finite and positive')
    channels_arg(','.join(map(str,channels)))
    out_dir.mkdir(parents=True,exist_ok=False)
    meta=base_metadata(frames,channels)
    meta.update(status='FAILED',valid_frames=0,board_capture='STARTED',
                bitstream=str(bit_path),bit_sha256=hashlib.sha256(bit_path.read_bytes()).hexdigest(),
                hwh_sha256=hashlib.sha256(bit_path.with_suffix('.hwh').read_bytes()).hexdigest())
    write_json(out_dir/'metadata.json',meta)
    buffer=None;ctrl=None;dma=None;transaction_ok=False;quiescent=False;failure=None;raw=None
    try:
        overlay=Overlay(str(bit_path),download=True)
        # PYNQ applies the HWH clock settings on download; record actual readback.
        from pynq import Clocks
        meta['fclk0_mhz_readback']=float(Clocks.fclk0_mhz)
        if abs(meta['fclk0_mhz_readback']-100)>0.001: raise RuntimeError('FCLK0 must be100MHz')
        ctrl=MMIO(CTRL_BASE,0x10000);dma=MMIO(DMA_BASE,0x10000)
        buffer=allocate(shape=(frames*8,),dtype=np.int32)
        meta.update(capture_transaction(ctrl,dma,buffer,frames,timeout))
        transaction_ok=True
    except BaseException as exc:
        failure=exc;meta['error']=repr(exc)
    finally:
        if ctrl is not None:
            try:
                meta['ctrl_before_cleanup']={hex(r):ctrl.read(r) for r in (8,0x10,0x14,0x18,0x28)}
            except Exception as exc:
                meta['control_snapshot_error']=repr(exc)
                if failure is None: failure=exc
            try:
                ctrl.write(4,0)
                wait_until(lambda:not(ctrl.read(8)&5),1.0,'clock/capture did not disable')
                meta['clock_disabled']=True
            except Exception as exc:
                meta['control_cleanup_error']=repr(exc)
                if failure is None: failure=exc
        if dma is not None:
            try:
                meta['dma_before_cleanup']={hex(r):dma.read(r) for r in (0x30,0x34,0x58)}
            except Exception as exc:
                meta['dma_snapshot_error']=repr(exc)
                if failure is None: failure=exc
            try:
                reset_dma(dma);quiescent=True;meta['dma_reset_and_halted']=True
            except Exception as exc:
                meta['dma_cleanup_error']=repr(exc)
                if failure is None: failure=exc
        if buffer is not None and quiescent:
            try:
                buffer.invalidate()
                raw=buffer.astype('<i4',copy=False).tobytes()
            except Exception as exc:
                meta['buffer_read_error']=repr(exc)
                if failure is None: failure=exc
            finally:
                try:
                    buffer.freebuffer()
                    meta['buffer_freed']=True
                except Exception as exc:
                    meta['buffer_free_error']=repr(exc)
                    if failure is None: failure=exc
        elif buffer is not None:
            # Keep the buffer alive after an unconfirmed DMA reset; avoid reuse.
            # CLI must remain running until operator stops PL, then terminate it.
            _quarantined_buffers.append(buffer)
            meta['buffer_freed']=False
            meta['operator_action']='DMA halt unconfirmed. Keep this process alive; stop PL or power down the board before terminating it.'
        if raw is not None:
            name='raw_i32le.bin' if transaction_ok else 'raw_unverified_i32le.bin'
            try:
                (out_dir/name).write_bytes(raw)
                meta.update(raw_file=name,raw_sha256=hashlib.sha256(raw).hexdigest(),raw_bytes=len(raw))
                if transaction_ok:
                    samples=decode_raw(raw)
                    write_wav(out_dir/'selected.wav',samples,channels)
                    meta.update(valid_frames=frames,duration_seconds=frames/FS,statistics=statistics(samples))
            except Exception as exc:
                meta['export_error']=repr(exc)
                if failure is None: failure=exc
        if transaction_ok and failure is None:
            meta.update(status='CAPTURE_VERIFIED',board_capture='COMPLETED')
        else:
            meta.update(status='FAILED',board_capture='FAILED')
        write_json(out_dir/'metadata.json',meta)
    if failure is not None: raise RuntimeError('capture failed; see '+str(out_dir/'metadata.json')) from failure
    return meta

_quarantined_buffers=[]

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    offline=sub.add_parser('convert',help='validate existing raw data and export PCM24 without board access')
    offline.add_argument('--raw',required=True);offline.add_argument('--out',required=True)
    offline.add_argument('--channels',default='0,1,2,3,4,5,6,7');offline.add_argument('--expected-frames',type=int)
    cap=sub.add_parser('capture',help='explicitly download overlay and capture on PYNQ')
    cap.add_argument('--bit',required=True);cap.add_argument('--out',required=True)
    cap.add_argument('--frames',type=int,default=FS);cap.add_argument('--timeout',type=float)
    cap.add_argument('--channels',default='0,1,2,3,4,5,6,7')
    args=parser.parse_args(argv)
    try:
        channels=channels_arg(args.channels)
        if args.command=='convert':
            meta=export_raw(args.raw,args.out,channels,args.expected_frames)
        else:
            timeout=args.timeout if args.timeout is not None else args.frames/FS+5.0
            if not math.isfinite(timeout) or timeout<=0: raise ValueError('timeout must be finite and positive')
            meta=capture_board(args.bit,args.out,args.frames,channels,timeout)
        print(json.dumps(meta,indent=2));return 0
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        if _quarantined_buffers:
            print('DMA halt unconfirmed. Stop PL or power down the board before ending this process.',file=sys.stderr,flush=True)
            while True: time.sleep(1)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
