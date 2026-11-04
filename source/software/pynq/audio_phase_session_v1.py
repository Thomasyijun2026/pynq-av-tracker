"""Fresh, same-initialization phase references with explicit data lifetime.

The 60 s lease is an operational expiry, not a measured acoustic validity
guarantee. Rejected observations never update the reference. This module owns
no device and produces no actuator command. Its direction adapter is a local
model around an identified anchor; absolute accuracy remains unmeasured.
"""
import copy
import hashlib
import math
import re
import numpy as np
import audio_phase_reference_V4 as phase
import audio_phase_guard_V1 as base


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite_number_required')
    return float(value)


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('identity_required')
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('sha256_required')
    return value


class ReferenceSession:
    def __init__(self, boot_id, installation_id, epoch, triggered, initialization_budget=10., lease_seconds=60.):
        self.identity = dict(boot_id=text(boot_id), installation_id=text(installation_id), epoch=text(epoch))
        self.triggered = number(triggered)
        self.budget = number(initialization_budget)
        self.lease = number(lease_seconds)
        if self.triggered < 0 or not 0 < self.budget <= 10 or not 0 < self.lease <= 3600:
            raise ValueError('invalid_bounded_lifetime')
        self.noise = self.reference = None
        self.noise_evidence = self.reference_evidence = None
        self.seen = set()
        self.invalidated = None

    def validate(self, pcm, evidence, now):
        now = number(now)
        if self.invalidated:
            raise ValueError('reference_invalidated:' + self.invalidated)
        if any(evidence.get(k) != v for k, v in self.identity.items()):
            raise ValueError('boot_installation_or_epoch_changed')
        if evidence.get('origin') != 'BOARD_CAPTURE' or evidence.get('clock_domain') != 'BOARD_MONOTONIC':
            raise ValueError('board_capture_required')
        if evidence.get('sample_time_basis') != 'TRANSACTION_ENVELOPE_ONLY':
            raise ValueError('sample_time_basis_required')
        if evidence.get('cleanup_verified') is not True or evidence.get('abi') != 0x444f4105 or evidence.get('fir_id') != 0x08040210:
            raise ValueError('capture_lifecycle_or_interface_invalid')
        begin, end = number(evidence.get('begin')), number(evidence.get('end'))
        if not self.triggered <= begin < end <= now:
            raise ValueError('capture_time_outside_initialization_epoch')
        raw_sha = digest(evidence.get('raw_sha256'))
        if type(evidence.get('window_first')) is not int or evidence['window_first'] < 0:
            raise ValueError('window_first_required')
        if type(evidence.get('raw_frames')) is not int or evidence['window_first'] + 32768 > evidence['raw_frames']:
            raise ValueError('window_outside_recording')
        x = np.asarray(pcm)
        if x.shape != (32768, 4) or not np.issubdtype(x.dtype, np.integer):
            raise ValueError('raw_signed_integer_window_required')
        if np.any((x <= -8388608) | (x >= 8388607)):
            raise ValueError('exact_full_scale')
        if any(np.ptp(x[:, k]) == 0 for k in range(4)):
            raise ValueError('constant_channel')
        if any(np.array_equal(x[:, i], x[:, j]) for i, j in base.PAIRS):
            raise ValueError('duplicate_channels')
        window_sha = hashlib.sha256(np.asarray(x, dtype='<i4').tobytes()).hexdigest()
        if digest(evidence.get('window_sha256')) != window_sha:
            raise ValueError('pcm_window_sha_mismatch')
        return x, dict(copy.deepcopy(evidence), window_sha256=window_sha), raw_sha

    def set_noise(self, pcm, evidence, now):
        if self.noise is not None or self.reference is not None:
            raise ValueError('noise_already_bound')
        x, ev, _ = self.validate(pcm, evidence, now)
        if number(now) - self.triggered > self.budget:
            raise ValueError('initialization_deadline_exceeded')
        self.noise = base.noise_reference(x)
        self.noise_evidence = ev

    def seal(self, pcm, evidence, now):
        if self.noise is None or self.reference is not None:
            raise ValueError('noise_missing_or_reference_already_sealed')
        x, ev, _ = self.validate(pcm, evidence, now)
        if number(now) - self.triggered > self.budget:
            raise ValueError('initialization_deadline_exceeded')
        if ev['begin'] < self.noise_evidence['end'] or ev['raw_sha256'] == self.noise_evidence['raw_sha256']:
            raise ValueError('reference_noise_recording_overlap')
        self.reference = phase.statistics(x)
        self.reference_evidence = ev
        self.seen.update([ev['raw_sha256'], self.noise_evidence['raw_sha256']])
        return self.manifest()

    def manifest(self):
        return dict(schema='AUDIO_PHASE_SESSION_V1', identity=copy.deepcopy(self.identity),
                    triggered=self.triggered, initialization_budget=self.budget, lease_seconds=self.lease,
                    noise_evidence=copy.deepcopy(self.noise_evidence), reference_evidence=copy.deepcopy(self.reference_evidence),
                    state='INVALIDATED' if self.invalidated else ('SEALED' if self.reference is not None else 'PENDING'),
                    invalidated=self.invalidated, policy=copy.deepcopy(phase.POLICY),
                    expiry_scope='OPERATIONAL_ONLY_NOT_MEASURED_TRANSFER_INVARIANCE', absolute_accuracy='NOT_VALIDATED')

    def invalidate(self, reason):
        self.invalidated = text(reason)

    def evaluate(self, pcm, evidence, now):
        try:
            if self.reference is None:
                raise ValueError('reference_missing')
            x, ev, sha = self.validate(pcm, evidence, now)
            if number(now) - self.reference_evidence['begin'] > self.lease:
                raise ValueError('reference_lease_expired')
            if ev['begin'] < self.reference_evidence['end']:
                raise ValueError('observation_precedes_reference')
            if sha in self.seen:
                raise ValueError('recording_already_consumed')
            self.seen.add(sha)
            result = phase.estimate(self.reference, x, self.noise)
            result.update(identity=copy.deepcopy(self.identity), reference_sha256=self.reference_evidence['raw_sha256'],
                          raw_sha256=sha, capture_evidence=ev, reference_age_seconds=number(now) - self.reference_evidence['begin'])
            return result
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return dict(status='REJECTED', reasons=[str(exc)], delta_vector=None, global_bearing=None, servo_command=None)


def local_direction(result, anchor):
    """Convert a valid relative vector using an explicitly identified local anchor.

    The reference vector is estimated from that reference's broadband geometry;
    it is not a camera measurement of array coordinates. No normalization or
    angle bias fitting to a photograph is allowed here.
    """
    if result.get('status') != 'VALID_RELATIVE_VECTOR' or result.get('reasons') != []:
        raise ValueError('rejected_phase_cannot_steer')
    if anchor.get('identity') != result.get('identity') or anchor.get('reference_sha256') != result.get('reference_sha256'):
        raise ValueError('anchor_reference_identity_mismatch')
    if anchor.get('visual_confirmed') is not True or anchor.get('reference_geometry_valid') is not True:
        raise ValueError('identified_visual_and_acoustic_anchor_required')
    if anchor.get('coordinate_frame') != 'ARRAY_FIXED' or anchor.get('vector_basis') != 'BROADBAND_GEOMETRY_ESTIMATE':
        raise ValueError('fixed_array_reference_vector_required')
    if len(anchor['projected_vector']) != 2 or len(result['delta_vector']) != 2:
        raise ValueError('two_dimensional_projection_required')
    u = [number(a) + number(b) for a, b in zip(anchor['projected_vector'], result['delta_vector'])]
    radius = math.hypot(*u)
    if not .12 < radius < 1.1:
        raise ValueError('projection_ill_defined')
    angle = math.degrees(math.atan2(u[0], u[1]))
    return dict(status='LOCAL_REFERENCE_BEARING', coordinate_frame='ARRAY_FIXED', bearing_deg=angle,
                projected_vector=u, projected_norm=radius, physical_angular_accuracy='NOT_VALIDATED',
                anchor_basis='SINGLE_KNOWN_SOURCE_AND_BROADBAND_MODEL', servo_command=None)
