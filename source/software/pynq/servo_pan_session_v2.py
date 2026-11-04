"""Pan session bound to an existing installation envelope, not the latest pose.

V1 protocol and cleanup are reused. The boot-bound one-turn envelope is supplied
by the board's persisted installation record. Repeated requests cannot walk it
around the servo and accumulate cable turns.
"""
import time
from servo_board_V3 import BoardServo
from servo_pan_session_V1 import PanSession as SessionV1
from servo_pmod_transport_V3 import PmodPanTransport


def validate_reference(reference,boot_id):
    if not isinstance(reference,dict) or reference.get('boot_id')!=boot_id:
        raise ValueError('Installation reference boot mismatch')
    if reference.get('scope')!='FIXED_INSTALLATION_ONE_TURN':raise ValueError('Installation reference scope')
    center=reference.get('center');low=reference.get('low');high=reference.get('high')
    if any(type(x)is not int for x in (center,low,high)):raise ValueError('Integer installation envelope required')
    if low!=center-2048 or high!=center+2048 or not -28000<=low<high<=28000:
        raise ValueError('Installation envelope must be exactly one turn')
    if not reference.get('evidence'):raise ValueError('Installation envelope evidence missing')
    return dict(reference)


class FixedTransport(PmodPanTransport):
    def __init__(self,mmio,reference,clock,sleep):
        super().__init__(mmio,clock,sleep)
        self.reference=dict(reference)

    def rebase(self,full_position):
        if not self.reference['low']<=full_position<=self.reference['high']:
            raise ValueError('Current full position outside installation envelope')
        super().rebase(full_position)
        self.trial.update(low=self.reference['low'],high=self.reference['high'],installation_center=self.reference['center'])


class PanSession(SessionV1):
    def __init__(self,mmio,reference,boot_id,speed=1800,acceleration=20,clock=time.monotonic,sleep=time.sleep):
        reference=validate_reference(reference,boot_id)
        self.clock,self.sleep=clock,sleep
        self.speed,self.acceleration=speed,acceleration
        self.moves=[];self.opened=False;self.modified=False;self.closed=False;self.cleanup=None
        self.reference=reference
        self.transport=FixedTransport(mmio,reference,clock,sleep)
        self.board=BoardServo(self.transport,clock,sleep)

    def open(self):
        ready=super().open()
        self.low,self.high=self.reference['low'],self.reference['high']
        ready.update(low=self.low,high=self.high,installation_center=self.reference['center'],
                     envelope_scope=self.reference['scope'])
        return ready

    def nearest_endpoint(self):
        p=self.position()['position']
        return self.low if p-self.low<self.high-p else self.high
