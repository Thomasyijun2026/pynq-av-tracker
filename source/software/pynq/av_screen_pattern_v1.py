"""Fixed sound-mark for the known PC display. No calibration or angle claim.

The dark border and unequal five bars are functional artwork, not a semantic
classifier. Animation and text must remain outside the 11 x 11 marker square.
"""
PATTERN_ID = 'PYNQ_SOUND_MARK_V1'
HEIGHTS = (3, 6, 9, 5, 2)
SIZE = 11


def cells():
    a = [[int(x in (0, 10) or y in (0, 10)) for x in range(SIZE)] for y in range(SIZE)]
    for k, height in enumerate(HEIGHTS):
        for y in range(10 - height, 10):
            a[y][1 + k * 2] = 1
    return a


def svg():
    rects = ''.join('<rect x="%d" y="%d" width="1" height="1"/>' % (x+1,y+1)
                    for y,row in enumerate(cells()) for x,v in enumerate(row) if v)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 13 13" '
            'shape-rendering="crispEdges" role="img" aria-label="声音定位识别图案">'
            '<rect width="13" height="13" fill="#fff"/><g fill="#10231f">'+rects+'</g></svg>')
