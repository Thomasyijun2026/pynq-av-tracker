"""Small V4L2 format transaction without an external v4l2-ctl dependency.

ABI checked against the board's linux/videodev2.h before opening a device.
Reference: https://docs.kernel.org/userspace-api/media/v4l/vidioc-g-fmt.html
"""
import ctypes,errno,os,re,struct
from pathlib import Path


class FormatUnion(ctypes.Union):
    _fields_=[('raw',ctypes.c_ubyte*200),('pointer_alignment',ctypes.c_void_p)]


class Format(ctypes.Structure):
    _fields_=[('type',ctypes.c_uint32),('fmt',FormatUnion)]


def request(number,size):return 0xc0000000|(size<<16)|(ord('V')<<8)|number


def fmt_fields(data):
    return list(struct.unpack_from('=12I',data,Format.fmt.offset))


class CameraFormat:
    def __init__(self):
        import fcntl
        header=Path('/usr/include/linux/videodev2.h').read_text()
        for name,number in [('ENUM_FMT',2),('G_FMT',4),('S_FMT',5),('ENUM_FRAMESIZES',74)]:
            if not re.search(r'#define\s+VIDIOC_'+name+r'\s+_IOWR\(\s*\x27V\x27\s*,\s*'+str(number)+r'\s*,',header):
                raise RuntimeError('Unexpected local V4L2 request definition '+name)
        self.fd=os.open('/dev/video0',os.O_RDWR|os.O_NONBLOCK);self.ioctl=fcntl.ioctl

    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None

    def get(self):
        b=bytearray(ctypes.sizeof(Format));struct.pack_into('=I',b,0,1)
        self.ioctl(self.fd,request(4,len(b)),b,True)
        return bytes(b)

    def restore(self,original):
        b=bytearray(original);self.ioctl(self.fd,request(5,len(b)),b,True)
        return fmt_fields(self.get())==fmt_fields(original)

    def advertised(self):
        lines=[]
        for index in range(32):
            b=bytearray(64);struct.pack_into('=II',b,0,index,1)
            try:self.ioctl(self.fd,request(2,len(b)),b,True)
            except OSError as exc:
                if exc.errno==errno.EINVAL:break
                raise
            fourcc=struct.unpack_from('=I',b,44)[0]
            name=struct.pack('=I',fourcc).decode('ascii');lines.append("[%d]: '%s'"%(index,name))
            for sizeindex in range(128):
                s=bytearray(44);struct.pack_into('=II',s,0,sizeindex,fourcc)
                try:self.ioctl(self.fd,request(74,len(s)),s,True)
                except OSError as exc:
                    if exc.errno==errno.EINVAL:break
                    raise
                kind=struct.unpack_from('=I',s,8)[0]
                if kind==1:
                    w,h=struct.unpack_from('=II',s,12);lines.append(' Size: Discrete %dx%d'%(w,h))
        return '\n'.join(lines)
