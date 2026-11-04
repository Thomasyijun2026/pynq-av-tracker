"""Full-turn FIFO search, low-cost proposals, unchanged static confirmation.

Only search images are grayscale. All cell tests use original-resolution
luminance. The last three stationary measurements still use the V5 detector.
Read brackets are not exposure timestamps. No angle comes from a PC request.
"""
import hashlib
import threading
import time
from av_phase_io_V10 import Visual as StaticVisual
from av_scan_queue_V1 import ScanQueue
from av_screen_sweep_V1 import plan, verify_travel, associate, choose


class Visual(StaticVisual):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scan_queue = ScanQueue(32)

    def open(self):
        from av_screen_detector_V6 import ScreenDetector
        self.scan_detector = ScreenDetector()
        return super().open()

    def _read(self):
        try:
            while not self.stop.is_set() and time.monotonic() < self.deadline:
                with self.lock:
                    requested = self.frames < self.read_goal or self.scanning.is_set()
                if not requested:
                    self.read_request.wait(.02)
                    self.read_request.clear()
                    continue
                scan_at_read = self.scanning.is_set()
                begin = time.monotonic()
                ok, frame = self.cap.read()
                end = time.monotonic()
                if not ok or frame.shape != (960, 1280, 3):
                    raise RuntimeError('camera_read_failed')
                with self.lock:
                    self.frames += 1
                    number = self.frames
                    self.latest = (number, begin, end, frame)
                if scan_at_read:
                    gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
                    self.scan_queue.put((number, begin, end, gray))
        except Exception as exc:
            self.reader_error = repr(exc)
            self.scan_queue.close()

    def _save_scan(self, item, vision):
        number, begin, end, gray = item
        started = time.monotonic()
        upright = self.cv2.rotate(gray, self.cv2.ROTATE_180)
        ok, encoded = self.cv2.imencode('.jpg', upright, [self.cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError('scan_jpeg_encode_failed')
        name = 'view_%02d.jpg'%len(self.saves)
        payload = encoded.tobytes()
        (self.out/name).write_bytes(payload)
        frame = dict(file=name, path=str(self.out/name), sha256=hashlib.sha256(payload).hexdigest(),
                     read_begin=begin, read_end=end, write_end=time.monotonic(), frame_number=number,
                     image_rotation=180, pixel_format='GRAY8', time_basis='V4L2_READ_BRACKET_NOT_EXPOSURE',
                     save_seconds=time.monotonic()-started)
        self.saves.append(dict(frame=frame, vision=vision))
        return frame

    def _vision(self):
        owned = False
        try:
            while True:
                self.check()
                if self.stop.is_set():
                    raise RuntimeError('scan_stop_requested')
                item = self.scan_queue.take()
                if item is None:
                    if self.scan_queue.finished():
                        break
                    continue
                owned = True
                found = self.scan_detector.infer_scan(item[3])
                frame = self._save_scan(item, found)
                self.scan_rows.append(dict(frame=frame, vision=found))
                self.scan_counts['processed'] += 1
                self.scan_queue.complete()
                owned = False
        except Exception as exc:
            self.scan_error = repr(exc)
            if owned:
                self.scan_queue.complete(failed=True)
            self.scan_queue.discard('SCAN_FAILED: '+repr(exc))

    def _finish_scan_worker(self):
        self.scanning.clear()
        self.scan_finished.set()
        self.scan_queue.close()
        if self.vision_worker is not None:
            # Every already admitted image is processed, or accounted for as
            # failed/discarded at the existing operation deadline.
            self.vision_worker.join()
        self.scan_counts['queue'] = self.scan_queue.snapshot()

    def center(self, axis):
        begin = time.monotonic()
        self._axis_view = None
        route = plan(int(self.position()['position']))
        self.sweep_result = dict(status='FULL_SWEEP_STARTED', route=route,
                                visual_decisions=self.scan_rows, counts=self.scan_counts,
                                search_pipeline='FIFO32_GRAY_FULL_RES_CELLS_PROPOSAL320')
        entry = self._move(route['entry'], reserve=3.8)
        # Drain before the first moving read; do not spend inference on the
        # entry movement or let that movement overwrite useful sweep images.
        drain = self.fresh(time.monotonic())
        self.sweep_result['entry_drain'] = dict(frame_number=drain[0], read_begin=drain[1], read_end=drain[2])
        self.scanning.set()
        self.sweep_started.set()
        self.read_request.set()
        self.vision_worker = threading.Thread(target=self._vision, name='screen_sweep_detector', daemon=False)
        self.vision_worker.start()
        try:
            sweep = self._move(route['exit'], reserve=1.6)
            self.sweep_result.update(entry=entry, sweep=sweep)
        finally:
            self._finish_scan_worker()
        if self.reader_error or self.scan_error:
            raise RuntimeError(self.reader_error or self.scan_error)
        q = self.scan_queue.snapshot()
        if q['submitted'] != q['completed'] or q['overflow'] or q['discarded'] or q['failed']:
            raise RuntimeError('scan_queue_incomplete')
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
