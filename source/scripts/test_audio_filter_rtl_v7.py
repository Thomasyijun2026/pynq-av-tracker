"""Generate ABI4 XSim vectors with independent direct-convolution integer gold."""
import hashlib,itertools,json,sys
from pathlib import Path
from datetime import datetime
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'software/pynq'),str(ROOT/'software/pynq/mic_doa_V2')]
import audio_filter_reference_V5 as ref

def main():
    out=ROOT/'logs/audio_filter_V7'/('rtl_vectors_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True,exist_ok=False);vectors=out/'vectors';vectors.mkdir()
    checks=[];sources={};cases=[]
    def require(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        if not ok:
            (out/'failed.json').write_text(json.dumps(checks,indent=2));raise AssertionError(name)
    def add_source(p):sources[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    def write_hex(name,values,width):
        (vectors/name).write_text(''.join(format(int(v)&((1<<(width*4))-1),'0'+str(width)+'x')+'\n' for v in values))
    rng=np.random.default_rng(11031922)
    source=rng.integers(-200000,200001,size=400,dtype=np.int64)
    x=np.column_stack([source[30-d:30-d+291] for d in [0,1,-3,-4]])
    short=[x,np.zeros_like(x),x*np.array([-1,1,1,1]),x.copy(),rng.choice([-8388608,8388607],size=x.shape)]
    short[3][145:]=short[3][145:,::-1]
    inputs=[(v,256,8,'SYNTHETIC_TEST_ONLY') for v in short]
    session=ROOT/'logs/av_standalone_V1/session_20261103_003121'
    for dirname in ['runtime_collect_v4_20261103_125622','runtime_collect_v4_20261103_165020','runtime_collect_v7_20261103_181333']:
        p=session/dirname/'audio_00'
        old=json.loads((p/'pl_snapshot.json').read_text())
        raw=np.fromfile(p/'raw_i32le.bin',dtype='<i4').reshape(-1,8)
        samples=raw[old['skip_frames']:,[0,4,6,7]][:32803]
        require(dirname+'_frame_count',samples.shape==(32803,4))
        inputs.append((samples,32768,0,p.relative_to(ROOT).as_posix()))
        add_source(p/'raw_i32le.bin');add_source(p/'pl_snapshot.json')
    for case,(samples,window,skip,label) in enumerate(inputs):
        z=ref.features(samples,window)
        coeff=np.r_[np.ones(8,dtype=np.int64),-np.ones(8,dtype=np.int64)]
        y=np.column_stack([np.convolve(samples[:,c].astype(np.int64)>>4,coeff)[:len(samples)]>>2 for c in range(4)])
        require(f'case{case}_direct_fir',np.array_equal(y,ref.filtered(samples)))
        require(f'case{case}_22bit_range',np.all(y>=-2097152) and np.all(y<=2097151))
        size=window//4;subs=[]
        for q in range(4):
            start=19+q*size
            subs.extend(int(np.dot(y[start:start+size,i],y[start+k:start+k+size,j])) for i,j in itertools.combinations(range(4),2) for k in range(-16,17))
        curves=np.asarray(subs,dtype=np.int64).reshape(4,6,33)
        sums=np.r_[curves.sum(axis=0).ravel(),[np.dot(y[19:19+window,c],y[19:19+window,c]) for c in range(4)]]
        require(f'case{case}_202_direct_sums',sums.tolist()==z['sums_i64'])
        require(f'case{case}_24_subpeaks',ref.select_peaks(curves).tolist()==z['subwindow_peaks'])
        # Large skipped inputs must not contaminate the zero-initialized FIR history.
        frames=rng.integers(-8388608,8388608,size=(len(samples)+skip,8),dtype=np.int64)
        frames[skip:,[0,4,6,7]]=samples
        packed=[sum((int(v)&0xffffffff)<<(32*c) for c,v in enumerate(row)) for row in frames]
        write_hex(f'case{case}_input.hex',packed,64)
        write_hex(f'case{case}_expected.hex',sums,16)
        write_hex(f'case{case}_sub_sums.hex',subs,16)
        diagnostic=[]
        for q in range(4):
            begin=19+q*size
            diagnostic.extend(subs[q*198:(q+1)*198])
            diagnostic.extend(int(y[begin:begin+size,c]@y[begin:begin+size,c]) for c in range(4))
        require(f'case{case}_diagnostic_energy_total',np.array(diagnostic).reshape(4,202).sum(axis=0).tolist()==sums.tolist())
        write_hex(f'case{case}_diagnostic.hex',diagnostic,16)
        write_hex(f'case{case}_peaks.hex',[sum((int(v)&255)<<(8*c) for c,v in enumerate(row)) for row in [z['integer_peaks'],*z['subwindow_peaks']]],12)
        g=z['guard'];write_hex(f'case{case}_guard.hex',[g['support']<<5|g['match_mask']<<1|int(g['stable'])],2)
        cases.append(dict(case=case,window=window,skip=skip,source=label,peaks=z['integer_peaks'],guard=g))
    for p in [Path(__file__),ROOT/'software/pynq/audio_filter_reference_V5.py',ROOT/'software/pynq/audio_peak_reference_V4.py',*sorted((ROOT/'fpga/audio_localize_V6').rglob('*.sv'))]:add_source(p)
    result=dict(status='OFFLINE_AUDIO_FILTER_RTL_V7_PASS',checks=checks,cases=cases,sources=sources,
                vectors=str(vectors),vector_sha={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in vectors.iterdir()},
                physical_accuracy='NOT_VALIDATED',filter_id=ref.FIR_ID)
    (out/'result.json').write_text(json.dumps(result,indent=2));print('PASS',len(checks),out,flush=True)

if __name__=='__main__':main()
