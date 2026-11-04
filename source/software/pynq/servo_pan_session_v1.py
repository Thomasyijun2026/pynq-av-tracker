"""Persistent board-local pan coordinates; deterministic cleanup and original arrival test."""
import time
from servo_board_V3 import BoardServo, packet, word_bytes, arrived
from servo_pmod_transport_V3 import PmodPanTransport


class PanSession:
    def __init__(self, mmio, speed=600, acceleration=5, clock=time.monotonic, sleep=time.sleep):
        self.clock,self.sleep=clock,sleep
        self.transport=PmodPanTransport(mmio,clock,sleep)
        self.board=BoardServo(self.transport,clock,sleep)
        self.speed,self.acceleration=speed,acceleration
        self.moves=[];self.opened=False;self.modified=False;self.closed=False
        self.cleanup=None

    def open(self):
        b=self.board
        try:
            pre=b.preflight()
            self.transport.configure_range(b.cfg,b.ram,pre['position'],self.speed,self.acceleration)
            b.command('arm_cleanup',tx=list(packet(3,[40,0])),minimum_rx=6)
            self.modified=True
            b.write('limit_torque',48,word_bytes(b.limit))
            b.write('acceleration',41,[self.acceleration])
            b.write('multiturn_limits',9,[0,0,0,0]);b.off_check('off_after_limits')
            b.write('full_feedback',18,[b.cfg[18]|16]);b.full=True;b.off_check('off_after_phase')
            expected=bytearray(b.cfg);expected[9:13]=b'\0'*4;expected[18]|=16
            if b.read('configuration_full',0,40)!=expected or b.read('lock_full',55,1)!=b'\x01':
                raise ValueError('Full configuration readback')
            f=b.telemetry('full_start')
            if f['moving'] or f['speed']:raise ValueError('Unexpected motion at session start')
            self.transport.rebase(f['position']);self.home=f['position']
            self.low,self.high=self.home-2048,self.home+2048
            self.opened=True
            return dict(status='PAN_READY',home=self.home,low=self.low,high=self.high,
                        speed=self.speed,acceleration=self.acceleration,torque=0)
        except BaseException:
            self.close()
            raise

    def position(self):
        if not self.opened or self.closed:raise ValueError('Session not open')
        return self.board.telemetry('position')

    def move(self,target,timeout=8,observe=None):
        if not self.opened or self.closed or type(target)is not int or not self.low<=target<=self.high:
            raise ValueError('Target outside fixed session -180..180 range')
        if not 0<timeout<=30:raise ValueError('Finite motion deadline required')
        b=self.board;start=self.clock();first=self.position();last=first
        if first['speed'] or first['moving']:raise ValueError('Previous motion not settled')
        record=dict(target=target,start=first['position'],begin=start,status='MOVING',samples=[],decision_owner='PYNQ')
        self.moves.append(record);stable=0
        try:
            b.goal('pan_goal',target,self.speed)
            direction=1 if target>=first['position'] else -1
            while self.clock()-start<timeout:
                f=self.position();record['samples'].append(f)
                dt=f['monotonic']-last['monotonic']
                if (not min(first['position'],target)-4<=f['position']<=max(first['position'],target)+4 or
                    abs(f['speed'])>self.speed*1.5 or
                    (f['position']-last['position'])*direction < -4 or
                    abs(f['position']-last['position'])>max(128,self.speed*2*dt)):
                    raise ValueError('Motion discontinuity or travel guard')
                if observe is not None:observe(f)
                stable=stable+1 if arrived(f,target) else 0
                if stable==3:
                    record.update(status='ARRIVED',end=self.clock(),residual=target-f['position'])
                    return record
                last=f;self.sleep(.03)
            raise TimeoutError('Arrival deadline; original 4-count / 3-stationary-sample test retained')
        except BaseException as exc:
            record.update(status='FAILED',error=repr(exc),end=self.clock())
            # Never continue a scan after an unconfirmed goal.
            self.close()
            raise

    def close(self):
        if self.closed:return self.cleanup
        b=self.board;r=dict(torque_off=False,configuration_restored=False,uart_closed=False,errors=[])
        if self.modified:
            try:
                b.write('torque_disable',40,[0]);b.off_check('off_verified');r['torque_off']=True
                f=b.telemetry('disabled')
                for _ in range(10):
                    if not f['speed'] and not f['moving']:break
                    self.sleep(.05);f=b.telemetry('disabled_settle')
                if f['speed'] or f['moving']:raise ValueError('Residual motion')
                b.write('restore_phase',18,[b.cfg[18]]);b.full=False;b.off_check('off_restore_phase')
                b.write('restore_limits',9,b.cfg[9:13]);b.off_check('off_restore_limits')
                b.write('restore_acceleration',41,[b.ram[1]])
                for addr in (44,46,48):b.write('restore_'+str(addr),addr,b.ram[addr-40:addr-38])
                after=b.read('sram_after',40,10)
                if any(after[j]!=b.ram[j] for j in (0,1,4,5,6,7,8,9)):raise ValueError('SRAM restore mismatch')
                if b.read('configuration_after',0,40)!=b.cfg or b.read('lock_after',55,1)!=b'\x01':raise ValueError('Configuration restore mismatch')
                r['configuration_restored']=True;r['final_feedback']=b.telemetry('final_disabled')
                b.command('disarm_cleanup')
            except BaseException as exc:r['errors'].append(repr(exc))
        else:r.update(torque_off=True,configuration_restored=True)
        try:
            r['uart']=self.transport.close();r['uart_closed']=r['uart']['uart_disabled_and_flushed']
        except BaseException as exc:r['errors'].append(repr(exc))
        r['cleanup_verified']=r['torque_off'] and r['configuration_restored'] and r['uart_closed'] and not r['errors']
        self.closed=True;self.cleanup=r
        return r
