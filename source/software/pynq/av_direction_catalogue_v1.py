"""Up to three board-owned acoustic direction candidates, not object identities.

The current PL estimator emits one dominant bearing per capture. This catalogue
can retain different observed directions over time; it does not separate three
simultaneous sources. No servo position or visual confirmation is fabricated.
"""
import copy
import math
import re
from av_audio_source_V1 import finite,validate_source

JOIN_DEGREES=8.0
RETAIN_SECONDS=20.0
CONFIRMATIONS=2

def angular_distance(a,b):
    return abs((finite(a,'angle')-finite(b,'angle')+180.)%360.-180.)

def circular_mean(values):
    x=sum(math.cos(math.radians(v)) for v in values)
    y=sum(math.sin(math.radians(v)) for v in values)
    if math.hypot(x,y)<1e-10:raise ValueError('Ambiguous circular mean')
    return (math.degrees(math.atan2(y,x))+180.)%360.-180.

def candidate_admission(candidate,boot_id,epoch,now):
    now=finite(now,'selection time')
    if (candidate.get('source_kind')!='DIRECTION_CANDIDATE' or
        candidate.get('boot_id')!=boot_id or candidate.get('catalogue_epoch')!=epoch or
        candidate.get('coordinate_frame')!='ARRAY_FIXED'):
        raise ValueError('Same installation ARRAY_FIXED candidate required')
    if not re.fullmatch(re.escape(epoch)+r'_d[0-9]{6,}',candidate.get('source_id','')):
        raise ValueError('Catalogue source identity required')
    if candidate.get('direction_status')!='CONFIRMED' or candidate.get('observations',0)<CONFIRMATIONS:
        raise ValueError('Direction candidate not confirmed')
    end=finite(candidate.get('last_capture_end'),'candidate last capture')
    published=finite(candidate.get('last_published_monotonic'),'candidate publication')
    bearing=finite(candidate.get('direction_center_deg'),'candidate direction')
    if not -180<=bearing<=180 or not 0<=end<=published<=now or now-end>RETAIN_SECONDS:
        raise ValueError('Direction candidate expired or timing invalid')
    if candidate.get('semantic_identity') is not None or candidate.get('simultaneous_source_separation') is not False:
        raise ValueError('Unsupported physical source identity')
    return dict(status='CANDIDATE_ACCEPTED',source_id=candidate['source_id'],
                direction_center_deg=bearing,age_seconds=now-end,catalogue_epoch=epoch)

def selected_audio_admission(candidate,fresh,boot_id,epoch,now):
    selected=candidate_admission(candidate,boot_id,epoch,now)
    audio=validate_source(fresh,boot_id,now)
    if fresh['source_id']!=selected['source_id']:raise ValueError('Selected source ID changed')
    delta=angular_distance(selected['direction_center_deg'],audio['bearing_deg'])
    if delta>JOIN_DEGREES:raise ValueError('Fresh audio is outside selected direction')
    return dict(status='SELECTED_DIRECTION_SUPPORTED',source_id=selected['source_id'],
                angular_distance_deg=delta,limit_deg=JOIN_DEGREES,audio=audio,
                physical_identity_verified=False)

class DirectionCatalogue:
    def __init__(self,boot_id,epoch):
        if not isinstance(boot_id,str) or not boot_id:raise ValueError('Boot ID required')
        if not isinstance(epoch,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',epoch):
            raise ValueError('Installation epoch required')
        self.boot_id=boot_id;self.epoch=epoch;self.tracks=[];self.counter=0
        self.last_now=None;self.last_capture_end=None;self.last_published=None
        self.audit=[];self.audit_sequence=0

    def _time(self,now):
        now=finite(now,'catalogue time')
        if now<0 or (self.last_now is not None and now<self.last_now):raise ValueError('Catalogue clock moved backwards')
        self.last_now=now
        expired=[x['source_id'] for x in self.tracks if now-x['last_capture_end']>RETAIN_SECONDS]
        self.tracks=[x for x in self.tracks if x['source_id'] not in expired]
        return now,expired

    def _record(self,status,now,**fields):
        self.audit_sequence+=1
        result=dict(status=status,audit_sequence=self.audit_sequence,monotonic=now,**fields)
        self.audit.append(copy.deepcopy(result));self.audit=self.audit[-64:]
        return result

    def ingest(self,source,now,epoch):
        now,expired=self._time(now)
        if epoch!=self.epoch:return self._record('REJECTED_EPOCH',now,expired=expired)
        try:admitted=validate_source(source,self.boot_id,now)
        except (ValueError,TypeError,AttributeError) as exc:
            return self._record('REJECTED_AUDIO',now,error=str(exc),expired=expired)
        evidence=source['audio_evidence'];envelope=evidence['capture_envelope']
        begin,end=envelope['begin_monotonic'],envelope['end_monotonic']
        published=evidence['published_monotonic']
        if (self.last_capture_end is not None and begin<self.last_capture_end) or (
            self.last_published is not None and published<=self.last_published):
            return self._record('REJECTED_OVERLAP_OR_REPLAY',now,expired=expired)
        self.last_capture_end=end;self.last_published=published
        bearing=admitted['bearing_deg']
        nearby=[x for x in self.tracks if angular_distance(x['direction_center_deg'],bearing)<=JOIN_DEGREES]
        if len(nearby)>1:return self._record('REJECTED_AMBIGUOUS_DIRECTION',now,expired=expired)
        if not nearby:
            if len(self.tracks)==3:return self._record('REJECTED_CAPACITY',now,expired=expired)
            self.counter+=1
            track=dict(source_id=self.epoch+'_d%06d'%self.counter,source_kind='DIRECTION_CANDIDATE',
                boot_id=self.boot_id,catalogue_epoch=self.epoch,coordinate_frame='ARRAY_FIXED',
                observations=0,bearings=[],semantic_identity=None,
                identity_scope='ACOUSTIC_DIRECTION_TRACK_NOT_PERSISTENT_OBJECT_ID',
                simultaneous_source_separation=False,first_capture_begin=begin)
            self.tracks.append(track)
        else:track=nearby[0]
        track['bearings']=(track['bearings']+[bearing])[-5:]
        track.update(observations=track['observations']+1,direction_center_deg=circular_mean(track['bearings']),
            last_capture_begin=begin,last_capture_end=end,last_published_monotonic=published,
            audio_bearing_deg=bearing,last_pcm_sha256=evidence['raw_sha256'],
            direction_status='CONFIRMED' if track['observations']+1>=CONFIRMATIONS else 'TENTATIVE')
        track['audio_event']=copy.deepcopy(source)
        track['audio_event']['source_id']=track['source_id']
        return self._record('UPDATED' if nearby else 'CREATED',now,source_id=track['source_id'],
            direction_status=track['direction_status'],expired=expired)

    def snapshot(self,now):
        now,expired=self._time(now)
        sources=[]
        for track in self.tracks:
            item={k:copy.deepcopy(v) for k,v in track.items() if k not in ('audio_event','bearings')}
            item.update(conservative_audio_age_seconds=now-track['last_capture_begin'],
                fresh_audio_required_before_motion=True,servo_target=None,image=None,
                visual_confirmed=False,physical_identity_verified=False)
            sources.append(item)
        return dict(boot_id=self.boot_id,catalogue_epoch=self.epoch,coordinate_frame='ARRAY_FIXED',
            sources=sources,max_sources=3,expired=expired,simultaneous_source_separation=False,
            physical_multi_source_validation=False,association_limit_deg=JOIN_DEGREES,
            retention_seconds=RETAIN_SECONDS,confirmations_required=CONFIRMATIONS)

    def select(self,source_id,now):
        self._time(now)
        candidate=next((x for x in self.tracks if x['source_id']==source_id),None)
        if candidate is None:raise ValueError('Unknown or expired direction')
        candidate_admission(candidate,self.boot_id,self.epoch,now)
        return copy.deepcopy(candidate)
