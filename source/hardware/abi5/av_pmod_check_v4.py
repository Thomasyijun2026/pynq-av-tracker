"""Finite audio/camera checks on the already loaded, reviewed PMOD candidate.

No Overlay download, servo packet, GPIO or buzzer command. This is a final
function prerequisite check, not a successful sound-to-servo-to-vision run.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def holders():
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    name = os.readlink(str(fd))
                    if name.startswith(('/dev/video', '/dev/ttyUSB', '/dev/ttyACM')):
                        found.append([proc.name, name])
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
    return found


class Check:
    def __init__(self, root, expected, boot):
        import pynq
        import mic_capture_fast
        import mic_doa_pmod_V5
        self.hw, self.mic, self.short = pynq, mic_capture_fast, mic_doa_pmod_V5
        self.root, self.expected, self.boot = Path(root), expected, boot
        for name, sha in expected.items():
            if digest(self.root/name) != sha:
                raise ValueError('Source hash differs: '+name)
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot:
            raise RuntimeError('Boot changed since direct servo probe')
        for name, addr in [('mic_capture_0',0x43c00000),('axi_dma_0',0x40400000),('servo_uart_0',0x43c10000)]:
            if pynq.PL.ip_dict[name]['phys_addr'] != addr:
                raise RuntimeError('Loaded address mismatch '+name)
        self.c = pynq.MMIO(0x43c00000,65536)
        self.d = pynq.MMIO(0x40400000,65536)
        self.u = pynq.MMIO(0x43c10000,65536)
        self.save('prepared.json', self.quiet())

    def save(self, name, value):
        (self.root/name).write_text(json.dumps(value,indent=2,allow_nan=False))

    def quiet(self):
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != self.boot:
            raise RuntimeError('Boot changed')
        if digest(self.hw.PL.bitfile_name) != self.expected['av_pmod.bit']:
            raise RuntimeError('Active candidate identity changed')
        if abs(self.hw.Clocks.fclk0_mhz-100) > .001:
            raise RuntimeError('FCLK differs')
        r = dict(boot_id=self.boot,bitfile=self.hw.PL.bitfile_name,
            audio_abi=[hex(self.c.read(0)),hex(self.c.read(0x40))],
            fir_id=f'{self.c.read(0xa4):#010x}',control=self.c.read(4),status=self.c.read(8),dma_control=self.d.read(0x30),dma_status=self.d.read(0x34),
            uart_abi=hex(self.u.read(0)),uart_control=self.u.read(4),uart_status=self.u.read(8),uart_counts=self.u.read(16),
            quarantined_buffers=len(self.mic._quarantined_buffers),holders=holders())
        r['cleanup_verified'] = bool(r['audio_abi']==['0x4d494301','0x444f4105'] and r['fir_id']=='0x08040210' and r['control']==0 and not(r['status']&5)
            and not(r['dma_control']&1) and r['dma_status']&1 and not(r['dma_status']&0x770)
            and r['uart_abi']=='0x55525431' and r['uart_control']==0 and r['uart_status']==2 and r['uart_counts']==0
            and not r['quarantined_buffers'] and not r['holders'])
        return r

    def camera(self):
        if not self.quiet()['cleanup_verified']:
            raise RuntimeError('Resources not idle before camera')
        cam = self.root/'camera'; cam.mkdir(exist_ok=False)
        r = dict(status='FAILED',camera_started=False,servo_started=False)
        power = None; original = None
        try:
            video = Path('/sys/class/video4linux/video0')
            usb = next(p for p in (video/'device').resolve().parents if (p/'idVendor').exists())
            r['usb_id'] = (usb/'idVendor').read_text().strip()+':'+(usb/'idProduct').read_text().strip()
            if r['usb_id'] != '1d6c:0103':
                raise RuntimeError('Unexpected camera')
            power = usb/'power/control'; original = power.read_text().strip()
            power.write_text('on'); r['camera_started'] = True
            worker = subprocess.run([sys.executable,'-B',str(self.root/'camera_worker.py'),str(cam)],capture_output=True,text=True,timeout=25)
            (cam/'stdout.txt').write_text(worker.stdout); (cam/'stderr.txt').write_text(worker.stderr)
            r['worker_returncode'] = worker.returncode
            r['camera'] = json.loads((cam/'camera_result.json').read_text())
            if worker.returncode != 0 or r['camera']['status'] != 'CAMERA_FRAME_SAVED' or not r['camera']['released']:
                raise RuntimeError('Camera worker failed or release unconfirmed')
            r['status'] = 'CAMERA_CAPTURE_PASS'
        except Exception as exc:
            r['error'] = repr(exc)
        finally:
            if power is not None and original is not None:
                power.write_text(original); r['power_restored'] = power.read_text().strip()==original
            r['postflight'] = self.quiet()
            r['cleanup_verified'] = r['postflight']['cleanup_verified'] and (power is None or r.get('power_restored',False))
            self.save('camera_stage.json',r)
        return r

    def audio(self, label):
        if label not in ('background','controlled') or not self.quiet()['cleanup_verified']:
            raise RuntimeError('Invalid label or non-idle hardware')
        r = dict(status='FAILED',label=label,servo_started=False,buzzer_started=False,boot_id=self.boot,
            physical_accuracy='NOT_VALIDATED',audio_call_begin=time.monotonic())
        try:
            folder = self.root/label
            r['answer'] = self.short.localize(self.mic,self.root,{'files':self.expected},folder)
            r['audio_call_end'] = time.monotonic()
            r['reporting'] = self.short.finalize_reporting(self.mic,folder)
            import numpy as np
            x = np.frombuffer((folder/'raw_i32le.bin').read_bytes(),dtype='<i4').reshape(-1,8)[:,[0,4,6,7]]
            r['input'] = dict(changed=np.any(x[1:]!=x[:-1],axis=0).tolist(),
                clipped=((x==-8388608)|(x==8388607)).sum(axis=0).tolist(),
                ac_rms=x.astype(float).std(axis=0).tolist(),
                duplicate_pairs=[[i,j] for i in range(4) for j in range(i+1,4) if np.array_equal(x[:,i],x[:,j])])
            h = r['input'];h['healthy'] = all(h['changed']) and not any(h['clipped']) and not h['duplicate_pairs'] and all(a>1 for a in h['ac_rms'])
            r['status'] = 'AUDIO_CAPTURED'
        except Exception as exc:
            r['error'] = repr(exc)
        finally:
            r['postflight'] = self.quiet()
            r['cleanup_verified'] = r['postflight']['cleanup_verified']
            self.save(label+'_stage.json',r)
        return r

    def finish(self):
        r = self.quiet()
        r.update(full_chain_verified=False,servo_motion=False,buzzer_driven=False,
            blockers=['SERVO_NO_REPLY','BUZZER_PINOUT_UNVERIFIED','COORDINATE_CALIBRATION_PENDING'])
        r['files'] = {str(p.relative_to(self.root)):digest(p) for p in self.root.rglob('*') if p.is_file()}
        self.save('final.json',r)
        return r
