"""Welch GCC-PHAT with explicit low-energy exclusion and 32x lag interpolation."""
import itertools
import numpy as np


def measure_pairs(samples, fs=31250, band=(150, 7000), nfft=2048, upsample=32):
    x = np.asarray(samples, dtype=float)
    if x.ndim != 2 or len(x) < nfft or not np.isfinite(x).all():
        raise ValueError('Finite samples-by-channels matrix of sufficient length required')
    if not 0 < band[0] < band[1] < fs / 2 or nfft % 2 or upsample < 1:
        raise ValueError('Invalid spectral parameters')
    starts = range(0, len(x) - nfft + 1, nfft // 2)
    segments = np.stack([x[i:i+nfft] for i in starts])
    segments -= segments.mean(axis=1, keepdims=True)
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(nfft) / nfft)
    spectra = np.fft.rfft(segments * window[None, :, None], axis=1)
    power = np.mean(np.abs(spectra)**2, axis=0)
    freq = np.fft.rfftfreq(nfft, 1 / fs)
    mask = (freq >= band[0]) & (freq <= band[1])
    result = []
    for i, j in itertools.combinations(range(x.shape[1]), 2):
        cross = np.mean(spectra[:, :, i].conj() * spectra[:, :, j], axis=0)
        coherence = np.clip(np.abs(cross)**2 / np.maximum(power[:, i] * power[:, j], 1e-30), 0, 1)
        energy_weight = np.sqrt(power[:, i] * power[:, j])
        # -60 dB per-channel power relative to that channel's in-band maximum.
        # Do not give numerical leakage in an unexcited band unit PHAT weight.
        usable = (mask & (power[:, i] > max(float(power[mask, i].max()), 1e-30) * 1e-6)
                  & (power[:, j] > max(float(power[mask, j].max()), 1e-30) * 1e-6)
                  & (np.abs(cross) > 1e-20))
        if int(usable.sum()) < 8 or float(energy_weight[mask].sum()) <= 0:
            raise ValueError('Insufficient excited frequency bins for delay estimation')
        phat = np.zeros_like(cross)
        phat[usable] = cross[usable] / np.abs(cross[usable])
        corr = np.fft.fftshift(np.fft.irfft(phat, n=nfft * upsample)) * upsample
        lags = (np.arange(nfft * upsample) - nfft * upsample // 2) / upsample
        search = np.flatnonzero(np.abs(lags) <= 64)
        peak = int(search[np.argmax(np.abs(corr[search]))])
        if peak in (search[0], search[-1]):
            raise ValueError('Delay peak touches search boundary')
        a, b, d = np.abs(corr[peak-1:peak+2])
        denominator = a - 2*b + d
        fine = float(np.clip(0.5 * (a-d) / denominator, -0.5, 0.5)) if abs(denominator) > 1e-20 else 0.0
        result.append({'indices': [i, j], 'lag_samples': float(lags[peak] + fine / upsample),
                       'coherence': float(np.sum(energy_weight[mask] * coherence[mask]) / np.sum(energy_weight[mask])),
                       'used_bins': int(usable.sum()), 'band_bins': int(mask.sum()),
                       'peak_polarity': int(np.sign(corr[peak]))})
    return result
