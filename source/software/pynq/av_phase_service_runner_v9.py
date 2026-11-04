"""Adapt board phase results to the existing observer/source-ID service."""
import copy, hashlib, shutil, time
from pathlib import Path
from av_phase_follow_V15 import Engine
from av_phase_follow_V7 import atomic
from av_direction_catalogue_V1 import selected_audio_admission


class ServiceEngine(Engine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected = None
        self.selected_verdict = None
        self.selection_admission = None

    def source(self, record, phase_result):
        event = super().source(record, phase_result)
        if self.selected is not None:
            candidate, epoch = self.selected
            event['source_id'] = candidate['source_id']
            # Executed before goal calculation and any visual.point call.
            checked = self.clock()
            self.selected_verdict = selected_audio_admission(candidate, event, self.boot, epoch, checked)
            self.selection_admission = dict(candidate=copy.deepcopy(candidate),
                checked_monotonic=checked, verdict=copy.deepcopy(self.selected_verdict))
        return event


class Runner:
    def __init__(self, engine, follow=True):
        self.engine, self.follow = engine, follow
        self.last = None

    def __call__(self, folder, cancelled, deadline, operation):
        folder = Path(folder)
        action = operation['action']
        begin = operation['trigger_monotonic']
        if deadline is not None or not isinstance(begin, (int,float)) or not 0 <= begin <= self.engine.clock():
            raise ValueError('No-limit operation requires its actual trigger timestamp')
        self.last = None
        self.engine.selected_verdict = None
        self.engine.selection_admission = None
        self.engine.selected = (operation['source'], operation['catalogue_epoch']) if action == 'select' else None
        try:
            raw = (self.engine.initialize(folder, trigger=begin) if action == 'initialize' else
                   self.engine.follow(folder, move=self.follow or action == 'select', trigger=begin))
        finally:
            self.engine.selected = None
        result = copy.deepcopy(raw)
        result.update(operation_action=action, result_artifact='service_result.json')
        if action == 'discover' and raw.get('source_event') is not None:
            # Valid audio may enter the radar after verified cleanup;
            # this does not turn an unconfirmed camera view into a success.
            result['follow_status'] = raw['status']
            if raw.get('local_cleanup_verified'):
                result['status'] = 'AUDIO_OBSERVATION_CAPTURED'
        if action == 'select' and raw.get('navigation_ready'):
            result['selected_before_move'] = copy.deepcopy(self.engine.selected_verdict)
            result['selection_admission'] = copy.deepcopy(self.engine.selection_admission)
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
