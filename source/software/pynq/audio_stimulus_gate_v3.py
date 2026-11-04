"""Match the complete bounded ten-second known reference without changing gates.

The scan entry movement can delay capture beyond the old first-four-second
search. This changes the identity search support only, never direction gates.
"""
import numpy as np
from scipy import fft, signal
from audio_stimulus_gate_V2 import StimulusGate as Prior, FS


class StimulusGate(Prior):
    def __init__(self, reference, source_sha256):
        super().__init__(reference, source_sha256)
        ref = np.asarray(reference, dtype=float)
        self.search_seconds = len(ref) / FS
        self.reference = signal.resample_poly(signal.sosfiltfilt(self.sos, ref), 1, 2)
        self.nfft = fft.next_fast_len(len(self.reference) + self.n - 1)
        self.spectrum = fft.rfft(self.reference, self.nfft)
        sums = np.r_[0., np.cumsum(self.reference)]
        squares = np.r_[0., np.cumsum(self.reference ** 2)]
        self.energy = np.maximum(squares[self.n:] - squares[:-self.n] -
                                 (sums[self.n:] - sums[:-self.n]) ** 2 / self.n, 0.)

    def evaluate(self, pcm):
        result = super().evaluate(pcm)
        result.update(schema='AUDIO_STIMULUS_GATE_V3',
                      max_source_search_seconds=self.search_seconds)
        return result
