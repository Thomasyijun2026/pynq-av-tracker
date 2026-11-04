"""Reference-relative phase V4. Shared spectral products and cached geometry; same frozen policy.

A fixed known source supplies frequency-dependent channel phase. The result is
only delta of the horizontal propagation vector. An absolute direction requires
independent geometry/calibration; spatial invariance of transfer is unverified.
"""
from itertools import combinations
import numpy as np
import audio_phase_guard_V1 as p


def products(x):
    z=p._stft(x)
    power=abs(z)**2
    cross=np.stack([np.conj(z[:,:,i])*z[:,:,j] for i,j in p.PAIRS],axis=2)
    return cross,power


def from_products(cross,power):
    power=np.mean(power,axis=0);c=np.mean(cross,axis=0)
    q=np.stack([abs(c[:,k])**2/np.maximum(power[:,i]*power[:,j],1e-100) for k,(i,j) in enumerate(p.PAIRS)],axis=1)
    return c,np.clip(q,0,1),power


def statistics(x):return from_products(*products(x))


_xx,_yy=np.meshgrid(np.arange(-2.2,2.201,.04),np.arange(-2.2,2.201,.04))
GRID=np.column_stack((_xx.ravel(),_yy.ravel()));GRID=GRID[np.linalg.norm(GRID,axis=1)<=2.2]
_i=(GRID@p.MATRIX.T)*p.OVERSAMPLE
LO=np.floor(_i).astype(np.int64);FRACTION=_i-LO
JAC=2*np.pi*p.FREQ[:,None,None]*p.MATRIX[None,:,:]/p.FS
STARTS=np.arange(0,len(p.FREQ),8)


def coarse_score(corr,den):
    total=np.zeros(len(GRID))
    for k in range(6):total+=(1-FRACTION[:,k])*corr[LO[:,k]%len(corr),k]+FRACTION[:,k]*corr[(LO[:,k]+1)%len(corr),k]
    return total*(p.NFFT*p.OVERSAMPLE)/(2*den)


POLICY=dict(min_score=.8,min_effective_bins=16,min_separation_z=2.58,
            max_model_radius=.15,max_subwindow_diameter=.1,quorum=3,
            spectral_cluster_bins=8,minimum_frequency_span_hz=200,
            interpretation='MODEL_ONLY_NOT_PHYSICAL_COVERAGE')


def fit(reference,current,noise):
    cr,qr,_=reference;c,q,power=current
    gain=np.maximum(1-noise/np.maximum(power,1e-100),0)
    w=q*qr*np.stack([np.minimum(gain[:,i],gain[:,j]) for i,j in p.PAIRS],axis=1)*p.BAND[:,None]
    den=float(w.sum())
    if den<=0:return dict(status='REJECTED',reasons=['no_support'],delta_vector=None)
    unit=c*np.conj(cr)/np.maximum(abs(c)*abs(cr),1e-100)
    corr=np.fft.irfft(unit*w,n=p.NFFT*p.OVERSAMPLE,axis=0)
    grid=GRID
    scores=coarse_score(corr,den);best=grid[int(np.argmax(scores))]
    xx,yy=np.meshgrid(np.arange(best[0]-.04,best[0]+.0401,.002),np.arange(best[1]-.04,best[1]+.0401,.002))
    fine=np.column_stack((xx.ravel(),yy.ravel()));fs=p._score(corr,fine,den);u=fine[int(np.argmax(fs))]
    phase=2*np.pi*p.FREQ[:,None]*(p.MATRIX@u)[None,:]/p.FS
    residual=np.angle(unit*np.exp(1j*phase));score=float(np.sum(w*np.cos(residual))/den)
    away=np.linalg.norm(grid-u,axis=1)>=.2;indices=np.flatnonzero(away);alternative=grid[indices[int(np.argmax(scores[away]))]]
    other_phase=2*np.pi*p.FREQ[:,None]*(p.MATRIX@alternative)[None,:]/p.FS
    frequency_gain=np.sum(w*(np.cos(residual)-np.real(unit*np.exp(1j*other_phase))),axis=1)/den
    clusters=np.add.reduceat(frequency_gain,STARTS)
    nonempty=np.add.reduceat(w.sum(axis=1),STARTS)>0
    clusters=clusters[nonempty];count=len(clusters)
    se=float(np.std(clusters,ddof=1)*np.sqrt(count)) if count>1 else 1e9
    gain_score=float(frequency_gain.sum());z=gain_score/max(se,1e-12)
    jac=JAC
    information=np.einsum('fk,fki,fkj->ij',w,jac,jac)
    if np.linalg.matrix_rank(information)<2:
        return dict(status='REJECTED',reasons=['insufficient_array_rank'],delta_vector=None)
    inv=np.linalg.pinv(information)
    scores_frequency=np.einsum('fk,fki,fk->fi',w,jac,np.sin(residual))
    grouped=np.add.reduceat(scores_frequency,STARTS,axis=0)[nonempty]
    meat=grouped.T@grouped
    covariance=inv@meat@inv*count/max(count-1,1)
    radius=float(np.sqrt(5.991*max(np.linalg.eigvalsh(covariance))))
    spectral=w.sum(axis=1);effective=float(den**2/max(spectral@spectral,1e-100))
    mean=float(spectral@p.FREQ/den);spread=float(np.sqrt(spectral@((p.FREQ-mean)**2)/den))
    reasons=[]
    if score<POLICY['min_score']:reasons.append('phase_model_mismatch')
    if z<POLICY['min_separation_z']:reasons.append('alternative_not_separated')
    if radius>POLICY['max_model_radius']:reasons.append('delay_vector_uncertainty')
    if effective<POLICY['min_effective_bins'] or spread<POLICY['minimum_frequency_span_hz']:reasons.append('insufficient_spectral_support')
    return dict(status='VALID_RELATIVE_VECTOR' if not reasons else 'REJECTED',reasons=reasons,delta_vector=u.tolist(),score=score,
                alternative=alternative.tolist(),score_gain=gain_score,separation_z=z,model_radius=radius,
                effective_bins=effective,frequency_span=spread,global_bearing=None,servo_command=None)


def _estimate(reference,x,noise):
    if x.shape!=(32768,4) or not np.isfinite(x).all() or any(np.ptp(x[:,k])==0 for k in range(4)):
        return dict(status='REJECTED',reasons=['invalid_or_constant_pcm'],delta_vector=None)
    cross,power=products(x)
    full=fit(reference,from_products(cross,power),noise)
    # Only STFT blocks completely contained in each 8192-sample window.
    # Starts are multiples of HOP=512; preserve the V3 13-frame subwindows.
    rows=[fit(reference,from_products(cross[k:k+13],power[k:k+13]),noise) for k in (0,16,32,48)]
    eligible=[k for k,r in enumerate(rows) if r['status']=='VALID_RELATIVE_VECTOR'];groups=[]
    for n in [4,3]:
        for group in combinations(eligible,n):
            vectors=np.array([rows[k]['delta_vector'] for k in group])
            diameter=max(np.linalg.norm(a-b) for a,b in combinations(vectors,2))
            if diameter<=POLICY['max_subwindow_diameter']:groups.append((n,-diameter,group))
    reasons=list(full['reasons'])
    if not groups:reasons.append('subwindow_vector_unstable')
    return dict(status='VALID_RELATIVE_VECTOR' if not reasons else 'REJECTED',reasons=reasons,
                delta_vector=full['delta_vector'] if not reasons else None,full=full,windows=rows,
                chosen_windows=list(max(groups)[2]) if groups else [],policy=POLICY,
                global_bearing=None,servo_command=None,spatial_transfer_invariance='NOT_VALIDATED')



def estimate(reference,x,noise):
    """Reject invalid transport/data before running the frozen phase fit."""
    failure=lambda reason:dict(status='REJECTED',reasons=[reason],delta_vector=None,global_bearing=None,servo_command=None)
    try:
        x=np.asarray(x,dtype=float); noise=np.asarray(noise,dtype=float)
        if len(reference)!=3:return failure('invalid_reference')
        c,q,power=(np.asarray(a) for a in reference)
        if c.shape!=(1025,6) or q.shape!=(1025,6) or power.shape!=(1025,4):return failure('invalid_reference')
        if not all(np.isfinite(a).all() for a in (c,q,power)) or (q<0).any() or (q>1).any() or (power<0).any():return failure('invalid_reference')
        if noise.shape!=(1025,4) or not np.isfinite(noise).all() or (noise<0).any():return failure('invalid_noise_psd')
        if x.shape!=(32768,4) or not np.isfinite(x).all():return failure('invalid_pcm')
        if any(np.ptp(x[:,k])==0 for k in range(4)):return failure('constant_channel')
        if any(np.array_equal(x[:,i],x[:,j]) for i,j in p.PAIRS):return failure('duplicate_channels')
        if np.any((x<=-8388608)|(x>=8388607)):return failure('exact_full_scale')
        if not np.any(q) or not np.any(power):return failure('empty_reference')
    except (TypeError,ValueError,OverflowError):return failure('invalid_data')
    return _estimate((c,q,power),x,noise)
