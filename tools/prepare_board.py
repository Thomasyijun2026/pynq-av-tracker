"""Prepare configuration and a service unit in a NEW materialized workspace only.
This script does not load an overlay, start a process, or contact the board.
"""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def prepare(workspace):
    w=Path(workspace).resolve();c=json.loads((ROOT/'config/board_template.json').read_text())
    code=w/'software/pynq';hw=w/'hardware/abi5';out=w/'service_install'
    if out.exists():raise ValueError('service_install already exists; never replace a live configuration')
    for base,manifest in ((code,c['installed_source_manifest']),(hw,c['hardware_files'])):
        for n,d in manifest.items():
            if hashlib.sha256((base/n).read_bytes()).hexdigest()!=d:raise ValueError('Source mismatch '+n)
    out.mkdir();state=w/'run_state';state.mkdir(exist_ok=True)
    config=dict(install_root=str(out),code=str(code),installation_id='pynq-av-tracker-v1',
        manifest=c['installed_source_manifest'],contract=c['runtime_contract'],
        base=dict(session_root=str(state),hardware_root=str(hw),hardware_files=c['hardware_files'],
                  known_wav_sha256=c['known_wav_sha256'],automatic_follow=True))
    (out/'config.json').write_text(json.dumps(config,indent=2))
    unit='''[Unit]
Description=PYNQ audiovisual tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=BOARD=Pynq-Z2
Environment=XILINX_XRT=/usr
Environment=PYTHONDONTWRITEBYTECODE=1
WorkingDirectory={code}
ExecStart=/usr/local/share/pynq-venv/bin/python3 -B {code}/av_service_bootstrap_V9.py --config {config}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''.format(code=code,config=out/'config.json')
    (out/'pynq-av-tracker.service').write_text(unit)
    print(json.dumps(dict(status='PREPARED_ONLY',config=str(out/'config.json'),started=False)))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',required=True)
    prepare(p.parse_args().workspace)
