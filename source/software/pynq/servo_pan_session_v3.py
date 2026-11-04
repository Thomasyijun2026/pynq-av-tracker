"""Bounded RAM-only duty/torque profile, retaining fixed travel and protections.

V2 telemetry plateaued at exactly 200/1000 output duty during 97% of cruise.
V3 permits separately tested 200/300/400 profiles. Voltage/current/temperature,
arrival, EEPROM lock, fixed envelope, torque-off and restoration are unchanged.
"""
import time
from servo_pan_session_V2 import PanSession as SessionV2,FixedTransport
from servo_board_V3 import BoardServo,word


class ProfileTransport(FixedTransport):
    def __init__(self,mmio,reference,clock,sleep,limit):
        if type(limit)is not int or limit not in (200,300,400):raise ValueError('Unverified duty profile')
        super().__init__(mmio,reference,clock,sleep);self.limit=limit

    def validate(self,request):
        data=request.get('tx',[])
        if len(data)>=6 and data[4]==3 and data[5]==48:
            if (request.get('op')!='exchange' or request.get('decision_owner')!='PYNQ' or
                any(type(x)is not int or not 0<=x<=255 for x in data)):
                raise ValueError('Board-owned valid write required')
            tx=bytes(data)
            if (len(tx)!=9 or tx[:3]!=b'\xff\xff\x01' or tx[3]!=5 or
                sum(tx[2:])%256!=255 or request.get('minimum_rx')!=6 or
                not self.armed or not self.torque_off or self.trial is None):
                raise ValueError('Torque-off bounded profile required')
            baseline=word(self.trial['ram'],8)
            if word(tx,6) not in (min(self.limit,baseline),baseline):raise ValueError('Duty profile exceeded')
            return tx,6
        return super().validate(request)


class ProfileBoard(BoardServo):
    def __init__(self,transport,clock,sleep,limit):
        super().__init__(transport,clock,sleep);self.profile_limit=limit

    def preflight(self):
        result=super().preflight()
        self.limit=min(self.profile_limit,word(self.ram,8))
        result['ram_duty_limit']=self.limit
        return result


class PanSession(SessionV2):
    def __init__(self,mmio,reference,boot_id,speed=1800,acceleration=20,torque_limit=300,clock=time.monotonic,sleep=time.sleep):
        super().__init__(mmio,reference,boot_id,speed,acceleration,clock,sleep)
        self.transport=ProfileTransport(mmio,self.reference,clock,sleep,torque_limit)
        self.board=ProfileBoard(self.transport,clock,sleep,torque_limit)
        self.profile_limit=torque_limit

    def open(self):
        result=super().open();result['ram_duty_limit']=self.board.limit
        if self.board.read('profile_duty_readback',48,2)!=bytes([self.board.limit&255,self.board.limit>>8]):
            self.close();raise ValueError('Duty profile readback mismatch')
        return result
