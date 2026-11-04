"""Independent host verification for one immutable raw/PL evidence folder."""
import hashlib,itertools,json,math
from pathlib import Path
import numpy as np
from pl_reference import features,estimate
from direction import read_positions
from analyze_recording import solve

def verify(folder,expected,coordinates,frames):
    folder=Path(folder)
    meta=json.loads((folder/'metadata.json').read_text())
    snap=json.loads((folder/'pl_snapshot.json').read_text())
    data=(folder/'raw_i32le.bin').read_bytes()
    raw=np.frombuffer(data,dtype='<i4').reshape(-1,8)
    assert hashlib.sha256(data).hexdigest()==meta['raw_sha256']
    assert len(data)==meta['raw_bytes']==meta['dma_actual_bytes']==frames*32
    assert len(raw)==meta['captured_frames']==meta['sent_frames']==meta['valid_frames']==frames
    assert meta['status']=='CAPTURE_VERIFIED' and meta['board_capture']=='COMPLETED'
    assert meta['clock_disabled'] and meta['dma_reset_and_halted'] and meta['buffer_freed']
    assert meta['ctrl_status']==11 and meta['dma_status']&0x770==0
    assert abs(meta['fclk0_mhz_readback']-100)<.001
    assert meta['bit_sha256']==expected['mic_localize.bit'] and meta['hwh_sha256']==expected['mic_localize.hwh']
    assert snap['artifacts_sha256']==expected and snap['status']==1
    assert snap['extension_abi']=='0x444f4102'
    assert snap['window_frames']==32768 and snap['skip_frames']==31250
    assert np.all((raw>=-8388608)&(raw<=8388607))
    x=raw[31250:,[0,4,6,7]]
    reference=features(x)
    hardware=np.asarray(snap['sums_i64'],dtype=np.int64)
    assert np.array_equal(reference['sums_i64'],hardware),'PL_REFERENCE_MISMATCH'
    assert reference['subwindow_peaks']==snap['subwindow_peaks'],'PL_SUBWINDOW_MISMATCH'
    assert reference['guard']==snap['guard'],'PL_TEMPORAL_GUARD_MISMATCH'
    peaks=[int(np.argmax(np.abs(reference['sums_i64'][p*33:(p+1)*33])))-16 for p in range(6)]
    assert peaks==snap['integer_peaks']
    channels=[]
    for c in [0,4,6,7]:
        a=raw[:,c];steady=a[31250:].astype(float);m=meta['statistics'][c]
        assert int(a.min())==m['min'] and int(a.max())==m['max']
        assert int((a==0).sum())==m['zeros']
        assert math.isclose(float(a.astype(float).std()),m['ac_rms'],rel_tol=1e-9,abs_tol=1e-6)
        changes=np.flatnonzero(np.r_[True,np.diff(a)!=0,True])
        one_second=[float(steady[k:k+31250].std()) for k in range(0,len(steady)-31250+1,31250)]
        row={'channel':c+1,'ac_rms':float(steady.std()),'unique':int(len(np.unique(a))),
             'clipped':int(((a==-8388608)|(a==8388607)).sum()),'max_constant_run':int(np.diff(changes).max()),
             'one_second_ac_rms':one_second}
        assert row['unique']>100 and row['clipped']==0 and row['max_constant_run']<32
        assert all(r>1 for r in one_second),'Silent second in selected input'
        channels.append(row)
    assert not any(np.array_equal(x[:,i],x[:,j]) for i,j in itertools.combinations(range(4),2))
    coords=read_positions(coordinates,[1,5,7,8]);pl=estimate(snap,coords)
    assert pl['valid']==snap['ps_estimate']['valid'] and pl['reasons']==snap['ps_estimate']['reasons']
    if 'lags' in pl:
        assert np.allclose(pl['lags'],snap['ps_estimate']['lags'],rtol=0,atol=1e-9)
        assert np.allclose(pl['fit']['projected_direction_xy'],snap['ps_estimate']['fit']['projected_direction_xy'],rtol=0,atol=1e-9)
    gcc=solve(x[19:19+32768].astype(float),coords)
    windows=[estimate(features(x[start:start+32768+35]),coords)
             for start in range(0,len(x)-32768-35+1,32768)]
    bands=[solve(x.astype(float),coords,band) for band in ((150,1500),(300,3000),(1500,7000))]
    # Raw energy spectrum describes changes in the existing source, not its identity.
    nfft=4096;segments=x[:len(x)//nfft*nfft].reshape(-1,nfft,4).astype(float)
    segments-=segments.mean(axis=1,keepdims=True)
    power=np.mean(abs(np.fft.rfft(segments*np.hanning(nfft)[None,:,None],axis=1))**2,axis=0)
    freq=np.fft.rfftfreq(nfft,1/31250)
    spectrum=[]
    for c in range(4):
        mask=(freq>=150)&(freq<=7000);peak=int(np.flatnonzero(mask)[np.argmax(power[mask,c])])
        spectrum.append({'channel':[1,5,7,8][c],'dominant_bin_hz':float(freq[peak]),
            'band_power_fraction':[float(power[(freq>=lo)&(freq<hi),c].sum()/power[mask,c].sum())
                                  for lo,hi in ((150,1500),(1500,3000),(3000,7000))]})
    return {'status':'DIGITAL_AND_PL_BIT_EXACT_PASS','raw_sha256':meta['raw_sha256'],'frames':frames,
            'artifact_sha256':expected,'exact_i64_features':202,'exact_pl_peaks':6,'exact_subwindow_peaks':24,'temporal_guard_match':True,'channels':channels,
            'pl_estimate':pl,'ps_estimate_ms':snap['ps_estimate_ms'],'independent_gcc_same_centers':gcc,
            'offline_fixed_point_windows':windows,'band_sensitivity':bands,'spectrum':spectrum,
            'physical_accuracy':'NOT_VALIDATED'}
