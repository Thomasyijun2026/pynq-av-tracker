"""V1 lifetime plus a computed known-stimulus identity gate before sealing."""
import copy
from audio_phase_session_V1 import ReferenceSession as LifetimeSession
from audio_stimulus_gate_V2 import StimulusGate


class ReferenceSession(LifetimeSession):
    def __init__(self, *args, stimulus_gate, **kwargs):
        if not isinstance(stimulus_gate, StimulusGate):
            raise ValueError('computed_stimulus_gate_required')
        super().__init__(*args, **kwargs)
        self.stimulus_gate = stimulus_gate
        self.stimulus_evidence = None
        self.reference_geometry = None

    def seal(self, *args, **kwargs):
        raise ValueError('use_try_seal_with_identity_and_geometry')

    def try_seal(self, pcm, evidence, now, geometry):
        try:
            self.validate(pcm, evidence, now)
            self.stimulus_evidence = self.stimulus_gate.evaluate(pcm)
            self.reference_geometry = copy.deepcopy(geometry)
            reasons = []
            if self.stimulus_evidence['status'] != 'SOURCE_PRESENT':
                reasons += ['known_stimulus_not_detected'] + self.stimulus_evidence['reasons']
            if geometry.get('valid') is not True or geometry.get('reasons') != []:
                reasons += ['reference_geometry_rejected']
            if reasons:
                self.invalidate(';'.join(reasons))
                return dict(status='REFERENCE_REJECTED', reasons=reasons, delta_vector=None)
            super().seal(pcm, evidence, now)
            return dict(status='REFERENCE_SEALED', reasons=[], delta_vector=None)
        except (ValueError, TypeError, KeyError) as exc:
            self.invalidate(str(exc))
            return dict(status='REFERENCE_REJECTED', reasons=[str(exc)], delta_vector=None)

    def manifest(self):
        result = super().manifest()
        result.update(schema='AUDIO_PHASE_SESSION_V3', stimulus=copy.deepcopy(self.stimulus_evidence),
                      reference_geometry=copy.deepcopy(self.reference_geometry),
                      known_source_scope='PRESENCE_AND_GEOMETRY_GATES_NOT_PHYSICAL_CALIBRATION')
        return result
