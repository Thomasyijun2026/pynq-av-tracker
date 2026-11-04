"""Read the present Windows default output; never changes volume or plays sound."""
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime
import json
from pathlib import Path
import uuid


class Guid(c.Structure):
    _fields_ = [('bytes', c.c_ubyte * 16)]

    def __init__(self, text):
        super().__init__((c.c_ubyte * 16).from_buffer_copy(uuid.UUID(text).bytes_le))


class PropertyKey(c.Structure):
    _fields_ = [('fmtid', Guid), ('pid', w.DWORD)]


class PropertyVariant(c.Structure):
    _fields_ = [('vt', w.USHORT), ('reserved', w.USHORT * 3), ('payload', c.c_ubyte * 16)]


def call(ptr, index, argtypes, *args):
    address = c.cast(ptr, c.POINTER(c.POINTER(c.c_void_p))).contents[index]
    method = c.WINFUNCTYPE(c.c_long, c.c_void_p, *argtypes)(address)
    hr = method(ptr, *args)
    if hr < 0:
        raise OSError(f'COM method {index} HRESULT 0x{hr & 0xffffffff:08x}')
    return hr


def main():
    out = Path(__file__).resolve().parents[1] / 'logs/av_pmod_check_V1' / ('pc_output_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out.mkdir()
    ole = c.OleDLL('ole32')
    ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
    ole.CoCreateInstance.argtypes = [c.POINTER(Guid), c.c_void_p, w.DWORD, c.POINTER(Guid), c.POINTER(c.c_void_p)]
    ole.CoTaskMemFree.argtypes = [c.c_void_p]
    pointers = []
    result = dict(pc_time=datetime.now().astimezone().isoformat(), scope='CURRENT_READ_ONLY_NOT_RETROACTIVE_PLAYBACK_VERIFICATION', changed_settings=False)
    initialized = False
    try:
        hr = ole.CoInitializeEx(None, 0)
        if hr < 0:
            raise OSError(f'CoInitializeEx {hr}')
        initialized = True
        enum = c.c_void_p()
        ole.CoCreateInstance(c.byref(Guid('bcde0395-e52f-467c-8e3d-c4579291692e')), None, 1,
                             c.byref(Guid('a95664d2-9614-4f35-a746-de8db63617e6')), c.byref(enum))
        pointers.append(enum)
        device = c.c_void_p()
        call(enum, 4, [c.c_int, c.c_int, c.POINTER(c.c_void_p)], 0, 0, c.byref(device))
        pointers.append(device)
        endpoint_id = c.c_void_p()
        call(device, 5, [c.POINTER(c.c_void_p)], c.byref(endpoint_id))
        try:
            result['endpoint_id'] = c.wstring_at(endpoint_id)
        finally:
            ole.CoTaskMemFree(endpoint_id)
        store = c.c_void_p()
        call(device, 4, [w.DWORD, c.POINTER(c.c_void_p)], 0, c.byref(store))
        pointers.append(store)
        key = PropertyKey(Guid('a45c254e-df1c-4efd-8020-67d146a850e0'), 14)
        value = PropertyVariant()
        call(store, 5, [c.POINTER(PropertyKey), c.POINTER(PropertyVariant)], c.byref(key), c.byref(value))
        try:
            assert value.vt == 31, f'Unexpected friendly-name type {value.vt}'
            result['friendly_name'] = c.wstring_at(c.c_void_p.from_buffer(value, 8).value)
        finally:
            ole.PropVariantClear(c.byref(value))
        volume = c.c_void_p()
        iid = Guid('5cdf2c82-841e-4546-9722-0cf74078229a')
        call(device, 3, [c.POINTER(Guid), w.DWORD, c.c_void_p, c.POINTER(c.c_void_p)], c.byref(iid), 23, None, c.byref(volume))
        pointers.append(volume)
        scalar, muted = c.c_float(), w.BOOL()
        call(volume, 9, [c.POINTER(c.c_float)], c.byref(scalar))
        call(volume, 15, [c.POINTER(w.BOOL)], c.byref(muted))
        result.update(status='READ_OK', master_volume_scalar=scalar.value, muted=bool(muted.value), session_volume='NOT_READ')
    except Exception as exc:
        result.update(status='READ_FAILED', error=repr(exc))
    finally:
        for ptr in reversed(pointers):
            if ptr.value:
                call(ptr, 2, [])
        if initialized:
            ole.CoUninitialize()
        with (out/'result.json').open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(run=str(out), **result), ensure_ascii=False))


if __name__ == '__main__':
    main()
