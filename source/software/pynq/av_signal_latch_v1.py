"""Latched stop flag for Python signal handlers, with no handler-side locks.

Polling waits are bounded to 20 ms. This is only a stop request; hardware
cleanup remains in the runtime's normal finally path.
"""
import math
import time


class SignalLatch:
    def __init__(self, clock=time.monotonic, sleeper=time.sleep):
        self._requested=False
        self._clock=clock
        self._sleep=sleeper

    def set(self, *_):
        # Python's main-thread signal handler can interrupt a locked Condition.
        # Do not log, call Event.set, acquire locks or perform hardware I/O here.
        self._requested=True

    def is_set(self):
        return self._requested

    def wait(self, timeout):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0:
            raise ValueError('Finite nonnegative stop wait required')
        deadline=self._clock()+timeout
        while not self._requested:
            remaining=deadline-self._clock()
            if remaining<=0:break
            self._sleep(min(.02,remaining))
        return self._requested
