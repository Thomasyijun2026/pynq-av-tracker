"""Independent full-turn candidate: consume the last fresh stationary axis view.

V9 measured three stationary views and then drained/inferred a fourth view at
the unchanged third pose before deciding whether to center. This version uses
that third measurement once. Any centering movement still requires a new
drained stationary image. Acquisition, fitting, motion and close guards remain.
Not a claim of an eleven-second hardware initialization.
"""
import copy
import hashlib
import time
from pathlib import Path

from av_axis_bootstrap_V1 import validate_fresh_axis
from av_pan_view_probe_V1 import stable
from av_phase_io_V9 import Visual as BootstrapVisual
from av_screen_sweep_V1 import LOW, HIGH, plan, verify_travel, associate, choose


class Visual(BootstrapVisual):
    def _move(self, target, *, reserve):
        self._axis_view = None
        return super()._move(target, reserve=reserve)

    def _measure_local_axis(self, unused_prior_axis):
        measured = super()._measure_local_axis(unused_prior_axis)
        self._axis_view = copy.deepcopy(measured['measurements'][-1])
        return measured

    def _center_from_axis_view(self, axis, triggered):
        self.check()
        validate_fresh_axis(axis, self.boot, triggered, time.monotonic())
        row, self._axis_view = self._axis_view, None
        if row is None or row != axis['measurements'][-1]:
            raise ValueError('axis_confirmation_not_owned_or_already_consumed')
        frame = row['frame']
        path = Path(frame['path'])
        # Only a source already written by this operation can be a confirmation.
        if path.parent.resolve() != self.out.resolve() or path.name != frame['file']:
            raise ValueError('axis_confirmation_source_outside_operation')
        if hashlib.sha256(path.read_bytes()).hexdigest() != frame['sha256']:
            raise ValueError('axis_confirmation_source_changed')
        current = self.position()
        stable(row['after'], current, row['position'])
        found, u = row['vision'], row['u']
        if abs(u - 319.5) <= 12:
            return dict(status='SCREEN_CENTER_CONFIRMED', position=round(row['position']),
                        goal=current['position'],
                        move=dict(status='NOT_NEEDED_ALREADY_CENTERED', command_sent=False),
                        vision=found, center_residual_px=u - 319.5, confirmation_frame=frame,
                        center_input='THIS_TRIGGER_THIRD_STATIONARY_AXIS_VIEW')
        slope = axis['best']['pixel_u_per_count']
        goal = round(current['position'] + (319.5 - u) / slope)
        if abs(goal - current['position']) > 256 or not LOW <= goal <= HIGH:
            raise ValueError('local_center_outside_measured_region')
        self.check()
        move = self.pan.move(goal, timeout=max(.1, self.deadline - time.monotonic() - .5),
                             observe=self.check)
        settled = time.monotonic()
        before = self.position()
        item = self.fresh(settled)
        after = self.position()
        q = stable(before, after, goal)
        found = self.detector.infer(item[3])
        saved = self.save(item, found)
        if found.get('status') != 'KNOWN_SCREEN_MATCHED':
            raise ValueError('return_marker_not_confirmed')
        if abs(found['best']['center_px'][0] / 2 - 319.5) > 12:
            raise ValueError('screen_center_residual_exceeded')
        return dict(status='SCREEN_CENTER_CONFIRMED', position=round(q), goal=goal,
                    move=move, vision=found, center_residual_px=found['best']['center_px'][0] / 2 - 319.5,
                    confirmation_frame=saved,
                    center_input='THIS_TRIGGER_THIRD_STATIONARY_AXIS_VIEW_THEN_FRESH_RETURN')

    def center(self, axis):
        begin = time.monotonic()
        self._axis_view = None
        route = plan(int(self.position()['position']))
        self.sweep_result = dict(status='FULL_SWEEP_STARTED', route=route,
                                visual_decisions=self.scan_rows, counts=self.scan_counts)
        self.scanning.set()
        self.read_request.set()
        import threading
        self.vision_worker = threading.Thread(target=self._vision, name='screen_sweep_detector', daemon=False)
        self.vision_worker.start()
        try:
            entry = self._move(route['entry'], reserve=3.8)
            self.sweep_started.set()
            sweep = self._move(route['exit'], reserve=1.6)
            self.sweep_result.update(entry=entry, sweep=sweep)
        finally:
            self._finish_scan_worker()
        if self.reader_error or self.scan_error:
            raise RuntimeError(self.reader_error or self.scan_error)
        travel = verify_travel(route, entry, sweep)
        samples = entry['samples'] + sweep['samples']
        for row in self.scan_rows:
            row['pose'] = associate(row['frame'], samples)
            if not sweep['begin'] <= row['frame']['read_begin'] <= row['frame']['read_end'] <= sweep['end']:
                row['pose'] = dict(status='OUTSIDE_FULL_SWEEP', pose=None)
        selected = choose(self.scan_rows, travel)
        self.sweep_result.update(travel=travel, selected=selected, status='RETURNING_TO_SCREEN')
        self._move(selected['goal'], reserve=.6)
        self.last_position = self.position()
        measured_axis = self._measure_local_axis(axis)
        answer = self._center_from_axis_view(measured_axis, begin)
        answer.update(full_sweep_this_trigger=True, sweep=self.sweep_result, measured_axis=measured_axis)
        self.sweep_result['status'] = 'FULL_SWEEP_AND_FRESH_CENTER_CONFIRMED'
        return answer
