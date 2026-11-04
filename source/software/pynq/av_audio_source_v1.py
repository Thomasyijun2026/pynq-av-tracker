"""Board-capture evidence for post-alignment steering, independent of object class.

Acquisition timestamps bound the transaction, not individual sample times.
Age is conservatively measured from transaction start; no synthetic timestamps
are substituted for an unavailable sample clock timestamp.
"""
import math
import re


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric ' + name + ' required')
    return float(value)


def validate_source(source, boot_id, now, max_age_seconds=5.0):
    now = finite(now, 'now')
    limit = finite(max_age_seconds, 'max age')
    if limit <= 0:
        raise ValueError('Positive source age limit required')
    if not isinstance(source.get('source_id'), str) or not source['source_id'].strip():
        raise ValueError('Source ID required')
    if source.get('boot_id') != boot_id or source.get('coordinate_frame') != 'ARRAY_FIXED':
        raise ValueError('Same-boot ARRAY_FIXED audio required')
    if source.get('audio_valid') is not True:
        raise ValueError('Rejected audio cannot steer')
    bearing = finite(source.get('audio_bearing_deg'), 'bearing')
    if not -180 <= bearing <= 180:
        raise ValueError('Bearing outside declared angular convention')
    evidence = source.get('audio_evidence', {})
    if evidence.get('origin') != 'BOARD_CAPTURE' or evidence.get('status') != 'VALID_BEARING':
        raise ValueError('Board audio capture evidence required')
    if evidence.get('selected_channels') != [1, 5, 7, 8] or evidence.get('reasons') != []:
        raise ValueError('Audio selection or rejection mismatch')
    if type(evidence.get('frames')) is not int or evidence['frames'] <= 0:
        raise ValueError('Captured frame count required')
    if not re.fullmatch('[0-9a-f]{64}', evidence.get('raw_sha256', '')):
        raise ValueError('PCM identity required')
    if any(evidence.get(k) is not True for k in ('clock_disabled', 'dma_reset_and_halted', 'buffer_freed')):
        raise ValueError('Audio cleanup not verified')
    health = evidence.get('health', {})
    if (health.get('changed') != [True] * 4 or health.get('clipped') != [False] * 4
            or health.get('duplicate_pairs') != []):
        raise ValueError('Four-channel input health rejected')
    rms = health.get('ac_rms', [])
    if len(rms) != 4 or any(finite(v, 'AC RMS') <= 1 for v in rms):
        raise ValueError('Four-channel input energy rejected')
    timing = evidence.get('capture_envelope', {})
    if (timing.get('clock_domain') != 'BOARD_MONOTONIC' or timing.get('boot_id') != boot_id
            or timing.get('sample_time_basis') != 'TRANSACTION_ENVELOPE_ONLY'):
        raise ValueError('Same-boot acquisition timing bound required')
    begin = finite(timing.get('begin_monotonic'), 'capture begin')
    end = finite(timing.get('end_monotonic'), 'capture end')
    published = finite(evidence.get('published_monotonic'), 'audio publication')
    if not 0 <= begin <= end <= published <= now:
        raise ValueError('Reordered or future audio timing')
    age = now - begin
    if age > limit:
        raise ValueError('Audio observation expired')
    return dict(status='AUDIO_SOURCE_ACCEPTED', source_id=source['source_id'], bearing_deg=bearing,
                raw_sha256=evidence['raw_sha256'], conservative_age_seconds=age,
                age_limit_seconds=limit, sample_time_basis='TRANSACTION_ENVELOPE_ONLY',
                semantic_label_required=False)


def make_source(source_id, answer, capture_meta, health, boot_id):
    """Adapt current PL/PS output without inventing source identity or timing."""
    if capture_meta.get('status') != 'CAPTURE_VERIFIED':
        raise ValueError('Capture not verified')
    if (capture_meta.get('raw_sha256') != answer.get('raw_sha256')
            or capture_meta.get('valid_frames') != answer.get('frames')):
        raise ValueError('Capture and bearing provenance disagree')
    evidence = {k: answer.get(k) for k in ('status', 'selected_channels', 'reasons', 'frames',
                'raw_sha256', 'clock_disabled', 'dma_reset_and_halted', 'buffer_freed')}
    evidence.update(origin='BOARD_CAPTURE', health=health,
                    published_monotonic=answer.get('output_monotonic'),
                    capture_envelope=dict(clock_domain='BOARD_MONOTONIC', boot_id=boot_id,
                        sample_time_basis='TRANSACTION_ENVELOPE_ONLY',
                        begin_monotonic=capture_meta.get('transaction_begin_monotonic'),
                        end_monotonic=capture_meta.get('transaction_end_monotonic')))
    return dict(source_id=source_id, boot_id=boot_id, coordinate_frame=answer.get('coordinate_frame'),
                audio_valid=answer.get('status') == 'VALID_BEARING',
                audio_bearing_deg=answer.get('bearing_deg'), audio_evidence=evidence,
                label='Acoustic source', semantic_identity=None)
