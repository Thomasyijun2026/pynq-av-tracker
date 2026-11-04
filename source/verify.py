"""Offline package integrity check. Never connects to a board or starts hardware."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def verify():
    manifest = json.loads((ROOT / 'MANIFEST.json').read_text(encoding='utf-8'))
    checked = []
    for name, expected in manifest['files'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError('Missing or escaping member: ' + name)
        data = path.read_bytes()
        if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError('Member changed: ' + name)
        if path.suffix == '.py':
            ast.parse(data, filename=name)
        checked.append(name)
    print(json.dumps(dict(status='PACKAGE_INTEGRITY_PASS', members=len(checked),
        bytes=sum(v['bytes'] for v in manifest['files'].values()),
        hardware_opened=False, functional_acceptance=manifest['functional_acceptance'])))
    return manifest


if __name__ == '__main__':
    verify()
