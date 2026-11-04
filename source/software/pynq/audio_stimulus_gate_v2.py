"""Known initialization stimulus identity, independent of direction estimation.

The correlation floor .10 and cross-channel offset spread 256 samples are
carried from the earlier source-alignment diagnostic. A pass proves detectable
stimulus identity only, not dominance, bearing accuracy or calibration. The
first four seconds of a known, non-looping source bound both work and ambiguity.
No hardware, playback or actuator is owned here.
"""
import hashlib
import math
import numpy as np
from scipy import fft, signal

FS = 31250
WINDOW = 32768
TRIM = 1024
MIN_CORRELATION = .10
MAX_OFFSET_SPREAD = 256


def sha_array(x):
    return hashlib.sha256(np.asarray(x, dtype='<i4').tobytes()).hexdigest()


class StimulusGate:
    def __init__(self, reference, source_sha256):
        if not isinstance(source_sha256, str) or len(source_sha256) != 64 or any(c not in '0123456789abcdef' for c in source_sha256):
            raise ValueError('source_sha256_required')
        ref = np.asarray(reference, dtype=float)
        if ref.ndim != 1 or not WINDOW + 4096 <= len(ref) <= FS * 10 or not np.isfinite(ref).all():
            raise ValueError('bounded_finite_mono_reference_required')
        self.sos = signal.butter(4, [300, 5000], btype='bandpass', fs=FS, output='sos')
        self.reference = signal.resample_poly(signal.sosfiltfilt(self.sos, ref[:FS*4]), 1, 2)
        if np.std(self.reference) < 1e-9:
            raise ValueError('reference_has_no_band_energy')
        self.n = (WINDOW - 2 * TRIM) // 2
        self.nfft = fft.next_fast_len(len(self.reference) + self.n - 1)
        self.spectrum = fft.rfft(self.reference, self.nfft)
        sums = np.r_[0., np.cumsum(self.reference)]
        squares = np.r_[0., np.cumsum(self.reference ** 2)]
        self.energy = np.maximum(squares[self.n:] - squares[:-self.n] -
                                 (sums[self.n:] - sums[:-self.n]) ** 2 / self.n, 0.)
        self.source_sha256 = source_sha256

    def evaluate(self, pcm):
        x = np.asarray(pcm)
        if x.shape != (WINDOW, 4) or not np.issubdtype(x.dtype, np.integer):
            raise ValueError('signed_integer_four_channel_window_required')
        evidence = dict(schema='AUDIO_STIMULUS_GATE_V2', source_sha256=self.source_sha256,
                        window_sha256=sha_array(x), reference_samples=len(self.reference),
                        sample_rate=FS, identity_analysis_rate=FS/2, max_source_search_seconds=4, min_abs_correlation=MIN_CORRELATION,
                        max_offset_spread_samples=MAX_OFFSET_SPREAD,
                        scope='IDENTITY_ONLY_NOT_DOMINANCE_OR_DIRECTION', bearing=None, servo_command=None)
        problems = []
        if np.any((x <= -8388608) | (x >= 8388607)):
            problems.append('full_scale')
        if any(np.ptp(x[:, c]) == 0 for c in range(4)):
            problems.append('constant_channel')
        if any(np.array_equal(x[:, i], x[:, j]) for i in range(4) for j in range(i + 1, 4)):
            problems.append('duplicate_channels')
        if problems:
            return dict(evidence, status='REJECTED', reasons=problems, channels=[])
        y = signal.resample_poly(signal.sosfiltfilt(self.sos, x.astype(float), axis=0), 1, 2, axis=0)[TRIM//2:-TRIM//2]
        y -= y.mean(axis=0)
        energy = np.sum(y * y, axis=0)
        dot = fft.irfft(self.spectrum[:, None] * fft.rfft(y[::-1], self.nfft, axis=0),
                        self.nfft, axis=0)[self.n-1:len(self.reference)]
        denom = np.sqrt(np.maximum(self.energy[:, None] * energy[None, :], np.finfo(float).tiny))
        correlation = dot / denom
        correlation[self.energy < max(float(self.energy.max()) * 1e-12, 1e-30)] = 0.
        rows = []
        for c in range(4):
            index = int(np.argmax(abs(correlation[:, c])))
            direct = float(np.corrcoef(y[:, c], self.reference[index:index+self.n])[0, 1]) if energy[c] else 0.
            if not math.isfinite(direct) or not np.isclose(direct, correlation[index, c], atol=2e-7, rtol=1e-7):
                raise ValueError('independent_scalar_correlation_mismatch')
            boundary = index in (0, len(correlation)-1)
            rows.append(dict(channel=c, correlation=direct, integer_offset=2*index,
                             raw_window_offset=2*index-TRIM, at_search_boundary=boundary))
            if abs(direct) <= MIN_CORRELATION:
                problems.append('source_not_detected_ch' + str(c))
            if boundary:
                problems.append('source_search_boundary_ch' + str(c))
        spread = max(r['integer_offset'] for r in rows) - min(r['integer_offset'] for r in rows)
        if spread >= MAX_OFFSET_SPREAD:
            problems.append('source_timing_inconsistent')
        return dict(evidence, status='SOURCE_PRESENT' if not problems else 'REJECTED', reasons=problems,
                    channels=rows, offset_spread_samples=spread)
