"""Eleven-second initialization and non-preemptive board-owned lifetime."""
import copy
from av_trigger_service_V9 import Coordinator as PreviousCoordinator
from av_trigger_service_V6 import ButtonEdges, make_handler, atomic_json


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, budget=11., **kwargs):
        if not 0 < budget <= 11: raise ValueError('Initialization limit is 11 seconds')
        self.display_phase = dict(phase='READY')
        super().__init__(*args, budget=min(budget, 10.), **kwargs)
        self.budget = budget
        self._save()

    def _state(self):
        state = super()._state()
        state.update(version=10, display_phase=copy.deepcopy(self.display_phase),
                     initialization_budget_seconds=self.budget,
                     lifecycle='FINISH_ACTIVE_OPERATION_THEN_CLOSE')
        return state

    def publish_phase(self, value):
        # Public progress has no full PCM/algorithm result or external angle.
        with self.lock:
            self.display_phase = {k:copy.deepcopy(v) for k,v in value.items()
                                  if k in ('phase', 'triggered')}
            self.display_phase.update(request_id=self.active, boot_id=self.boot_id,
                                      published_monotonic=self.clock())

    def _accept(self, job, result, now):
        super()._accept(job, result, now)
        if job['action'] == 'discover' and job['status'] == 'DISCOVERY_RECORDED':
            if result.get('navigation_ready') and result.get('follow_status') == 'AUDIO_SOURCE_VIEW_READY':
                sid = job['catalogue_verdict']['source_id']
                self.views[sid] = dict(request_id=job['id'], operation_sequence=job['sequence'],
                    image='confirmation.jpg', camera_view_saved=True, visual_confirmed=False,
                    view_audio_bearing_deg=result['source_event']['audio_bearing_deg'], view_monotonic=now)
                job['navigation_ready'] = True

    def request_cancel(self):
        # The user requested no interference with a running operation. BTN1/close
        # suppress future discovery, while the existing worker cleans up normally.
        with self.lock:
            if self.pending is not None:
                self.jobs[self.pending].update(status='CANCELLED_BEFORE_ACTION', action_started=False)
                self.pending = None
            self.discovery_hold = 'USER_REQUEST_FINISH_CURRENT_THEN_IDLE'
            self._save()
            return dict(active=self.active, cancel_requested=False, stop_after_current=True)

    def close(self, timeout=None):
        with self.lock:
            self.closed = True
            if self.pending is not None:
                self.jobs[self.pending].update(status='CANCELLED_BEFORE_ACTION', action_started=False)
                self.pending = None
            worker = self.worker
        if worker is not None: worker.join(timeout)
        with self.lock:
            self._save()
            return dict(worker_closed=worker is None or not worker.is_alive(), active=self.active,
                        active_worker_interrupted=False)
