"""Full scan V15 with a frozen raw/installation servo datum per board boot."""
from pathlib import Path
import threading
from av_phase_io_V7 import installation_reference
from av_phase_io_V15 import Visual as Previous
from av_phase_io_V13 import MoveWatchdog

class Visual(Previous):
    def __init__(self, out, deadline, boot, coordinate_path):
        super().__init__(out, deadline, boot)
        self.coordinate_path = Path(coordinate_path)

    def open(self):
        import cv2
        from pynq import MMIO
        from av_camera_format_V1 import CameraFormat
        from av_screen_detector_V5 import ScreenDetector
        from av_screen_detector_V6 import ScreenDetector as ScanDetector
        from servo_pan_session_V8 import PanSession
        self.scan_detector=ScanDetector()
        self.cv2=cv2;cv2.setNumThreads(1);self.detector=ScreenDetector()
        self.check()
        reference=installation_reference(self.boot)
        self.pan=PanSession(MMIO(0x43c10000,65536),reference,self.boot,self.coordinate_path,speed=1800,acceleration=20,torque_limit=500)
        self.ready=self.pan.open();self.last_position=self.pan.position()
        usb=next(p for p in (Path('/sys/class/video4linux/video0')/'device').resolve().parents if (p/'idVendor').exists())
        if (usb/'idVendor').read_text().strip()+':'+(usb/'idProduct').read_text().strip()!='1d6c:0103':raise ValueError('camera_identity')
        fmt=CameraFormat()
        try:self.original_format=fmt.get()
        finally:fmt.close()
        self.power=usb/'power/control';self.original_power=self.power.read_text().strip();self.power.write_text('on')
        self.cap=cv2.VideoCapture(0,cv2.CAP_V4L2)
        if not self.cap.isOpened():raise RuntimeError('camera_open_failed')
        for prop,value in [(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG')),(cv2.CAP_PROP_FRAME_WIDTH,1280),
                           (cv2.CAP_PROP_FRAME_HEIGHT,960),(cv2.CAP_PROP_BUFFERSIZE,1)]:
            if not self.cap.set(prop,value):raise RuntimeError('camera_property_failed')
        if [int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]!=[1280,960]:
            raise RuntimeError('camera_format_mismatch')
        self.reader=threading.Thread(target=self._read,name='bounded_camera_reader',daemon=True);self.reader.start()
        self.pan=MoveWatchdog(self.pan)
        return self.ready
