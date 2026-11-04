"""V3 profile with interval-based telemetry timing; V1-V3 preserved."""
from servo_pan_session_V3 import PanSession as PriorSession, ProfileBoard
from servo_observation_time_V1 import interval, motion_check
from servo_board_V3 import arrived


class TimedProfileBoard(ProfileBoard):
    def telemetry(self, name):
        f = super().telemetry(name)
        event = self.events[-1]
        if event['request'].get('name') != name:
            raise ValueError('Telemetry event identity')
        response = event['response']
        a, b = response['begin_monotonic'], response['end_monotonic']
        if not event['board_begin'] <= a <= b <= event['board_end'] <= f['monotonic']:
            raise ValueError('Telemetry clock order')
        f.update(time_basis='REQUEST_RESPONSE_BRACKET', observation_begin_monotonic=a,
                 observation_end_monotonic=b, completion_monotonic=f['monotonic'],
                 estimated_observation_monotonic=(a+b)/2,
                 observation_half_width_seconds=(b-a)/2, protocol_seq=event['request']['seq'])
        interval(f)
        return f


class PanSession(PriorSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.board = TimedProfileBoard(self.transport, self.clock, self.sleep, self.profile_limit)

    def move(self, target, timeout=8, observe=None):
        if not self.opened or self.closed or type(target) is not int or not self.low <= target <= self.high:
            raise ValueError('Target outside fixed session -180..180 range')
        if not 0 < timeout <= 30:
            raise ValueError('Finite motion deadline required')
        start = self.clock()
        record = dict(target=target, begin=start, status='MOVING', samples=[], timing_checks=[], decision_owner='PYNQ')
        self.moves.append(record)
        try:
            first = self.position()
            if first['speed'] or first['moving']:
                raise ValueError('Previous motion not settled')
            record['start'] = first['position']
            last, stable = first, 0
            self.board.goal('pan_goal', target, self.speed)
            while self.clock()-start < timeout:
                current = self.position()
                record['samples'].append(current)
                record['timing_checks'].append(motion_check(first, last, current, target, self.speed))
                if observe is not None:
                    observe(current)
                stable = stable+1 if arrived(current, target) else 0
                if stable == 3:
                    record.update(status='ARRIVED', end=self.clock(), residual=target-current['position'])
                    return record
                last = current
                self.sleep(.03)
            raise TimeoutError('Arrival deadline; original 4-count / 3-stationary-sample test retained')
        except BaseException as exc:
            record.update(status='FAILED', error=repr(exc), end=self.clock())
            self.close()
            raise
