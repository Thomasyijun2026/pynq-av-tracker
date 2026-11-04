"""V6: board coordinator accepts fresh acoustic follow with all original gates.

STARTUP/BTN0/HTTP use the same admission, deadline and result path. Interrupted
jobs are recorded, never replayed. A refused job never invokes the scan runner.
"""
import copy,json,math,os,threading,time,uuid,hashlib
from pathlib import Path


def atomic_json(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        f.write(json.dumps(value,ensure_ascii=False,separators=(",",":"),allow_nan=False));f.flush();os.fsync(f.fileno())
    os.replace(str(temporary),str(path))


class ButtonEdges:
    """50 ms stable release before arming, one event per physical press."""
    def __init__(self,debounce=.05):
        if debounce<=0:raise ValueError('Positive debounce required')
        self.delay=debounce;self.candidate=[None]*4;self.since=[None]*4
        self.stable=[None]*4;self.armed=[False]*4;self.last_time=None

    def update(self,raw,now):
        if type(raw)is not int or not 0<=raw<16 or not math.isfinite(now):raise ValueError('Button input')
        if self.last_time is not None and now<self.last_time:raise ValueError('Clock moved backwards')
        self.last_time=now;pressed=[]
        for i in range(4):
            bit=(raw>>i)&1
            if bit!=self.candidate[i]:self.candidate[i]=bit;self.since[i]=now
            if now-self.since[i]>=self.delay and bit!=self.stable[i]:
                self.stable[i]=bit
                if bit==0:self.armed[i]=True
                elif self.armed[i]:pressed.append(i);self.armed[i]=False
        return pressed


class Coordinator:
    def __init__(self,folder,boot_id,admission,runner,quiet,budget=10.,clock=time.monotonic):
        if not 0<budget<=10:raise ValueError('Initialization deadline must be at most 10 seconds')
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.state_file=self.folder/'state.json';self.boot_id=boot_id
        self.admission,self.runner,self.quiet,self.budget,self.clock=admission,runner,quiet,budget,clock
        self.lock=threading.RLock();self.cancel=threading.Event();self.worker=None;self.active=None;self.closed=False
        self.jobs={};self.sources=[];self.sequence=0;self.latched_block=None;self.result_refs={};self.input_hold=None
        if self.state_file.exists():
            old=json.loads(self.state_file.read_text())
            for job in old.get('jobs',[]):
                ref=job.pop('result_reference',None)
                if ref is not None:
                    path=(self.folder/ref['path']).resolve()
                    if self.folder.resolve() not in path.parents:raise ValueError('Result reference outside control directory')
                    data=path.read_bytes()
                    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Result reference SHA mismatch')
                    job['result']=json.loads(data);self.result_refs[job['id']]=ref
                if job['status'] in ('RUNNING','CANCELLING','DEADLINE_CLEANUP'):
                    job.update(status='INTERRUPTED_NO_REPLAY',cleanup_verified=False)
                if job['status'] in ('INTERRUPTED_NO_REPLAY','CLEANUP_UNCONFIRMED'):
                    self.latched_block='PRIOR_CLEANUP_REQUIRES_RECONCILIATION'
                self.jobs[job['id']]=job
            self.sequence=old.get('sequence',0)
        self._save()

    def _state(self):
        return dict(version=1,boot_id=self.boot_id,pid=os.getpid(),decision_owner='PYNQ',
                    sequence=self.sequence,active=self.active,closed=self.closed,
                    jobs=list(self.jobs.values()),sources=self.sources,max_sources=3,
                    latched_block=self.latched_block,input_hold=self.input_hold,calibration='RELATIVE_SINGLE_SOURCE' if self.sources else 'PENDING',
                    physical_multi_source_validation=False)

    def _save(self):
        # Large immutable evidence is written once; UI history never copies it.
        state=self._state();records=[]
        for job in state['jobs']:
            row={k:v for k,v in job.items() if k!='result'}
            if job.get('result') is not None:
                ref=self.result_refs.get(job['id'])
                if ref is None:
                    artifact=job['result'].get('result_artifact')
                    operation=self.folder/('operation_%06d'%job['sequence'])
                    path=operation/artifact if artifact in ('summary.json','aligned_summary.json','service_result.json') else None
                    if path is None or not path.is_file():
                        path=self.folder/('result_%06d.json'%job['sequence']);atomic_json(path,job['result'])
                    ref=dict(path=str(path.relative_to(self.folder)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                    self.result_refs[job['id']]=ref
                row['result_reference']=ref
            records.append(row)
        state['jobs']=records;atomic_json(self.state_file,state)

    def brief(self):
        with self.lock:
            state=self._state();state['jobs']=[{k:v for k,v in j.items() if k not in ('result','cleanup')} for j in state['jobs']]
            return copy.deepcopy(state)

    def snapshot(self):
        with self.lock:return copy.deepcopy(self._state())

    def _complete_input_hold(self,j):
        """Persist an unstarted input-hold request with a fresh idle check.

        No worker, camera, DMA or servo action was started. A failed idle check
        remains quarantined rather than being called a successful cleanup.
        """
        self.input_hold=dict(reason=j['reason'],evidence=j['admission'].get('evidence'))
        j['action_started']=False
        try:q=self.quiet()
        except Exception as exc:q=dict(cleanup_verified=False,error=repr(exc))
        j.update(cleanup=q,cleanup_verified=q.get('cleanup_verified') is True,
                 finished_monotonic=self.clock())
        if not j['cleanup_verified']:
            j['status']='CLEANUP_UNCONFIRMED';self.latched_block='CLEANUP_UNCONFIRMED'
        self._save()
        published=self.clock()
        j.update(published_monotonic=published,total_seconds=published-j['trigger_monotonic'],
                 deadline_met=published<=j['trigger_monotonic']+j['budget_seconds'])
        self._save()
        return copy.deepcopy(j)

    def trigger(self,source,request_id,action='initialize',source_id=None):
        if source not in ('STARTUP','BUTTON0','HTTP','TEST'):raise ValueError('Unsupported trigger source')
        if not isinstance(request_id,str) or len(request_id)>80 or not request_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in request_id):raise ValueError('Request id')
        if action not in ('initialize','select'):raise ValueError('Unsupported action')
        if action=='initialize' and source_id is not None:raise ValueError('Initialization does not take a source')
        if action=='select' and not isinstance(source_id,str):raise ValueError('Source ID required')
        with self.lock:
            if request_id in self.jobs:
                old=self.jobs[request_id]
                if old.get('action','initialize')!=action or old.get('source_id')!=source_id:raise ValueError('Request ID reused with different action')
                return copy.deepcopy(old)
            if len(self.jobs)>=512:return dict(id=request_id,status='REJECTED_HISTORY_FULL',full_chain_verified=False)
            self.sequence+=1
            budget=self.budget if action=='initialize' else 5.
            j=dict(id=request_id,sequence=self.sequence,source=source,boot_id=self.boot_id,trigger_monotonic=self.clock(),action=action,source_id=source_id,
                   status='ADMISSION',full_chain_verified=False,within_5_seconds=False,
                   within_budget=False,budget_seconds=budget,navigation_ready=False)
            self.jobs[request_id]=j
            if self.closed:j.update(status='REJECTED_CLOSED')
            elif self.latched_block:j.update(status='BLOCKED',reason=self.latched_block)
            elif self.active is not None:j.update(status='REJECTED_BUSY',reason='Existing operation must finish cleanup')
            elif action=='select' and not any(s['source_id']==source_id for s in self.sources):j.update(status='REJECTED_UNKNOWN_SOURCE')
            else:
                try:ready=self.admission()
                except Exception as exc:ready=dict(ready=False,reason='ADMISSION_EXCEPTION',error=repr(exc))
                j['admission']=ready
                if not ready.get('ready'):
                    j.update(status='BLOCKED',reason=ready.get('reason','NOT_READY'))
                    if ready.get('input_hold') is True:return self._complete_input_hold(j)
                elif self.clock()>=j['trigger_monotonic']+budget:j.update(status='DEADLINE_BEFORE_ACTION')
                else:
                    if action=='initialize':self.sources=[]
                    self.active=request_id;self.cancel.clear();j.update(status='RUNNING',deadline_monotonic=j['trigger_monotonic']+budget)
                    self.worker=threading.Thread(target=self._execute,args=(request_id,),name='av_board_scan',daemon=False)
                    self._save();self.worker.start();return copy.deepcopy(j)
            self._save();return copy.deepcopy(j)

    def _execute(self,request_id):
        j=self.jobs[request_id];deadline=j['deadline_monotonic'];result=None;failure=None
        def cancelled():return self.cancel.is_set() or self.clock()>=deadline
        operation=dict(action=j['action'],source_id=j['source_id'])
        if j['action']=='select':operation['source']=copy.deepcopy(next(s for s in self.sources if s['source_id']==j['source_id']))
        try:result=self.runner(self.folder/('operation_%06d'%j['sequence']),cancelled,deadline,operation)
        except Exception as exc:failure=repr(exc)
        try:q=self.quiet()
        except Exception as exc:q=dict(cleanup_verified=False,error=repr(exc))
        with self.lock:
            now=self.clock();duration=now-j['trigger_monotonic']
            j.update(finished_monotonic=now,total_seconds=duration,cleanup=q,cleanup_verified=q.get('cleanup_verified',False),
                     result=result,error=failure)
            # Both the physical verdict and cleanup belong inside the service latency.
            if not q.get('cleanup_verified'):
                j['status']='CLEANUP_UNCONFIRMED';self.latched_block='CLEANUP_UNCONFIRMED'
            elif failure:j['status']='FAILED'
            elif self.cancel.is_set():j['status']='CANCELLED'
            elif now>deadline:j['status']='DEADLINE_EXCEEDED'
            else:
                full=bool(result and result.get('full_chain_verified') is True and result.get('calibration_status')=='CALIBRATED'
                          and result.get('status')=='TARGET_CONFIRMED' and result.get('source_anchor',{}).get('source_id'))
                navigation=bool(result and result.get('navigation_ready') and result.get('local_cleanup_verified') and
                    result.get('calibration_status')=='RELATIVE_SINGLE_SOURCE_ALIGNED' and
                    result.get('status') == ('RELATIVE_SOURCE_CONFIRMED' if j['action']=='initialize' else 'AUDIO_SOURCE_VIEW_READY'))
                j.update(status='TARGET_CONFIRMED' if full else ('RELATIVE_TARGET_CONFIRMED' if navigation else 'NOT_VERIFIED'),full_chain_verified=full,
                         navigation_ready=navigation,within_5_seconds=(full or navigation) and duration<=5,within_budget=(full or navigation) and duration<=j['budget_seconds'])
            j['deadline_met']=duration<=j['budget_seconds']
            if j['full_chain_verified'] or j['navigation_ready']:
                # Only new actual verified source results enter the up-to-three list.
                anchor=copy.deepcopy(result['source_anchor']);anchor.update(request_id=request_id,operation_sequence=j['sequence'],relative_alignment=copy.deepcopy(result['relative_alignment']),boot_id=self.boot_id)
                self.sources=([s for s in self.sources if s['source_id']!=anchor['source_id']]+[anchor])[-3:]
            self.active=None;self._save()
            published=self.clock();j.update(published_monotonic=published,total_seconds=published-j['trigger_monotonic'])
            j['deadline_met']=published<=deadline
            if not j['deadline_met'] and (j['navigation_ready'] or j['full_chain_verified']):
                j.update(status='DEADLINE_EXCEEDED',navigation_ready=False,full_chain_verified=False,within_budget=False,within_5_seconds=False)
                self.sources=[s for s in self.sources if s.get('request_id')!=request_id]
            elif j['navigation_ready'] or j['full_chain_verified']:
                j.update(within_5_seconds=j['total_seconds']<=5,within_budget=j['total_seconds']<=j['budget_seconds'])
            self._save()

    def request_cancel(self):
        with self.lock:
            if self.active is not None:
                self.cancel.set();self.jobs[self.active]['status']='CANCELLING';self._save()
            return dict(active=self.active,cancel_requested=self.cancel.is_set())

    def maintain(self):
        with self.lock:
            if self.active is not None and self.clock()>=self.jobs[self.active]['deadline_monotonic'] and self.jobs[self.active]['status']!='DEADLINE_CLEANUP':
                self.jobs[self.active]['status']='DEADLINE_CLEANUP';self._save()

    def close(self,timeout=20):
        with self.lock:self.closed=True;self.cancel.set();worker=self.worker
        if worker is not None:worker.join(timeout)
        with self.lock:
            self._save();return dict(worker_closed=worker is None or not worker.is_alive(),active=self.active)


def make_handler(engine,allowed=('127.0.0.1','192.168.2.1')):
    from http.server import BaseHTTPRequestHandler
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def answer(self,status,body):
            data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.client_address[0] not in allowed:return self.answer(403,dict(error='CLIENT_NOT_ALLOWED'))
            if self.path=='/status':return self.answer(200,engine.snapshot())
            if self.path=='/brief':return self.answer(200,engine.brief())
            self.answer(404,dict(error='NOT_FOUND'))
        def do_POST(self):
            if self.client_address[0] not in allowed:return self.answer(403,dict(error='CLIENT_NOT_ALLOWED'))
            if self.headers.get('Content-Type')!='application/json' or self.headers.get('X-AV-Request')!='1':return self.answer(400,dict(error='JSON_REQUEST_HEADER_REQUIRED'))
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+self.headers.get('Host',''):return self.answer(403,dict(error='ORIGIN_MISMATCH'))
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=1024:raise ValueError('Request size')
                body=json.loads(self.rfile.read(n))
                if self.path=='/trigger':
                    if set(body)!={'request_id'}:raise ValueError('Only board-owned alignment requests accepted; no angles')
                    return self.answer(202,engine.trigger('HTTP',body['request_id']))
                if self.path=='/select':
                    if set(body)!={'request_id','source_id'}:raise ValueError('Select accepts only a source ID; no angles')
                    return self.answer(202,engine.trigger('HTTP',body['request_id'],'select',body['source_id']))
                if self.path=='/cancel':return self.answer(200,engine.request_cancel())
                self.answer(404,dict(error='NOT_FOUND'))
            except (ValueError,KeyError,TypeError) as exc:self.answer(400,dict(error=str(exc)))
    return Handler
