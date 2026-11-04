"""Read the immutable ABI4 PL snapshot, with independently checked guard bits."""
import numpy as np
from audio_peak_reference_V4 import peak_guard, select_peaks


def unpack_peaks(packed):
    return [int.from_bytes(bytes([(packed>>(8*p))&255]),'little',signed=True) for p in range(6)]


def read_snapshot(ctrl):
    if ctrl.read(0)!=0x4d494301 or ctrl.read(0x40)!=0x444f4104:
        raise ValueError('Unexpected capture/DOA ABI4')
    if ctrl.read(0x54)!=0x04100604 or ctrl.read(0x74)!=0x00010304:
        raise ValueError('Unexpected channel/lag/temporal configuration')
    if [ctrl.read(r) for r in (0x98,0x9c,0xa0)] != [0x060a0704,0x00000309,0x50504831]:
        raise ValueError('Unexpected physical positive peak policy')
    if ctrl.read(0xa4)!=0x08040210:
        raise ValueError('Unexpected M8 FIR identity')
    status=ctrl.read(0x44)
    if status!=1:
        raise ValueError(f'DOA snapshot not ready or error: status={status:#x}')
    window=ctrl.read(0x48);frames=ctrl.read(0x4c);skip=ctrl.read(0x50)
    if frames!=window or not 4<=window<=32768 or window%4 or ctrl.read(0x70)!=window//4:
        raise ValueError('Invalid/incomplete accumulation window')
    words=[]
    for index in range(202):
        ctrl.write(0x58,index)
        lo=int(ctrl.read(0x5c));hi=int(ctrl.read(0x60))
        word=(hi<<32)|lo
        words.append(word-(1<<64) if word&(1<<63) else word)
    peaks=unpack_peaks(int(ctrl.read(0x64))|(int(ctrl.read(0x68))<<32))
    sub=[unpack_peaks(int(ctrl.read(0x78+q*8))|(int(ctrl.read(0x7c+q*8))<<32)) for q in range(4)]
    guard=peak_guard(peaks,sub)
    hw_guard=ctrl.read(0x6c)
    expect=guard['support']<<5 | guard['match_mask']<<1 | int(guard['stable'])
    if hw_guard!=expect:
        raise ValueError('PL temporal guard mismatch')
    if ctrl.read(0x44)!=status or ctrl.read(0x4c)!=frames:
        raise RuntimeError('Snapshot changed during read; concurrent writer or reset')
    sums=np.asarray(words,dtype=np.int64)
    independent=select_peaks(sums[:198].reshape(6,33)).tolist()
    if peaks!=independent:
        raise ValueError('PL peak/snapshot disagreement')
    return {'fir_id':'0x08040210','extension_abi':'0x444f4104','status':status,'window_frames':window,
            'skip_frames':skip,'first_center_raw_index':skip+19,'last_input_raw_index':skip+35+window-1,
            'sums_i64':words,'integer_peaks':peaks,'subwindow_peaks':sub,'guard':guard,
            'peak_readback_checks':'PASS','temporal_readback_checks':'PASS'}
