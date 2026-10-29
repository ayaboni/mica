"""Builds core/static/core/images/chat-doodles.png - the doodle wallpaper tile for the student chat.

The tile is a transparent PNG of black strokes (only the alpha channel matters). The page tints it with
CSS (see .chat-stage::before in student-dashboard.html), so one file serves light and dark mode.
It is seamless: it can repeat in both directions.

Uses the four hand-drawn doodles in ./src (pencil, flower, book, heart) plus extra symbols and
affirmations drawn here in the same monoline style.   Run:  python make_chat_doodles.py
"""
import math, random, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.morphology import skeletonize
import cv2

T = 1200            # tile size in pixels (shown at 600 CSS px, so it is sharp on 2x screens)
STROKE = 4.4        # line weight, in tile pixels
SS = 4              # supersampling for drawn strokes
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 7
OUT = sys.argv[2] if len(sys.argv) > 2 else 'chat-doodles.png'
rng = random.Random(SEED)

# ---------------------------------------------------------------- stroke helpers
def spline(pts, closed=False, n=10):
    P = [np.array(p, float) for p in pts]
    if closed:
        P = [P[-1]] + P + [P[0], P[1]]
    else:
        P = [P[0]] + P + [P[-1]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0, 1, n, endpoint=False):
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(out[0] if closed else P[-2])
    return out

def wob(pts, j):
    """Hand-drawn wobble: nudge every control point a little."""
    return [(x + rng.uniform(-j, j), y + rng.uniform(-j, j)) for x, y in pts]

def arc(cx, cy, r, a0, a1, n=28, j=0.0):
    pts = []
    for k in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * k / n)
        rr = r + (rng.uniform(-j, j) if j else 0)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts

def circle(cx, cy, r, j=1.2, gap=14):
    """Almost-closed circle: the ends overlap a touch, like a quick pen stroke."""
    a0 = rng.uniform(0, 360)
    return spline(wob(arc(cx, cy, r, a0, a0 + 360 - gap + 24, n=10), j))

def line(*pts, j=0.8, closed=False):
    return spline(wob(list(pts), j), closed=closed)

def render(strokes, width=STROKE, pad=14):
    """Draw polylines (in any coordinate space) on a tight transparent canvas -> float alpha array."""
    allp = np.array([p for s in strokes for p in s])
    x0, y0 = allp.min(0) - pad - width; x1, y1 = allp.max(0) + pad + width
    W, H = int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0))
    im = Image.new('L', (W * SS, H * SS), 0)
    d = ImageDraw.Draw(im)
    w = max(1, int(round(width * SS)))
    for s in strokes:
        pts = [((x - x0) * SS, (y - y0) * SS) for x, y in s]
        d.line(pts, fill=255, width=w, joint='curve')
        r = w / 2
        for (x, y) in (pts[0], pts[-1]):
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    im = im.resize((W, H), Image.LANCZOS)
    return np.asarray(im).astype(float) / 255

# ---------------------------------------------------------------- the hand-drawn doodles you supplied
def load_doodle(name, height):
    a = np.asarray(Image.open(f'src/{name}.png').convert('RGB')).astype(float)
    alpha = np.clip((255 - a @ [0.299, 0.587, 0.114]) / 255, 0, 1)
    alpha = np.where(alpha > 0.08, alpha, 0)
    lab, n = ndi.label(alpha > 0.35, structure=np.ones((3, 3)))
    areas = ndi.sum(alpha > 0.35, lab, range(1, n + 1))
    for i, ar in enumerate(areas, 1):
        if ar < 30:                          # the stray cursor circles in the uploads
            alpha[lab == i] = 0
    ys, xs = np.where(alpha > 0)
    alpha = alpha[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    scale = height / alpha.shape[0]
    h, w = int(round(alpha.shape[0] * scale)), int(round(alpha.shape[1] * scale))
    out = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_AREA)
    # keep the line weight close to the drawn symbols (shrinking thins strokes)
    mask = out > 0.5
    sk = skeletonize(mask)
    cur = 2 * ndi.distance_transform_edt(mask)[sk].mean() if sk.any() else STROKE
    if cur < STROKE - 0.4:
        k = int(round((STROKE - cur) / 2))
        if k:
            out = cv2.dilate(out, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    pad = 8
    return np.pad(out, pad)

# ---------------------------------------------------------------- extra symbols (nominal size ~100 units)
def moon():
    o = [(40 * math.cos(math.radians(a)), 40 * math.sin(math.radians(a))) for a in np.linspace(50, 310, 16)]
    i = [(14 + 32.8 * math.cos(math.radians(a)), 32.8 * math.sin(math.radians(a))) for a in np.linspace(-69, -291, 14)]
    return [spline(wob(o + i, 0.9), closed=True)]

def sun():
    s = [circle(0, 0, 21, j=1)]
    for k in range(9):
        a = math.radians(k * 40 + rng.uniform(-6, 6)); r0, r1 = 33, 33 + rng.uniform(10, 16)
        s.append([(r0 * math.cos(a), r0 * math.sin(a)), (r1 * math.cos(a), r1 * math.sin(a))])
    return s

def cloud():
    C = [(-30, 6, 15), (-9, -9, 21), (16, -4, 17), (34, 8, 12)]
    base = 21
    strokes = []
    for i, (cx, cy, r) in enumerate(C):
        keep, cur = [], []
        for a in np.linspace(0, 360, 181):
            x, y = cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))
            inside = any(math.hypot(x - ox, y - oy) < orr - 0.8 for j, (ox, oy, orr) in enumerate(C) if j != i)
            if y > base or inside:
                if cur: keep.append(cur); cur = []
            else:
                cur.append((x, y))
        if cur: keep.append(cur)
        if len(keep) > 1 and keep[0][0] == keep[-1][-1]:
            pass
        strokes += [wob(k, 0.4) for k in keep if len(k) > 3]
    strokes.append(line((-45, base), (-20, base + 1), (10, base), (46, base + 1), j=0.7))
    return strokes

def sparkle(r=28):
    return [spline(wob([(0, -r), (r * .16, -r * .16), (r, 0), (r * .16, r * .16), (0, r), (-r * .16, r * .16),
                        (-r, 0), (-r * .16, -r * .16)], 0.7), closed=True, n=8)]

def star(r=26):
    pts = []
    for k in range(10):
        a = math.radians(-90 + k * 36); rr = r if k % 2 == 0 else r * .46
        pts.append((rr * math.cos(a), rr * math.sin(a)))
    return [spline(wob(pts, 0.8), closed=True, n=6)]

def leafshape(L, W):
    return spline(wob([(0, 0), (L * .3, -W), (L * .75, -W * .8), (L, 0), (L * .7, W * .75), (L * .3, W * .85)], 0.6), closed=True, n=8)

def sprout():
    s = [line((0, 44), (2, 24), (-1, 6), (0, -8))]
    l1 = leafshape(34, 11); l2 = leafshape(30, 10)
    ca, sa = math.cos(math.radians(-35)), math.sin(math.radians(-35))
    s.append([(0 + x * ca - y * sa, -8 + x * sa + y * ca) for x, y in l1])
    ca, sa = math.cos(math.radians(215)), math.sin(math.radians(215))
    s.append([(0 + x * ca - y * sa, 2 + x * sa + y * ca) for x, y in [(x, y) for x, y in l2]])
    s.append(line((-26, 46), (26, 46), j=0.5))
    return s

def sprig():
    s = [line((0, 50), (4, 22), (0, -6), (-3, -34))]
    for k, (yy, ang, L) in enumerate([(30, -50, 26), (14, 230, 26), (-4, -50, 24), (-18, 230, 22)]):
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        s.append([(x * ca - y * sa, yy + x * sa + y * ca) for x, y in leafshape(L, 8)])
    return s

def butterfly():
    def mirror(p): return [(-x, y) for x, y in p]
    up = spline(wob([(0, -4), (16, -30), (40, -26), (42, -4), (16, 4)], 1), closed=True, n=8)
    lo = spline(wob([(0, 6), (20, 8), (32, 24), (24, 40), (8, 28)], 1), closed=True, n=8)
    return [up, mirror(up), lo, mirror(lo), line((0, -16), (0, 30), j=0.4),
            line((0, -16), (-8, -30), (-14, -32), j=0.4), line((0, -16), (8, -30), (14, -32), j=0.4)]

def smiley(calm=False):
    s = [circle(0, 0, 27, j=1.0)]
    if calm:
        s += [spline([(-15, -4), (-10, -9), (-5, -4)]), spline([(5, -4), (10, -9), (15, -4)])]
    else:
        s += [line((-10, -9), (-10, -3), j=0.2), line((10, -9), (10, -3), j=0.2)]
    s.append(spline(wob([(-14, 6), (-6, 14), (6, 14), (14, 6)], 0.8)))
    return s

def teacup():
    body = spline(wob([(-26, -4), (26, -4), (22, 14), (12, 26), (-12, 26), (-22, 14)], 0.7), closed=True, n=8)
    handle = spline([(26, 2), (40, 2), (40, 16), (24, 17)])
    saucer = line((-36, 32), (0, 35), (36, 32), j=0.4)
    steam = [spline(wob([(x, -12), (x + 6, -22), (x - 5, -32), (x + 4, -42)], 0.6)) for x in (-10, 6)]
    return [body, handle, saucer] + steam

def cactus():
    pot = spline(wob([(-18, 20), (18, 20), (14, 46), (-14, 46)], 0.6), closed=True, n=5)
    body = spline(wob([(-9, 20), (-10, -14), (0, -24), (10, -14), (9, 20)], 0.5), n=8)
    armL = spline([(-9, 6), (-22, 6), (-24, -8)]); armR = spline([(9, -2), (22, -2), (23, -16)])
    return [pot, body, armL, armR, line((-22, 20), (22, 20), j=0.3)]

def lotus():
    mid = spline(wob([(0, 20), (-12, -2), (0, -30), (12, -2)], 0.7), closed=True, n=8)
    left = spline(wob([(-4, 20), (-26, 0), (-38, -12), (-14, 4)], 0.7), closed=True, n=8)
    right = spline(wob([(4, 20), (26, 0), (38, -12), (14, 4)], 0.7), closed=True, n=8)
    return [mid, left, right, line((-34, 28), (-10, 33), (10, 33), (34, 28), j=0.5)]

def rainbow():
    s = []
    for r in (44, 32, 20):
        s.append(spline(wob(arc(0, 20, r, 180, 360, n=8), 0.6)))
    s.append(spline([(-52, 22), (-44, 22), (-36, 22)])); s.append(spline([(36, 22), (44, 22), (52, 22)]))
    return s

def spiral():
    pts = [(2.1 * t * math.cos(t), 2.1 * t * math.sin(t)) for t in np.linspace(0.4, 4.6 * math.pi / 1.2, 40)]
    return [spline(wob(pts, 0.5), n=4)]

def squiggle(length=150, amp=7, periods=2.5):
    n = 40
    pts = [(-length / 2 + length * k / n, amp * math.sin(2 * math.pi * periods * k / n)) for k in range(n + 1)]
    return [spline(wob(pts, 0.5), n=3)]

def plus(r=7):
    return [line((-r, 0), (r, 0), j=0.3), line((0, -r), (0, r), j=0.3)]

def dot(r=6):
    return [circle(0, 0, r, j=0.3, gap=2)]

# ---------------------------------------------------------------- hand lettering (single-stroke)
G = {  # x right, y down; baseline 0, x-height -1, ascender -1.75
 'a': ([[(.75,-.7),(.45,-1),(.12,-.75),(.1,-.25),(.4,0),(.75,-.3)], [(.78,-1),(.78,.05)]], .95),
 'b': ([[(.05,-1.75),(.05,0)], [(.05,-.4),(.3,-.95),(.65,-.8),(.75,-.4),(.55,-.02),(.2,-.1),(.05,-.3)]], .9),
 'd': ([[(.75,-.7),(.45,-1),(.12,-.75),(.1,-.25),(.4,0),(.75,-.3)], [(.78,-1.75),(.78,.05)]], .95),
 'e': ([[(.1,-.5),(.7,-.55),(.65,-.85),(.4,-1),(.12,-.75),(.08,-.3),(.35,0),(.7,-.15)]], .85),
 'f': ([[(.65,-1.65),(.4,-1.8),(.25,-1.5),(.25,.05)], [(0,-1),(.6,-1)]], .7),
 'g': ([[(.75,-.7),(.45,-1),(.12,-.75),(.1,-.25),(.4,0),(.75,-.3)], [(.78,-1),(.78,.5),(.55,.78),(.2,.68)]], .95),
 'h': ([[(.05,-1.75),(.05,0)], [(.05,-.6),(.3,-1),(.6,-.9),(.7,-.5),(.7,0)]], .85),
 'i': ([[(.1,-1),(.1,0)], [(.1,-1.4),(.1,-1.36)]], .3),
 'k': ([[(.05,-1.75),(.05,0)], [(.65,-1),(.05,-.4)], [(.2,-.55),(.7,0)]], .8),
 'l': ([[(.1,-1.75),(.1,-.1),(.3,0)]], .45),
 'm': ([[(.05,-1),(.05,0)], [(.05,-.6),(.2,-1),(.45,-.9),(.5,-.5),(.5,0)], [(.5,-.6),(.65,-1),(.9,-.9),(.95,-.5),(.95,0)]], 1.15),
 'n': ([[(.05,-1),(.05,0)], [(.05,-.6),(.3,-1),(.6,-.9),(.7,-.5),(.7,0)]], .85),
 'o': ([[(.4,-1),(.12,-.75),(.1,-.25),(.4,0),(.7,-.25),(.7,-.75),(.4,-1)]], .85),
 'r': ([[(.05,-1),(.05,0)], [(.05,-.55),(.25,-.95),(.5,-.95)]], .6),
 's': ([[(.6,-.85),(.35,-1),(.12,-.8),(.35,-.5),(.62,-.25),(.4,0),(.08,-.15)]], .75),
 't': ([[(.3,-1.5),(.3,-.1),(.5,.03)], [(.05,-1),(.6,-1)]], .7),
 'u': ([[(.05,-1),(.05,-.3),(.3,0),(.65,-.3)], [(.7,-1),(.7,0)]], .85),
 'v': ([[(.05,-1),(.4,0),(.75,-1)]], .85),
 'y': ([[(.05,-1),(.4,0)], [(.75,-1),(.4,0),(.2,.6),(.05,.65)]], .85),
 'I': ([[(.15,-1.7),(.15,0)]], .4),
 "'": ([[(.1,-1.6),(.05,-1.3)]], .3),
}

def lettering(text, xh=27, wobble=1.6, wave=2.2):
    strokes, x = [], 0.0
    for ch in text:
        if ch == ' ':
            x += .55; continue
        glyph, w = G[ch]
        for st in glyph:
            if len(st) == 2 and st[0] == st[1]:
                continue
            pts = [((x + gx) * xh, gy * xh) for gx, gy in st]
            pts = wob(pts, wobble)
            strokes.append(spline(pts, n=8) if len(pts) > 2 else spline(pts, n=2))
        x += w + .28
    # gentle baseline wave
    out = []
    for s in strokes:
        out.append([(px, py + wave * math.sin(px / (xh * 3.2))) for px, py in s])
    return out

# ---------------------------------------------------------------- assemble the tile
def rotate(a, deg):
    h, w = a.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1)
    c, s = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * s + w * c), int(h * c + w * s)
    M[0, 2] += nw / 2 - w / 2; M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(a, M, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=0).clip(0, 1)

def fit(strokes, size, rot):
    """Render drawn strokes so their longest side is ~size px, then rotate."""
    allp = np.array([p for s in strokes for p in s]); span = (allp.max(0) - allp.min(0)).max()
    k = size / span
    scaled = [[(x * k, y * k) for x, y in s] for s in strokes]
    return rotate(render(scaled), rot)

def D(name, h, rot): return rotate(load_doodle(name, h), rot)

items = [  # (artwork, extra spacing)
    D('flower', 150, -8), D('flower', 105, 14), D('book', 150, 6), D('book', 104, -14),
    D('pencil', 160, -4), D('pencil', 112, 30), D('heart', 92, -10), D('heart', 66, 12), D('heart', 52, -22),
    fit(moon(), 96, 20), fit(sun(), 112, 0), fit(cloud(), 118, -4), fit(rainbow(), 118, 6),
    fit(sparkle(), 70, 0), fit(sparkle(), 52, 12), fit(sparkle(), 40, 0), fit(star(), 58, 14), fit(star(), 42, -12),
    fit(sprout(), 108, -8), fit(sprig(), 118, 12), fit(butterfly(), 98, -12), fit(smiley(), 82, 8),
    fit(smiley(True), 70, -10), fit(teacup(), 100, -6), fit(cactus(), 108, 4), fit(lotus(), 100, 0),
    fit(spiral(), 66, 0), fit(squiggle(), 170, 28), fit(squiggle(120, 6, 2), 130, -52),
    fit(lettering('I am loved'), 238, -7), fit(lettering('breathe'), 170, 5),
    fit(lettering('you matter'), 224, -4), fit(lettering("it's okay to rest"), 330, 3),
    fit(lettering('I am enough'), 262, 6),
    fit(plus(), 22, 0), fit(plus(), 20, 25), fit(plus(), 18, 0), fit(dot(), 16, 0), fit(dot(), 14, 0), fit(dot(), 18, 0),
]

def radius(a):
    return 0.5 * math.hypot(*a.shape) * 0.62

placed = []
order = sorted(range(len(items)), key=lambda i: -items[i].size)
canvas = np.zeros((T, T))

def wdist(x1, y1, x2, y2):
    dx, dy = abs(x1 - x2), abs(y1 - y2)
    return math.hypot(min(dx, T - dx), min(dy, T - dy))

def paste_wrap(c, a, cx, cy):
    h, w = a.shape
    for ox in (-T, 0, T):
        for oy in (-T, 0, T):
            x0, y0 = int(round(cx - w / 2)) + ox, int(round(cy - h / 2)) + oy
            xs, ys = max(0, x0), max(0, y0); xe, ye = min(T, x0 + w), min(T, y0 + h)
            if xe <= xs or ye <= ys:
                continue
            sub = a[ys - y0:ye - y0, xs - x0:xe - x0]
            c[ys:ye, xs:xe] = np.maximum(c[ys:ye, xs:xe], sub)

for i in order:
    a, r = items[i], radius(items[i])
    best = None
    for gap in (22, 14, 8, 2, -6):
        for _ in range(1600):
            x, y = rng.uniform(0, T), rng.uniform(0, T)
            if all(wdist(x, y, px, py) >= r + pr + gap for px, py, pr in placed):
                best = (x, y); break
        if best: break
    if not best:
        print('could not place item', i, a.shape); continue
    placed.append((best[0], best[1], r)); paste_wrap(canvas, a, *best)

alpha = (canvas * 255).clip(0, 255).astype(np.uint8)
out = np.zeros((T, T, 4), np.uint8); out[..., 3] = alpha          # black strokes, alpha only
Image.fromarray(out, 'RGBA').save(OUT, optimize=True)
print('placed', len(placed), 'of', len(items), '->', OUT, 'coverage', round(float((canvas > 0.3).mean()) * 100, 1), '%')
