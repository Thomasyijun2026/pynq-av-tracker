"""PS reference for source-independent, noise-weighted phase localization.

Frozen candidate V1. Raw synchronized PCM comes from PL. No source WAV,
photograph, expected bearing, or servo angle is an input to this estimator.
This module does not access hardware and does not replace the ABI5 driver.
"""
from itertools import combinations
import numpy as np

FS=31250
NFFT=2048
HOP=512
OVERSAMPLE=32
SLOTS=(0,4,6,7)
XY=np.array(((2,50),(2,10),(66,10),(98,10)),dtype=float)/1000
PAIRS=tuple(combinations(range(4),2))
MATRIX=np.array([-(XY[j]-XY[i])*FS/343 for i,j in PAIRS])
FREQ=np.fft.rfftfreq(NFFT,1/FS)
BAND=(FREQ>=300)&(FREQ<=7000)
POLICY=dict(version=1,fs=FS,nfft=NFFT,hop=HOP,oversample=OVERSAMPLE,
            band_hz=[300,7000],noise_percentile=20,quorum=3,max_angle_diameter=10.,
            minimum_projection=.12,maximum_projection=1.1,minimum_score=.5,
            minimum_effective_bins=16,minimum_frequency_std_hz=200.,minimum_peak_separation_score=.02)


def _validate(raw):
    x=np.asarray(raw)
    if x.ndim!=2 or x.shape[1]!=4 or len(x)<8192 or not np.isfinite(x).all():
        raise ValueError('Expected finite synchronized [frames,4] PCM')
    return x.astype(np.float64)


def _stft(raw):
    x=_validate(raw)
    blocks=np.stack([x[s:s+NFFT] for s in range(0,len(x)-NFFT+1,HOP)])
    return np.fft.rfft((blocks-blocks.mean(axis=1,keepdims=True))*np.hanning(NFFT)[None,:,None],axis=1)


def noise_reference(raw):
    """Frozen temporal 20th-percentile PSD of an explicitly preceding capture."""
    return np.percentile(abs(_stft(raw))**2,20,axis=0)


def _score(corr, vectors, denominator):
    lags=vectors@MATRIX.T
    indices=lags*OVERSAMPLE
    lo=np.floor(indices).astype(np.int64); fraction=indices-lo
    total=np.zeros(len(vectors))
    for k in range(6):
        total+=(1-fraction[:,k])*corr[lo[:,k]%len(corr),k]+fraction[:,k]*corr[(lo[:,k]+1)%len(corr),k]
    return total*(NFFT*OVERSAMPLE)/(2*denominator)


def _grid(angles,radii):
    aa,rr=np.meshgrid(np.radians(angles),radii,indexing='ij')
    return np.column_stack(((np.sin(aa)*rr).ravel(),(np.cos(aa)*rr).ravel()))


COARSE=_grid(np.arange(-180,180,2.),np.arange(0,1.1001,.02))


def window_estimate(raw,noise):
    x=_validate(raw);n=np.asarray(noise,dtype=float)
    if n.shape!=(NFFT//2+1,4) or not np.isfinite(n).all() or (n<0).any():
        raise ValueError('Invalid preceding noise PSD')
    if any(np.ptp(x[:,c])==0 for c in range(4)):
        return dict(status='REJECTED',reasons=['constant_channel'],bearing_deg=None)
    if any(np.array_equal(x[:,i],x[:,j]) for i,j in PAIRS):
        return dict(status='REJECTED',reasons=['duplicate_channels'],bearing_deg=None)
    z=_stft(x);p=np.mean(abs(z)**2,axis=0)
    c=np.stack([np.mean(np.conj(z[:,:,i])*z[:,:,j],axis=0) for i,j in PAIRS],axis=1)
    coherence=np.clip(np.stack([abs(c[:,k])**2/np.maximum(p[:,i]*p[:,j],1e-100)
                               for k,(i,j) in enumerate(PAIRS)],axis=1),0,1)
    signal_fraction=np.maximum(1-n/np.maximum(p,1e-100),0)
    weights=np.stack([np.minimum(signal_fraction[:,i],signal_fraction[:,j]) for i,j in PAIRS],axis=1)*coherence*BAND[:,None]
    w=weights.sum(axis=1);den=float(w.sum());reasons=[]
    if den<=0:return dict(status='REJECTED',reasons=['no_spectral_support'],bearing_deg=None)
    effective=float(den**2/np.maximum(w@w,1e-100));mean=float(w@FREQ/den)
    spread=float(np.sqrt(w@((FREQ-mean)**2)/den))
    if effective<POLICY['minimum_effective_bins']:reasons.append('narrow_spectral_support')
    if spread<POLICY['minimum_frequency_std_hz']:reasons.append('insufficient_frequency_span')
    unit=c/np.maximum(abs(c),1e-100)
    corr=np.fft.irfft(unit*weights,n=NFFT*OVERSAMPLE,axis=0)
    scores=_score(corr,COARSE,den);best=int(np.argmax(scores));u=COARSE[best]
    angle=float(np.degrees(np.arctan2(u[0],u[1])));radius=float(np.linalg.norm(u))
    fine=_grid(np.arange(angle-2,angle+2.01,.1),np.arange(max(0,radius-.025),min(1.1,radius+.025)+.0001,.002))
    fscores=_score(corr,fine,den);u=fine[int(np.argmax(fscores))]
    angle=float((np.degrees(np.arctan2(u[0],u[1]))+180)%360-180);radius=float(np.linalg.norm(u))
    score=float(fscores.max());aa=np.degrees(np.arctan2(COARSE[:,0],COARSE[:,1]));away=abs((aa-angle+180)%360-180)>=20
    second=float(np.max(scores[away]));gap=score-second
    if not POLICY['minimum_projection']<=radius<=POLICY['maximum_projection']:reasons.append('projection_out_of_range')
    if score<POLICY['minimum_score']:reasons.append('inconsistent_spectral_phase')
    if gap<POLICY['minimum_peak_separation_score']:reasons.append('ambiguous_phase_direction')
    return dict(status='VALID' if not reasons else 'REJECTED',bearing_deg=angle if not reasons else None,
                diagnostic_bearing_deg=angle,reasons=reasons,projected_norm=radius,phase_score=score,
                score_separation_20deg=gap,effective_bins=effective,frequency_std_hz=spread)


def estimate(raw,noise):
    x=_validate(raw)
    if len(x)!=32768:raise ValueError('Candidate uses exactly 32768 synchronized samples')
    full=window_estimate(x,noise)
    windows=[window_estimate(x[k:k+8192],noise) for k in range(0,32768,8192)]
    eligible=[k for k,row in enumerate(windows) if row['status']=='VALID']
    groups=[]
    for count in range(4,2,-1):
        for group in combinations(eligible,count):
            values=[windows[k]['bearing_deg'] for k in group]
            diameter=max(abs((a-b+180)%360-180) for a,b in combinations(values,2))
            if diameter<=POLICY['max_angle_diameter']:groups.append((count,-diameter,group))
    reasons=[];bearing=None;chosen=[]
    if full['status']!='VALID':reasons+=['full_'+r for r in full['reasons']]
    if not groups:reasons.append('fractional_direction_unstable')
    elif full['status']=='VALID':
        chosen=list(max(groups)[2]);base=full['bearing_deg']
        deltas=np.array([(windows[k]['bearing_deg']-base+180)%360-180 for k in chosen])
        if max(abs(deltas))>POLICY['max_angle_diameter']:reasons.append('full_subwindow_disagreement')
        else:bearing=float((base+np.median(deltas)+180)%360-180)
    return dict(status='VALID' if not reasons else 'REJECTED',bearing_deg=bearing if not reasons else None,
                reasons=reasons,chosen_windows=chosen,full=full,windows=windows,policy=POLICY,
                decision_owner='PS_REFERENCE_CANDIDATE_NOT_DEPLOYED')
