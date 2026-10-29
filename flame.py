"""The Cindrix flame: smooth outline points used by the app banner, the website header and the ZIM icon."""

# Cubic Bezier segments in a 0..1 box: (start, c1, c2, end). One tongue leaning right with a curl on the left.
OUTER = [((0.56, 0.00), (0.60, 0.20), (0.98, 0.36), (0.94, 0.68)),
         ((0.94, 0.68), (0.92, 0.88), (0.74, 1.00), (0.50, 1.00)),
         ((0.50, 1.00), (0.24, 1.00), (0.04, 0.86), (0.07, 0.62)),
         ((0.07, 0.62), (0.09, 0.46), (0.20, 0.38), (0.30, 0.30)),
         ((0.30, 0.30), (0.30, 0.40), (0.34, 0.46), (0.40, 0.48)),
         ((0.40, 0.48), (0.30, 0.30), (0.46, 0.10), (0.56, 0.00))]
INNER = [((0.52, 0.50), (0.60, 0.60), (0.78, 0.68), (0.72, 0.84)),
         ((0.72, 0.84), (0.68, 0.95), (0.58, 0.98), (0.50, 0.98)),
         ((0.50, 0.98), (0.40, 0.98), (0.30, 0.92), (0.30, 0.82)),
         ((0.30, 0.82), (0.30, 0.68), (0.46, 0.62), (0.52, 0.50))]


def scaled(segs, k, ox=0.5, oy=1.0):
    """The same shape shrunk by k about the point (ox, oy): used for the middle layer of the flame."""
    def p(pt):
        return (ox + (pt[0] - ox) * k, oy + (pt[1] - oy) * k)
    return [tuple(p(pt) for pt in seg) for seg in segs]


MIDDLE = scaled(OUTER, 0.72)
# back to front: deep orange body, amber middle, pale core
LAYERS = [(OUTER, "#c9780f"), (MIDDLE, "#e9a82e"), (INNER, "#ffe08a")]


def _bez(seg, n):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = seg
    pts = []
    for i in range(n):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
                    u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3))
    return pts


def outline(segs, x, y, w, h, n=14):
    """Flat [x1, y1, x2, y2, ...] polygon for the flame placed in the box (x, y, w, h)."""
    out = []
    for seg in segs:
        for px, py in _bez(seg, n):
            out += [round(x + px * w, 2), round(y + py * h, 2)]
    return out


def svg(w=40, h=50):
    def path(segs):
        p = f"M{segs[0][0][0] * w:.1f} {segs[0][0][1] * h:.1f}"
        for (_, c1, c2, e) in segs:
            p += f"C{c1[0] * w:.1f} {c1[1] * h:.1f} {c2[0] * w:.1f} {c2[1] * h:.1f} {e[0] * w:.1f} {e[1] * h:.1f}"
        return p + "Z"
    paths = "".join(f'<path d="{path(segs)}" fill="{col}"/>' for segs, col in LAYERS)
    return f'<svg class="flame" viewBox="0 0 {w} {h}" aria-hidden="true">{paths}</svg>'
