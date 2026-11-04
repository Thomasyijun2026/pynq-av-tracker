"""No total initialization limit, with unchanged evidence and phase semantics."""
from audio_phase_session_V4 import ReferenceSession as Prior, local_direction, phase
from audio_phase_session_V1 import number, text
from audio_stimulus_gate_V2 import StimulusGate


class ReferenceSession(Prior):
    def __init__(self, boot_id, installation_id, epoch, triggered,
                 initialization_budget=None, lease_seconds=60., *, stimulus_gate):
        if not isinstance(stimulus_gate, StimulusGate):
            raise ValueError('computed_stimulus_gate_required')
        self.identity = dict(boot_id=text(boot_id), installation_id=text(installation_id), epoch=text(epoch))
        self.triggered = number(triggered)
        self.budget = float("inf")
        if initialization_budget is not None:
            raise ValueError("Total duration limit must be disabled")
        self.lease = number(lease_seconds)
        if self.triggered < 0 or not 0 < self.lease <= 3600:
            raise ValueError('invalid_bounded_lifetime')
        self.noise = self.reference = None
        self.noise_evidence = self.reference_evidence = None
        self.seen = set()
        self.invalidated = None
        self.stimulus_gate = stimulus_gate
        self.stimulus_evidence = None
        self.reference_geometry = None

    def manifest(self):
        result = super().manifest()
        result.update(schema='AUDIO_PHASE_SESSION_V7', initialization_budget_seconds=None, initialization_budget=None)
        return result
