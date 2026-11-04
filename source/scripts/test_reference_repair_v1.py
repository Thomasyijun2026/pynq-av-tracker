"""Affected reference and timestamp regression. No board, serial or playback."""
import copy,hashlib,importlib.util,json,sys,threading,time
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'software/pynq'),str(ROOT/'scripts')]
import numpy as np
from av_screen_sweep_V1 import verify_travel as old_travel
from av_screen_sweep_V2 import verify_travel
from audio_stimulus_gate_V2 import StimulusGate as OldGate
from audio_stimulus_gate_V3 import StimulusGate
from av_phase_follow_V13 import Engine
from av_init_proof_V4 import validate
from test_av_init_proof_V1 import fixture as old_fixture

def fixture():
    """Synthetic sensor records for the complete new runtime test only."""
    r=old_fixture();s=r['visual']['sweep']
    for record in (s['entry'],s['sweep']):
        for row in record['samples']:
            row.update(time_basis='REQUEST_RESPONSE_BRACKET',observation_begin_monotonic=row['monotonic']-.003,
                       observation_end_monotonic=row['monotonic']-.0001)
    s['travel']=verify_travel(s['route'],s['entry'],s['sweep'])
    q=r['visual']['measured_axis']['measurements'][-1]['position'];r['visual']['position']=q
    pose=dict(position=q,full_angle=True,speed=0,moving=0)
    ev=dict(boot_id='TEST_ONLY',origin='BOARD_CAPTURE',clock_domain='BOARD_MONOTONIC',cleanup_verified=True,
            begin=108.1,end=110.2,raw_sha256='b'*64)
    r['stationary_audio']=dict(status='STATIONARY_REFERENCE_CAPTURED',before=[dict(pose,monotonic=107.8+i*.05) for i in range(3)],
        after=dict(pose,monotonic=110.3),evidence=ev,source_phase='INITIALIZING_STATIC_REFERENCE',confirmed_view_sha256=r['confirmation_frame']['sha256'])
    r['scan_audio']=dict(evidence=dict(ev,begin=103.8,end=105.897152,raw_sha256='a'*64))
    r['source_anchor']['reference_sha256']=ev['raw_sha256']
    return r

def main():
    out=ROOT/'logs/av_reference_repair_V1'/('offline_'+datetime.now().strftime('%Y%m%d_%H%M%S'));out.mkdir(parents=True)
    checks=[];status='FAILED';error=None;details={}
    def ck(v,name):
        checks.append(dict(name=name,passed=bool(v)))
        if not v:raise AssertionError(name)
    def reject(fn,name):
        try:fn()
        except (ValueError,KeyError,RuntimeError):ck(True,name)
        else:ck(False,name)
    try:
        src=ROOT/'logs/av_no_total_limit_V1/manual_failure_20261104_130110'
        snapshot=json.loads((src/'runtime_current.json').read_text())
        for j in snapshot['jobs'][-2:]:
            s=j['result']['visual_cleanup']['sweep'];prefix='saved_request_'+str(j['sequence'])
            reject(lambda:old_travel(s['route'],s['entry'],s['sweep']),prefix+'_old_failure_reproduced')
            v=verify_travel(s['route'],s['entry'],s['sweep']);ck(v['status']=='FULL_TURN_ENCODER_VERIFIED',prefix+'_real_bracket_validated_offline')
            details[prefix]=v
        s=copy.deepcopy(snapshot['jobs'][-1]['result']['visual_cleanup']['sweep'])
        mutations={
            'impossible_jump':lambda x:x['sweep']['samples'][15].update(position=-1000),
            'missing_bracket':lambda x:x['sweep']['samples'][15].pop('observation_begin_monotonic'),
            'wide_bracket':lambda x:x['sweep']['samples'][15].update(observation_begin_monotonic=x['sweep']['samples'][15]['monotonic']-.2),
            'reversed_bracket':lambda x:x['sweep']['samples'][15].update(observation_end_monotonic=x['sweep']['samples'][15]['monotonic']+.01),
            'five_count_endpoint':lambda x:x['sweep']['samples'][-1].update(position=x['route']['exit']+5),
            'wrong_target':lambda x:x['sweep'].update(target=100)}
        for name,change in mutations.items():
            bad=copy.deepcopy(s);change(bad);reject(lambda:verify_travel(bad['route'],bad['entry'],bad['sweep']),name+'_rejected')
        known=ROOT/'logs/av_service_install_V4/installed_service_20261104_125313'
        import zipfile,io
        with zipfile.ZipFile(known/'source.zip') as z:reference=np.load(io.BytesIO(z.read('known_source.npy')),allow_pickle=False)
        sha='d835a32f414431e79a11d82fb785a483d84fe3e1b7f930ef85bfe26652903aea'
        gate=StimulusGate(reference,sha);old=OldGate(reference,sha)
        for n,digest in [(1,'6dbe4bbb714f1a7e82b328452d584a6c330baa089daaa1e436d1aa46b553dad2'),(2,'e6971a8445a959d57f68cdea1bf0a1be3f7de9830ec5cad576cc56e49b64c009')]:
            raw=(src/('operation_%06d'%n)/'raw_i32le.bin').read_bytes();ck(hashlib.sha256(raw).hexdigest()==digest,'pcm_original_sha_'+str(n))
            pcm=np.frombuffer(raw,dtype='<i4').reshape(-1,8)[31269:31269+32768][:,[0,4,6,7]]
            before,after=old.evaluate(pcm),gate.evaluate(pcm);details['identity_'+str(n)]={'old':before,'new':after}
            ck(after['status']=='SOURCE_PRESENT','saved_pcm_identity_recovered_'+str(n))
            ck(after['min_abs_correlation']==before['min_abs_correlation']==.1 and after['max_offset_spread_samples']==before['max_offset_spread_samples']==256,'identity_thresholds_unchanged_'+str(n))
            if n==1:ck(before['status']=='REJECTED','first_four_seconds_false_rejection_reproduced')
        rng=np.random.default_rng(734)
        for name,pcm in [('zero',np.zeros((32768,4),dtype=np.int32)),('unrelated_noise',rng.integers(-100000,100000,size=(32768,4),dtype=np.int32)),('rail',np.full((32768,4),8388607,dtype=np.int32)),('copied_channels',np.repeat(rng.integers(-10000,10000,size=(32768,1),dtype=np.int32),4,axis=1))]:
            ck(gate.evaluate(pcm)['status']!='SOURCE_PRESENT',name+'_identity_rejected')
        reject(lambda:StimulusGate(np.r_[reference,1.],sha),'over_ten_second_reference_still_rejected')
        good=fixture();ck(validate(good,'TEST_ONLY',100.,119.)['current_trigger'],'full_new_proof_synthetic_only')
        mutations={
            'no_static_reference':lambda r:r.pop('stationary_audio'),
            'moving_static_pose':lambda r:r['stationary_audio']['after'].update(moving=1),
            'changed_pose':lambda r:r['stationary_audio']['after'].update(position=r['visual']['position']+5),
            'stale_capture':lambda r:r['stationary_audio']['evidence'].update(begin=101.),
            'other_boot':lambda r:r['stationary_audio']['evidence'].update(boot_id='OLD'),
            'other_image':lambda r:r['stationary_audio'].update(confirmed_view_sha256='0'*64),
            'reused_raw':lambda r:r['stationary_audio']['evidence'].update(raw_sha256='a'*64),
            'false_overlap':lambda r:r['audio_sweep_overlap'].update(seconds=1.),
            'missing_audio_cleanup':lambda r:r['stationary_audio']['evidence'].update(cleanup_verified=False)}
        for name,change in mutations.items():
            bad=copy.deepcopy(good);change(bad);reject(lambda:validate(bad,'TEST_ONLY',100.,119.),name+'_handoff_rejected')
        # Actual initialization function, only acquisition/motion/session doubles.
        for mode in ('good_static_bad_scan','bad_static_good_scan','pose_changed','cleanup_failed'):
            events=[];time_value=[100.];lock=threading.Lock();scan_captured=threading.Event()
            def now():
                with lock:time_value[0]+=.01;return time_value[0]
            class Capture:
                def background(self,path):return dict(pcm=None,evidence={})
                def __call__(self,path):
                    kind=Path(path).name;events.append(kind);start=now()
                    if kind=='scan_reference':scan_captured.set();time.sleep(.01)
                    valid=(kind=='reference' and mode!='bad_static_good_scan') or (kind=='scan_reference' and mode=='bad_static_good_scan')
                    return dict(pcm=None,evidence=dict(begin=start,end=now(),raw_sha256=('b' if kind=='reference' else 'a')*64),
                        metadata=dict(transaction_begin_monotonic=start,transaction_end_monotonic=now()),
                        snapshot=dict(ps_estimate=dict(valid=valid,reasons=[] if valid else ['inconsistent'],bearing_deg=42.,fit=dict(projected_direction_xy=[.5,.5]))))
            class View:
                def __init__(self):self.sweep_started=threading.Event();self.done=False;self.pose_count=0
                def open(self):events.append('open')
                def center(self,axis):
                    begin=now();self.sweep_started.set();assert scan_captured.wait(1);time.sleep(.03)
                    self.done=True;events.append('static_view_confirmed')
                    return dict(status='SCREEN_CENTER_CONFIRMED',full_sweep_this_trigger=True,position=100,
                        measured_axis={'status':'FIXTURE'},confirmation_frame=dict(sha256='c'*64),sweep=dict(sweep=dict(begin=begin,end=now())))
                def position(self):
                    assert self.done;self.pose_count+=1;events.append('pose')
                    return dict(position=105 if mode=='pose_changed' and self.pose_count==4 else 100,full_angle=True,moving=0,speed=0,monotonic=now())
                def close(self):events.append('close');return dict(cleanup_verified=mode!='cleanup_failed')
            class Session:
                def __init__(self,*a,**k):self.identity={};self.reference_evidence={}
                def set_noise(self,*a):events.append('background')
                def try_seal(self,pcm,evidence,clock,geometry):
                    self.reference_evidence=evidence;events.append('seal')
                    return dict(status='REFERENCE_SEALED' if geometry['valid'] else 'REFERENCE_REJECTED')
                def invalidate(self,*a):events.append('invalidate')
                def manifest(self):return {}
            engine=object.__new__(Engine);engine.boot='TEST_ONLY';engine.installation='test';engine.clock=now;engine.sleep=lambda dt:None
            engine.capture=Capture();engine.visual_factory=lambda *a:View();engine._evidence=lambda r:r['evidence']
            engine.gate=type('G',(),{'evaluate':lambda s,pcm:{'status':'SOURCE_PRESENT'}})();engine.publish=lambda v:events.append(v['phase'])
            engine.quiet=lambda:dict(cleanup_verified=True);engine.installation_record=lambda:{}
            with patch('av_phase_follow_V13.ReferenceSession',Session),patch('av_phase_follow_V13.align',return_value={'test_only':True}):result=engine.initialize(out/mode,trigger=100.)
            ck(result['navigation_ready']==(mode=='good_static_bad_scan'),mode+'_acceptance')
            ck(events.index('scan_reference')<events.index('static_view_confirmed')<events.index('reference'),mode+'_capture_order')
            ck('close' in events and (out/mode/'result.json').exists(),mode+'_finalizer_and_evidence')
            details[mode]=dict(events=events,result=result)
        status='PASS_REFERENCE_AND_TELEMETRY_REPAIR_PC'
    except BaseException as exc:error=repr(exc);raise
    finally:
        names=['av_screen_sweep_V2.py','audio_stimulus_gate_V3.py','av_phase_follow_V13.py','av_phase_io_V13.py','av_init_proof_V4.py','av_phase_service_runner_V7.py','av_trigger_service_V15.py','av_runtime_contract_V6.py','av_phase_runtime_V9.py','av_service_bootstrap_V5.py']
        data=dict(status=status,error=error,checks=checks,details=details,source_sha256={n:hashlib.sha256((ROOT/'software/pynq'/n).read_bytes()).hexdigest() for n in names},hardware_actions=0)
        (out/'result.json').write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps(dict(status=status,checks=len(checks),error=error,evidence=str(out))))
if __name__=='__main__':main()
