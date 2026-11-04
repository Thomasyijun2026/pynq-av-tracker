"""Board-only known-display marker detector; accepts no expected angle.

V5 refines contour corners on full-resolution luminance, with a four-pixel bound.
V4 finds contours at width <=640 and rescales corners to original pixels.
All cell tests use the full-resolution original grayscale image.
V3 computes the identical 121 cell medians in a single NumPy operation.
V2 enumerates global Otsu plus fixed mid/dark luminance segmentations.
All original area/side/border/cell/contrast/rotation gates are unchanged.
Checks convex quad, contrast, border, cell signature and rotation separation.
Score is a pattern agreement/contrast measure, never a semantic probability.
"""
import time
import cv2
import numpy as np
from av_screen_pattern_V1 import cells, PATTERN_ID


class ScreenDetector:
    def __init__(self):
        self.signature = np.array(cells(), np.uint8)
        self.border = np.zeros((11, 11), bool)
        self.border[0, :] = self.border[-1, :] = True
        self.border[:, 0] = self.border[:, -1] = True

    def infer(self, raw, upright_rotation=180):
        start = time.monotonic()
        if raw is None or raw.ndim != 3 or raw.shape[2] != 3:
            raise ValueError('BGR image required')
        if upright_rotation not in (0, 180):
            raise ValueError('Explicit supported image orientation required')
        img = cv2.rotate(raw, cv2.ROTATE_180) if upright_rotation == 180 else raw
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        scale=min(1.,640./gray.shape[1])
        small=cv2.resize(gray,(round(gray.shape[1]*scale),round(gray.shape[0]*scale)),interpolation=cv2.INTER_AREA) if scale<1 else gray
        methods=[('otsu', cv2.threshold(small,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1])]
        methods += [('level_%d'%v,cv2.threshold(small,v,255,cv2.THRESH_BINARY_INV)[1]) for v in (128,192)]
        matches=[];checked=0
        for segmentation,bw in methods:
            contours=cv2.findContours(bw,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[-2]
            for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:80]:
                area = cv2.contourArea(contour)/(scale*scale)
                if area < 3600 or area > .85 * gray.size:
                    continue
                q = cv2.approxPolyDP(contour, .025*cv2.arcLength(contour, True), True)
                if len(q) != 4 or not cv2.isContourConvex(q):
                    continue
                p = q.reshape(4,2).astype(np.float32)/scale
                center = p.mean(axis=0)
                p = p[np.argsort(np.arctan2(p[:,1]-center[1],p[:,0]-center[0]))]
                p = np.roll(p, -int(np.argmin(p.sum(axis=1))), axis=0)
                # Downsampled integer corners can put thin border samples
                # outside the printed black border. Refine on original pixels.
                refined=cv2.cornerSubPix(gray,p.reshape(-1,1,2).copy(),(4,4),(-1,-1),
                    (cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_MAX_ITER,20,.01)).reshape(4,2)
                if (np.max(np.linalg.norm(refined-p,axis=1))<=4 and
                        cv2.isContourConvex(refined.reshape(-1,1,2)) and
                        cv2.contourArea(refined)>=3600):
                    p=refined
                center=p.mean(axis=0)
                sides = np.linalg.norm(p-np.roll(p,1,axis=0),axis=1)
                if min(sides) < 55 or max(sides)/min(sides)>3:
                    continue
                checked += 1
                h = cv2.getPerspectiveTransform(p, np.float32([[0,0],[219,0],[219,219],[0,219]]))
                patch = cv2.warpPerspective(gray, h, (220,220))
                values = np.median(patch.reshape(11,20,11,20)[:,6:14,:,6:14], axis=(1,3))
                low, high = np.percentile(values, [15, 85])
                if high-low < 45:
                    continue
                bits = (values < (low+high)/2).astype(np.uint8)
                if np.count_nonzero(bits[self.border] != 1)>1:
                    continue
                distances = [int(np.count_nonzero(np.rot90(bits,k)[1:-1,1:-1] != self.signature[1:-1,1:-1])) for k in range(4)]
                order = np.argsort(distances); rotation = int(order[0]); errors = distances[rotation]
                if errors > 2 or distances[int(order[1])]-errors < 12:
                    continue
                # The center half of each cell must agree, not just one noisy pixel.
                aligned = np.rot90(values,rotation)
                expected_dark = aligned[self.signature.astype(bool)]
                expected_light = aligned[~self.signature.astype(bool)]
                contrast = float(np.median(expected_light)-np.median(expected_dark))
                if contrast < 45:
                    continue
                x0,y0=p.min(axis=0);x1,y1=p.max(axis=0)
                item=dict(segmentation=segmentation,pattern_id=PATTERN_ID,matched=True,reference_id=PATTERN_ID,label='known_pc_screen',
                          score=(1-errors/81)*min(1,contrast/160),cell_errors=errors,
                          rotation_quarters=rotation,contrast=contrast,quad_upright=p.tolist(),
                          center_px=center.tolist(),box_xyxy=[float(x0),float(y0),float(x1),float(y1)])
                if not any(np.linalg.norm(center-np.array(o['center_px']))<15 for o in matches):
                    matches.append(item)
        best=max(matches,key=lambda o:o['score']) if matches else None
        return dict(status='KNOWN_SCREEN_MATCHED' if best else 'NO_MATCH',best=best,objects=matches,
                    checked_quads=checked,processing_seconds=time.monotonic()-start,
                    completed_monotonic=time.monotonic(),image_rotation=upright_rotation,
                    image_size=[img.shape[1],img.shape[0]],score_kind='SCREEN_PATTERN_AGREEMENT',
                    semantic_probability=False,acoustic_identity_confirmed=False)

    def propose(self, raw):
        found=self.infer(raw)
        return [dict(box_upright=o['box_xyxy'],proposal_score=o['score'],pattern_id=PATTERN_ID)
                for o in found['objects']]
