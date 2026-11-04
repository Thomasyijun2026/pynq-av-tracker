"""Replay hash-bound recorded excerpts against the V2 reference and RTL evidence.

These are old hardware recordings with new offline/RTL expectations, not V2 board captures.
"""
import argparse, hashlib, json, math
from pathlib import Path
import numpy as np
from run_localizer_V2 import check_bundle
from pl_reference import features, estimate
from direction import read_positions


def equivalent(a,b):
    if isinstance(b,dict):return isinstance(a,dict) and a.keys()==b.keys() and all(equivalent(a[k],v) for k,v in b.items())
    if isinstance(b,list):return isinstance(a,list) and len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b))
    if isinstance(b,float):return isinstance(a,(int,float)) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9)
    return type(a) is type(b) and a==b


def replay(folder):
    folder=Path(folder).resolve()
    check_bundle(folder)
    evidence=folder/'examples'
    manifest=json.loads((evidence/'manifest.json').read_text(encoding='utf-8'))
    coords=read_positions(folder/'component_coordinates_V20.csv',[1,5,7,8])
    rows=[]
    for case in manifest['cases']:
        data=(evidence/case['raw_file']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=case['raw_sha256']:
            raise ValueError('Example raw SHA mismatch: '+case['name'])
        raw=np.frombuffer(data,dtype='<i4').reshape(-1,4)
        expected=json.loads((evidence/case['expected_file']).read_text(encoding='utf-8'))
        snapshot=features(raw)
        if snapshot!=expected['reference_snapshot']:
            raise ValueError('202 sums / 30 peaks / guard mismatch: '+case['name'])
        result=estimate(snapshot,coords)
        if not equivalent(result,expected['estimate']):
            raise ValueError('Estimate mismatch: '+case['name'])
        rows.append({'name':case['name'],'valid':result['valid'],'bearing_deg':result['bearing_deg'],
                     'candidate_bearing_deg':result['candidate_bearing_deg'],
                     'reasons':result['reasons'],'guard':snapshot['guard']})
    return {'status':'OFFLINE_REPLAY_PASS','cases':rows,'scope':'V1 recorded PCM, V2 reference and RTL',
            'V2_board_validation':'NOT_RUN','physical_accuracy':'NOT_VALIDATED'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=replay(args.bundle)
    with args.out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    print(json.dumps(result))
