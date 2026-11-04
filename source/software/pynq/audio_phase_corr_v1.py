"""Exact lag-limited inverse transform by a cached chirp convolution.

The reference estimator needs a small interval of the 65536-point inverse
transform. This evaluates those same samples; frequency weights and scores
are unchanged. Zero entries outside the interval must never be queried.
"""
import numpy as np
import audio_phase_guard_V1 as p

N = p.NFFT * p.OVERSAMPLE
K = len(p.FREQ)
# Coarse radius 2.2 plus the entire fine-search square, including interpolation.
RADIUS = 2.2 + np.sqrt(2.) * .0401
LIMIT = int(np.ceil(np.max(np.linalg.norm(p.MATRIX, axis=1)) * RADIUS * p.OVERSAMPLE)) + 2
M = 2 * LIMIT + 1
SIZE = 1 << (K + M - 2).bit_length()
omega = 2 * np.pi / N
k = np.arange(K)
n = np.arange(M)
PRE = np.exp(1j * omega * (k*k/2 - k*LIMIT))
POST = np.exp(1j * omega * n*n/2)
t = np.arange(-(K-1), M)
KERNEL = np.fft.fft(np.exp(-.5j * omega * t*t), SIZE)


def inverse(spectrum):
    x = np.asarray(spectrum)
    if x.shape != (K, 6) or not np.isfinite(x).all():
        raise ValueError('finite_1025_by_6_spectrum_required')
    conv = np.fft.ifft(np.fft.fft(x * PRE[:, None], SIZE, axis=0) * KERNEL[:, None], axis=0)
    values = (2 * np.real(conv[K-1:K-1+M] * POST[:, None]) - np.real(x[0])[None, :]) / N
    result = np.zeros((N, 6))
    result[np.arange(-LIMIT, LIMIT+1) % N] = values
    return result
