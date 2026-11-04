"""Board-owned reference initialization and sound-driven view decisions.

No PC angle or visual verdict enters this API. A known display is used only
for initialization. Later views are driven by accepted microphone observations.
The reference lease and conservative capture age remain explicit.
"""
import copy, hashlib, json, math, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from audio_phase_session_V5 import ReferenceSession, local_direction
from av_pan_alignment_V1 import align, target_count
from av_audio_source_V1 import make_source, validate_source


def atomic(path, value):
    path=Path(path);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False));tmp.replace(path)


class Engine:
    def __init__(self, boot, installation, axis, stimulus_gate, capture, visual_factory, quiet,
                 publish=lambda value:None, clock=time.monotonic, sleep=time.sleep):
        if axis.get('status')!='VISUAL_PAN_RESPONSE_MEASURED' or axis.get('boot_id')!=boot:
            raise ValueError('current_boot_measured_axis_required')
        self.boot,self.installation,self.axis=boot,installation,copy.deepcopy(axis)
        self.gate,self.capture,self.visual_factory,self.quiet=stimulus_gate,capture,visual_factory,quiet
        self.publish,self.clock,self.sleep=publish,clock,sleep
        self.context=self.anchor=self.mapping=None

    def _evidence(self, record):
        meta=record['metadata'];x=record['pcm'];snap=record['snapshot']
        if not all(meta.get(k) is True for k in ('clock_disabled','dma_reset_and_halted','buffer_freed')):
            raise ValueError('audio_cleanup_unconfirmed')
        return dict(self.context.identity,origin='BOARD_CAPTURE',clock_domain='BOARD_MONOTONIC',
            sample_time_basis='TRANSACTION_ENVELOPE_ONLY',cleanup_verified=True,abi=0x444f4105,fir_id=0x08040210,
            begin=meta['transaction_begin_monotonic'],end=meta['transaction_end_monotonic'],
            raw_sha256=meta['raw_sha256'],window_first=snap['first_center_raw_index'],raw_frames=meta['valid_frames'],
            window_sha256=hashlib.sha256(np.asarray(x,dtype='<i4').tobytes()).hexdigest())

    def _quiet_result(self):
        try:return self.quiet()
        except Exception as exc:return dict(cleanup_verified=False,error=repr(exc))

    @staticmethod
    def _close_visual(visual):
        try:return visual.close()
        except Exception as exc:return dict(cleanup_verified=False,error=repr(exc))

    def initialize(self, out, trigger=None):
        out=Path(out);out.mkdir(parents=True,exist_ok=False);begin=self.clock() if trigger is None else trigger
        deadline=begin+11.;self.mapping=self.anchor=None
        self.context=ReferenceSession(self.boot,self.installation,out.name,begin,stimulus_gate=self.gate,initialization_budget=11.)
        r=dict(status='INITIALIZATION_FAILED',boot_id=self.boot,triggered=begin,decision_owner='PYNQ',
               initialization_mode='CURRENT_VISIBLE_KNOWN_SCREEN',full_sweep_this_trigger=False,navigation_ready=False,
               physical_angular_accuracy='NOT_VALIDATED',buzzer_driven=False)
        vision=None;future=None;r['timings']={}
        try:
            self.publish(dict(phase='INITIALIZING_BACKGROUND',triggered=begin))
            # Independent device paths. The worker owns its own ordinary cleanup.
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self._initialize_view,out/'camera',deadline)
                noise=self.capture(out/'background');r['timings']['noise_ready']=self.clock()
                self.context.set_noise(noise['pcm'],self._evidence(noise),self.clock());r['timings']['noise_validated']=self.clock()
                # Finish the protected visual motion and torque cleanup before
                # reference audio; keep failed geometry and timing rejections.
                vision=future.result();r['visual']=vision;r['timings'].setdefault('visual_joined',self.clock())
                if vision.get('status')!='SCREEN_CENTER_CONFIRMED' or not vision.get('cleanup_verified'):
                    raise ValueError('screen_center_unconfirmed')
                r['timings']['reference_announced']=self.clock();self.publish(dict(phase='INITIALIZING_REFERENCE',triggered=begin))
                self.sleep(1.0)  # Bounded post-motion/stimulus settling, within user-approved 11-second budget.
                reference=self.capture(out/'reference');r['timings']['reference_ready']=self.clock()
                geometry=reference['snapshot']['ps_estimate']
                r['reference_decision']=self.context.try_seal(reference['pcm'],self._evidence(reference),self.clock(),geometry);r['timings']['seal_complete']=self.clock()
                vision=future.result();r['visual']=vision;r['timings'].setdefault('visual_joined',self.clock())
                if r['reference_decision']['status']!='REFERENCE_SEALED':raise ValueError('audio_reference_rejected')
                if vision.get('status')!='SCREEN_CENTER_CONFIRMED' or not vision.get('cleanup_verified'):
                    raise ValueError('screen_center_unconfirmed')
                self.anchor=dict(identity=copy.deepcopy(self.context.identity),reference_sha256=self.context.reference_evidence['raw_sha256'],
                    visual_confirmed=True,reference_geometry_valid=True,coordinate_frame='ARRAY_FIXED',
                    vector_basis='BROADBAND_GEOMETRY_ESTIMATE',projected_vector=geometry['fit']['projected_direction_xy'],
                    source_identity='PYNQ_SOUND_MARK_V1_WITH_MATCHED_TEST_WAV',audio_valid=True,
                    audio_bearing_deg=geometry['bearing_deg'],servo_view_position=int(vision['position']))
                self.mapping=align(self.axis,self.anchor,self.installation_record(),self.boot)
                r.update(status='RELATIVE_SOURCE_CONFIRMED',navigation_ready=True,calibration_status='RELATIVE_SINGLE_SOURCE_ALIGNED',
                         relative_alignment=copy.deepcopy(self.mapping),source_anchor=copy.deepcopy(self.anchor),
                         initialization_anchor=copy.deepcopy(self.anchor),confirmation_frame=vision['confirmation_frame'])
        except Exception as exc:
            r.update(error=repr(exc));self.mapping=self.anchor=None
            if self.context is not None:self.context.invalidate('initialization_failed')
        finally:
            # The executor has joined naturally even when audio failed. Retain
            # the worker's cleanup result instead of treating no local result as success.
            if future is not None and vision is None:
                try:vision=future.result()
                except Exception as exc:vision=dict(status='VISUAL_FAILED',cleanup_verified=False,error=repr(exc))
            if vision is not None:r['visual']=vision
            r['reference_manifest']=self.context.manifest();r['postflight']=self._quiet_result()
            r['local_cleanup_verified']=bool(r['postflight']['cleanup_verified'] and vision is not None and vision.get('cleanup_verified'))
            r['completed_monotonic']=self.clock();r['elapsed_seconds']=r['completed_monotonic']-begin
            atomic(out/'result.json',r)
            r['persisted_monotonic']=self.clock();r['elapsed_seconds']=r['persisted_monotonic']-begin
            if r['elapsed_seconds']>11 or not r['local_cleanup_verified']:
                r.update(status='INITIALIZATION_DEADLINE_OR_CLEANUP_FAILED',navigation_ready=False)
                self.mapping=self.anchor=None;self.context.invalidate('initialization_deadline_or_cleanup')
            r['reference_manifest']=self.context.manifest()
            atomic(out/'result.json',r)
            self.publish(dict(phase='ALIGNED' if r['navigation_ready'] else 'INITIALIZATION_REJECTED',result=r))
        return r

    def installation_record(self):
        return dict(boot_id=self.boot,scope='FIXED_INSTALLATION_ONE_TURN',center=727,low=-1321,high=2775)

    def _initialize_view(self, out, deadline):
        visual=self.visual_factory(out,deadline)
        try:
            visual.open()
            result=visual.center(self.axis)
        except Exception as exc:result=dict(status='VISUAL_FAILED',error=repr(exc))
        finally:
            cleanup=self._close_visual(visual)
            result.update(cleanup=cleanup,cleanup_verified=cleanup['cleanup_verified'])
        return result

    def source(self, record, phase_result):
        bearing=local_direction(phase_result,self.anchor)
        answer=copy.deepcopy(record['answer'])
        raw_status=dict(status=answer['status'],bearing_deg=answer.get('bearing_deg'),reasons=answer['reasons'])
        answer.update(status='VALID_BEARING',bearing_deg=bearing['bearing_deg'],coordinate_frame='ARRAY_FIXED',
                      reasons=[],output_monotonic=self.clock())
        event=make_source('audio_'+record['metadata']['raw_sha256'][:16],answer,record['metadata'],record['health'],self.boot)
        event['audio_evidence'].update(estimator='REFERENCE_PHASE_V5',phase=phase_result,
                                      raw_pl_geometry=raw_status,local_direction=bearing)
        validate_source(event,self.boot,self.clock())
        return event

    def follow(self, out, *, move=True, trigger=None):
        out=Path(out);out.mkdir(parents=True,exist_ok=False);begin=self.clock() if trigger is None else trigger
        deadline=begin+5.;visual=None;r=dict(status='AUDIO_REJECTED',boot_id=self.boot,triggered=begin,navigation_ready=False,
            decision_owner='PYNQ',motion_started=False,full_chain_verified=False,buzzer_driven=False)
        try:
            if self.mapping is None or self.anchor is None:raise ValueError('alignment_pending')
            with ThreadPoolExecutor(max_workers=1) as pool:
                if move:
                    visual=self.visual_factory(out/'camera',deadline)
                    ready=pool.submit(visual.open)
                record=self.capture(out/'audio')
                phase_start=self.clock();decision=self.context.evaluate(record['pcm'],self._evidence(record),phase_start)
                r.update(phase=decision,phase_seconds=self.clock()-phase_start)
                if move:ready.result()
                if decision['status']!='VALID_RELATIVE_VECTOR':raise ValueError('audio_phase_rejected')
                event=self.source(record,decision);r['source_event']=event
                if move:
                    pose=visual.position();goal=target_count(self.mapping,event['audio_bearing_deg'],int(pose['position']),self.boot)
                    if self.clock()+visual.minimum_move_time(goal)+.45>=deadline:raise TimeoutError('insufficient_view_deadline')
                    r['motion_started']=True;r['target_count']=goal
                    r['view']=visual.point(goal)
                    r['confirmation_frame']=r['view']['confirmation_frame']
                    r.update(status='AUDIO_SOURCE_VIEW_READY',navigation_ready=True,relative_alignment=copy.deepcopy(self.mapping),
                             calibration_status='RELATIVE_SINGLE_SOURCE_ALIGNED',source_anchor=copy.deepcopy(self.anchor))
                else:r.update(status='AUDIO_OBSERVATION_CAPTURED')
        except Exception as exc:r['error']=repr(exc)
        finally:
            r['visual_cleanup']=self._close_visual(visual) if visual is not None else dict(cleanup_verified=True,not_opened=True)
            r['postflight']=self._quiet_result();r['local_cleanup_verified']=bool(r['postflight']['cleanup_verified'] and r['visual_cleanup']['cleanup_verified'])
            r['completed_monotonic']=self.clock();r['elapsed_seconds']=r['completed_monotonic']-begin
            atomic(out/'result.json',r);r['persisted_monotonic']=self.clock();r['elapsed_seconds']=r['persisted_monotonic']-begin
            if r['elapsed_seconds']>5 or not r['local_cleanup_verified']:
                r.update(status='FOLLOW_DEADLINE_OR_CLEANUP_FAILED',navigation_ready=False)
            atomic(out/'result.json',r);self.publish(dict(phase='FOLLOW_COMPLETE',result=r))
        return r
