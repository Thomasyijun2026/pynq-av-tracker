"""Existing ABI5 capture and protected pan with an operation-scoped camera reader."""
import hashlib,json,threading,time
from pathlib import Path
import numpy as np
from av_phase_follow_V7 import atomic


class Capture:
    def __init__(self, check, bundle, files):
        self.check,self.bundle,self.files=check,Path(bundle),files

    def __call__(self, out):
        import mic_doa_pmod_V5
        out=Path(out)
        answer=mic_doa_pmod_V5.localize(self.check.mic,self.bundle,{'files':self.files},out)
        meta=json.loads((out/'metadata.json').read_text());snap=json.loads((out/'pl_snapshot.json').read_text())
        raw=np.fromfile(str(out/'raw_i32le.bin'),dtype='<i4').reshape(-1,8)
        first=snap['first_center_raw_index'];x=raw[first:first+32768][:,[0,4,6,7]]
        channels=raw[:,[0,4,6,7]];pairs=[(a,b) for a in range(4) for b in range(a+1,4)]
        health=dict(changed=[bool(np.ptp(channels[:,k])) for k in range(4)],
            clipped=[bool(np.any((channels[:,k]<=-8388608)|(channels[:,k]>=8388607))) for k in range(4)],
            duplicate_pairs=[[a,b] for a,b in pairs if np.array_equal(channels[:,a],channels[:,b])],
            ac_rms=np.std(channels.astype(float),axis=0).tolist())
        atomic(out/'health.json',health)
        return dict(answer=answer,metadata=meta,snapshot=snap,pcm=x,health=health)


class Visual:
    def __init__(self, out, deadline, boot):
        self.out,self.deadline,self.boot=Path(out),deadline,boot
        self.out.mkdir(parents=True,exist_ok=False)
        self.pan=self.cap=self.reader=None
        self.original_format=self.power=self.original_power=None
        self.stop=threading.Event();self.lock=threading.Lock();self.latest=None;self.frames=0
        self.reader_error=None;self.closed=None;self.last_position=None;self.saves=[]
        self.read_goal=0;self.read_request=threading.Event()

    def check(self, *_):
        if time.monotonic()>=self.deadline-.15:raise TimeoutError('visual_operation_deadline')

    def open(self):
        import cv2
        from pynq import MMIO
        from av_camera_format_V1 import CameraFormat
        from av_screen_detector_V5 import ScreenDetector
        from servo_pan_session_V5 import PanSession
        self.cv2=cv2;cv2.setNumThreads(1);self.detector=ScreenDetector()
        self.check()
        reference=installation_reference(self.boot)
        self.pan=PanSession(MMIO(0x43c10000,65536),reference,self.boot,speed=1800,acceleration=20,torque_limit=500)
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
        return self.ready

    def _read(self):
        try:
            while not self.stop.is_set() and time.monotonic()<self.deadline:
                with self.lock:requested=self.frames<self.read_goal
                if not requested:
                    self.read_request.wait(.02);self.read_request.clear();continue
                begin=time.monotonic();ok,frame=self.cap.read();end=time.monotonic()
                if not ok or frame.shape!=(960,1280,3):raise RuntimeError('camera_read_failed')
                with self.lock:
                    self.frames+=1;self.latest=(self.frames,begin,end,frame)
        except Exception as exc:self.reader_error=repr(exc)

    def fresh(self, after):
        with self.lock:
            start=self.frames;self.read_goal=start+3
        self.read_request.set()
        while True:
            self.check()
            if self.reader_error:raise RuntimeError(self.reader_error)
            with self.lock:item=self.latest
            # Exactly three demanded reads after the pose: conservative buffer drain.
            # No decoding competes with inference/audio while no frame is needed.
            # These are read brackets, not hardware exposure timestamps.
            # Three completed reads after the pose: conservative buffer drain,
            # explicitly not a hardware exposure timestamp.
            if item is not None and item[0]>=start+3 and item[1]>=after:return item
            if item is not None and item[0]>=start+3 and item[1]<after:
                with self.lock:self.read_goal=self.frames+1
                self.read_request.set()
            time.sleep(.005)

    def save(self, item, vision=None):
        number,begin,end,frame=item
        upright=self.cv2.rotate(frame,self.cv2.ROTATE_180)
        ok,encoded=self.cv2.imencode('.jpg',upright,[self.cv2.IMWRITE_JPEG_QUALITY,90])
        if not ok:raise RuntimeError('jpeg_encode_failed')
        name='view_%02d.jpg'%len(self.saves);payload=encoded.tobytes();(self.out/name).write_bytes(payload)
        result=dict(file=name,path=str(self.out/name),sha256=hashlib.sha256(payload).hexdigest(),read_begin=begin,read_end=end,
                    write_end=time.monotonic(),frame_number=number,image_rotation=180,time_basis='DRAINED_READ_NOT_EXPOSURE')
        self.saves.append(dict(frame=result,vision=vision));return result

    def position(self):
        self.last_position=self.pan.position();return self.last_position

    def minimum_move_time(self, target):
        return abs(target-self.last_position['position'])/1800.+.15

    def center(self, axis):
        from av_pan_view_probe_V1 import stable
        before=self.position();item=self.fresh(time.monotonic());after=self.position()
        stable(before,after,int(before['position']))
        found=self.detector.infer(item[3]);initial_saved=self.save(item,found)
        if found.get('status')!='KNOWN_SCREEN_MATCHED':raise ValueError('known_screen_not_in_current_view')
        slope=axis['best']['pixel_u_per_count'];u=found['best']['center_px'][0]/2
        if abs(u-319.5)<=12:
            # Same centering limit as the post-move branch; no new movement
            # means the fresh stationary frame is already the final view.
            return dict(status='SCREEN_CENTER_CONFIRMED',position=round((before['position']+after['position'])/2),
                goal=after['position'],move=dict(status='NOT_NEEDED_ALREADY_CENTERED',command_sent=False),
                vision=found,center_residual_px=u-319.5,confirmation_frame=initial_saved)
        goal=round(after['position']+(319.5-u)/slope)
        if abs(goal-after['position'])>256 or not -1321<=goal<=2775:raise ValueError('local_center_outside_measured_region')
        self.check();move=self.pan.move(goal,timeout=max(.1,self.deadline-time.monotonic()-.5),observe=self.check)
        settled=time.monotonic();before=self.position();item=self.fresh(settled);after=self.position()
        q=stable(before,after,goal);found=self.detector.infer(item[3]);saved=self.save(item,found)
        if found.get('status')!='KNOWN_SCREEN_MATCHED':raise ValueError('return_marker_not_confirmed')
        if abs(found['best']['center_px'][0]/2-319.5)>12:raise ValueError('screen_center_residual_exceeded')
        return dict(status='SCREEN_CENTER_CONFIRMED',position=round(q),goal=goal,move=move,vision=found,
                    center_residual_px=found['best']['center_px'][0]/2-319.5,confirmation_frame=saved)

    def point(self, goal):
        from av_pan_view_probe_V1 import stable
        self.check();motion=self.pan.move(goal,timeout=max(.1,self.deadline-time.monotonic()-.3),observe=self.check)
        settled=time.monotonic();before=self.position();item=self.fresh(settled);after=self.position()
        q=stable(before,after,goal);saved=self.save(item)
        return dict(status='FRESH_VIEW_AT_AUDIO_DIRECTION',position=q,move=motion,confirmation_frame=saved,
                    semantic_identity=None,semantic_match_required=False)

    def close(self):
        if self.closed is not None:return self.closed
        result=dict(cleanup_verified=False,errors=[],frames=self.frames)
        self.stop.set();self.read_request.set()
        if self.reader is not None:self.reader.join(2.)
        if self.reader is not None and self.reader.is_alive():
            result['errors'].append('camera_reader_still_active')
        else:
            if self.cap is not None:self.cap.release()
            result['camera_released']=self.cap is None or not self.cap.isOpened()
            if self.original_format is not None:
                try:
                    from av_camera_format_V1 import CameraFormat
                    fmt=CameraFormat()
                    try:result['format_restored']=fmt.restore(self.original_format)
                    finally:fmt.close()
                except Exception as exc:result['errors'].append(repr(exc))
            else:result['format_restored']=True
            if self.power is not None:
                try:
                    self.power.write_text(self.original_power)
                    result['power_restored']=self.power.read_text().strip()==self.original_power
                except Exception as exc:result['errors'].append(repr(exc))
            else:result['power_restored']=True
        if self.pan is not None:
            result['pan']=self.pan.close()
            result.update(feedback=self.pan.board.feedback,protocol=self.pan.board.events)
        else:result['pan']=dict(cleanup_verified=True,not_opened=True)
        result['cleanup_verified']=bool(not result['errors'] and result.get('camera_released') and result.get('format_restored')
            and result.get('power_restored') and result['pan']['cleanup_verified'])
        result['saved_frames']=self.saves;atomic(self.out/'cleanup.json',result);self.closed=result
        return result


def installation_reference(boot):
    return dict(boot_id=boot,scope='FIXED_INSTALLATION_ONE_TURN',center=727,low=-1321,high=2775,
                evidence='Fixed user-authorized one-turn envelope; same-boot stationary marker response screen_pan_axis_v1_20261103_225522')
