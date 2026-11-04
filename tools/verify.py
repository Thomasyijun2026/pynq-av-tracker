"""Offline hashes, syntax, local links, publication scope and source integrity."""
import ast,hashlib,json,re,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def verify():
    m=json.loads((ROOT/'manifests/package.json').read_text())
    files=m['files'];python=0;links=0;docs=0
    for name,v in files.items():
        p=(ROOT/name).resolve()
        if not p.is_relative_to(ROOT):raise ValueError(name)
        b=p.read_bytes()
        if len(b)!=v['bytes'] or hashlib.sha256(b).hexdigest()!=v['sha256']:raise ValueError('Changed: '+name)
        if p.suffix=='.py':ast.parse(b,filename=name);python+=1
        if p.suffix=='.md':
            t=b.decode('utf8');docs+=1
            if re.search(r'雷达|点选|转台反馈|radar|turntable feedback|point.selection',t,re.I):raise ValueError('Excluded documentation scope: '+name)
            for url in re.findall(r'\]\(([^)]+)\)',t):
                if '://' in url or url.startswith('#'):continue
                path=(p.parent/url.split('#')[0]).resolve()
                if not path.is_relative_to(ROOT) or not path.exists():raise ValueError('Broken link '+name+':'+url)
                links+=1
    actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts and '__pycache__' not in p.parts}
    if actual!=set(files)|{'manifests/package.json'}:raise ValueError('Unexpected/missing files: '+str(sorted(actual.symmetric_difference(set(files)|{'manifests/package.json'}))))
    src=json.loads((ROOT/'manifests/source.json').read_text())
    for name,v in src['files'].items():
        if files[name]['sha256']!=v['sha256']:raise ValueError('Source manifest '+name)
    result=dict(status='PUBLIC_PACKAGE_PASS',files=len(files),python_syntax=python,documents=docs,local_links=links,source_files=len(src['files']),hardware_opened=False)
    print(json.dumps(result));return result
if __name__=='__main__':verify()
