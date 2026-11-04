"""Background-only adapter; the reference/follow entry remains exactly V7."""
import hashlib
from pathlib import Path
import numpy as np
from av_phase_io_V7 import Capture as PriorCapture
from av_phase_follow_V7 import atomic


class Capture(PriorCapture):
    def background(self, out):
        from mic_background_V1 import capture_background
        out = Path(out)
        meta = capture_background(self.check.mic, self.bundle, {'files': self.files}, out)
        data = (out / meta['raw_file']).read_bytes()
        if len(data) != 32768 * 32 or hashlib.sha256(data).hexdigest() != meta['raw_sha256']:
            raise ValueError('background_saved_pcm_mismatch')
        raw = np.frombuffer(data, dtype='<i4').reshape(32768, 8)
        x = raw[:, [0, 4, 6, 7]]
        health = dict(changed=[bool(np.ptp(x[:, k])) for k in range(4)],
            clipped=[bool(np.any((x[:, k] <= -8388608) | (x[:, k] >= 8388607))) for k in range(4)],
            duplicate_pairs=[[a, b] for a in range(4) for b in range(a + 1, 4)
                             if np.array_equal(x[:, a], x[:, b])],
            ac_rms=np.std(x.astype(float), axis=0).tolist())
        atomic(out / 'health.json', health)
        # 'snapshot' is the engine's window descriptor, explicitly not MMIO.
        # No PL-ready bit, correlation value, or valid direction is fabricated.
        descriptor = dict(first_center_raw_index=0, schema='RAW_BACKGROUND_WINDOW_V1',
                          origin='SAVED_PCM_WINDOW', pl_snapshot_read=False)
        atomic(out / 'background_window.json', descriptor)
        return dict(answer=dict(status='BACKGROUND_ONLY', bearing_deg=None),
                    metadata=meta, snapshot=descriptor, pcm=x, health=health)
