"""Unchanged Linux lifetime/intent backend with exact runtime V5 readiness."""
from av_runtime_backend_V1 import Backend as Previous, LinuxHost, identity
from av_runtime_binding_V2 import validate_plan, readiness, explicit_finish


class Backend(Previous):
    def __init__(self, plan, *args, **kwargs):
        validate_plan(plan)
        super().__init__(plan, *args, **kwargs)

    def observe(self, record, now):
        if record.get('status') == 'ABORTED_BEFORE_SPAWN':
            return dict(status='START_ABORTED_BEFORE_SPAWN', ready=False, automatic_initialize=False,
                        reason=record.get('reason'))
        if (record.get('status') != 'SPAWNED' or record.get('plan_sha256') != self.plan['plan_sha256']
                or record.get('config_sha256') != self.plan['config_sha256']):
            return dict(status='START_OUTCOME_UNKNOWN', ready=False, automatic_initialize=False)
        return readiness(record, self.observation(record), self.host.now())

    def finish_description(self, operator_requested=False):
        record = self.intent()
        if record is None: return dict(status='NO_ATTEMPT', request=None)
        return explicit_finish(record, self.observation(record), self.host.now(), operator_requested)
