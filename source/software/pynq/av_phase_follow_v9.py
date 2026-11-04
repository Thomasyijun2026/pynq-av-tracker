"""Independent full-sweep V9 candidate with a raw-only noise acquisition.

The PL localization window, phase/identity gates, motion guards and V7 follow
remain unchanged. No constant wait is used as proof that playback has begun.
Not deployed over an active runtime; requires fresh bounded hardware acceptance.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from av_phase_follow_V7 import Engine as PriorEngine, atomic, ReferenceSession, align


class Engine(PriorEngine):
    def initialize(self, out, trigger=None):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=False)
        begin = self.clock() if trigger is None else trigger
        deadline = begin + 11.
        self.mapping = self.anchor = None
        self.context = ReferenceSession(self.boot, self.installation, out.name, begin,
                                       stimulus_gate=self.gate, initialization_budget=11.)
        result = dict(status='INITIALIZATION_FAILED', boot_id=self.boot, triggered=begin,
                      decision_owner='PYNQ', initialization_mode='FULL_TURN_AUDIOVISUAL_SEARCH',
                      full_sweep_this_trigger=False, navigation_ready=False, buzzer_driven=False,
                      physical_angular_accuracy='NOT_VALIDATED', timings={})
        visual = None
        opened = scanning = None
        try:
            if self.clock() >= deadline:
                raise TimeoutError('initialization_request_expired_before_devices')
            self.publish(dict(phase='INITIALIZING_BACKGROUND', triggered=begin))
            visual = self.visual_factory(out / 'camera', deadline)
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
                    if self.clock() >= deadline - 1.5:
                        raise TimeoutError('full_sweep_start_deadline')
                    self.sleep(.005)
                reference = self.capture(out / 'reference')
                geometry = reference['snapshot']['ps_estimate']
                result['reference_decision'] = self.context.try_seal(
                    reference['pcm'], self._evidence(reference), self.clock(), geometry)
                result['timings']['reference_analyzed'] = self.clock()
                vision = scanning.result()
                result['visual'] = vision
                result['full_sweep_this_trigger'] = bool(vision.get('full_sweep_this_trigger'))
                if (vision.get('status') != 'SCREEN_CENTER_CONFIRMED' or
                        not result['full_sweep_this_trigger']):
                    raise ValueError('full_sweep_screen_center_unconfirmed')
                sweep = vision['sweep']['sweep']
                meta = reference['metadata']
                overlap = min(sweep['end'], meta['transaction_end_monotonic']) - max(
                    sweep['begin'], meta['transaction_begin_monotonic'])
                result['audio_sweep_overlap'] = dict(seconds=max(0., overlap),
                    basis='AUDIO_TRANSACTION_AND_MOTION_ENVELOPES_NOT_SAMPLE_EXPOSURE_SYNC')
                if overlap <= 0:
                    raise ValueError('audio_and_full_sweep_did_not_overlap')
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
            if result['elapsed_seconds'] > 11 or not result['local_cleanup_verified']:
                result.update(status='INITIALIZATION_DEADLINE_OR_CLEANUP_FAILED', navigation_ready=False)
                self.mapping = self.anchor = None
                self.context.invalidate('initialization_deadline_or_cleanup')
            result['reference_manifest'] = self.context.manifest()
            atomic(out / 'result.json', result)
            self.publish(dict(phase='ALIGNED' if result['navigation_ready'] else 'INITIALIZATION_REJECTED', result=result))
        return result
