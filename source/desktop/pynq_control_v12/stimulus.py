"""First-WAV stimulus driven by the board's reference phase; no board commands."""
import copy
import ctypes
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from pc_test_playback_V3 import Playback, endpoint, valid_saved_volume

WAV = ROOT / 'logs/av_standalone_V1/session_20261103_003121/scan_fast_20261103_020644/broadband_left_only_V1.wav'
SHA = 'd835a32f414431e79a11d82fb785a483d84fe3e1b7f930ef85bfe26652903aea'
ENDPOINT = '{0.0.0.00000000}.{3bc501b7-9629-4877-b48b-6a4758e18e5f}'
DISPLAY = 'http://192.168.2.99:8765/display'


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def identity(value):
    if not isinstance(value, dict):
        raise ValueError('Display response is not an object')
    boot, rid = value.get('boot_id'), value.get('request_id')
    if not isinstance(boot, str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', boot):
        raise ValueError('Display boot identity missing')
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', rid):
        raise ValueError('Display request identity missing')
    for name in ('runtime_pid', 'runtime_start_ticks'):
        if type(value.get(name)) is not int or value[name] <= 0:
            raise ValueError('Display process identity missing')
    return boot, value['runtime_pid'], value['runtime_start_ticks'], rid, value.get('phase')


def eligible(value):
    try:
        identity(value)
        age = value['server_monotonic'] - value['published_monotonic']
        return (math.isfinite(age) and 0 <= age <= 2 and
                value.get('phase') in ('INITIALIZING_REFERENCE','INITIALIZING_STATIC_REFERENCE') and
                value.get('active') == value['request_id'] and value.get('closed') is False and
                value.get('stimulus') == 'BROADBAND_FIRST_AUDIO_ONLY' and
                value.get('maximum_play_seconds') == 35)
    except (KeyError, TypeError, ValueError):
        return False


def read_display():
    request = urllib.request.Request(DISPLAY, headers={'Accept': 'application/json'}, method='GET')
    with urllib.request.urlopen(request, timeout=.6) as response:
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise ValueError('Invalid display response')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Display must be an object')
        return value


def preflight():
    if hashlib.sha256(WAV.read_bytes()).hexdigest() != SHA:
        raise ValueError('First WAV SHA differs')
    # Read only. Temporary unmute/30% level happens inside the bounded player,
    # which restores both original values when the board's phase ends.
    with endpoint() as (read, _mute, _volume):
        value = read()
    if value['endpoint_id'] != ENDPOINT:
        raise ValueError('Default output differs from the confirmed internal speakers')
    if not valid_saved_volume(value['master_volume_scalar']):
        raise ValueError('Invalid saved endpoint volume')
    return dict(source_sha256=SHA, endpoint=value, changed_settings=False)


class Stimulus:
    def __init__(self, out, claims=None, fetch=read_display, check=preflight,
                 factory=Playback, clock=time.monotonic, mutex=True):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.claims = Path(claims or ROOT / 'logs/av_desktop_stimulus_V2/claims')
        self.claims.mkdir(parents=True, exist_ok=True)
        self.fetch, self.check, self.factory, self.clock = fetch, check, factory, clock
        self.mutex = mutex
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.player = self.run = None
        self.last_seen = self.clock()
        self.blocked = False
        self.value = dict(status='STARTING', ready=False, playing=False, board_commands_sent=0,
                          message='正在检查第一段测试音与电脑扬声器')

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def update(self, **extra):
        with self.lock:
            self.value.update(extra, updated_pc_unix=time.time())
            value = copy.deepcopy(self.value)
        write_json(self.out / 'status.json', value)

    def prepare(self):
        details = self.check()
        self.update(status='READY', ready=True, playing=False, preflight=details,
                    message='第一段测试音已就绪；手动初始化进入参考音采集后自动播放')

    def finish_player(self, reason):
        player, run = self.player, self.run
        if player is None:
            return
        error = None
        try:
            player.__exit__()
        except Exception as exc:
            error = repr(exc)
            self.blocked = True
        run.update(ended_pc_unix=time.time(), stop_reason=reason,
                   playback=copy.deepcopy(player.result), cleanup_error=error)
        write_json(self.out / (run['token'] + '.json'), run)
        self.player = self.run = None
        self.update(status='ERROR' if error else 'FINISHED', ready=not self.blocked,
                    playing=False if not error else None, last_result=run,
                    message='测试音停止状态待核对：' + error if error else '本次测试音已停止，音量和静音状态已恢复')

    def tick(self, value, error=None):
        now = self.clock()
        if self.player is not None:
            run = self.run
            reason = None
            if value is not None:
                try:
                    same = identity(value) == tuple(run['identity'])
                except ValueError:
                    same = False
                if not same:
                    reason = 'IDENTITY_CHANGED'
                elif (value.get('closed') is not False or
                      value.get('active') != value.get('request_id') or
                      value.get('phase') not in ('INITIALIZING_REFERENCE','INITIALIZING_STATIC_REFERENCE')):
                    reason = 'REFERENCE_ENDED'
                else:
                    self.last_seen = now
            if now - run['started_monotonic_pc'] >= 35:
                reason = reason or 'MAXIMUM_35_SECONDS'
            if now - self.last_seen >= 1.5:
                reason = reason or 'BOARD_CONNECTION_LOST'
            if reason:
                self.finish_player(reason)
                return
        if self.player is not None or self.blocked or not eligible(value):
            return
        ident = identity(value)
        # Same key as the historical controller: restarting a desktop must never
        # replay a request whose earlier playback outcome was uncertain.
        key = '%s_%s_%s' % (ident[0], ident[2], ident[3])
        if ident[4]=='INITIALIZING_STATIC_REFERENCE':key+='_stationary_reference'
        token = hashlib.sha256(key.encode()).hexdigest()[:24]
        old_history = ROOT / 'logs/av_desktop_V1/stimulus_history.json'
        if old_history.exists() and token in json.loads(old_history.read_text(encoding='utf-8')):
            return
        record = dict(token=token, identity=ident, request_id=ident[3], source_sha256=SHA,
                      observed_display=copy.deepcopy(value), intent_pc_unix=time.time(),
                      board_commands_sent=0)
        try:
            with (self.claims / (token + '.json')).open('x', encoding='utf-8') as f:
                json.dump(record, f)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            return
        record['started_monotonic_pc'] = now
        self.run = record
        write_json(self.out / (token + '.json'), record)
        try:
            self.player = self.factory(WAV, ENDPOINT, maximum_seconds=35, target_volume=.3)
            self.player.__enter__()
            self.last_seen = self.clock()
            record['playback'] = copy.deepcopy(self.player.result)
            write_json(self.out / (token + '.json'), record)
            self.update(status='PLAYING', ready=True, playing=True, request_id=ident[3],
                        observed_board_phase=value['phase'],
                        message='正在播放第一段测试音，板端正在确认静止声音方向' if ident[4]=='INITIALIZING_STATIC_REFERENCE' else '正在播放第一段测试音，配合板端采音和转圈')
        except Exception as exc:
            record['start_error'] = repr(exc)
            self.blocked = True
            self.finish_player('PLAYBACK_START_FAILED')
            write_json(self.out / (token + '.json'), record)
            self.update(status='ERROR', ready=False, error=repr(exc),
                        message='测试音未能开始播放：' + str(exc))

    def start(self):
        if self.thread is not None:
            raise RuntimeError('Stimulus controller already started')
        self.thread = threading.Thread(target=self.loop, name='pynq-first-audio', daemon=False)
        self.thread.start()

    def loop(self):
        handle = None
        try:
            if self.mutex:
                kernel = ctypes.windll.kernel32
                kernel.CreateMutexW.restype = ctypes.c_void_p
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
                kernel.SetLastError(0)
                handle = kernel.CreateMutexW(None, True, 'Local\\PYNQ_Desktop_Stimulus_V1')
                if not handle:
                    raise OSError('Stimulus mutex creation failed')
                if kernel.GetLastError() == 183:
                    kernel.CloseHandle(handle)
                    handle = None
                    raise RuntimeError('Another stimulus controller owns playback')
            self.prepare()
            while not self.stop.is_set():
                try:
                    value, error = self.fetch(), None
                except Exception as exc:
                    value, error = None, repr(exc)
                self.tick(value, error)
                self.stop.wait(.1)
        except Exception as exc:
            self.update(status='ERROR', ready=False, error=repr(exc), message='测试音控制不可用：' + str(exc))
        finally:
            if self.player is not None:
                self.finish_player('DESKTOP_EXIT')
            if handle:
                kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
            self.update(closed=True, ready=False)

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(10)
        return self.thread is None or not self.thread.is_alive()
