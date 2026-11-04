"""Local receiver: serves the actual observer HTML and relays IDs only.

No hardware libraries, image inference, angle calculation or automatic POSTs.
An unavailable board returns 503; never fabricates a running/accepted result.
"""
import argparse, hashlib, json, os, re, signal, sys, threading, time
import urllib.request, urllib.error, urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'software/pynq'))
from av_screen_pattern_V1 import svg, PATTERN_ID


class Relay:
    def __init__(self, upstream, out):
        if upstream != 'http://192.168.2.99:8770':
            raise ValueError('Fixed project board observer required')
        self.upstream=upstream; self.out=Path(out); self.lock=threading.Lock(); self.sent=set()

    def request(self, path, body=None):
        headers={'Content-Type':'application/json','X-AV-Request':'1'}
        req=urllib.request.Request(self.upstream+path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=2) as r:
                data=r.read(6*1024*1024+1)
                if len(data)>6*1024*1024:raise ValueError('Oversized upstream response')
                return r.status,r.headers.get('Content-Type','application/json'),data
        except urllib.error.HTTPError as e:
            return e.code,'application/json',e.read(4096)

    def command(self, path, body):
        keys={'request_id','source_id'} if path=='/api/select' else {'request_id'}
        if path not in ('/api/initialize','/api/select') or type(body)is not dict or set(body)!=keys:
            raise ValueError('Only source_id and request_id are accepted')
        rid=body['request_id']
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',rid):raise ValueError('Invalid request ID')
        if 'source_id' in body and (not isinstance(body['source_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',body['source_id'])):raise ValueError('Invalid source ID')
        with self.lock:
            if rid in self.sent:raise ValueError('Already submitted; inspect state, do not resend')
            record=self.out/(rid+'.json')
            with record.open('x',encoding='utf-8') as f:json.dump(dict(path=path,body=body,status='SUBMITTING',pc_unix=time.time()),f)
            self.sent.add(rid)
        try:
            result=self.request(path,body)
        except Exception as exc:
            record.write_text(json.dumps(dict(path=path,body=body,status='OUTCOME_UNKNOWN',error=str(exc))),encoding='utf-8')
            raise
        record.write_text(json.dumps(dict(path=path,body=body,status='HTTP_RESULT',code=result[0],response=result[2].decode('utf-8','replace'))),encoding='utf-8')
        return result


def handler(relay,page):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,code,mime,data):
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            try:
                parsed=urllib.parse.urlsplit(self.path)
                if parsed.path=='/' and not parsed.query:return self.send(200,'text/html; charset=utf-8',page)
                if self.path=='/screen-pattern.svg':return self.send(200,'image/svg+xml',svg().encode())
                if self.path=='/api/receiver':return self.send(200,'application/json',json.dumps(dict(role='DISPLAY_AND_ID_RELAY_ONLY',pattern_id=PATTERN_ID,upstream=relay.upstream,pid=os.getpid())).encode())
                if self.path=='/api/state':return self.send(*relay.request(self.path))
                q=urllib.parse.parse_qs(parsed.query,strict_parsing=True)
                if parsed.path=='/api/image' and set(q)=={'source','sequence'} and all(len(v)==1 for v in q.values()) and q['sequence'][0].isdigit() and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',q['source'][0]):
                    return self.send(*relay.request(self.path))
                return self.send(404,'application/json',b'{"error":"not found"}')
            except Exception as exc:self.send(503,'application/json',json.dumps(dict(error=str(exc),board_connected=False)).encode())
        def do_POST(self):
            try:
                if self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)):raise ValueError('Local host required')
                origin=self.headers.get('Origin')
                if origin and origin!='http://'+self.headers.get('Host'):raise ValueError('Origin mismatch')
                if self.headers.get('Content-Type')!='application/json' or self.headers.get('X-AV-Request')!='1':raise ValueError('Explicit JSON command required')
                size=int(self.headers.get('Content-Length','0'))
                if not 1<=size<=1024:raise ValueError('Invalid body length')
                body=json.loads(self.rfile.read(size))
                return self.send(*relay.command(self.path,body))
            except ValueError as exc:self.send(409,'application/json',json.dumps(dict(error=str(exc))).encode())
            except Exception as exc:self.send(503,'application/json',json.dumps(dict(error=str(exc),outcome='UNKNOWN_DO_NOT_RESEND')).encode())
    return Handler


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--port',type=int,default=8780);p.add_argument('--seconds',type=int,default=36000)
    args=p.parse_args();out=Path(args.out).resolve()
    if out.drive.upper()!='D:' or not 1<=args.seconds<=43200:raise ValueError('D drive and bounded lifetime required')
    out.mkdir(parents=True,exist_ok=False)
    page=(ROOT/'software/pynq/av_observer_ui_V7.html').read_bytes()
    relay=Relay('http://192.168.2.99:8770',out)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(relay,page));server.daemon_threads=True
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    owner=dict(pid=os.getpid(),url='http://127.0.0.1:'+str(args.port)+'/',started_pc_unix=time.time(),max_seconds=args.seconds,page_sha256=hashlib.sha256(page).hexdigest(),decision_owner='PYNQ')
    (out/'owner.json').write_text(json.dumps(owner,indent=2),encoding='utf-8')
    pointer=ROOT/'logs/av_receiver_V1/active.json';pointer.parent.mkdir(exist_ok=True);pointer.write_text(json.dumps(dict(owner,path=str(out)),indent=2),encoding='utf-8')
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();print(json.dumps(owner),flush=True)
    try:stop.wait(args.seconds)
    finally:
        server.shutdown();server.server_close();worker.join(3)
        (out/'closed.json').write_text(json.dumps(dict(closed=True,pc_unix=time.time())),encoding='utf-8')


if __name__=='__main__':main()
