"""First-trigger axis bootstrap with the original selection/artifact contract."""
from av_phase_follow_V10 import Engine
from av_phase_service_runner_V1 import ServiceEngine as PreviousServiceEngine, Runner


class ServiceEngine(Engine, PreviousServiceEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # V10 initializes without the old V7 axis precondition, so initialize
        # the source-selection state that V1's constructor previously supplied.
        self.selected = None
        self.selected_verdict = None
