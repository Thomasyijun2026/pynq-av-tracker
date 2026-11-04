"""Boot-bound motion admission and cooperative cancellation, without hardware imports.

Cleanup is never cancelled. Guarded register proxies exist only while the
original capture transaction runs; the original caller retains DMA ownership.
"""
import math,time


class OperationStopped(RuntimeError):
    pass


class Deadline:
    def __init__(self,end,cancelled=lambda:False,clock=time.monotonic,cleanup_reserve=1.2):
        if not all(math.isfinite(v) for v in (end,cleanup_reserve)) or cleanup_reserve<0:
            raise ValueError('Finite deadline/reserve required')
        self.end,self.cancelled,self.clock,self.reserve=end,cancelled,clock,cleanup_reserve

    def check(self):
        if self.cancelled():raise OperationStopped('CANCEL_REQUESTED')
        if self.clock()>=self.end-self.reserve:raise OperationStopped('DEADLINE_CLEANUP_RESERVE')

    def remaining(self,maximum):
        self.check()
        if not math.isfinite(maximum) or maximum<=0:raise ValueError('Positive phase limit')
        return min(maximum,self.end-self.reserve-self.clock())


class MotionWindow:
    def __init__(self,record,boot,clock=time.monotonic):
        fields=('anchor_monotonic','not_before_monotonic','stop_monotonic')
        if record.get('boot_id')!=boot:raise ValueError('Motion window boot mismatch')
        if any(type(record.get(k)) not in (int,float) or not math.isfinite(record[k]) for k in fields):
            raise ValueError('Finite monotonic window required')
        if not record['anchor_monotonic']<=record['not_before_monotonic']<record['stop_monotonic']:
            raise ValueError('Invalid motion window order')
        if record['stop_monotonic']-record['anchor_monotonic']>86400:raise ValueError('Window longer than one day')
        self.record=dict(record);self.boot=boot;self.clock=clock

    def admission(self,current_boot,operation_seconds=10.):
        now=self.clock();r=self.record
        if current_boot!=self.boot:return dict(ready=False,reason='BOOT_CHANGED')
        if now<r['anchor_monotonic']:return dict(ready=False,reason='MONOTONIC_BEFORE_ANCHOR')
        if now<r['not_before_monotonic']:return dict(ready=False,reason='MOTION_NOT_BEFORE_0300',remaining_seconds=r['not_before_monotonic']-now)
        if not 0<operation_seconds<=10:raise ValueError('Operation budget')
        if now+operation_seconds>r['stop_monotonic']:return dict(ready=False,reason='NIGHT_DEADLINE')
        return dict(ready=True,boot_id=self.boot,monotonic=now)


class _Registers:
    def __init__(self,registers,deadline):self.registers,self.deadline=registers,deadline
    def read(self,*args,**kwargs):
        self.deadline.check();return self.registers.read(*args,**kwargs)
    def write(self,*args,**kwargs):
        self.deadline.check();return self.registers.write(*args,**kwargs)


class CancelableMic:
    def __init__(self,module,deadline):self.module,self.deadline=module,deadline
    def __getattr__(self,name):return getattr(self.module,name)
    def capture_transaction(self,ctrl,dma,buffer,frames,timeout):
        timeout=self.deadline.remaining(min(timeout,6.))
        return self.module.capture_transaction(_Registers(ctrl,self.deadline),_Registers(dma,self.deadline),buffer,frames,timeout)
