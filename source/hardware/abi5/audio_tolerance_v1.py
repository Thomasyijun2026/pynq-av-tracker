"""User-authorized 20 percent quality tolerance; unchanged PL data/angle fit.

This is a quality-gate allowance, not a measured angular accuracy claim.
Physical delay, raw input health, temporal stability and actuator protections
are not relaxed. Every decision retains its original strict assessment.
"""
import copy
import math
from audio_peak_reference_V4 import estimate as strict_estimate

VERSION = 'QUALITY_TOLERANCE_20_PERCENT_V1'
ALLOWANCE = .20
STRICT = dict(weak_pair_correlation=.2, ambiguous_peak=.08,
              inconsistent_geometry=.5, inconsistent_delay_cycle=1.)
THRESHOLDS = dict(weak_pair_correlation=.16, ambiguous_peak=.064,
                  inconsistent_geometry=.6, inconsistent_delay_cycle=1.2)


def apply_tolerance(strict):
    result = copy.deepcopy(strict)
    result.update(policy_version=VERSION, peak_policy_version=strict['policy_version'],
                  strict_assessment=copy.deepcopy(strict), tolerance_fraction=ALLOWANCE,
                  tolerance_scope='CORRELATION_CONTRAST_GEOMETRY_AND_CYCLE_GATES_ONLY',
                  strict_thresholds=dict(STRICT), applied_thresholds=dict(THRESHOLDS),
                  physical_bearing_accuracy='NOT_VALIDATED')
    reasons = list(strict['reasons'])
    if 'fit' in strict:
        numbers = list(strict['strengths']) + list(strict['peak_contrast']) + [strict['fit']['weighted_rms_residual_samples']]
        numbers += [c['closure_error_samples'] for c in strict['fit']['cycle_closures']]
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError('Non-finite quality metrics')
        values = dict(weak_pair_correlation=min(strict['strengths']),
                      ambiguous_peak=min(strict['peak_contrast']),
                      inconsistent_geometry=strict['fit']['weighted_rms_residual_samples'],
                      inconsistent_delay_cycle=max(abs(c['closure_error_samples'])
                          for c in strict['fit']['cycle_closures']))
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError('Non-finite quality metrics')
        for name, value in values.items():
            fails = value < THRESHOLDS[name] if name in ('weak_pair_correlation', 'ambiguous_peak') else value > THRESHOLDS[name]
            if fails and name not in reasons:
                reasons.append(name)
            elif not fails and name in reasons:
                reasons.remove(name)
        result['quality_metrics'] = values
    valid = not reasons and strict.get('candidate_bearing_deg') is not None
    if valid and not math.isfinite(strict['candidate_bearing_deg']):
        raise ValueError('Non-finite bearing')
    result.update(valid=valid, reasons=reasons,
                  bearing_deg=strict.get('candidate_bearing_deg') if valid else None,
                  quality_grade=('STRICT_PASS' if strict['valid'] else 'RELAXED_20_PASS') if valid else 'REJECTED')
    return result


def estimate(snapshot, positions):
    return apply_tolerance(strict_estimate(snapshot, positions))
