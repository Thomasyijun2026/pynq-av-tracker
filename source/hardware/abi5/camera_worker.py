
import cv2,json,time,hashlib,sys
from pathlib import Path
out=Path(sys.argv[1]);cap=None;r={'status':'FAILED','frames':0,'released':False}
try:
    cv2.setNumThreads(1);cap=cv2.VideoCapture('/dev/video0',cv2.CAP_V4L2)
    assert cap.isOpened(),'Camera not opened'
    cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,640);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
    cap.set(cv2.CAP_PROP_FPS,30);cap.set(cv2.CAP_PROP_BUFFERSIZE,1)
    for i in range(12):
        begin=time.monotonic();ok,frame=cap.read();end=time.monotonic()
        assert ok and frame.shape==(480,640,3),'Camera read failed'
        r['frames']+=1
    assert cv2.imwrite(str(out/'current.jpg'),frame,[cv2.IMWRITE_JPEG_QUALITY,92])
    r.update(status='CAMERA_FRAME_SAVED',read_begin=begin,read_end=end,write_end=time.monotonic(),sha256=hashlib.sha256((out/'current.jpg').read_bytes()).hexdigest())
except BaseException as e:r['error']=repr(e)
finally:
    if cap is not None:cap.release();r['released']=True
    (out/'camera_result.json').write_text(json.dumps(r,indent=2))
