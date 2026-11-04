"""Fresh V3 source lifetime with the numerically equivalent V5 phase estimator."""
import copy
from audio_phase_session_V3 import ReferenceSession as Prior
from audio_phase_session_V1 import number, local_direction
import audio_phase_reference_V5 as phase


class ReferenceSession(Prior):
    def evaluate(self, pcm, evidence, now):
        try:
            if self.reference is None:
                raise ValueError('reference_missing')
            x, ev, sha = self.validate(pcm, evidence, now)
            if number(now) - self.reference_evidence['begin'] > self.lease:
                raise ValueError('reference_lease_expired')
            if ev['begin'] < self.reference_evidence['end']:
                raise ValueError('observation_precedes_reference')
            if sha in self.seen:
                raise ValueError('recording_already_consumed')
            self.seen.add(sha)
            result = phase.estimate(self.reference, x, self.noise)
            result.update(identity=copy.deepcopy(self.identity), reference_sha256=self.reference_evidence['raw_sha256'],
                          raw_sha256=sha, capture_evidence=ev, reference_age_seconds=number(now)-self.reference_evidence['begin'])
            return result
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return dict(status='REJECTED', reasons=[str(exc)], delta_vector=None, global_bearing=None, servo_command=None)

    def manifest(self):
        result=super().manifest()
        result.update(schema='AUDIO_PHASE_SESSION_V4',estimator='V5_EXACT_LAG_TRANSFORM',policy=copy.deepcopy(phase.POLICY))
        return result
