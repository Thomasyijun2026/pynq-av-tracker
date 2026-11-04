"""No total-duration SLA. Device, evidence, geometry and cleanup guards retained."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from av_phase_follow_V10 import Engine as PriorEngine
from av_phase_follow_V7 import atomic, align, target_count
from audio_phase_session_V7 import ReferenceSession
from av_pan_view_probe_V1 import stable


class Engine(PriorEngine):
    def initialize(self, out, trigger=None):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=False)
        begin = self.clock() if trigger is None else trigger
        deadline = None
        self.axis = dict(status="AXIS_UNMEASURED", boot_id=self.boot)
        self.axis_triggered = begin
        self.mapping = self.anchor = None
        self.context = ReferenceSession(self.boot, self.installation, out.name, begin,
                                       stimulus_gate=self.gate, initialization_budget=None)
        result = dict(status='INITIALIZATION_FAILED', boot_id=self.boot, triggered=begin,
                      decision_owner='PYNQ', initialization_mode='FULL_TURN_AUDIOVISUAL_SEARCH',
                      full_sweep_this_trigger=False, navigation_ready=False, buzzer_driven=False,
                      physical_angular_accuracy='NOT_VALIDATED', timings={})
        visual = None
        opened = scanning = None
        try:
            self.publish(dict(phase='INITIALIZING_BACKGROUND', triggered=begin))
            visual = self.visual_factory(out / 'camera', deadline)
            visual.progress = lambda step: self.publish(dict(phase='INITIALIZING_REFERENCE', triggered=begin, visual_step=step))
            with ThreadPoolExecutor(max_workers=1) as pool:
                opened = pool.submit(visual.open)
                noise = self.capture.background(out / 'background')
                self.context.set_noise(noise['pcm'], self._evidence(noise), self.clock())
                opened.result()
                result['timings']['devices_ready'] = self.clock()
                # The PC plays only the first broadband when it sees this board
                # phase. Camera movement and reference capture are board-owned.
                self.publish(dict(phase='INITIALIZING_REFERENCE', triggered=begin,
                                  visual_step='FULL_TURN_SEARCH'))
                # No fixed extra one-second host wait. ABI5 still captures
                # 65536 frames, including its 31250-frame skip and 4096-frame
                # input warmup. The unchanged WAV identity and signal gates
                # reject an absent/late stimulus rather than assuming onset.
                result['timings']['reference_announced'] = self.clock()
                result['background_usage'] = 'RAW_NOISE_ONLY_32768_FRAMES'
                result['reference_frames'] = 65536
                scanning = pool.submit(visual.center, self.axis)
                while not visual.sweep_started.is_set():
                    if scanning.done():
                        scanning.result()
                        raise ValueError('full_sweep_not_started')
                    self.sleep(.005)
                scan_reference = self.capture(out / 'scan_reference')
                result['scan_audio'] = dict(evidence=self._evidence(scan_reference),
                    geometry=copy.deepcopy(scan_reference['snapshot']['ps_estimate']),
                    stimulus=self.gate.evaluate(scan_reference['pcm']),
                    usage='MOVING_SCAN_EVIDENCE_ONLY_NOT_ALIGNMENT_ANCHOR')
                result['timings']['scan_audio_analyzed'] = self.clock()
                vision = scanning.result()
                result['visual'] = vision
                result['full_sweep_this_trigger'] = bool(vision.get('full_sweep_this_trigger'))
                if (vision.get('status') != 'SCREEN_CENTER_CONFIRMED' or
                        not result['full_sweep_this_trigger']):
                    raise ValueError('full_sweep_screen_center_unconfirmed')
                sweep = vision['sweep']['sweep']
                meta = scan_reference['metadata']
                overlap = min(sweep['end'], meta['transaction_end_monotonic']) - max(
                    sweep['begin'], meta['transaction_begin_monotonic'])
                result['audio_sweep_overlap'] = dict(seconds=max(0., overlap),
                    basis='AUDIO_TRANSACTION_AND_MOTION_ENVELOPES_NOT_SAMPLE_EXPOSURE_SYNC')
                if overlap <= 0:
                    raise ValueError('audio_and_full_sweep_did_not_overlap')
                # Independent stationary source confirmation. The display phase
                # restarts the SAME first WAV once, with its own persistent PC
                # playback claim; PC supplies no angle or acceptance decision.
                self.publish(dict(phase='INITIALIZING_STATIC_REFERENCE', triggered=begin,
                                  visual_step='CONFIRMING_STATIONARY_AUDIO'))
                result['timings']['stationary_reference_announced']=self.clock()
                poses=[]
                for _ in range(3):
                    poses.append(visual.position())
                    stable(poses[-1],poses[-1],int(vision['position']))
                    self.sleep(.05)
                reference=self.capture(out/'reference')
                after=visual.position()
                stable(poses[0],after,int(vision['position']))
                evidence=self._evidence(reference)
                result['stationary_audio']=dict(status='STATIONARY_REFERENCE_CAPTURED',
                    before=poses,after=after,evidence=evidence,source_phase='INITIALIZING_STATIC_REFERENCE',
                    confirmed_view_sha256=vision['confirmation_frame']['sha256'],
                    semantics='TRANSACTION_BRACKET_STATIONARY_FEEDBACK_NOT_SAMPLE_EXPOSURE_TIME')
                geometry=reference['snapshot']['ps_estimate']
                result['reference_decision']=self.context.try_seal(
                    reference['pcm'],evidence,self.clock(),geometry)
                result['timings']['reference_analyzed']=self.clock()
                if result['reference_decision']['status'] != 'REFERENCE_SEALED':
                    raise ValueError('audio_reference_rejected')
                self.anchor = dict(identity=copy.deepcopy(self.context.identity),
                    reference_sha256=self.context.reference_evidence['raw_sha256'], visual_confirmed=True,
                    reference_geometry_valid=True, coordinate_frame='ARRAY_FIXED',
                    vector_basis='BROADBAND_GEOMETRY_ESTIMATE', projected_vector=geometry['fit']['projected_direction_xy'],
                    source_identity='PYNQ_SOUND_MARK_V1_WITH_MATCHED_TEST_WAV', audio_valid=True,
                    audio_bearing_deg=geometry['bearing_deg'], servo_view_position=int(vision['position']))
                self.axis = copy.deepcopy(vision['measured_axis'])
                self.mapping = align(self.axis, self.anchor, self.installation_record(), self.boot)
                result.update(status='RELATIVE_SOURCE_CONFIRMED', navigation_ready=True,
                    calibration_status='RELATIVE_SINGLE_SOURCE_ALIGNED', relative_alignment=copy.deepcopy(self.mapping),
                    source_anchor=copy.deepcopy(self.anchor), initialization_anchor=copy.deepcopy(self.anchor),
                    confirmation_frame=vision['confirmation_frame'])
        except Exception as exc:
            result['error'] = repr(exc)
            self.mapping = self.anchor = None
            self.context.invalidate('initialization_failed')
        finally:
            # Executor completion precedes device cleanup, including failure.
            cleanup = self._close_visual(visual) if visual is not None else dict(cleanup_verified=True, not_opened=True)
            result['visual_cleanup'] = cleanup
            result['postflight'] = self._quiet_result()
            result['local_cleanup_verified'] = bool(cleanup['cleanup_verified'] and result['postflight']['cleanup_verified'])
            result['reference_manifest'] = self.context.manifest()
            result['completed_monotonic'] = self.clock()
            result['elapsed_seconds'] = self.clock() - begin
            atomic(out / 'result.json', result)
            result['persisted_monotonic'] = self.clock()
            result['elapsed_seconds'] = self.clock() - begin
            if not result['local_cleanup_verified']:
                result.update(status='INITIALIZATION_CLEANUP_FAILED', navigation_ready=False)
                self.mapping = self.anchor = None
                self.context.invalidate('initialization_cleanup')
            result['reference_manifest'] = self.context.manifest()
            atomic(out / 'result.json', result)
            self.publish(dict(phase='ALIGNED' if result['navigation_ready'] else 'INITIALIZATION_REJECTED', result=result))
        return result

    def follow(self, out, *, move=True, trigger=None):
        out=Path(out);out.mkdir(parents=True,exist_ok=False);begin=self.clock() if trigger is None else trigger
        deadline=None;visual=None;r=dict(status='AUDIO_REJECTED',boot_id=self.boot,triggered=begin,navigation_ready=False,
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
            if not r['local_cleanup_verified']:
                r.update(status='FOLLOW_CLEANUP_FAILED',navigation_ready=False)
            atomic(out/'result.json',r);self.publish(dict(phase='FOLLOW_COMPLETE',result=r))
        return r
