"""One full scan plus target return; no accumulated operation deadline.

Protocol timeouts and a 30-second single-move watchdog remain. Fresh camera
reads have their own five-second stall watchdog. Neither is a total-time SLA.
"""
import time
from av_phase_io_V11 import Visual as Previous
from av_screen_sweep_V2 import plan, verify_travel, associate, choose


class MoveWatchdog:
    def __init__(self, pan): self.pan=pan
    def __getattr__(self, name): return getattr(self.pan,name)
    def move(self, target, timeout=30., **kwargs):
        return self.pan.move(target,timeout=min(30.,timeout),**kwargs)


class Visual(Previous):
    def __init__(self, out, deadline, boot):
        if deadline is not None: raise ValueError('Total operation deadline must be disabled')
        super().__init__(out,float('inf'),boot)
        self.progress=lambda step: None

    def open(self):
        result=super().open()
        self.pan=MoveWatchdog(self.pan)
        return result

    def fresh(self, after):
        end=time.monotonic()+5.
        with self.lock:
            start=self.frames;self.read_goal=start+3
        self.read_request.set()
        while True:
            self.check()
            if self.reader_error: raise RuntimeError(self.reader_error)
            if time.monotonic()>=end: raise TimeoutError('camera_fresh_read_stalled')
            with self.lock:item=self.latest
            if item is not None and item[0]>=start+3 and item[1]>=after:return item
            if item is not None and item[0]>=start+3 and item[1]<after:
                with self.lock:self.read_goal=self.frames+1
                self.read_request.set()
            time.sleep(.005)

    def center(self, axis):
        import threading
        begin=time.monotonic();self._axis_view=None
        route=plan(int(self.position()['position']))
        self.sweep_result=dict(status='FULL_SWEEP_STARTED',route=route,
            visual_decisions=self.scan_rows,counts=self.scan_counts,
            search_pipeline='FIFO32_GRAY_FULL_RES_CELLS_PROPOSAL320',
            scan_count=1,return_motion_is_scan=False,total_duration_limit_seconds=None)
        self.progress('MOVING_TO_SCAN_START')
        entry=self._move(route['entry'],reserve=0.)
        drain=self.fresh(time.monotonic())
        self.sweep_result['entry_drain']=dict(frame_number=drain[0],read_begin=drain[1],read_end=drain[2])
        self.scanning.set();self.sweep_started.set();self.read_request.set()
        self.vision_worker=threading.Thread(target=self._vision,name='screen_sweep_detector',daemon=False)
        self.vision_worker.start();self.progress('SCANNING_ONE_FULL_TURN')
        try:
            sweep=self._move(route['exit'],reserve=0.)
            self.sweep_result.update(entry=entry,sweep=sweep)
        finally:
            self.progress('ANALYZING_SCAN_FRAMES')
            self._finish_scan_worker()
        if self.reader_error or self.scan_error:raise RuntimeError(self.reader_error or self.scan_error)
        q=self.scan_queue.snapshot()
        if q['submitted']!=q['completed'] or q['overflow'] or q['discarded'] or q['failed']:
            raise RuntimeError('scan_queue_incomplete')
        travel=verify_travel(route,entry,sweep);samples=entry['samples']+sweep['samples']
        for row in self.scan_rows:
            row['pose']=associate(row['frame'],samples)
            if not sweep['begin']<=row['frame']['read_begin']<=row['frame']['read_end']<=sweep['end']:
                row['pose']=dict(status='OUTSIDE_FULL_SWEEP',pose=None)
        selected=choose(self.scan_rows,travel)
        self.sweep_result.update(travel=travel,selected=selected,status='RETURNING_TO_SCREEN')
        self.progress('RETURNING_TO_DETECTED_SCREEN')
        self.sweep_result['return_motion']=self._move(selected['goal'],reserve=0.)
        self.last_position=self.position();self.progress('MEASURING_AXIS_WITH_STATIC_FRAMES')
        measured_axis=self._measure_local_axis(axis)
        self.progress('CONFIRMING_SCREEN_CENTER')
        answer=self._center_from_axis_view(measured_axis,begin)
        answer.update(full_sweep_this_trigger=True,sweep=self.sweep_result,measured_axis=measured_axis)
        self.sweep_result['status']='FULL_SWEEP_AND_FRESH_CENTER_CONFIRMED'
        return answer
