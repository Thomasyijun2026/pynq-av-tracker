"""Restore original case-sensitive source paths without editing the public snapshot."""
import argparse, hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def materialize(target):
    target=Path(target).resolve()
    if target==ROOT or ROOT in target.parents:raise ValueError('Use an independent empty output directory')
    if target.exists() and any(target.iterdir()):raise ValueError('Output must be empty; existing projects are never overwritten')
    m=json.loads((ROOT/'manifests/source.json').read_text())
    files={}
    for name,v in m['files'].items():
        data=(ROOT/name).read_bytes()
        if len(data)!=v['bytes'] or hashlib.sha256(data).hexdigest()!=v['sha256']:raise ValueError(name)
        p=target/v['restore_path']
        if not p.resolve().is_relative_to(target):raise ValueError('Invalid restore path')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        files[v['restore_path']]=dict(bytes=v['bytes'],sha256=v['sha256'],source=name)
    (target/'MANIFEST.json').write_text(json.dumps(dict(schema=1,files=files,
        functional_acceptance='HISTORICAL_EVIDENCE_SEE_PUBLIC_VALIDATION'),indent=2))
    print(json.dumps(dict(status='MATERIALIZED',files=len(files),target=str(target),hardware_opened=False)))
    return target
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    materialize(p.parse_args().output)
