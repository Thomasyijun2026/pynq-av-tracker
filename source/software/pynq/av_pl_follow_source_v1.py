"""Use the current PL consensus for post-alignment sound, with original timing."""
import copy
import hashlib
import json
import math
from av_audio_source_V1 import make_source, validate_source, finite


def identity(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def current_source(record, boot_id):
    answer, meta, snap = record['answer'], record['metadata'], record['snapshot']
    policy = snap['ps_estimate']
    if any(meta.get(k) is not True or answer.get(k) is not True
           for k in ('clock_disabled', 'dma_reset_and_halted', 'buffer_freed')):
        raise ValueError('Current capture cleanup provenance mismatch')
    if (snap.get('extension_abi') != '0x444f4105' or snap.get('fir_id') != '0x08040210'
            or policy.get('policy_version') != 'FRACTIONAL_SUBWINDOW_CONSENSUS_V1'
            or answer.get('audio_policy_version') != policy['policy_version']
            or answer.get('coordinate_frame') != 'ARRAY_FIXED'
            or answer.get('status') != ('VALID_BEARING' if policy.get('valid') is True else 'REJECTED_SIGNAL')
            or answer.get('reasons') != policy.get('reasons')):
        raise ValueError('Current PL policy provenance mismatch')
    if policy.get('valid') is True:
        if not math.isclose(finite(answer.get('bearing_deg'), 'bearing'),
                            finite(policy.get('bearing_deg'), 'PL bearing'), abs_tol=1e-9, rel_tol=0):
            raise ValueError('Current PL bearing mismatch')
    elif answer.get('bearing_deg') is not None:
        raise ValueError('Rejected PL direction must remain absent')
    event = make_source('audio_'+meta['raw_sha256'][:16], copy.deepcopy(answer),
                        meta, copy.deepcopy(record['health']), boot_id)
    event['audio_evidence'].update(estimator='CURRENT_PL_FRACTIONAL_CONSENSUS_V1',
        reference_phase_required=False, policy=copy.deepcopy(policy))
    return event


def admit(source, boot_id, now):
    verdict = validate_source(source, boot_id, now)
    return dict(status='FRESH_AUDIO_ADMITTED', boot_id=boot_id, checked_monotonic=now,
                source_sha256=identity(source), verdict=verdict)


def validate_admission(source, receipt, boot_id, now):
    """Recheck pre-motion admission without pretending a saved sound is new."""
    checked = finite(receipt.get('checked_monotonic'), 'admission timestamp')
    if (receipt.get('status') != 'FRESH_AUDIO_ADMITTED' or receipt.get('boot_id') != boot_id
            or receipt.get('source_sha256') != identity(source) or not 0 <= checked <= now):
        raise ValueError('Original fresh-audio admission required')
    verdict = validate_source(source, boot_id, checked)
    if verdict != receipt.get('verdict'):
        raise ValueError('Audio admission verdict changed')
    return verdict
