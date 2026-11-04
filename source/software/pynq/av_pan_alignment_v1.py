"""Measured image/pan response and a separate relative azimuth map.

No intrinsic/extrinsic 3-D calibration is fabricated. The yaw convention is
explicit: image has been rotated upright, array +Z and servo rotation axis
point upward. A single identified source establishes only the yaw offset.
"""
import math,statistics


def wrap(angle):
    if isinstance(angle,bool) or not math.isfinite(angle):raise ValueError('Finite angle required')
    return (angle+180.)%360.-180.


def project(h,point):
    if len(h)!=3 or any(len(row)!=3 for row in h):raise ValueError('Homography shape')
    if not all(math.isfinite(v) for row in h for v in row):raise ValueError('Homography finite')
    x,y=[v/2 for v in point];z=h[2][0]*x+h[2][1]*y+h[2][2]
    if abs(z)<1e-8:raise ValueError('Point at infinity')
    return [2*(h[k][0]*x+h[k][1]*y+h[k][2])/z for k in (0,1)]


def measure_axis(result,references):
    if result.get('status')!='STATIONARY_VIEWS_COLLECTED' or not result.get('cleanup_verified') or not result.get('boot_id'):
        raise ValueError('Completed stationary measurement required')
    refs={x['id']:x for x in references};groups={};rejected=[]
    for row in result['observations']:
        before,after=row['before'],row['after'];f=row['frame'];v=row['vision']
        if any(not x.get('full_angle') or x['speed'] or x['moving'] or abs(x['position']-row['goal'])>4 for x in (before,after)):
            raise ValueError('Unsettled measurement')
        if not f['read_begin']<=f['read_end']<=f['write_end']<=v['completed_monotonic']:
            raise ValueError('Measurement timestamps reordered')
        if v.get('image_rotation')!=180:raise ValueError('Upright image convention changed')
        for c in v['candidates']:
            if not c.get('matched'):continue
            ref=refs.get(c['reference_id'])
            if ref is None or ref['sha256']!=c['reference_sha']:raise ValueError('Reference identity')
            uv=project(c['homography'],ref['object']['center_px'])
            if not 0<=uv[0]<=639 or not 0<=uv[1]<=479:
                rejected.append(dict(file=f['file'],reason='REFERENCE_POINT_OUTSIDE_IMAGE'));continue
            groups.setdefault(c['reference_id'],[]).append(dict(q=row['position'],goal=row['goal'],uv=uv,file=f['file'],sha256=f['sha256'],score=c['score']))
    candidates=[]
    for ref_id,rows in groups.items():
        points=[]
        for goal in sorted(set(x['goal'] for x in rows)):
            r=[x for x in rows if x['goal']==goal]
            points.append(dict(q=statistics.median(x['q'] for x in r),u=statistics.median(x['uv'][0] for x in r),v=statistics.median(x['uv'][1] for x in r),samples=len(r)))
        if len(points)<3 or points[-1]['q']-points[0]['q']<200:
            rejected.append(dict(reference_id=ref_id,reason='INSUFFICIENT_ANGULAR_BASELINE'));continue
        slopes=[(b['u']-a['u'])/(b['q']-a['q']) for a,b in zip(points,points[1:])]
        slope=statistics.median(slopes)
        qbar=statistics.mean(x['q'] for x in points);ubar=statistics.mean(x['u'] for x in points)
        fitted=sum((x['q']-qbar)*(x['u']-ubar) for x in points)/sum((x['q']-qbar)**2 for x in points)
        intercept=ubar-fitted*qbar
        residual=max(abs(x['u']-(fitted*x['q']+intercept)) for x in points)
        # Predict the third view from the first two; it was not used for that prediction.
        a,b,c=points[:3];holdout=abs(c['u']-(a['u']+(b['u']-a['u'])*(c['q']-a['q'])/(b['q']-a['q'])))
        if abs(slope)<.1 or any(s*slope<=0 for s in slopes) or residual>6 or holdout>10:
            rejected.append(dict(reference_id=ref_id,reason='RESPONSE_MODEL_REJECTED',slope=slope,residual=residual,holdout=holdout));continue
        candidates.append(dict(reference_id=ref_id,reference_sha=refs[ref_id]['sha256'],points=points,source_frames=rows,
             pixel_u_per_count=fitted,holdout_error_px=holdout,max_residual_px=residual,
             upright_yaw_sign=1 if fitted<0 else -1,center_count_local_linear=(319.5-intercept)/fitted))
    if not candidates:return dict(status='VISUAL_AXIS_NOT_MEASURED',boot_id=result['boot_id'],rejected=rejected,full_calibration=False)
    if len(set(x['upright_yaw_sign'] for x in candidates))!=1:raise ValueError('Conflicting image yaw signs')
    best=max(candidates,key=lambda x:len(x['source_frames']))
    return dict(status='VISUAL_PAN_RESPONSE_MEASURED',boot_id=result['boot_id'],best=best,candidates=candidates,rejected=rejected,
        scope='STATIONARY_IMAGE_DISPLACEMENT',full_calibration=False,
        yaw_convention='UPRIGHT_IMAGE_ARRAY_Z_AND_SERVO_AXIS_UP',pixel_scope='ROTATED_180_RAW_UNRECTIFIED')


def align(axis,anchor,reference,boot_id):
    if axis.get('status')!='VISUAL_PAN_RESPONSE_MEASURED' or axis['boot_id']!=boot_id or reference['boot_id']!=boot_id:
        raise ValueError('Same-boot measured yaw response required')
    if anchor.get('coordinate_frame')!='ARRAY_FIXED' or not anchor.get('audio_valid') or not anchor.get('visual_confirmed'):
        raise ValueError('Valid array bearing and identified view required')
    q=anchor['servo_view_position'];beta=wrap(anchor['audio_bearing_deg']);sign=axis['best']['upright_yaw_sign']
    if type(q)is not int or not reference['low']<=q<=reference['high']:raise ValueError('Anchor outside fixed envelope')
    return dict(status='RELATIVE_SINGLE_SOURCE_ALIGNED',boot_id=boot_id,anchor_bearing_deg=beta,anchor_count=q,
                counts_per_turn=4096,servo_sign=sign,low=reference['low'],high=reference['high'],
                yaw_convention=axis['yaw_convention'],source_identity=anchor['source_identity'],
                physical_angular_accuracy='NOT_VALIDATED',intrinsics_extrinsics='CALIBRATION_PENDING',
                physical_multi_source_validation=False)


def target_count(mapping,bearing,current,boot_id):
    if mapping.get('status')!='RELATIVE_SINGLE_SOURCE_ALIGNED' or mapping['boot_id']!=boot_id:raise ValueError('Stale/missing azimuth map')
    if type(current)is not int or not mapping['low']<=current<=mapping['high']:raise ValueError('Current pose outside installation')
    raw=mapping['anchor_count']+mapping['servo_sign']*wrap(bearing-mapping['anchor_bearing_deg'])*4096/360
    choices=[int(round(raw+4096*k)) for k in (-2,-1,0,1,2)]
    choices=[q for q in choices if mapping['low']<=q<=mapping['high']]
    if not choices:raise ValueError('No cable-envelope-equivalent target')
    return min(choices,key=lambda q:abs(q-current))
