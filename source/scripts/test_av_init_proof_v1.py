"""New full-turn result boundary regression. All motions/images are synthetic."""
import copy,hashlib,json,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'software/pynq'))
from av_init_proof_V1 import validate
from av_axis_bootstrap_V1 import CONVENTION,SCOPE
from av_screen_sweep_V1 import plan,verify_travel,associate,choose,measure_axis
from av_trigger_service_V11 import Coordinator as Old
from av_trigger_service_V12 import Coordinator


def fixture():
    def move(a,b,t):
        rows=[dict(position=round(a+(b-a)*i/64),monotonic=t+i*.05,
                   speed=1800 if i<64 else 0,moving=1 if i<64 else 0) for i in range(1,65)]
        rows.extend(dict(position=b,monotonic=t+3.25+i*.05,speed=0,moving=0) for i in range(2))
        return dict(status='ARRIVED',target=b,start=a,begin=t,end=t+3.3,samples=rows)
    route=plan(1700);entry=move(1700,route['entry'],100.1);sweep=move(route['entry'],route['exit'],103.5)
    travel=verify_travel(route,entry,sweep)
    image=dict(read_begin=104.,read_end=104.02)
    records=[dict(frame=image,pose=associate(image,entry['samples']+sweep['samples']),
                  vision=dict(status='KNOWN_SCREEN_MATCHED',image_size=[1280,960],best=dict(score=.95,center_px=[640,480])))]
    selected=choose(records,travel);q=selected['goal'];measurements=[]
    for n,position in enumerate((q,q+128,q+256),1):
        t=107.+n*.2;u=319.5+(q+256-position)*.5
        pose=dict(position=position,full_angle=True,moving=0,speed=0)
        frame=dict(file='stationary_%d.jpg'%n,sha256=hashlib.sha256(str(n).encode()).hexdigest(),
                   frame_number=n,image_rotation=180,time_basis='DRAINED_READ_NOT_EXPOSURE',
                   read_begin=t,read_end=t+.01,write_end=t+.02)
        vision=dict(status='KNOWN_SCREEN_MATCHED',image_size=[1280,960],best=dict(score=.95,center_px=[2*u,480]))
        measurements.append(dict(boot_id='TEST_ONLY',clock_domain='BOARD_MONOTONIC',position=position,u=u,
                                 before=pose,after=copy.deepcopy(pose),frame=frame,vision=vision))
    axis=dict(status='VISUAL_PAN_RESPONSE_MEASURED',boot_id='TEST_ONLY',clock_domain='BOARD_MONOTONIC',
              evidence_scope=SCOPE,yaw_convention=CONVENTION,measurements=measurements,best=measure_axis(measurements))
    return dict(boot_id='TEST_ONLY',status='RELATIVE_SOURCE_CONFIRMED',navigation_ready=True,local_cleanup_verified=True,
                initialization_mode='FULL_TURN_AUDIOVISUAL_SEARCH',full_sweep_this_trigger=True,
                calibration_status='RELATIVE_SINGLE_SOURCE_ALIGNED',relative_alignment=dict(boot_id='TEST_ONLY'),
                source_anchor=dict(scope='SYNTHETIC_TEST_ONLY'),confirmation_frame=copy.deepcopy(measurements[-1]['frame']),
                visual=dict(status='SCREEN_CENTER_CONFIRMED',full_sweep_this_trigger=True,measured_axis=axis,
                            center_residual_px=0.,confirmation_frame=copy.deepcopy(measurements[-1]['frame']),
                            sweep=dict(route=route,entry=entry,sweep=sweep,travel=travel,visual_decisions=records,selected=selected)),
                audio_sweep_overlap=dict(seconds=2.097152,basis='AUDIO_TRANSACTION_AND_MOTION_ENVELOPES_NOT_SAMPLE_EXPOSURE_SYNC'))


def main():
    out=ROOT/'logs/av_init_proof_V1'/('offline_'+datetime.now().strftime('%Y%m%d_%H%M%S'));out.mkdir(parents=True)
    checks=[]
    def ck(ok,name):
        checks.append(dict(name=name,passed=bool(ok)))
        if not ok:raise AssertionError(name)
    def reject(fn,name):
        try:fn()
        except (ValueError,KeyError):ck(True,name)
        else:ck(False,name)
    def coordinator(cls,name):
        return cls(out/name,'TEST_ONLY',lambda:dict(ready=True),lambda *args:None,
                   lambda:dict(cleanup_verified=True),clock=lambda:108.5)
    try:
        good=fixture();ck(validate(good,'TEST_ONLY',100.,108.5)['same_boot'],'complete_same_trigger_evidence_accepted')
        changes={
            'missing_full_turn_flag':lambda r:r.pop('full_sweep_this_trigger'),
            'false_full_turn_flag':lambda r:r.update(full_sweep_this_trigger=False),
            'other_boot':lambda r:r.update(boot_id='OLD'),
            'legacy_local_mode':lambda r:r.update(initialization_mode='LOCAL_VIEW'),
            'visual_not_full_turn':lambda r:r['visual'].update(full_sweep_this_trigger=False),
            'five_count_endpoint':lambda r:r['visual']['sweep']['sweep']['samples'][-1].update(position=-1316),
            'feedback_time_gap':lambda r:r['visual']['sweep']['sweep']['samples'][4].update(monotonic=109.),
            'old_motion_interval':lambda r:r['visual']['sweep']['entry'].update(begin=99.),
            'fabricated_travel':lambda r:r['visual']['sweep']['travel'].update(measured_travel=4095),
            'wrong_frame_pose':lambda r:r['visual']['sweep']['visual_decisions'][0]['pose'].update(pose=0),
            'not_winning_frame':lambda r:r['visual']['sweep']['selected'].update(search_quality=.99),
            'old_axis_boot':lambda r:r['visual']['measured_axis'].update(boot_id='OLD'),
            'stale_axis_image':lambda r:r['visual']['measured_axis']['measurements'][0]['frame'].update(read_begin=99.),
            'axis_at_other_return_pose':lambda r:r['visual']['sweep']['selected'].update(goal=1600),
            'confirmation_other_sha':lambda r:r['confirmation_frame'].update(sha256='a'*64),
            'confirmation_from_moving_scan':lambda r:r['confirmation_frame'].update(read_begin=104.),
            'outside_center':lambda r:r['visual'].update(center_residual_px=12.001),
            'no_audio_overlap':lambda r:r['audio_sweep_overlap'].update(seconds=0.),
            'exposure_sync_claim':lambda r:r['audio_sweep_overlap'].update(basis='SENSOR_EXPOSURE_SYNC'),
        }
        for name,change in changes.items():
            candidate=copy.deepcopy(good);change(candidate)
            reject(lambda:validate(candidate,'TEST_ONLY',100.,108.5),name+'_rejected')
        reject(lambda:validate(good,'TEST_ONLY',100.,111.001),'publication_over_eleven_seconds_rejected')
        old=coordinator(Old,'old_contract');raw=copy.deepcopy(good);raw['full_sweep_this_trigger']=False
        old_job=dict(action='initialize',trigger_monotonic=100.)
        old._accept(old_job,raw,108.5)
        ck(old_job['navigation_ready'] is True,'prior_service_acceptance_gap_reproduced_without_hardware')
        old.close(timeout=1.)
        for name,record,accepted in [('complete',good,True),('false_turn',raw,False)]:
            c=coordinator(Coordinator,name);job=dict(action='initialize',trigger_monotonic=100.)
            if accepted:
                c._accept(job,copy.deepcopy(record),108.5)
                ck(job['status']=='RELATIVE_TARGET_CONFIRMED' and c.alignment is not None,'new_contract_accepts_complete_evidence')
            else:
                reject(lambda:c._accept(job,copy.deepcopy(record),108.5),'new_contract_rejects_full_turn_false_before_alignment')
                ck(c.alignment is None,'failed_proof_cannot_publish_mapping')
            c.close(timeout=1.)
        paths=['software/pynq/av_init_proof_V1.py','software/pynq/av_trigger_service_V12.py','scripts/test_av_init_proof_V1.py',
               'software/pynq/av_axis_bootstrap_V1.py','software/pynq/av_screen_sweep_V1.py']
        result=dict(status='PC_PROOF_CONTRACT_PASS',checks=len(checks),assertions=checks,
                    source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in paths},
                    scope='SYNTHETIC_TEST_ONLY',deployed=False,hardware_test=False)
        (out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(dict(status=result['status'],checks=len(checks),result=str(out/'result.json'))))
    except BaseException as exc:
        (out/'result.json').write_text(json.dumps(dict(status='FAILED',error=repr(exc),checks=checks),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':main()
