"""Bounded 350-feature known-computer matching, seeded only by saved semantic detections.

This is a geometric match score, not a calibrated class probability. It does
not establish that the visible computer emitted a particular sound.
"""
import hashlib,time
from pathlib import Path
import cv2
import numpy as np


class KnownComputer:
    def __init__(self,references):
        self.orb=cv2.ORB_create(nfeatures=350,scaleFactor=1.15,nlevels=8,edgeThreshold=8,fastThreshold=12)
        self.matcher=cv2.BFMatcher(cv2.NORM_HAMMING)
        self.references=[]
        for ref in references:
            path=Path(ref['path']);obj=ref['object']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('Reference SHA mismatch')
            if obj['label']!='laptop' or obj['confidence']<.5:raise ValueError('Semantic laptop seed required')
            raw=cv2.imread(str(path))
            if raw is None or raw.shape!=(480,640,3):raise ValueError('Reference resolution')
            gray=self.gray(raw);box=np.asarray(obj['box_xyxy'],dtype=float)/2
            x0,y0,x1,y1=box
            # Erode rectangular detections to exclude object/background boundaries.
            dx,dy=(x1-x0)*.08,(y1-y0)*.08
            mask=np.zeros((240,320),np.uint8)
            cv2.rectangle(mask,(int(x0+dx),int(y0+dy)),(int(x1-dx),int(y1-dy)),255,-1)
            kp,des=self.orb.detectAndCompute(gray,mask)
            if des is None or len(kp)<24:raise ValueError('Insufficient seeded computer features')
            self.references.append(dict(id=ref['id'],sha256=ref['sha256'],object=obj,kp=kp,des=des,mask_pixels=int(np.count_nonzero(mask))))

    @staticmethod
    def gray(raw):
        if raw is None or raw.shape!=(480,640,3):raise ValueError('Input resolution')
        return cv2.resize(cv2.cvtColor(cv2.rotate(raw,cv2.ROTATE_180),cv2.COLOR_BGR2GRAY),(320,240),interpolation=cv2.INTER_AREA)

    def infer(self,raw):
        begin=time.monotonic();gray=self.gray(raw);kp,des=self.orb.detectAndCompute(gray,None);feature_end=time.monotonic()
        candidates=[]
        for ref in self.references:
            item=dict(reference_id=ref['id'],reference_sha=ref['sha256'],matched=False,score=0.,good=0,inliers=0)
            if des is not None and len(des)>=2:
                pairs=self.matcher.knnMatch(ref['des'],des,k=2)
                good=[p[0] for p in pairs if len(p)==2 and p[0].distance<.70*p[1].distance]
                item['good']=len(good)
                if len(good)>=16:
                    src=np.float32([ref['kp'][m.queryIdx].pt for m in good]).reshape(-1,1,2)
                    dst=np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1,1,2)
                    H,mask=cv2.findHomography(src,dst,cv2.RANSAC,1.5)
                    if H is not None and mask is not None and np.isfinite(H).all():
                        keep=mask.ravel().astype(bool);n=int(keep.sum());ratio=n/len(good)
                        err=np.linalg.norm(cv2.perspectiveTransform(src,H)-dst,axis=2).ravel()[keep]
                        hull=float(cv2.contourArea(cv2.convexHull(dst[keep]))) if n>=3 else 0.
                        residual=float(np.median(err)) if n else 999.
                        item.update(inliers=n,inlier_ratio=ratio,residual_px=residual*2,hull_pixels=hull*4)
                        if n>=16 and ratio>=.45 and residual<=1.5 and hull>=150:
                            center=np.median(dst[keep].reshape(-1,2),axis=0)*2
                            score=float(ref['object']['confidence'])*ratio*min(1.,n/60.)
                            item.update(matched=True,score=score,observed_feature_center_px=center.tolist(),homography=H.tolist())
            candidates.append(item)
        best=max(candidates,key=lambda x:(x['matched'],x['score'])) if candidates else None
        return dict(status='KNOWN_COMPUTER_MATCHED' if best and best['matched'] else 'NO_MATCH',
                    best=best,candidates=candidates,processing_seconds=time.monotonic()-begin,feature_seconds=feature_end-begin,matching_seconds=time.monotonic()-feature_end,
                    completed_monotonic=time.monotonic(),score_kind='SEMANTIC_SEEDED_GEOMETRIC_MATCH',
                    image_rotation=180,semantic_recomputed=False,acoustic_identity_confirmed=False)
