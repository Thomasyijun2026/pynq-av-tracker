"""One scan, bounded stationary reacquisition, then a newly measured axis."""
import copy
import time
from av_phase_io_V13 import Visual as Previous
from av_static_reacquire_V1 import nearby, dither_targets, center_hint, axis_targets
from av_axis_bootstrap_V1 import CONVENTION, SCOPE
from av_screen_sweep_V1 import measure_axis
from av_pan_view_probe_V1 import stable


class Visual(Previous):
    def _marker_attempt(self):
        before=self.position()
        item=self.fresh(time.monotonic())
        after=self.position()
        stable(before,after,int(before['position']))
        found=self.detector.infer(item[3]);frame=self.save(item,found)
        row=dict(position=after['position'],frame=frame,vision=found,before=before,after=after,
                 boot_id=self.boot,clock_domain='BOARD_MONOTONIC')
        self.sweep_result.setdefault('stationary_search_attempts',[]).append(copy.deepcopy(row))
        if found.get('status')!='KNOWN_SCREEN_MATCHED':return None
        row['u']=found['best']['center_px'][0]/2
        return row

    def _marker_here(self):
        # Two new drained observations handle a transient display/camera change.
        # Communication, motion and temperature faults propagate immediately.
        for _ in range(2):
            row=self._marker_attempt()
            if row is not None:return row
        return None

    def _measure_local_axis(self, unused_prior_axis):
        self.progress('REACQUIRING_STATIC_SCREEN')
        origin=int(self.position()['position']);first=None
        for target in nearby(origin):
            if int(self.position()['position'])!=target:self._move(target,reserve=0.)
            first=self._marker_here()
            if first is not None:break
        if first is None:raise ValueError('static_screen_not_reacquired_keep_pattern_visible')
        second=None
        for target in dither_targets(first['position']):
            self._move(target,reserve=0.)
            second=self._marker_here()
            if second is not None:break
        if second is None:raise ValueError('stationary_probe_marker_lost_keep_pattern_visible')
        hint=center_hint(first,second)
        self.sweep_result['stationary_center_hint']=hint
        self.progress('RETURNING_TO_STATIC_SCREEN_CENTER')
        self._move(hint['goal'],reserve=0.)
        targets=axis_targets(hint['goal'])
        self.sweep_result['axis_probe_plan']=dict(status='PROBE_PLAN_ONLY',targets=targets,
            basis='CURRENT_TRIGGER_STATIONARY_DITHER',usable_for_alignment=False,
            requires_three_fresh_stationary_views=True)
        self.progress('MEASURING_AXIS_WITH_STATIC_FRAMES')
        rows=[]
        for target in targets:
            self._move(target,reserve=0.)
            row=self._marker_here()
            if row is None:raise ValueError('screen_lost_during_axis_confirmation_keep_pattern_visible')
            rows.append(row)
        fit=measure_axis(rows)
        axis=dict(boot_id=self.boot,status='VISUAL_PAN_RESPONSE_MEASURED',
            clock_domain='BOARD_MONOTONIC',evidence_scope=SCOPE,measurements=rows,best=fit,
            yaw_convention=CONVENTION,full_calibration=False,pixel_scope='ROTATED_180_RAW_UNRECTIFIED')
        self.sweep_result['measured_axis']=axis
        self._axis_view=copy.deepcopy(rows[-1])
        return axis
