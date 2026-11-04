"""Independent running-sum reference for the fixed 16-tap PL FIR candidate.

Input is int24 PCM; shift right four first, FIR [1]*8+[-1]*8, then
arithmetic right shift two. Zero initial history, original 19-frame center.
Physical-domain peaks, window and quality gates retain their prior definitions.
"""
import itertools
import numpy as np
from audio_peak_reference_V4 import select_peaks,peak_guard,WINDOW,CENTER

M=8
FIR_ID=0x08040210  # half-length 8 / input shift 4 / result shift 2 / 16 taps

def filtered(samples):
    x=np.asarray(samples)
    if x.ndim!=2 or x.shape[1]!=4 or x.dtype.kind not in 'iu':
        raise ValueError('Four integer int24 channels required')
    if np.any(x < -8388608) or np.any(x > 8388607):raise ValueError('Not int24')
    a=np.pad(x.astype(np.int64)>>4,((15,0),(0,0)))
    prefix=np.vstack((np.zeros((1,4),np.int64),np.cumsum(a,axis=0,dtype=np.int64)))
    return (prefix[16:]-2*prefix[8:-8]+prefix[:-16])>>2

def features(samples,window=WINDOW):
    if not isinstance(window,int) or not 4<=window<=WINDOW or window%4:
        raise ValueError('Window must divide by four within 4..32768')
    y=filtered(samples)
    if len(y)<window+35:raise ValueError('Need window+35 frames')
    sub=np.zeros((4,202),np.int64);size=window//4
    for q in range(4):
        start=CENTER+q*size
        for p,(i,j) in enumerate(itertools.combinations(range(4),2)):
            for lag in range(-16,17):
                sub[q,33*p+lag+16]=np.dot(y[start:start+size,i],y[start+lag:start+lag+size,j])
        for c in range(4):sub[q,198+c]=np.dot(y[start:start+size,c],y[start:start+size,c])
    sums=sub.sum(axis=0)
    full=select_peaks(sums[:198].reshape(6,33)).tolist()
    small=select_peaks(sub[:,:198].reshape(4,6,33)).tolist()
    return dict(sums_i64=sums.tolist(),integer_peaks=full,subwindow_peaks=small,
                guard=peak_guard(full,small))
