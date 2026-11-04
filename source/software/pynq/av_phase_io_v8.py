"""Operation-scoped full-turn screen search, followed by a new stationary image.

V7 hardware guards and close path are retained. A moving image gives only a
return hint. The ordinary center procedure must then confirm a fresh view.
"""
import threading
import time
import copy
from av_phase_io_V7 import Visual as PriorVisual, Capture, installation_reference
from av_screen_sweep_V1 import plan, verify_travel, associate, choose


class Visual(PriorVisual):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scanning = threading.Event()
        self.scan_finished = threading.Event()
        self.sweep_started = threading.Event()
        self.vision_worker = None
        self.scan_rows = []
        self.scan_error = None
        self.scan_counts = dict(processed=0, replaced=0)
        self.sweep_result = None

    def _read(self):
        try:
            while not self.stop.is_set() and time.monotonic() < self.deadline:
                with self.lock:
                    requested = self.frames < self.read_goal or self.scanning.is_set()
                if not requested:
                    self.read_request.wait(.02)
                    self.read_request.clear()
                    continue
                begin = time.monotonic()
                ok, frame = self.cap.read()
                end = time.monotonic()
                if not ok or frame.shape != (960, 1280, 3):
                    raise RuntimeError('camera_read_failed')
                with self.lock:
                    self.frames += 1
                    self.latest = (self.frames, begin, end, frame)
        except Exception as exc:
            self.reader_error = repr(exc)

    def _vision(self):
        previous = 0
        try:
            while not self.scan_finished.is_set() and not self.stop.is_set():
                self.check()
                with self.lock:
                    item = self.latest
                if item is None or item[0] <= previous:
                    time.sleep(.003)
                    continue
                if previous:
                    self.scan_counts['replaced'] += max(0, item[0] - previous - 1)
                previous = item[0]
                found = self.detector.infer(item[3])
                frame = self.save(item, found)
                self.scan_rows.append(dict(frame=frame, vision=found))
                self.scan_counts['processed'] += 1
        except Exception as exc:
            self.scan_error = repr(exc)

    def _move(self, target, *, reserve):
        self.check()
        available = self.deadline - time.monotonic() - reserve
        if available <= 0:
            raise TimeoutError('full_sweep_remaining_budget')
        return self.pan.move(target, timeout=min(30., available), observe=self.check)

    def _finish_scan_worker(self):
        self.scan_finished.set()
        if self.vision_worker is not None:
            # A running inference owns its image and finishes normally. No device
            # is released underneath a reader or consumer.
            self.vision_worker.join()
        self.scanning.clear()

    def center(self, axis):
        route = plan(int(self.position()['position']))
        self.sweep_result = dict(status='FULL_SWEEP_STARTED', route=route,
                                 visual_decisions=self.scan_rows, counts=self.scan_counts)
        self.scanning.set()
        self.read_request.set()
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
        # V7 performs a new three-read drain and stationary checks before and
        # after inference, optionally a bounded local centering move, and a new
        # confirmation. No sweep frame can be accepted as this confirmation.
        answer = super().center(measured_axis)
        answer.update(full_sweep_this_trigger=True, sweep=self.sweep_result, measured_axis=measured_axis)
        self.sweep_result['status'] = 'FULL_SWEEP_AND_FRESH_CENTER_CONFIRMED'
        return answer

    def _stationary_marker(self):
        from av_pan_view_probe_V1 import stable
        before = self.position()
        item = self.fresh(time.monotonic())
        after = self.position()
        stable(before, after, int(before['position']))
        found = self.detector.infer(item[3])
        frame = self.save(item, found)
        if found.get('status') != 'KNOWN_SCREEN_MATCHED':
            raise ValueError('screen_lost_during_axis_confirmation')
        return dict(position=after['position'], u=found['best']['center_px'][0] / 2,
                    frame=frame, vision=found, before=before, after=after)

    def _measure_local_axis(self, old):
        first = self._stationary_marker()
        # Probe towards the image centre using the historical axis only as a
        # small search hint. New measurements, not that hint, determine yaw sign.
        prior = old['best']['pixel_u_per_count']
        direction = 1 if (319.5 - first['u']) / prior >= 0 else -1
        if not -1321 <= first['position'] + direction * 256 <= 2775:
            direction = -direction
        measurements = [first]
        for step in (128, 256):
            self._move(first['position'] + direction * step, reserve=.6)
            measurements.append(self._stationary_marker())
        from av_screen_sweep_V1 import measure_axis
        fit = measure_axis(measurements)
        measured = copy.deepcopy(old)
        measured.update(boot_id=self.boot, status='VISUAL_PAN_RESPONSE_MEASURED',
                        evidence_scope='THIS_INITIALIZATION_THREE_STATIONARY_MARKER_VIEWS',
                        measurements=measurements)
        measured['best'] = dict(old['best'], **fit)
        self.sweep_result['measured_axis'] = measured
        return measured

    def close(self):
        self._finish_scan_worker()
        result = super().close()
        result['sweep'] = self.sweep_result
        result['sweep_detector_closed'] = self.vision_worker is None or not self.vision_worker.is_alive()
        result['cleanup_verified'] = result['cleanup_verified'] and result['sweep_detector_closed']
        from av_phase_follow_V7 import atomic
        atomic(self.out / 'cleanup.json', result)
        return result
