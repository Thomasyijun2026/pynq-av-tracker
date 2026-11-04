"""V5 motion guards with explicit, persistent servo accumulator translation."""
import time
from servo_coordinates_V1 import load_or_create
from servo_pan_session_V2 import PanSession as FixedSession
from servo_pan_session_V4 import TimedProfileBoard
from servo_pan_session_V5 import PanSession as Previous, TrialTransport
from servo_pmod_transport_V3 import PmodPanTransport


class CoordinateTransport(TrialTransport):
    def __init__(self, mmio, reference, clock, sleep, limit, coordinate_path):
        super().__init__(mmio, reference, clock, sleep, limit)
        self.coordinate_path = coordinate_path
        self.datum = None

    def rebase(self, raw):
        single = self.trial['home']
        PmodPanTransport.rebase(self, raw)
        datum = load_or_create(self.coordinate_path, raw, single, self.reference)
        offset = datum['raw_minus_installation']
        self.trial.update(low=self.reference['low']+offset,
                          high=self.reference['high']+offset,
                          installation_center=self.reference['center'],
                          raw_minus_installation=offset)
        self.datum = datum


class CoordinateBoard(TimedProfileBoard):
    def map_feedback(self, feedback):
        if self.full and self.transport.datum is not None and 'raw_position' not in feedback:
            offset = self.transport.datum['raw_minus_installation']
            feedback.update(raw_position=feedback['position'],
                            raw_internal_target=feedback['internal_target'],
                            raw_minus_installation=offset,
                            coordinate_frame='FIXED_INSTALLATION_ONE_TURN')
            feedback['position'] -= offset
            feedback['internal_target'] -= offset
        return feedback

    def telemetry(self, name):
        return self.map_feedback(super().telemetry(name))

    def goal(self, name, value, speed):
        datum = self.transport.datum
        if datum is None or type(value) is not int:
            raise ValueError('Fixed installation datum required before a goal')
        ref = self.transport.reference
        if not ref['low'] <= value <= ref['high']:
            raise ValueError('Goal outside fixed installation envelope')
        raw = value+datum['raw_minus_installation']
        super().goal(name, raw, speed)


class PanSession(Previous):
    def __init__(self, mmio, reference, boot_id, coordinate_path, speed=1800,
                 acceleration=20, torque_limit=500, clock=time.monotonic, sleep=time.sleep):
        FixedSession.__init__(self, mmio, reference, boot_id, speed, acceleration, clock, sleep)
        self.transport = CoordinateTransport(mmio, self.reference, clock, sleep,
                                             torque_limit, coordinate_path)
        self.board = CoordinateBoard(self.transport, clock, sleep, torque_limit)
        self.profile_limit = torque_limit

    def open(self):
        ready = super().open()
        raw = self.home
        self.home = raw-self.transport.datum['raw_minus_installation']
        self.board.map_feedback(self.board.feedback[-1])
        ready.update(home=self.home, raw_home=raw, datum=dict(self.transport.datum))
        return ready

    def close(self):
        result = super().close()
        result['coordinate_datum'] = self.transport.datum
        return result
