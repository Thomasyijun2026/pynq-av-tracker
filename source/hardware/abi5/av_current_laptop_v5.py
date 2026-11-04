"""Full-resolution known-laptop matcher. No yaw calibration or servo decisions."""
import cv2
import numpy as np
import time

class CurrentLaptopTemplate:
    def __init__(self, reference, current_reference):
        self.detector = cv2.ORB_create(nfeatures=1200)
        reference_detector = cv2.ORB_create(nfeatures=400)
        self.planes = []
        self.reference_diagnostics = []
        references = [
            ('previous_view',reference,[('keyboard',[[11,72],[302,74],[299,132],[45,137]]),('screen',[[58,134],[311,145],[292,332],[38,309]])]),
            ('current_view',current_reference,[('keyboard',[[0,74],[184,78],[212,121],[0,113]]),('screen',[[20,125],[238,144],[217,337],[10,272]])])]
        for view, image, regions in references:
            if image is None or image.shape[:2] != (480,640):
                raise ValueError('Expected raw 640x480 reference')
            gray = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
            for name, coords in regions:
                polygon = np.array(coords,np.float32)
                mask = np.zeros((480,640),np.uint8)
                cv2.fillPoly(mask,[polygon.astype(np.int32)],255)
                kp, des = reference_detector.detectAndCompute(gray,mask)
                valid = des is not None and len(kp)>=20
                self.reference_diagnostics.append(dict(view=view,plane=name,keypoints=len(kp),usable=valid))
                if valid:self.planes.append((view+'_'+name,polygon,kp,des))
        if not self.planes:raise ValueError('No reference plane has enough features')
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    def match(self, image):
        start = time.monotonic()
        if image is None or image.shape[:2] != (480,640):
            raise ValueError('Expected raw 640x480 frame')
        kp, des = self.detector.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
        candidates = []
        for name, polygon, refkp, refdes in self.planes:
            out = dict(plane=name, status='NOT_FOUND', matched=False, keypoints=len(kp),
                       reference_keypoints=len(refkp), good_matches=0, inliers=0, polygon_px=None)
            if des is not None and len(des) >= 2:
                pairs = self.matcher.knnMatch(refdes, des, k=2)
                good = [p[0] for p in pairs if len(p)==2 and p[0].distance < .70*p[1].distance]
                # A query image feature counts only once towards the geometric evidence.
                unique = {}
                for m in sorted(good, key=lambda m:m.distance):
                    unique.setdefault(m.trainIdx, m)
                good = list(unique.values())
                out['good_matches'] = len(good)
                if len(good) >= 12:
                    src = np.float32([refkp[m.queryIdx].pt for m in good]).reshape(-1,1,2)
                    dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1,1,2)
                    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.)
                    if H is not None and mask is not None:
                        kept = mask.ravel().astype(bool)
                        n = int(kept.sum()); ratio = n/len(good)
                        residual = np.linalg.norm(cv2.perspectiveTransform(src,H)-dst,axis=2).ravel()[kept]
                        area = float(cv2.contourArea(cv2.convexHull(dst[kept]))) if n>=3 else 0.
                        projected = cv2.perspectiveTransform(polygon.reshape(-1,1,2),H).reshape(-1,2)
                        median = float(np.median(residual)) if n else 999.
                        finite = bool(np.isfinite(projected).all())
                        convex = bool(cv2.isContourConvex(projected)) if finite else False
                        projected_area = float(cv2.contourArea(projected)) if finite else 0.
                        out.update(inliers=n, inlier_ratio=ratio, reprojection_median_px=median,
                                   inlier_hull_area_px2=area, projected_area_px2=projected_area,
                                   projected_convex=convex)
                        if n>=12 and ratio>=.45 and median<=3 and area>=300 and finite and convex and 300<=projected_area<=307200:
                            out.update(status='KNOWN_LAPTOP_MATCHED', matched=True,
                                polygon_px=projected.tolist(),
                                observed_feature_center_px=np.median(dst[kept].reshape(-1,2),axis=0).tolist())
            candidates.append(out)
        best = max(candidates, key=lambda x:(x['matched'],x['inliers']))
        result = dict(best)
        result.update(target='MANUALLY_LABELLED_SOURCE_LAPTOP', plane_results=candidates,
            processing_seconds=time.monotonic()-start,
            scope='CURRENT_LABELLED_LAPTOP_ONLY_NOT_GENERIC_SOURCE_DETECTION',
            method='ORB_TWO_LABELLED_VIEWS_REFERENCE400_QUERY1200')
        return result
