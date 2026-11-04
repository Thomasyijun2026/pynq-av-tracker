"""Adapt board phase results to the existing observer/source-ID service."""
import copy, hashlib, shutil, time
from pathlib import Path
from av_phase_follow_V7 import Engine, atomic
from av_direction_catalogue_V1 import selected_audio_admission


class ServiceEngine(Engine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected = None
        self.selected_verdict = None

    def source(self, record, phase_result):
        event = super().source(record, phase_result)
        if self.selected is not None:
            candidate, epoch = self.selected
            event['source_id'] = candidate['source_id']
            # Executed before goal calculation and any visual.point call.
            self.selected_verdict = selected_audio_admission(candidate, event, self.boot, epoch, self.clock())
        return event


class Runner:
    def __init__(self, engine, follow=True):
        self.engine, self.follow = engine, follow
        self.last = None

    def __call__(self, folder, cancelled, deadline, operation):
        folder = Path(folder)
        action = operation['action']
        begin = deadline - (11. if action == 'initialize' else 5.)
        if time.monotonic() >= deadline: raise TimeoutError('Request expired before operation')
        self.last = None
        self.engine.selected_verdict = None
        self.engine.selected = (operation['source'], operation['catalogue_epoch']) if action == 'select' else None
        try:
            raw = (self.engine.initialize(folder, trigger=begin) if action == 'initialize' else
                   self.engine.follow(folder, move=self.follow or action == 'select', trigger=begin))
        finally:
            self.engine.selected = None
        result = copy.deepcopy(raw)
        result.update(operation_action=action, result_artifact='service_result.json')
        if action == 'discover' and raw.get('source_event') is not None:
            # A missed view deadline is not a successful camera follow. Its valid
            # audio can still enter the radar if the operation itself ended in time.
            result['follow_status'] = raw['status']
            if raw.get('local_cleanup_verified') and raw['elapsed_seconds'] <= 5.:
                result['status'] = 'AUDIO_OBSERVATION_CAPTURED'
        if action == 'select' and raw.get('navigation_ready'):
            result['selected_before_move'] = copy.deepcopy(self.engine.selected_verdict)
            event = result['source_event']
            result['source_anchor'] = dict(source_id=event['source_id'], source_kind='DIRECTION_CANDIDATE',
                audio_event=copy.deepcopy(event), audio_bearing_deg=event['audio_bearing_deg'],
                visual_confirmed=False, camera_view_saved=True)
        frame = result.get('confirmation_frame')
        if frame:
            source = folder/'camera'/Path(frame['file']).name
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != frame['sha256']:
                raise ValueError('Confirmed frame source/hash mismatch')
            destination = folder/'confirmation.jpg'
            shutil.copyfile(str(source), str(destination))
            if hashlib.sha256(destination.read_bytes()).hexdigest() != frame['sha256']:
                raise ValueError('Confirmed frame copy mismatch')
            result['confirmation_frame'] = dict(frame, file='confirmation.jpg')
        self.last = result
        atomic(folder/'service_result.json', result)
        return result
