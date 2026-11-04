"""No total-time acceptance threshold; identity, evidence and cleanup stay required."""
import copy, threading
from av_trigger_service_V11 import Coordinator as Previous
from av_trigger_service_V6 import ButtonEdges, make_handler, atomic_json
from av_audio_rejection_V2 import healthy_inconclusive
from av_direction_catalogue_V2 import DirectionCatalogue
from av_phase_rejection_V1 import healthy_phase_rejection

class Coordinator(Previous):
    def __init__(self, *args, budget=None, **kwargs):
        if budget is not None: raise ValueError('Total duration limit must be disabled')
        super().__init__(*args,budget=10.,**kwargs)
        self.budget=None
        self._save()

    def _state(self):
        state=super()._state()
        state.update(version=18, initialization_evidence_contract='FULL_TURN_STATIC_AXIS_V1',
                     initialization_budget_seconds=None,selection_budget_seconds=None,
                     operation_time_policy='NO_TOTAL_DURATION_LIMIT')
        return state

    def _start(self,j):
        """Called only when the prior hardware worker has returned."""
        if self.closed:j.update(status='REJECTED_CLOSED')
        elif self.latched_block:j.update(status='BLOCKED',reason=self.latched_block)
        else:
            try:ready=self.admission()
            except Exception as exc:ready=dict(ready=False,reason='ADMISSION_EXCEPTION',error=repr(exc))
            j['admission']=ready
            if not ready.get('ready'):
                j.update(status='BLOCKED',reason=ready.get('reason','NOT_READY'))
                if ready.get('input_hold') is True:return self._complete_input_hold(j)
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
            self.sequence+=1;begin=self.clock();budget=None
            j=dict(id=request_id,sequence=self.sequence,source=source,boot_id=self.boot_id,
                action=action,source_id=source_id,trigger_monotonic=begin,
                deadline_monotonic=None,budget_seconds=budget,status='ADMISSION',
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
        def cancelled():return self.cancel.is_set()
        operation=dict(action=j['action'],source_id=j['source_id'],catalogue_epoch=self.catalogue.epoch,trigger_monotonic=j['trigger_monotonic'])
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
            else:
                try:self._accept(j,result,now)
                except Exception as exc:j.update(status='NOT_VERIFIED',error=repr(exc))
            accepted=j['status'] in ('RELATIVE_TARGET_CONFIRMED','DISCOVERY_RECORDED','DISCOVERY_REJECTED')
            if not accepted:
                self.catalogue,self.alignment,self.initialization_anchor,self.views=before
            backoff=self.discovery_interval
            if j['action']=='discover' and j['status'] not in ('CANCELLED','DISCOVERY_RECORDED'):
                soft=(j['status']=='DISCOVERY_REJECTED' and j['cleanup_verified'] and
                      (healthy_phase_rejection(result or {},self.boot_id,j['trigger_monotonic'],now,self.initialization_anchor) or
                       healthy_inconclusive((result or {}).get('source_event',{}),self.boot_id,now)))
                if soft:
                    self.inconclusive_windows+=1;self.discovery_failures=0
                    backoff=max(self.discovery_interval,5.0)
                    self.last_rejection=dict(request_id=j['id'],classification='HEALTHY_INCONCLUSIVE_SOUND',
                        reasons=copy.deepcopy(j.get('healthy_phase_reasons') or result['source_event']['audio_evidence']['reasons']),
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
            j.update(published_monotonic=published,total_seconds=published-j['trigger_monotonic'],deadline_met=None)
            j['within_budget']=None
            j['within_5_seconds']=j['navigation_ready'] and j['total_seconds']<=5
            self._save()

    def maintain(self):
        with self.lock:
            if self.active is None and self.pending is not None and (self.worker is None or not self.worker.is_alive()):
                rid=self.pending;self.pending=None;self._start(self.jobs[rid])

    def _reset_catalogue(self):
        super()._reset_catalogue()
        self.catalogue=DirectionCatalogue(self.boot_id,self.catalogue.epoch)

    def _ingest_result(self,job,result,now):
        source=result['source_event']
        receipt=result.get('source_admission')
        if source.get('audio_valid') is True:
            if (not isinstance(receipt,dict) or
                    not job['trigger_monotonic'] <= receipt.get('checked_monotonic',-1) <= result['completed_monotonic'] <= now or
                    source['audio_evidence']['capture_envelope']['begin_monotonic'] < job['trigger_monotonic']):
                raise ValueError('Current operation audio admission required')
        selection=result.get('selection_admission') if job['action']=='select' else None
        if job['action']=='select' and not isinstance(selection,dict):
            raise ValueError('Current selection admission required')
        return self.catalogue.ingest(source,now,self.catalogue.epoch,admission=receipt,selection=selection)

    def _accept(self, job, result, now):
        if job['action']=='discover' and isinstance(result,dict) and result.get('source_event') is not None:
            if result.get('local_cleanup_verified') is not True or result.get('status')!='AUDIO_OBSERVATION_CAPTURED':
                raise ValueError('Cleaned current audio observation required')
            verdict=self._ingest_result(job,result,now)
            job['catalogue_verdict']=verdict
            job['status']='DISCOVERY_RECORDED' if verdict['status'] in ('CREATED','UPDATED') else 'DISCOVERY_REJECTED'
            if job['status']=='DISCOVERY_RECORDED' and result.get('navigation_ready') and result.get('follow_status')=='AUDIO_SOURCE_VIEW_READY':
                sid=verdict['source_id']
                self.views[sid]=dict(request_id=job['id'],operation_sequence=job['sequence'],image='confirmation.jpg',
                    camera_view_saved=True,visual_confirmed=False,view_audio_bearing_deg=result['source_event']['audio_bearing_deg'],view_monotonic=now)
                job['navigation_ready']=True
            return
        if job['action']=='select' and isinstance(result,dict) and result.get('navigation_ready') is True:
            if (result.get('local_cleanup_verified') is not True or result.get('status')!='AUDIO_SOURCE_VIEW_READY' or
                    result.get('calibration_status')!='RELATIVE_SINGLE_SOURCE_ALIGNED' or
                    result.get('selected_before_move',{}).get('status')!='SELECTED_DIRECTION_SUPPORTED' or
                    result.get('source_anchor',{}).get('source_id')!=job['source_id']):
                raise ValueError('Cleaned selected direction support required')
            verdict=self._ingest_result(job,result,now)
            if verdict.get('source_id')!=job['source_id'] or verdict['status']!='UPDATED':
                raise ValueError('Fresh selection did not update selected track')
            self.views[job['source_id']]=dict(request_id=job['id'],operation_sequence=job['sequence'],image='confirmation.jpg',
                camera_view_saved=True,visual_confirmed=False,view_audio_bearing_deg=result['source_event']['audio_bearing_deg'],view_monotonic=now)
            job.update(status='RELATIVE_TARGET_CONFIRMED',navigation_ready=True)
            return
        from av_init_proof_V5 import validate
        if job['action']=='discover' and healthy_phase_rejection(result or {},self.boot_id,job['trigger_monotonic'],now,self.initialization_anchor):
            job.update(status='DISCOVERY_REJECTED',navigation_ready=False,healthy_phase_reasons=copy.deepcopy(result['phase']['reasons']),catalogue_verdict=dict(status='HEALTHY_PHASE_REJECTION_NO_SOURCE',bearing_published=False))
            return
        if job['action']=='initialize' and isinstance(result,dict) and result.get('navigation_ready') is True:
            proof=validate(result,self.boot_id,job['trigger_monotonic'],now)
            super()._accept(job,result,now)
            job['initialization_evidence_check']=proof
            return
        if isinstance(result,dict) and result.get('status') in ('INITIALIZATION_CLEANUP_FAILED','FOLLOW_CLEANUP_FAILED'):
            job.update(status='INITIALIZATION_REJECTED' if job['action']=='initialize' else 'FOLLOW_REJECTED',navigation_ready=False,reason=result.get('error',result['status']))
            return
        return super()._accept(job,result,now)

    def _complete_input_hold(self, j):
        self.input_hold=dict(reason=j['reason'],evidence=j['admission'].get('evidence'))
        j['action_started']=False
        try:q=self.quiet()
        except Exception as exc:q=dict(cleanup_verified=False,error=repr(exc))
        j.update(cleanup=q,cleanup_verified=q.get('cleanup_verified') is True,finished_monotonic=self.clock())
        if not j['cleanup_verified']:
            j['status']='CLEANUP_UNCONFIRMED';self.latched_block='CLEANUP_UNCONFIRMED'
        self._save();published=self.clock()
        j.update(published_monotonic=published,total_seconds=published-j['trigger_monotonic'],deadline_met=None)
        self._save();return copy.deepcopy(j)

    def publish_phase(self, value):
        with self.lock:
            self.display_phase={k:copy.deepcopy(v) for k,v in value.items() if k in ('phase','triggered','visual_step')}
            self.display_phase.update(request_id=self.active,boot_id=self.boot_id,published_monotonic=self.clock())
