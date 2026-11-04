"""Read completed runtime evidence without changing its process or decisions."""
import hashlib
import json
import math
import time
from pathlib import Path
from av_observer_ui_V6 import Observer as Previous


def read_result(control, job, boot):
    control = Path(control).resolve()
    if job.get('boot_id') != boot or job.get('cleanup_verified') is not True:
        raise ValueError('Completed same-boot cleanup required')
    ref = job['result_reference']
    path = (control / ref['path']).resolve()
    if control not in path.parents or path.name != 'service_result.json':
        raise ValueError('Result path outside operation')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != ref['sha256']:
        raise ValueError('Result SHA mismatch')
    result = json.loads(payload)
    if result.get('boot_id') != boot or result.get('local_cleanup_verified') is not True:
        raise ValueError('Result boot/cleanup mismatch')
    if result.get('operation_action') != job.get('action'):
        raise ValueError('Result action mismatch')
    return path, result


class Observer(Previous):
    """Adds true rejection reasons, request clocks and last completed image.

    The original accepted/rejected status is retained. This observer does not
    promote a rejected result, issue a command, or open a hardware interface.
    """
    def __init__(self, *args, clock=time.monotonic, **kwargs):
        super().__init__(*args, **kwargs)
        self.clock = clock

    def metadata(self):
        data = json.loads((self.control / 'state.json').read_text())
        if data.get('boot_id') != self.boot:
            raise ValueError('Control metadata boot mismatch')
        return data

    def state(self):
        state = super().state()
        meta = self.metadata()
        state['full_turn_initialization_supported'] = meta.get('full_turn_initialization_supported') is True
        jobs = {j['id']: j for j in meta['jobs']}
        for public in state['jobs']:
            job = jobs.get(public['id'])
            if not job or job.get('sequence') != public.get('sequence'):
                continue  # A fresh request can race a read-only snapshot.
            for key in ('trigger_monotonic', 'deadline_monotonic', 'finished_monotonic'):
                public[key] = job.get(key)
            if public['id'] in (state['active'], state.get('pending')) or not job.get('result_reference'):
                continue
            try:
                _, result = read_result(self.control, job, self.boot)
                public['operation_status'] = result['status']
                public['full_sweep_this_trigger'] = result.get('full_sweep_this_trigger', False)
                if result.get('navigation_ready') is False:
                    public['failure_reason'] = ((result.get('visual') or {}).get('error')
                                                or result.get('error') or result['status'])
                public['evidence_sha256'] = job['result_reference']['sha256']
            except (KeyError, ValueError, OSError) as exc:
                public['evidence_error'] = str(exc)
        state['observer_monotonic'] = self.clock()
        return state

    def completed_preview(self):
        meta = self.metadata()
        for job in reversed(meta['jobs']):
            if (job['id'] in (meta.get('active'), meta.get('pending')) or
                    not job.get('cleanup_verified') or not job.get('result_reference')):
                continue
            path, result = read_result(self.control, job, self.boot)
            cleanup = result.get('visual_cleanup') or ((result.get('visual') or {}).get('cleanup') or {})
            frames = cleanup.get('saved_frames', [])
            frame = frames[-1]['frame'] if frames else result.get('confirmation_frame')
            if frame is None:
                continue
            name = frame['file']
            if Path(name).name != name:
                raise ValueError('Preview filename invalid')
            image = path.parent / ('camera/' + name if frames else name)
            payload = image.read_bytes()
            if hashlib.sha256(payload).hexdigest() != frame['sha256']:
                raise ValueError('Preview image SHA mismatch')
            times = [frame[k] for k in ('read_begin', 'read_end', 'write_end')]
            now = self.clock()
            if (not all(isinstance(t, (int, float)) and math.isfinite(t) for t in times)
                    or not job['trigger_monotonic'] <= times[0] <= times[1] <= times[2] <= now):
                raise ValueError('Preview time bounds invalid')
            return dict(image_url='/api/image?source=completed_preview&sequence=' + str(job['sequence']),
                        age_seconds=now - frame['read_end'], live=False, sha256=frame['sha256'],
                        boot_id=self.boot, request_id=job['id'], sequence=job['sequence'],
                        caption='最近一次任务相机画面', path=str(image))
        return None

    def image(self, sid, sequence):
        if sid == 'completed_preview':
            preview = self.completed_preview()
            if preview is None or str(preview['sequence']) != sequence:
                raise ValueError('Completed image changed; refresh state')
            data = Path(preview['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != preview['sha256']:
                raise ValueError('Completed image changed during read')
            return data
        return super().image(sid, sequence)
