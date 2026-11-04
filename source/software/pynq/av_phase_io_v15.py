"""One full scan supplemented by two fresh stationary endpoint search views."""
import time
from av_phase_io_V14 import Visual as Previous
from av_screen_sweep_V2 import plan,verify_travel,choose
from av_search_endpoint_V1 import expected_rows
from av_pan_view_probe_V1 import stable

class Visual(Previous):
    def _endpoint_view(self,role):
        before=self.position()
        item=self.fresh(time.monotonic())
        after=self.position()
        stable(before,after,int(before['position']))
        vision=self.detector.infer(item[3])
        frame=self.save(item,vision)
        return dict(endpoint_role=role,boot_id=self.boot,clock_domain='BOARD_MONOTONIC',
                    position=after['position'],before=before,after=after,
                    frame=frame,vision=vision)

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
        self.sweep_result['endpoint_views']=[self._endpoint_view('ENTRY')]
        self.sweep_result['entry_drain']=self.sweep_result['endpoint_views'][0]['frame']
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
        travel=verify_travel(route,entry,sweep)
        self.sweep_result['endpoint_views'].append(self._endpoint_view('EXIT'))
        rows=expected_rows(self.sweep_result,self.boot,entry['begin'],time.monotonic())
        for row,pose in rows:row['pose']=pose
        selected=choose([row for row,pose in rows],travel)
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
