"""One hardware owner: alignment, automatic direction discovery, selection.

Discovery records successive dominant directions; it does not separate multiple
simultaneous sound sources. A queued user request waits for discovery to finish naturally, including
its worker and cleanup. It never sets the active cancellation event. That wait is included in the original request deadline.
"""
import copy
import threading
import time
import uuid
from av_trigger_service_V6 import Coordinator as PreviousCoordinator
from av_trigger_service_V6 import ButtonEdges,make_handler,atomic_json
from av_direction_catalogue_V1 import DirectionCatalogue
from av_audio_rejection_V1 import healthy_inconclusive


class Coordinator(PreviousCoordinator):
    def __init__(self,*args,discovery_interval=1.0,**kwargs):
        if not .5<=discovery_interval<=10:raise ValueError('Discovery interval')
        self.alignment=None;self.initialization_anchor=None;self.views={}
        self.catalogue=None;self.pending=None;self.next_discovery=0.
        self.discovery_interval=discovery_interval;self.discovery_sequence=0
        self.discovery_hold=None;self.discovery_failures=0;self.inconclusive_windows=0;self.last_rejection=None
        super().__init__(*args,**kwargs)
        for job in self.jobs.values():
            if job.get('status') in ('WAITING_FOR_AUDIO_CLEANUP','WAITING_FOR_WORKER_RETURN','WAITING_FOR_WORKER_COMPLETION'):
                job.update(status='INTERRUPTED_BEFORE_ACTION_NO_REPLAY',action_started=False)
        self._reset_catalogue();self._save()

    def _reset_catalogue(self):
        self.catalogue=DirectionCatalogue(self.boot_id,'c'+uuid.uuid4().hex[:20])
        self.alignment=None;self.initialization_anchor=None;self.views={};self.sources=[]
        self.discovery_hold=None;self.discovery_failures=0;self.inconclusive_windows=0;self.last_rejection=None

    def _state(self):
        if self.catalogue is not None:
            catalogue=self.catalogue.snapshot(self.clock());self.sources=catalogue['sources']
            for item in self.sources:
                view=self.views.get(item['source_id'])
                if view:item.update(copy.deepcopy(view))
            live={s['source_id'] for s in self.sources}
            self.views={k:v for k,v in self.views.items() if k in live}
        else:catalogue=None
        state=super()._state()
        state.update(version=9,pending=self.pending,
            calibration='RELATIVE_SINGLE_SOURCE' if self.alignment else 'PENDING',
            relative_alignment=copy.deepcopy(self.alignment),
            initialization_anchor=copy.deepcopy(self.initialization_anchor),
            catalogue_epoch=None if self.catalogue is None else self.catalogue.epoch,
            catalogue=catalogue,automatic_discovery=True,
            discovery_hold=self.discovery_hold,discovery_failures=self.discovery_failures,
            inconclusive_windows=self.inconclusive_windows,last_audio_rejection=copy.deepcopy(self.last_rejection),
            simultaneous_source_separation=False)
        return state

    def _prune_discoveries(self):
        old=[j for j in self.jobs.values() if j.get('source')=='AUTODISCOVERY' and
             j['id'] not in (self.active,self.pending)]
        for job in old[:-64]:
            self.jobs.pop(job['id']);self.result_refs.pop(job['id'],None)

    def _start(self,j):
        """Called only when the prior hardware worker has returned."""
        if self.closed:j.update(status='REJECTED_CLOSED')
        elif self.latched_block:j.update(status='BLOCKED',reason=self.latched_block)
        elif self.clock()>=j['deadline_monotonic']:j.update(status='DEADLINE_BEFORE_ACTION')
        else:
            try:ready=self.admission()
            except Exception as exc:ready=dict(ready=False,reason='ADMISSION_EXCEPTION',error=repr(exc))
            j['admission']=ready
            if not ready.get('ready'):
                j.update(status='BLOCKED',reason=ready.get('reason','NOT_READY'))
                if ready.get('input_hold') is True:return self._complete_input_hold(j)
            elif self.clock()>=j['deadline_monotonic']:j.update(status='DEADLINE_BEFORE_ACTION')
            else:
                if j['action']=='initialize':self._reset_catalogue()
                self.active=j['id'];self.cancel.clear();j.update(status='RUNNING',action_started=True)
                self.worker=threading.Thread(target=self._execute,args=(j['id'],),name='av_board_owner',daemon=False)
                self._save();self.worker.start();return copy.deepcopy(j)
        j['action_started']=False;self._save();return copy.deepcopy(j)

    def trigger(self,source,request_id,action='initialize',source_id=None):
        if source not in ('STARTUP','BUTTON0','HTTP','TEST','AUTODISCOVERY'):raise ValueError('Unsupported trigger source')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in request_id):raise ValueError('Request id')
        if action not in ('initialize','select','discover'):raise ValueError('Unsupported action')
        if action=='discover' and source!='AUTODISCOVERY':raise ValueError('Discovery is board scheduled')
        if action!='discover' and source=='AUTODISCOVERY':raise ValueError('Automatic source only discovers')
        if action!='select' and source_id is not None:raise ValueError('Unexpected source ID')
        if action=='select' and not isinstance(source_id,str):raise ValueError('Source ID required')
        with self.lock:
            if request_id in self.jobs:
                old=self.jobs[request_id]
                if old.get('action','initialize')!=action or old.get('source_id')!=source_id:raise ValueError('Request ID reused with different action')
                return copy.deepcopy(old)
            self._prune_discoveries()
            if sum(j.get('source')!='AUTODISCOVERY' for j in self.jobs.values())>=512:
                return dict(id=request_id,status='REJECTED_HISTORY_FULL',full_chain_verified=False)
            self.sequence+=1;begin=self.clock();budget=self.budget if action=='initialize' else 5.
            j=dict(id=request_id,sequence=self.sequence,source=source,boot_id=self.boot_id,
                action=action,source_id=source_id,trigger_monotonic=begin,
                deadline_monotonic=begin+budget,budget_seconds=budget,status='ADMISSION',
                full_chain_verified=False,navigation_ready=False,within_5_seconds=False,within_budget=False)
            self.jobs[request_id]=j
            if self.closed:j.update(status='REJECTED_CLOSED')
            elif self.latched_block:j.update(status='BLOCKED',reason=self.latched_block)
            elif self.pending is not None:j.update(status='REJECTED_BUSY')
            elif action!='initialize' and self.alignment is None:j.update(status='BLOCKED',reason='ALIGNMENT_PENDING')
            else:
                if action=='select':
                    try:self.catalogue.select(source_id,begin)
                    except ValueError as exc:
                        j.update(status='REJECTED_SOURCE',reason=str(exc));self._save();return copy.deepcopy(j)
                if self.active is not None:
                    if self.jobs[self.active]['action']=='discover' and action!='discover':
                        self.pending=request_id;j.update(status='WAITING_FOR_WORKER_COMPLETION',action_started=False)
                    else:j.update(status='REJECTED_BUSY')
                elif self.worker is not None and self.worker.is_alive():
                    self.pending=request_id;j.update(status='WAITING_FOR_WORKER_RETURN',action_started=False)
                else:return self._start(j)
            self._save();return copy.deepcopy(j)

    def _execute(self,request_id):
        j=self.jobs[request_id];deadline=j['deadline_monotonic'];result=None;failure=None
        def cancelled():return self.cancel.is_set() or self.clock()>=deadline
        operation=dict(action=j['action'],source_id=j['source_id'],catalogue_epoch=self.catalogue.epoch)
        try:
            if j['action']=='select':
                with self.lock:
                    operation['source']=self.catalogue.select(j['source_id'],self.clock())
                    operation['source']['relative_alignment']=copy.deepcopy(self.alignment)
            result=self.runner(self.folder/('operation_%06d'%j['sequence']),cancelled,deadline,operation)
        except Exception as exc:failure=repr(exc)
        try:q=self.quiet()
        except Exception as exc:q=dict(cleanup_verified=False,error=repr(exc))
        with self.lock:
            now=self.clock();duration=now-j['trigger_monotonic']
            before=(copy.deepcopy(self.catalogue),copy.deepcopy(self.alignment),copy.deepcopy(self.initialization_anchor),copy.deepcopy(self.views))
            j.update(finished_monotonic=now,total_seconds=duration,cleanup=q,
                cleanup_verified=q.get('cleanup_verified') is True,result=result,error=failure)
            if not j['cleanup_verified']:
                j['status']='CLEANUP_UNCONFIRMED';self.latched_block='CLEANUP_UNCONFIRMED'
            elif failure:j['status']='FAILED'
            elif self.cancel.is_set():j['status']='CANCELLED'
            elif now>deadline:j['status']='DEADLINE_EXCEEDED'
            else:
                try:self._accept(j,result,now)
                except Exception as exc:j.update(status='NOT_VERIFIED',error=repr(exc))
            accepted=j['status'] in ('RELATIVE_TARGET_CONFIRMED','DISCOVERY_RECORDED','DISCOVERY_REJECTED')
            if not accepted:
                self.catalogue,self.alignment,self.initialization_anchor,self.views=before
            backoff=self.discovery_interval
            if j['action']=='discover' and j['status'] not in ('CANCELLED','DISCOVERY_RECORDED'):
                soft=(j['status']=='DISCOVERY_REJECTED' and j['cleanup_verified'] and
                      healthy_inconclusive((result or {}).get('source_event',{}),self.boot_id,now))
                if soft:
                    self.inconclusive_windows+=1;self.discovery_failures=0
                    backoff=max(self.discovery_interval,5.0)
                    self.last_rejection=dict(request_id=j['id'],classification='HEALTHY_INCONCLUSIVE_SOUND',
                        reasons=copy.deepcopy(result['source_event']['audio_evidence']['reasons']),
                        retry_after_seconds=backoff,bearing_published=False)
                    j['listener_policy']=copy.deepcopy(self.last_rejection)
                else:
                    fault=j['status']!='DISCOVERY_REJECTED' or j.get('catalogue_verdict',{}).get('status')=='REJECTED_AUDIO'
                    if fault:
                        self.discovery_failures+=1
                        if self.discovery_failures>=2:self.discovery_hold='TWO_FAILED_OBSERVATIONS_REQUIRES_NEW_EVIDENCE'
            elif j['status']=='DISCOVERY_RECORDED':
                self.discovery_failures=0;self.inconclusive_windows=0;self.last_rejection=None
            self.active=None;self.next_discovery=now+backoff
            self._save();published=self.clock()
            j.update(published_monotonic=published,total_seconds=published-j['trigger_monotonic'],deadline_met=published<=deadline)
            if not j['deadline_met'] and accepted:
                self.catalogue,self.alignment,self.initialization_anchor,self.views=before
                j.update(status='DEADLINE_EXCEEDED',navigation_ready=False,within_budget=False,within_5_seconds=False)
            else:
                j['within_budget']=accepted and j['deadline_met']
                j['within_5_seconds']=j['navigation_ready'] and j['total_seconds']<=5
            self._save()

    def _accept(self,j,result,now):
        if not result or result.get('local_cleanup_verified') is not True:
            raise ValueError('Local cleanup required')
        action=j['action']
        if action=='discover':
            if result.get('status')!='AUDIO_OBSERVATION_CAPTURED':raise ValueError('Discovery result phase')
            verdict=self.catalogue.ingest(result['source_event'],now,self.catalogue.epoch)
            j['catalogue_verdict']=verdict
            j['status']='DISCOVERY_RECORDED' if verdict['status'] in ('CREATED','UPDATED') else 'DISCOVERY_REJECTED'
            return
        expected='RELATIVE_SOURCE_CONFIRMED' if action=='initialize' else 'AUDIO_SOURCE_VIEW_READY'
        if (result.get('navigation_ready') is not True or result.get('status')!=expected or
            result.get('calibration_status')!='RELATIVE_SINGLE_SOURCE_ALIGNED'):
            raise ValueError('Navigation result phase')
        if action=='initialize':
            self.alignment=copy.deepcopy(result['relative_alignment'])
            self.initialization_anchor=copy.deepcopy(result.get('initialization_anchor',result['source_anchor']))
        else:
            anchor=result['source_anchor']
            if anchor['source_id']!=j['source_id']:raise ValueError('Selected identity changed')
            if result.get('selected_before_move',{}).get('status')!='SELECTED_DIRECTION_SUPPORTED':
                raise ValueError('Selected direction support required')
            verdict=self.catalogue.ingest(anchor['audio_event'],now,self.catalogue.epoch)
            if verdict.get('source_id')!=j['source_id'] or verdict['status']!='UPDATED':
                raise ValueError('Fresh selection did not update selected track')
            self.views[j['source_id']]=dict(request_id=j['id'],operation_sequence=j['sequence'],
                image='confirmation.jpg',camera_view_saved=True,visual_confirmed=False,
                view_audio_bearing_deg=anchor['audio_bearing_deg'],view_monotonic=now)
        j.update(status='RELATIVE_TARGET_CONFIRMED',navigation_ready=True)

    def maintain(self):
        super().maintain()
        with self.lock:
            if self.active is None and self.pending is not None and (self.worker is None or not self.worker.is_alive()):
                rid=self.pending;self.pending=None;self._start(self.jobs[rid])

    def schedule_discovery(self):
        with self.lock:
            if (self.closed or self.latched_block or self.input_hold or self.discovery_hold or self.alignment is None or
                self.active is not None or self.pending is not None or self.clock()<self.next_discovery or
                (self.worker is not None and self.worker.is_alive())):return None
            self.discovery_sequence+=1
            return self.trigger('AUTODISCOVERY','auto_'+self.catalogue.epoch+'_'+str(self.discovery_sequence),'discover')

    def request_cancel(self):
        with self.lock:
            if self.pending is not None:
                self.jobs[self.pending].update(status='CANCELLED_BEFORE_ACTION',action_started=False)
                self.pending=None
            return super().request_cancel()

    def close(self,timeout=20):
        self.request_cancel()
        return super().close(timeout)
