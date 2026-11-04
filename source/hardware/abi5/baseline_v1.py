"""Bit-exact reference for bounded FIR/lag accumulator; no board I/O."""
import itertools
import numpy as np
from direction import fit_direction

CHANNELS=[1,5,7,8]
PAIRS=list(itertools.combinations(CHANNELS,2))
LAG=16
WINDOW=32768
CENTER=19  # first valid FIR at index3, full +/-16 ring at input35
SHIFT=4

def features(samples, window=WINDOW):
    x=np.asarray(samples,dtype=np.int64)
    if x.ndim!=2 or x.shape[1]!=4 or len(x)<window+35:
        raise ValueError('Need four channels and window+35 input frames')
    if np.any(x < -8388608) or np.any(x>8388607): raise ValueError('Not int24')
    a=x>>SHIFT
    pad=np.pad(a,((3,0),(0,0)))
    y=pad[3:]+pad[2:-1]-pad[1:-2]-pad[:-3]
    sums=[]
    for i,j in itertools.combinations(range(4),2):
        base=y[CENTER:CENTER+window,i]
        sums.extend(int(np.dot(base,y[CENTER+k:CENTER+window+k,j])) for k in range(-LAG,LAG+1))
    sums.extend(int(np.dot(y[CENTER:CENTER+window,i],y[CENTER:CENTER+window,i])) for i in range(4))
    return np.array(sums,dtype=np.int64)

def estimate(sums, positions):
    a=np.asarray(sums,dtype=np.int64)
    if a.shape!=(202,): raise ValueError('202 signed64 features required')
    energy=a[198:].astype(float)
    if np.any(energy<=0): return {'valid':False,'reasons':['zero_filtered_energy']}
    lags=[]; strengths=[]; peaks=[]; contrast=[]; tone_residuals=[]
    reasons=[]
    for p,(i,j) in enumerate(itertools.combinations(range(4),2)):
        r=a[p*33:(p+1)*33].astype(float)
        # A single sinusoid obeys r[k-1]+r[k+1]=2*cos(omega)*r[k].
        # Test that recurrence on signed correlation; a near-exact sinusoid
        # is insufficient broadband evidence, even if one peak looks unique.
        mid_sequence=r[1:-1]; neighbours=r[:-2]+r[2:]
        den_sequence=float(mid_sequence@mid_sequence)
        factor=float(mid_sequence@neighbours)/den_sequence if den_sequence else 0.
        residual=neighbours-factor*mid_sequence
        tone_residuals.append(float(np.linalg.norm(residual)/max(np.linalg.norm(r),1.)))
        rabs=np.abs(r); pk=int(np.argmax(rabs)); peaks.append(pk-LAG)
        if pk in (0,32):
            return {'valid':False,'reasons':['search_boundary'],'pair':p}
        left,mid,right=rabs[pk-1:pk+2]
        den=left-2*mid+right
        frac=float(np.clip(.5*(left-right)/den,-.5,.5)) if den else 0.
        lags.append(pk-LAG+frac)
        strengths.append(float(min(1,mid/np.sqrt(energy[i]*energy[j]))))
        side=rabs.copy(); side[max(0,pk-4):min(33,pk+5)]=0
        contrast.append(float(1-side.max()/mid))
    fit=fit_direction(positions,PAIRS,lags,np.maximum(strengths,1e-6))
    # Predeclared engineering rejection gates, not calibrated probabilities.
    if min(strengths)<.2: reasons.append('weak_pair_correlation')
    if min(contrast)<.08: reasons.append('ambiguous_peak')
    if max(tone_residuals)<.001: reasons.append('near_single_tone')
    if not fit['all_measured_delays_within_physical_limits']: reasons.append('nonphysical_delay')
    if fit['weighted_rms_residual_samples']>.5: reasons.append('inconsistent_geometry')
    if max(abs(c['closure_error_samples']) for c in fit['cycle_closures'])>1.0: reasons.append('inconsistent_delay_cycle')
    if not .12<fit['projected_norm']<1.1: reasons.append('projection_ill_defined')
    return {'valid':not reasons,'reasons':reasons,'integer_peaks':peaks,'lags':lags,
            'strengths':strengths,'peak_contrast':contrast,'tone_shape_residuals':tone_residuals,'fit':fit,
            'physical_bearing_accuracy':'NOT_VALIDATED'}
