"""Board-owned, finite stationary camera/pan observations. No audio operation.

This measures pixel displacement, not complete camera/array calibration.
Goals are generated from current full feedback and the fixed installation.
"""
import argparse,hashlib,json,sys,time
from pathlib import Path


def plan(home, low, high):
    if any(type(x) is not int for x in (home,low,high)) or high-low!=4096 or not low<=home<=high:
        raise ValueError('Fixed one-turn envelope and current full position required')
    sign=1 if high-home>=home-low else -1
    result=[home+sign*i for i in (0,128,256)]
    if not all(low<=q<=high for q in result):raise ValueError('Probe outside installation')
    return result


def stable(before,after,target):
    for f in (before,after):
        if not f.get('full_angle') or f['speed'] or f['moving'] or abs(f['position']-target)>4:
            raise ValueError('Stationary full-angle evidence required')
    if abs(before['position']-after['position'])>4:raise ValueError('Position changed during frame capture')
    return (before['position']+after['position'])/2


def observe(pan, cap, matcher, save_frame, clock=time.monotonic, deadline=None):
    deadline=clock()+20 if deadline is None else deadline
    out=dict(status='STARTED',decision_owner='PYNQ',observations=[],moves=[],full_calibration=False)
    def check(_=None):
        if clock()>=deadline:raise TimeoutError('Finite stationary probe deadline')
    try:
        check();out['ready']=pan.open();home=pan.home;goals=plan(home,pan.low,pan.high)
        out.update(start_full=home,goals=goals)
        if not cap.isOpened():raise RuntimeError('Camera open failed')
        for goal in goals:
            check();out['moves'].append(pan.move(goal,timeout=min(5,deadline-clock()),observe=check))
            before=pan.position();check()
            # Drain frames while the actuator is held stationary. Read timestamps
            # remain host-read times, never sensor exposure timestamps.
            for _ in range(12):
                check();ok,img=cap.read()
                if not ok or getattr(img,'shape',None)!=(480,640,3):raise RuntimeError('Camera read failed')
            for repeat in range(2):
                check();begin=clock();ok,img=cap.read();end=clock()
                if not ok or getattr(img,'shape',None)!=(480,640,3):raise RuntimeError('Camera read failed')
                after=pan.position();q=stable(before,after,goal)
                frame=save_frame(img,len(out['observations']))
                result=matcher.infer(img)
                out['observations'].append(dict(goal=goal,position=q,before=before,after=after,
                    frame=dict(frame,read_begin=begin,read_end=end,time_scope='STATIONARY_DRAINED_READ_NOT_EXPOSURE'),vision=result))
                before=after
        check();out['return']=pan.move(home,timeout=min(5,deadline-clock()),observe=check)
        out['status']='STATIONARY_VIEWS_COLLECTED'
    except BaseException as exc:out.update(status='FAILED',error=repr(exc))
    finally:
        out['pan_cleanup']=pan.close()
        out['camera_release_begin']=clock()
        try:cap.release();out['camera_released']=True
        except BaseException as exc:out.update(camera_released=False,camera_cleanup_error=repr(exc))
        out['finished']=clock();out['cleanup_verified']=out['pan_cleanup']['cleanup_verified'] and out['camera_released']
        out['servo_feedback']=pan.board.feedback;out['servo_protocol']=pan.board.events
    return out


def main():
    import fcntl
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--runtime',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();cfg=json.loads(Path(args.config).read_text());runtime=Path(args.runtime);out=Path(args.out);out.mkdir(exist_ok=False)
    parent=Path(cfg['session_root']);bundle=Path(cfg['hardware_root']);boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    for name,sha in cfg['runtime_files'].items():
        if hashlib.sha256((runtime/name).read_bytes()).hexdigest()!=sha:raise ValueError('Runtime source changed '+name)
    sys.path.insert(0,str(bundle));sys.path.insert(0,str(runtime))
    from av_operation_control_V1 import MotionWindow
    if not MotionWindow(cfg['motion_window'],boot).admission(boot,10)['ready'] or time.monotonic()+25>=cfg['motion_window']['stop_monotonic']:raise RuntimeError('Motion time window closed')
    owner=(parent/'standalone_runtime.lock').open('a+')
    fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
    import cv2,pynq
    from av_pmod_check_V1 import Check
    from av_known_computer_V2 import KnownComputer
    from servo_pan_session_V4 import PanSession
    class ProbeCheck(Check):
        def save(self,name,value):(out/('hardware_'+name)).write_text(json.dumps(value,indent=2))
    checker=ProbeCheck(bundle,cfg['hardware_files'],boot)
    if not checker.quiet()['cleanup_verified']:raise RuntimeError('Existing hardware activity')
    refs=json.loads((parent/'computer_references_V2.json').read_text());matcher=KnownComputer(refs)
    reference=json.loads((parent/'installation_reference_V1.json').read_text())
    usb=next(x for x in (Path('/sys/class/video4linux/video0')/'device').resolve().parents if (x/'idVendor').exists())
    if (usb/'idVendor').read_text().strip()+':'+(usb/'idProduct').read_text().strip()!='1d6c:0103':raise ValueError('Camera identity')
    power=usb/'power/control';original=power.read_text().strip()
    def save(img,index):
        name='view_%02d.jpg'%index;path=out/name
        if not cv2.imwrite(str(path),img,[cv2.IMWRITE_JPEG_QUALITY,90]):raise IOError('JPEG save failed')
        return dict(file=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),write_end=time.monotonic())
    cap=None;pan=None;begin=time.monotonic();result=dict(status='PREPARING',cleanup_verified=False)
    try:
        power.write_text('on');cap=cv2.VideoCapture('/dev/video0',cv2.CAP_V4L2)
        for k,v in ((cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG')),(cv2.CAP_PROP_FRAME_WIDTH,640),(cv2.CAP_PROP_FRAME_HEIGHT,480),(cv2.CAP_PROP_FPS,30),(cv2.CAP_PROP_BUFFERSIZE,1)):cap.set(k,v)
        pan=PanSession(pynq.MMIO(0x43c10000,65536),reference,boot,1200,10,torque_limit=200)
        result=observe(pan,cap,matcher,save,deadline=begin+20)
    except BaseException as exc:
        result.update(status='FAILED',error=repr(exc))
        result['pan_cleanup']=dict(cleanup_verified=True) if pan is None else pan.close()
        if cap is not None:cap.release()
        result.update(camera_released=True,cleanup_verified=result['pan_cleanup']['cleanup_verified'])
    finally:
        power.write_text(original);result['power_restored']=power.read_text().strip()==original
    result.update(boot_id=boot,begin=begin,scope='STATIONARY_VISUAL_AXIS_PROBE_NO_AUDIO',source_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),postflight=checker.quiet(),total_seconds=time.monotonic()-begin)
    result['cleanup_verified']=bool(result['cleanup_verified'] and result['power_restored'] and result['postflight']['cleanup_verified'])
    (out/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    fcntl.flock(owner,fcntl.LOCK_UN);owner.close()
    print(json.dumps({k:result.get(k) for k in ('status','error','goals','total_seconds','cleanup_verified')}),flush=True)
    if result['status']=='FAILED' or not result['cleanup_verified']:raise SystemExit(1)


if __name__=='__main__':main()
