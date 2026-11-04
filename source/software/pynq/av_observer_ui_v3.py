"""Board-hosted observer page. Only source IDs/trigger IDs cross the API.

No hardware imports, no angles accepted, and no automatic command retry.
Runtime process/boot is pinned; a changed runtime requires a new UI process.
"""
import argparse,hashlib,json,signal,threading,time,urllib.request,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path


class Observer:
    def __init__(self,control,pid,boot,backend='http://127.0.0.1:8765'):
        self.control=Path(control).resolve();self.pid=pid;self.boot=boot;self.backend=backend

    def call(self,path,body=None):
        headers={'Content-Type':'application/json','X-AV-Request':'1'}
        request=urllib.request.Request(self.backend+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)

    def state(self):
        state=self.call('/brief')
        if state['pid']!=self.pid or state['boot_id']!=self.boot:raise ValueError('Runtime identity changed')
        sources=[]
        for source in state['sources'][:3]:
            sid=source['source_id'];seq=source['operation_sequence']
            sources.append(dict(source_id=sid,label=source.get('label','source'),bearing_deg=source['audio_bearing_deg'],
                match_score=source.get('score'),operation_sequence=seq,request_id=source['request_id'],
                image_url='/api/image?'+urllib.parse.urlencode(dict(source=sid,sequence=seq))))
        jobs=[{k:j.get(k) for k in ('id','sequence','action','source_id','status','total_seconds','budget_seconds','navigation_ready','cleanup_verified','reason','error')} for j in state['jobs'][-10:]]
        return dict(pid=self.pid,boot_id=self.boot,decision_owner='PYNQ',active=state['active'],sources=sources,max_sources=3,
            calibration=state['calibration'],input_hold=state.get('input_hold'),jobs=jobs,latched_block=state['latched_block'],closed=state['closed'],
            physical_multi_source_validation=state['physical_multi_source_validation'])

    def command(self,action,body):
        expected={'request_id','source_id'} if action=='select' else {'request_id'}
        if action not in ('initialize','select') or type(body)is not dict or set(body)!=expected:
            raise ValueError('Only request_id and source_id are accepted')
        rid=body['request_id']
        if not isinstance(rid,str) or not 1<=len(rid)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in rid):
            raise ValueError('Invalid request ID')
        state=self.state()
        if state['closed'] or state['latched_block']:raise ValueError('Runtime unavailable')
        if state['input_hold']:raise ValueError('相机画面不可用，初始化尚未启动。')
        if action=='select' and body['source_id'] not in [s['source_id'] for s in state['sources']]:raise ValueError('Unknown source')
        return self.call('/select' if action=='select' else '/trigger',body)

    def image(self,sid,sequence):
        state=self.state()
        source=next((s for s in state['sources'] if s['source_id']==sid and str(s['operation_sequence'])==sequence),None)
        if source is None:raise ValueError('View no longer current')
        metadata=json.loads((self.control/'state.json').read_text())
        job=next(j for j in metadata['jobs'] if j['id']==source['request_id'] and j.get('navigation_ready'))
        ref=job['result_reference'];path=(self.control/ref['path']).resolve()
        if self.control not in path.parents:raise ValueError('Result outside control directory')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Result SHA mismatch')
        result=json.loads(data)
        frame=result.get('confirmation_frame')
        if frame is None:
            frame=next((f for f in result.get('frames',[]) if f['file']=='confirmation.jpg'),None)
        if frame is None or Path(frame['file']).name!=frame['file']:raise ValueError('Confirmed image missing')
        image=(path.parent/frame['file']).read_bytes()
        if hashlib.sha256(image).hexdigest()!=frame['sha256']:raise ValueError('Image SHA mismatch')
        return image


def handler(observer,page,allowed=('127.0.0.1','192.168.2.1')):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def send(self,status,data,kind='application/json; charset=utf-8'):
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.client_address[0] not in allowed:return self.send(403,dict(error='Client not allowed'))
            try:
                parsed=urllib.parse.urlparse(self.path)
                if parsed.path=='/':return self.send(200,page,'text/html; charset=utf-8')
                if parsed.path=='/api/state':return self.send(200,observer.state())
                if parsed.path=='/api/image':
                    params=urllib.parse.parse_qs(parsed.query,strict_parsing=True)
                    if set(params)!={'source','sequence'} or any(len(v)!=1 for v in params.values()):raise ValueError('Image parameters')
                    return self.send(200,observer.image(params['source'][0],params['sequence'][0]),'image/jpeg')
                return self.send(404,dict(error='Not found'))
            except Exception as exc:self.send(409,dict(error=str(exc)))
        def do_POST(self):
            if self.client_address[0] not in allowed:return self.send(403,dict(error='Client not allowed'))
            if self.headers.get('Content-Type')!='application/json' or self.headers.get('X-AV-Request')!='1':return self.send(400,dict(error='JSON request required'))
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+self.headers.get('Host',''):return self.send(403,dict(error='Origin mismatch'))
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024:raise ValueError('Request size')
                body=json.loads(self.rfile.read(size))
                action={'/api/initialize':'initialize','/api/select':'select'}.get(self.path)
                return self.send(202,observer.command(action,body))
            except Exception as exc:self.send(409,dict(error=str(exc),request_repeated=False))
    return Handler


def main():
    p=argparse.ArgumentParser();p.add_argument('--control',required=True);p.add_argument('--pid',required=True,type=int)
    p.add_argument('--boot',required=True);p.add_argument('--port',default=8770,type=int);p.add_argument('--duration',default=9000,type=float)
    args=p.parse_args();observer=Observer(args.control,args.pid,args.boot);observer.state()
    if not 1<=args.duration<=18000:raise ValueError('Bounded duration required')
    server=ThreadingHTTPServer(('0.0.0.0',args.port),handler(observer,Path(__file__).with_suffix('.html').read_bytes()))
    server.daemon_threads=True;stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    print(json.dumps(dict(status='OBSERVER_READY',port=args.port,runtime_pid=args.pid)),flush=True)
    stop.wait(args.duration);server.shutdown();worker.join(3);server.server_close()
    print(json.dumps(dict(status='OBSERVER_CLOSED',worker_closed=not worker.is_alive())),flush=True)

if __name__=='__main__':main()
