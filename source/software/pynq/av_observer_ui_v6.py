"""Direction catalogue observer; selections contain IDs, never motor angles."""
from av_signal_latch_V1 import SignalLatch
import argparse,json,signal,threading,urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from av_observer_ui_V3 import Observer as PreviousObserver,handler


class Observer(PreviousObserver):
    def state(self):
        state=self.call('/brief')
        if state['pid']!=self.pid or state['boot_id']!=self.boot:raise ValueError('Runtime identity changed')
        sources=[]
        active=next((j for j in state['jobs'] if j['id']==state['active']),{})
        can_request=not(state['closed'] or state['latched_block'] or state.get('input_hold') or state.get('pending'))
        # A running discovery owns the hardware until its ordinary finalizer.
        # New UI commands must not turn an observation into a cancellation.
        can_request=can_request and not state['active']
        for item in state['sources'][:3]:
            if item.get('source_kind')!='DIRECTION_CANDIDATE':raise ValueError('Direction catalogue required')
            sid=item['source_id'];seq=item.get('operation_sequence')
            image_url=None
            if item.get('camera_view_saved') and seq is not None:
                image_url='/api/image?'+urllib.parse.urlencode(dict(source=sid,sequence=seq))
            sources.append(dict(source_id=sid,label='声源方向',bearing_deg=item['direction_center_deg'],
                direction_status=item['direction_status'],observations=item['observations'],
                age_seconds=item['conservative_audio_age_seconds'],
                selectable=can_request and item['direction_status']=='CONFIRMED' and state['calibration']=='RELATIVE_SINGLE_SOURCE',
                image_url=image_url,operation_sequence=seq,request_id=item.get('request_id'),
                view_bearing_deg=item.get('view_audio_bearing_deg'),physical_identity_verified=False))
        jobs=[{k:j.get(k) for k in ('id','sequence','action','source_id','status','total_seconds','budget_seconds','navigation_ready','cleanup_verified','reason','error')} for j in state['jobs'][-10:]]
        return dict(pid=self.pid,boot_id=self.boot,decision_owner='PYNQ',active=state['active'],
            active_action=active.get('action'),pending=state.get('pending'),can_request=can_request,
            sources=sources,max_sources=3,calibration=state['calibration'],input_hold=state.get('input_hold'),
            discovery_hold=state.get('discovery_hold'),jobs=jobs,latched_block=state['latched_block'],closed=state['closed'],
            physical_multi_source_validation=False,simultaneous_source_separation=False)

    def command(self,action,body):
        expected={'request_id','source_id'} if action=='select' else {'request_id'}
        if action not in ('initialize','select') or type(body)is not dict or set(body)!=expected:
            raise ValueError('Only request_id and source_id are accepted')
        rid=body['request_id']
        if not isinstance(rid,str) or not 1<=len(rid)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in rid):raise ValueError('Invalid request ID')
        state=self.state()
        if not state['can_request']:raise ValueError('板端当前无法开始新动作，请查看当前任务状态。')
        if action=='select' and not any(s['source_id']==body['source_id'] and s['selectable'] for s in state['sources']):
            raise ValueError('所选方向尚未确认或已过期。')
        return self.call('/select' if action=='select' else '/trigger',body)


def main():
    p=argparse.ArgumentParser();p.add_argument('--control',required=True);p.add_argument('--pid',type=int,required=True)
    p.add_argument('--boot',required=True);p.add_argument('--port',type=int,default=8770);p.add_argument('--duration',type=float,required=True)
    args=p.parse_args()
    if not 1<=args.duration<=18000:raise ValueError('Bounded observer duration required')
    stop=SignalLatch();observer=Observer(args.control,args.pid,args.boot);observer.state()
    server=ThreadingHTTPServer(('0.0.0.0',args.port),handler(observer,Path(__file__).with_suffix('.html').read_bytes()))
    server.daemon_threads=True
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    print(json.dumps(dict(status='OBSERVER_READY',port=args.port,runtime_pid=args.pid)),flush=True)
    try:stop.wait(args.duration)
    finally:server.shutdown();server.server_close();thread.join(2)
    print(json.dumps(dict(status='OBSERVER_CLOSED',worker_closed=not thread.is_alive())),flush=True)

if __name__=='__main__':main()
