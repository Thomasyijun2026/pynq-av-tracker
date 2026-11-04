"""Independent 500/1000 RAM duty candidate; V4 timing and guards retained."""
import time
from servo_pan_session_V2 import PanSession as FixedSession,FixedTransport
from servo_pan_session_V3 import ProfileTransport
from servo_pan_session_V4 import PanSession as PriorSession,TimedProfileBoard

class TrialTransport(ProfileTransport):
    def __init__(self,mmio,reference,clock,sleep,limit):
        if type(limit)is not int or limit not in (200,300,400,500):raise ValueError('Unverified duty profile')
        FixedTransport.__init__(self,mmio,reference,clock,sleep);self.limit=limit

class PanSession(PriorSession):
    def __init__(self,mmio,reference,boot_id,speed=1800,acceleration=20,torque_limit=500,clock=time.monotonic,sleep=time.sleep):
        FixedSession.__init__(self,mmio,reference,boot_id,speed,acceleration,clock,sleep)
        self.transport=TrialTransport(mmio,self.reference,clock,sleep,torque_limit)
        self.board=TimedProfileBoard(self.transport,clock,sleep,torque_limit)
        self.profile_limit=torque_limit
