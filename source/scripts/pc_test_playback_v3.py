"""Bounded 30pct controlled stimulus, restoring endpoint volume and mute.
SetMasterVolumeLevelScalar: https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmastervolumelevelscalar"""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
from datetime import datetime
import threading
import math
import time
import winsound
from read_pc_audio_output_V1 import Guid, call


@contextmanager
def endpoint():
    ole=c.OleDLL('ole32');ptrs=[];initialized=False
    ole.CoInitializeEx.argtypes=[c.c_void_p,w.DWORD]
    ole.CoCreateInstance.argtypes=[c.POINTER(Guid),c.c_void_p,w.DWORD,c.POINTER(Guid),c.POINTER(c.c_void_p)]
    ole.CoTaskMemFree.argtypes=[c.c_void_p]
    try:
        hr=ole.CoInitializeEx(None,0)
        if hr<0:raise OSError(f'COM initialization failed: {hr}')
        initialized=True
        enum=c.c_void_p()
        hr=ole.CoCreateInstance(c.byref(Guid('bcde0395-e52f-467c-8e3d-c4579291692e')),None,1,
            c.byref(Guid('a95664d2-9614-4f35-a746-de8db63617e6')),c.byref(enum))
        if hr<0:raise OSError(f'Endpoint creation failed: {hr}')
        ptrs.append(enum);device=c.c_void_p()
        call(enum,4,[c.c_int,c.c_int,c.POINTER(c.c_void_p)],0,0,c.byref(device));ptrs.append(device)
        identifier=c.c_void_p();call(device,5,[c.POINTER(c.c_void_p)],c.byref(identifier))
        try:identifier_text=c.wstring_at(identifier)
        finally:ole.CoTaskMemFree(identifier)
        volume=c.c_void_p();iid=Guid('5cdf2c82-841e-4546-9722-0cf74078229a')
        call(device,3,[c.POINTER(Guid),w.DWORD,c.c_void_p,c.POINTER(c.c_void_p)],c.byref(iid),23,None,c.byref(volume));ptrs.append(volume)
        def read():
            scalar=c.c_float();mute=w.BOOL()
            call(volume,9,[c.POINTER(c.c_float)],c.byref(scalar));call(volume,15,[c.POINTER(w.BOOL)],c.byref(mute))
            return dict(endpoint_id=identifier_text,master_volume_scalar=scalar.value,muted=bool(mute.value))
        def set_mute(value):
            call(volume,14,[w.BOOL,c.c_void_p],bool(value),None)
        def set_volume(value):
            if not 0<=value<=1:raise ValueError('Endpoint scalar')
            call(volume,7,[c.c_float,c.c_void_p],float(value),None)
        yield read,set_mute,set_volume
    finally:
        for pointer in reversed(ptrs):call(pointer,2,[])
        if initialized:ole.CoUninitialize()


def valid_saved_volume(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


class Playback:
    def __init__(self,wav,expected_endpoint,maximum_seconds=20,target_volume=.30):
        if not 0 < target_volume <= .30:raise ValueError('Bounded test level')
        self.target_volume=target_volume
        self.wav=str(wav);self.expected_endpoint=expected_endpoint;self.maximum_seconds=maximum_seconds
        self.finish=threading.Event();self.ready=threading.Event()
        self.result=dict(maximum_seconds=maximum_seconds,volume_changed=False,events=[])

    def event(self,name,**extra):
        self.result['events'].append(dict(event=name,pc_time=datetime.now().astimezone().isoformat(),monotonic=time.monotonic(),**extra))

    def worker(self):
        try:
            with endpoint() as (read,set_mute,set_volume):
                before=read();self.result['before']=before
                assert before['endpoint_id']==self.expected_endpoint,'Default output changed'
                assert valid_saved_volume(before['master_volume_scalar']),'Invalid saved endpoint volume'
                changed=False
                try:
                    set_volume(self.target_volume);self.result['volume_changed']=True
                    if before['muted']:
                        set_mute(False);changed=True
                    actual=read();assert actual['endpoint_id']==self.expected_endpoint
                    assert not actual['muted']
                    assert abs(actual['master_volume_scalar']-self.target_volume)<1e-6
                    self.result['playback_endpoint']=actual
                    self.event('PLAY_CALL')
                    winsound.PlaySound(self.wav,winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
                    self.ready.set()
                    self.event('STARTED')
                    self.finish.wait(self.maximum_seconds)
                    self.event('STOP',reason='REQUEST_RETURNED' if self.finish.is_set() else 'DEADLINE')
                finally:
                    winsound.PlaySound(None,0)
                    set_volume(before['master_volume_scalar'])
                    if changed:set_mute(before['muted'])
                    after=read();self.result['after']=after
                    self.result['restored']=after['muted']==before['muted'] and after['master_volume_scalar']==before['master_volume_scalar']
                    self.result['playing']=False
        except BaseException as exc:self.result['error']=repr(exc)
        finally:self.ready.set()

    def __enter__(self):
        self.thread=threading.Thread(target=self.worker,name='bounded_test_playback',daemon=False);self.thread.start()
        if not self.ready.wait(8) or self.result.get('error'):
            self.finish.set();self.thread.join(5)
            raise RuntimeError('Playback did not start: '+repr(self.result))
        return self

    def __exit__(self,*unused):
        self.finish.set();self.thread.join(8)
        if self.thread.is_alive() or self.result.get('error') or not self.result.get('restored'):
            raise RuntimeError('Playback restoration unconfirmed: '+repr(self.result))
