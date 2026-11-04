"""Initialization establishes the map; later sound uses current PL directions."""
import copy
from pathlib import Path
from av_phase_follow_V13 import Engine as Previous
from av_phase_follow_V7 import atomic, target_count
from av_pl_follow_source_V1 import current_source, admit


class Engine(Previous):
    def source(self, record, phase_result=None):
        return current_source(record, self.boot)

    def follow(self, out, *, move=True, trigger=None):
        out = Path(out); out.mkdir(parents=True, exist_ok=False)
        begin = self.clock() if trigger is None else trigger
        visual = None
        r = dict(status='AUDIO_REJECTED', boot_id=self.boot, triggered=begin,
                 navigation_ready=False, decision_owner='PYNQ', motion_started=False,
                 full_chain_verified=False, buzzer_driven=False,
                 follow_estimator='CURRENT_PL_FRACTIONAL_CONSENSUS_V1')
        try:
            if self.mapping is None or self.anchor is None:
                raise ValueError('alignment_pending')
            record = self.capture(out/'audio')
            event = self.source(record, None)
            r['source_event'] = event
            if not event['audio_valid']:
                r['audio_rejection_reasons'] = copy.deepcopy(event['audio_evidence']['reasons'])
                raise ValueError('current_audio_direction_inconclusive')
            r['source_admission'] = admit(event, self.boot, self.clock())
            if move:
                visual = self.visual_factory(out/'camera', None)
                visual.open()
                pose = visual.position()
                # Hardware freshness stays five seconds, measured from capture start.
                r['source_admission'] = admit(event, self.boot, self.clock())
                goal = target_count(self.mapping, event['audio_bearing_deg'], int(pose['position']), self.boot)
                r['motion_started'] = True; r['target_count'] = goal
                r['view'] = visual.point(goal)
                r['confirmation_frame'] = r['view']['confirmation_frame']
                r.update(status='AUDIO_SOURCE_VIEW_READY', navigation_ready=True,
                         relative_alignment=copy.deepcopy(self.mapping),
                         calibration_status='RELATIVE_SINGLE_SOURCE_ALIGNED',
                         source_anchor=copy.deepcopy(self.anchor))
            else:
                r['status'] = 'AUDIO_OBSERVATION_CAPTURED'
        except Exception as exc:
            r['error'] = repr(exc)
        finally:
            r['visual_cleanup'] = (self._close_visual(visual) if visual is not None else
                                   dict(cleanup_verified=True, not_opened=True))
            r['postflight'] = self._quiet_result()
            r['local_cleanup_verified'] = bool(r['postflight']['cleanup_verified'] and
                                               r['visual_cleanup']['cleanup_verified'])
            r['completed_monotonic'] = self.clock()
            r['elapsed_seconds'] = r['completed_monotonic'] - begin
            atomic(out/'result.json', r)
            r['persisted_monotonic'] = self.clock()
            r['elapsed_seconds'] = r['persisted_monotonic'] - begin
            if not r['local_cleanup_verified']:
                r.update(status='FOLLOW_CLEANUP_FAILED', navigation_ready=False)
            atomic(out/'result.json', r)
            self.publish(dict(phase='FOLLOW_COMPLETE', result=r))
        return r
