"""Relocatable, explicit offline reproduction. No deploy/download operation exists.

Python 3.13 / NumPy, Vivado 2026.1. All generated data stays under this package.
The original generation script is unchanged; its ROOT derives from its own file.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from verify import verify

ROOT = Path(__file__).resolve().parent
BIN = Path('D:/Vivado2026.1/2026.1/Vivado/bin')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['verify', 'vectors', 'simulate', 'build', 'review'])
    parser.add_argument('--proof', help='Explicit local proof JSON; never searches another checkout')
    parser.add_argument('--build-root', default='D:/pynq_build_V1', help='Short D-drive directory for Vivado generated files')
    args = parser.parse_args()
    manifest = verify()
    if args.stage == 'verify':
        return
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out = ROOT / 'reproduction_runs' / (args.stage + '_' + stamp)
    out.mkdir(parents=True, exist_ok=False)
    temp = ROOT / 'reproduction_runs' / 'temp'
    temp.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE='1')
    record = dict(status='RUNNING', stage=args.stage, package_root=str(ROOT),
                  started_pc=datetime.datetime.now().isoformat(), steps=[], hardware_opened=False)

    def save():
        (out / 'result.json').write_text(json.dumps(record, indent=2), encoding='utf-8')

    def invoke(command, cwd, label, marker=None):
        row = dict(label=label, command=command, cwd=str(cwd))
        record['steps'].append(row)
        save()
        with (out / (label + '.log')).open('wb') as log:
            proc = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
            row['pid'] = proc.pid
            save()
            try:
                row['exit_code'] = proc.wait(timeout=2700)
            except subprocess.TimeoutExpired:
                record['status'] = 'WAIT_TIMEOUT_PROCESS_PRESERVED'
                save()
                raise
        content = (out / (label + '.log')).read_text(errors='replace')
        row['passed'] = row['exit_code'] == 0 and (not marker or marker in content)
        save()
        if not row['passed']:
            raise RuntimeError(label + ' failed; inspect ' + str(out))

    save()
    try:
        if args.stage == 'vectors':
            entry = ROOT / 'scripts/test_audio_filter_rtl_V7.py'
            invoke([sys.executable, '-B', str(entry)], ROOT, 'vectors', 'PASS')
            proofs = list((ROOT / 'logs/audio_filter_V7').glob('rtl_vectors_*/result.json'))
            generated = max(proofs, key=lambda p: p.stat().st_mtime_ns)
            proof = read(generated)
            if proof['status'] != 'OFFLINE_AUDIO_FILTER_RTL_V7_PASS':
                raise ValueError('Vector proof failed')
            for name, digest in proof['sources'].items():
                if sha(ROOT / name) != digest:
                    raise ValueError('Vector source changed: ' + name)
            record['proof'] = str(generated)
        elif args.stage == 'simulate':
            if not args.proof:
                raise ValueError('Pass the explicit vectors result.json with --proof')
            proof_path = Path(args.proof).resolve()
            if not proof_path.is_relative_to(ROOT):
                raise ValueError('Proof must belong to this extracted package')
            proof = read(proof_path)
            if proof.get('status') != 'OFFLINE_AUDIO_FILTER_RTL_V7_PASS':
                raise ValueError('Expected successful integer vector proof')
            for name, digest in proof['sources'].items():
                if sha(ROOT / name) != digest:
                    raise ValueError('Simulation source changed: ' + name)
            work = out / 'work'
            work.mkdir()
            for name, digest in proof['vector_sha'].items():
                source = Path(proof['vectors']) / name
                if not source.resolve().is_relative_to(ROOT) or sha(source) != digest:
                    raise ValueError('Vector changed or outside package: ' + name)
                shutil.copyfile(source, work / name)
            rtl = ROOT / 'fpga/audio_localize_V6'
            for top, marker in [('doa_correlator_tb', 'DOA_CORRELATOR_TEST_PASS'),
                                ('mic_capture_tb', 'MIC_CAPTURE_TEST_PASS')]:
                sources = sorted((rtl / 'rtl').glob('*.sv')) + [rtl / 'sim' / (top + '.sv')]
                for tool, values in [('xvlog', ['-sv', *map(str, sources)]),
                                     ('xelab', [top, '-s', top + '_package']),
                                     ('xsim', [top + '_package', '-runall'])]:
                    invoke([str(BIN / (tool + '.bat')), *values], work,
                           top + '_' + tool, marker if tool == 'xsim' else None)
            record.update(status='RELOCATED_XSIM_PASS', vector_proof=str(proof_path),
                          sources=proof['sources'])
        elif args.stage == 'build':
            if not args.proof:
                raise ValueError('Explicit successful relocated XSim result required')
            proof_path = Path(args.proof).resolve()
            proof = read(proof_path)
            if not proof_path.is_relative_to(ROOT) or proof.get('status') != 'RELOCATED_XSIM_PASS':
                raise ValueError('Build needs this checkout\'s completed simulation')
            for name, digest in proof['sources'].items():
                if sha(ROOT / name) != digest:
                    raise ValueError('Source changed after simulation: ' + name)
            build_base = Path(args.build_root).resolve()
            if build_base.drive.upper() != 'D:' or len(str(build_base)) > 48 or not str(build_base).isascii():
                raise ValueError('Vivado build-root must be an ASCII D-drive path of at most 48 characters')
            build = build_base / ('b_' + stamp)
            build.mkdir(parents=True, exist_ok=False)
            record['build_directory'] = str(build)
            save()
            invoke([str(BIN / 'vivado.bat'), '-mode', 'batch', '-source',
                    str(ROOT / 'fpga/av_pmod_V7/tcl/build.tcl'), '-log', str(out / 'vivado.log'),
                    '-journal', str(out / 'vivado.jou'), '-tclargs', str(build)], build,
                   'vivado', 'AV_PMOD_AUDIO_V7_BUILD_PASS')
            summary = dict(status='BUILD_PASS_PENDING_REVIEW', exit_code=0, run=str(build),
                sources={n: v['sha256'] for n, v in manifest['files'].items() if n.startswith('fpga/')},
                outputs={p.name: sha(p) for p in (build / 'output').iterdir() if p.is_file()})
            (out / 'summary.json').write_text(json.dumps(summary, indent=2))
            shutil.copyfile(out / 'vivado.log', out / 'console.log')
            record.update(status='BUILD_PASS_PENDING_REVIEW', proof=str(out / 'summary.json'))
        elif args.stage == 'review':
            if not args.proof:
                raise ValueError('Explicit new summary.json required')
            evidence = Path(args.proof).resolve().parent
            if not evidence.is_relative_to(ROOT):
                raise ValueError('Review must use this extracted package')
            # Reuse the unchanged 66-condition reviewer; inject only path/I/O
            # helpers, avoiding the original helper\'s historical workspace root.
            import types
            helper = types.ModuleType('av_overnight_V1')
            helper.ROOT = ROOT
            helper.ZIP_SHA = '216c125b66dbec20545e77d48b79a129542cb58e0912a533b9288edade56f328'
            helper.sha, helper.read = sha, read
            helper.dump = lambda p, obj: Path(p).write_text(json.dumps(obj, indent=2), encoding='utf-8')
            sys.modules['av_overnight_V1'] = helper
            sys.argv = ['review_av_pmod_audio_V7.py', str(evidence)]
            runpy.run_path(str(ROOT / 'scripts/review_av_pmod_audio_V7.py'), run_name='__main__')
            checked = read(evidence / 'review.json')
            if checked['status'] != 'PASS_IMPLEMENTATION_REVIEW':
                raise ValueError('Implementation review failed')
            record.update(status='PASS_IMPLEMENTATION_REVIEW', proof=str(evidence / 'review.json'))
        if record['status'] == 'RUNNING':
            record['status'] = 'PASS'
    except BaseException as exc:
        if record['status'] == 'RUNNING':
            record['status'] = 'FAILED'
        record['error'] = repr(exc)
        save()
        raise
    record['ended_pc'] = datetime.datetime.now().isoformat()
    save()
    print(json.dumps(dict(status=record['status'], evidence=str(out), hardware_opened=False)))


if __name__ == '__main__':
    main()
