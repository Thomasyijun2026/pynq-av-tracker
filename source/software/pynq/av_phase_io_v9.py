"""Full-turn I/O candidate which measures axis without a historical axis file."""
from av_phase_io_V8 import Visual as PreviousVisual
from av_axis_bootstrap_V1 import probe_plan, CONVENTION, SCOPE
from av_screen_sweep_V1 import measure_axis


class Visual(PreviousVisual):
    def _stationary_marker(self):
        row = super()._stationary_marker()
        row.update(boot_id=self.boot, clock_domain='BOARD_MONOTONIC')
        return row

    def _measure_local_axis(self, unused_prior_axis):
        first = self._stationary_marker()
        plan = probe_plan(first, self.scan_rows)
        self.sweep_result['axis_probe_plan'] = plan
        rows = [first]
        for target in plan['targets']:
            self._move(target, reserve=.6)
            rows.append(self._stationary_marker())
        fit = measure_axis(rows)
        measured = dict(boot_id=self.boot, status='VISUAL_PAN_RESPONSE_MEASURED',
                        clock_domain='BOARD_MONOTONIC', evidence_scope=SCOPE,
                        measurements=rows, best=fit, yaw_convention=CONVENTION,
                        full_calibration=False, pixel_scope='ROTATED_180_RAW_UNRECTIFIED')
        self.sweep_result['measured_axis'] = measured
        return measured
