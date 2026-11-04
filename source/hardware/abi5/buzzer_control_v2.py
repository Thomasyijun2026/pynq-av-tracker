"""Finite PL buzzer control. No import-time GPIO/overlay/pulse or power-pin drive.

The production adapter is one dual-channel AXI GPIO at 0x43c30000: command
channel at +0, read-only status channel +8. The physical connection and module
polarity must have evidence before arming. No automatic polarity trial.
"""
import threading,time

ADDRESS=0x43c30000
MAX_MS=1000


def validate_wiring(wiring):
    expected={'VCC':12,'IO':10,'GND':11}
    if (wiring.get('connector')!='PMODA'
        or wiring.get('current_module_to_physical')!=expected
        or wiring.get('hardware_drive_allowed') is not True
        or type(wiring.get('active_low')) is not bool
        or not wiring.get('verification_evidence')):
        raise ValueError('BUZZER_WIRING_OR_POLARITY_NOT_VERIFIED')
    return bool(wiring['active_low'])


class Buzzer:
    def __init__(self,mmio,wiring,*,clock=time.monotonic,sleep=time.sleep):
        self.low=validate_wiring(wiring)
        self.io,self.clock,self.sleep=mmio,clock,sleep
        self.lock=threading.Lock();self.command=0;self.armed=False
        if self.status()['abi']!=0x425a:raise ValueError('BUZZER_ABI_MISMATCH')

    def status(self):
        word=self.io.read(8)
        return dict(abi=word>>16,completed=(word>>8)&255,fault=bool(word&128),
            clipped=bool(word&64),pin=bool(word&16),cooldown=bool(word&8),
            token=bool(word&4),busy=bool(word&2),armed=bool(word&1))

    def _write(self,word):
        self.io.write(0,word);self.command=word

    def arm_silent(self,*,pwm=False):
        with self.lock:
            if self.status()['busy']:raise RuntimeError('BUZZER_BUSY')
            self._write(0)
            self._write((1<<31)|(int(self.low)<<30)|(int(pwm)<<29))
            s=self.status()
            if not s['armed'] or s['busy'] or s['pin']!=self.low:
                raise RuntimeError('BUZZER_INACTIVE_LEVEL_READBACK_FAILED')
            self.armed=True
            return s

    def stop(self):
        # Keep an armed module at its verified inactive logic level. High-Z
        # is NOT a claim of silence for an active-low module with a floating IO.
        with self.lock:
            self._write(self.command & 0xffff0000)
            s=self.status()
            if s['busy'] or (s['armed'] and s['pin']!=self.low):
                raise RuntimeError('BUZZER_STOP_READBACK_FAILED')
            return s

    def beep(self,duration_ms=150):
        if isinstance(duration_ms,bool) or not isinstance(duration_ms,int) or not 1<=duration_ms<=MAX_MS:
            raise ValueError('BUZZER_DURATION_MUST_BE_1_TO_1000_MS')
        with self.lock:
            s=self.status()
            if not self.armed or not s['armed']:raise RuntimeError('BUZZER_NOT_ARMED')
            if s['busy'] or s['cooldown']:raise RuntimeError('BUZZER_BUSY_OR_COOLDOWN')
            initial=s['completed'];start=self.clock()
            word=(self.command & 0xffff0000) ^ (1<<28)
            try:
                self._write(word | duration_ms)
                while True:
                    s=self.status()
                    if s['fault']:raise RuntimeError('BUZZER_COMMAND_REJECTED')
                    if not s['busy'] and s['completed']==((initial+1)&255):break
                    if self.clock()-start>duration_ms/1000+.2:raise TimeoutError('BUZZER_FEEDBACK_TIMEOUT')
                    self.sleep(.002)
                if s['pin']!=self.low:raise RuntimeError('BUZZER_INACTIVE_LEVEL_READBACK_FAILED')
                return dict(status='PL_PULSE_COMPLETED',duration_ms=duration_ms,
                    seconds=self.clock()-start,readback=s,audible_verified=False)
            finally:
                self._write(self.command & 0xffff0000)

    def close(self):
        return self.stop()
