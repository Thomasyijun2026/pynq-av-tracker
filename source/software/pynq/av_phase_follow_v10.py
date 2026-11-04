"""Full-turn V10: initial alignment measures axis inside the trigger operation.

Reuses V9 capture/parallel sweep/deadline/finalizers and V7 follow. A stored
axis is never promoted into this operation's measured response.
"""
import time
from av_phase_follow_V9 import Engine as PreviousEngine
from av_axis_bootstrap_V1 import validate_fresh_axis


class Engine(PreviousEngine):
    def __init__(self, boot, installation, unused_axis, stimulus_gate, capture, visual_factory, quiet,
                 publish=lambda value: None, clock=time.monotonic, sleep=time.sleep):
        # The old constructor required calibration before the first calibration
        # could start. Readiness now means the service can acquire that evidence.
        self.boot, self.installation = boot, installation
        self.gate, self.capture, self.visual_factory, self.quiet = stimulus_gate, capture, visual_factory, quiet
        self.publish, self.clock, self.sleep = publish, clock, sleep
        self.context = self.anchor = self.mapping = None
        self.axis = dict(status='AXIS_UNMEASURED', boot_id=boot)
        self.axis_triggered = None

    def initialize(self, out, trigger=None):
        begin = self.clock() if trigger is None else trigger
        self.axis = dict(status='AXIS_UNMEASURED', boot_id=self.boot)
        self.axis_triggered = begin
        return super().initialize(out, trigger=begin)

    def installation_record(self):
        # V9 calls this immediately before align(). Validate independent fresh
        # image/position evidence again, including its relation to this trigger.
        if self.axis_triggered is None:
            raise ValueError('axis_initialization_not_started')
        validate_fresh_axis(self.axis, self.boot, self.axis_triggered, self.clock())
        return super().installation_record()
