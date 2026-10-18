"""Dibujo de los luchadores: anatomía, rostro, pelo y vestuario.

Cada figura se construye como una lista de piezas (tubos musculados, polígonos, círculos y trazos)
en centímetros locales, agrupadas por profundidad:

    0 brazo trasero · 1 pierna trasera · 2 torso · 3 pierna delantera · 4 ropa del torso
    5 cuello y cabeza · 6 brazo delantero

Cada grupo pinta primero su contorno y después su relleno, así que el brazo delantero queda
recortado sobre el pecho y el trasero se esconde detrás. La figura se dibuja con supermuestreo
(2× por defecto) y se reduce con filtrado: bordes suaves, sin dientes de sierra.
"""
import math
from typing import Dict, List, Tuple

import pygame

from .anatomy import BODIES, LOOKS, SPINE_T
from .camera import Camera
from .config import *
from .fighter import Fighter
from .poses import fighter_pose, stance as _stance

LIGHT = (-0.72, -0.69)          # dirección de la luz en pantalla: de los shōji, arriba a la izquierda
OUTLINE = (16, 11, 9)


# ─────────────────────────────────────────────────────────────────────────────
#  PRIMITIVAS (se mantienen para efectos y compatibilidad)
# ─────────────────────────────────────────────────────────────────────────────
def draw_capsule(surf, c, a, b, r):
    if r < 1:
        r = 1
    pygame.draw.line(surf, c, a, b, int(r * 2))
    pygame.draw.circle(surf, c, (int(a[0]), int(a[1])), int(r))
    pygame.draw.circle(surf, c, (int(b[0]), int(b[1])), int(r))


def _norm(x, y):
    ln = math.hypot(x, y)
    if ln < 1e-9:
        return 0.0, 0.0
    return x / ln, y / ln


def _rot(x, y, a):
    ca, sa = math.cos(a), math.sin(a)
    return x * ca - y * sa, x * sa + y * ca


def _lerp2(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def tube_pts(a, b, ra, rb, bulge=0.0, bt=0.45, side=0.0, grow=0.0, k=1.0, off=(0.0, 0.0), n=7):
    """Contorno de un miembro que se estrecha de ra a rb con un abultamiento muscular.

    side: +1 abulta hacia el lado izquierdo del eje (a→b), -1 hacia el derecho, 0 simétrico.
    """
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    if L < 1e-6:
        ux, uy = 0.0, 1.0
    else:
        ux, uy = dx / L, dy / L
    px, py = -uy, ux
    left, right = [], []
    for i in range(n):
        t = i / (n - 1)
        r = ra + (rb - ra) * t
        s = (t - bt + 0.4) / 0.8
        bump = bulge * math.sin(math.pi * s) if 0.0 < s < 1.0 else 0.0
        rl = (r + bump * (1 + side) * 0.5) * k + grow
        rr = (r + bump * (1 - side) * 0.5) * k + grow
        cx, cy = ax + dx * t + off[0], ay + dy * t + off[1]
        left.append((cx + px * rl, cy + py * rl))
        right.append((cx - px * rr, cy - py * rr))
    re_, rs = rb * k + grow, ra * k + grow
    capb, capa = [], []
    for j in range(1, 5):
        th = math.pi * j / 5
        c, s = math.cos(th), math.sin(th)
        capb.append((bx + off[0] + (px * c + ux * s) * re_, by + off[1] + (py * c + uy * s) * re_))
        capa.append((ax + off[0] - (px * c + ux * s) * rs, ay + off[1] - (py * c + uy * s) * rs))
    return left + capb + right[::-1] + capa


def poly_area(p):
    return sum(p[i - 1][0] * p[i][1] - p[i][0] * p[i - 1][1] for i in range(len(p))) * 0.5


def offset_poly(pts, d):
    """Desplaza un polígono hacia fuera (d>0) o hacia dentro (d<0) a lo largo de las normales."""
    n = len(pts)
    if n < 3 or d == 0:
        return pts
    s = 1.0 if poly_area(pts) > 0 else -1.0
    out = []
    for i in range(n):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        e1 = _norm(x1 - x0, y1 - y0)
        e2 = _norm(x2 - x1, y2 - y1)
        n1 = (e1[1], -e1[0])
        n2 = (e2[1], -e2[0])
        nx, ny = n1[0] + n2[0], n1[1] + n2[1]
        ln = math.hypot(nx, ny)
        if ln < 1e-6:
            nx, ny = n1
        else:
            nx, ny = nx / ln, ny / ln
        m = 1.0 / max(0.45, nx * n1[0] + ny * n1[1])
        out.append((x1 + nx * d * m * s, y1 + ny * d * m * s))
    return out


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ─────────────────────────────────────────────────────────────────────────────
#  CURVAS: splines, suavizado, cintas, pinceladas y mechones
# ─────────────────────────────────────────────────────────────────────────────
def catmull(pts, per=5):
    """Spline Catmull-Rom que pasa por todos los puntos (curvas suaves, sin aristas)."""
    if len(pts) < 3:
        if len(pts) == 2:
            return [_lerp2(pts[0], pts[1], i / per) for i in range(per + 1)]
        return list(pts)
    P = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(P) - 2):
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = P[i - 1], P[i], P[i + 1], P[i + 2]
        for j in range(per):
            t = j / per
            t2, t3 = t * t, t * t * t
            out.append((0.5 * (2 * x1 + (x2 - x0) * t + (2 * x0 - 5 * x1 + 4 * x2 - x3) * t2 + (3 * x1 - x0 - 3 * x2 + x3) * t3),
                        0.5 * (2 * y1 + (y2 - y0) * t + (2 * y0 - 5 * y1 + 4 * y2 - y3) * t2 + (3 * y1 - y0 - 3 * y2 + y3) * t3)))
    out.append(pts[-1])
    return out


def chaikin(pts, it=1, closed=True):
    """Suavizado de esquinas: cada iteración corta los vértices en 1/4 y 3/4."""
    for _ in range(it):
        n = len(pts)
        if n < 3:
            return pts
        out = []
        rng = range(n) if closed else range(n - 1)
        if not closed:
            out.append(pts[0])
        for a in rng:
            (x0, y0), (x1, y1) = pts[a], pts[(a + 1) % n]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        if not closed:
            out.append(pts[-1])
        pts = out
    return pts


def _smooth_keys(keys, u):
    """Interpola un perfil [(u, valor)] con transición en coseno (curvas musculares suaves)."""
    if u <= keys[0][0]:
        return keys[0][1]
    for i in range(1, len(keys)):
        if u <= keys[i][0]:
            u0, v0 = keys[i - 1]
            u1, v1 = keys[i]
            t = (u - u0) / max(1e-6, u1 - u0)
            t = (1 - math.cos(math.pi * t)) * 0.5
            return v0 + (v1 - v0) * t
    return keys[-1][1]


def ribbon_frame(C, n_per=6):
    """Línea central suavizada, longitud normalizada y normales izquierdas."""
    P = catmull(C, n_per)
    L = [0.0]
    for a in range(1, len(P)):
        L.append(L[-1] + math.hypot(P[a][0] - P[a - 1][0], P[a][1] - P[a - 1][1]))
    tot = L[-1] or 1.0
    U = [l / tot for l in L]
    Nn = []
    for a in range(len(P)):
        b0, b1 = P[max(0, a - 1)], P[min(len(P) - 1, a + 1)]
        tx, ty = _norm(b1[0] - b0[0], b1[1] - b0[1])
        if tx == 0 and ty == 0:
            tx, ty = 0.0, 1.0
        Nn.append((-ty, tx))
    return P, U, Nn


LIGHT_V = (-0.72, -0.69)


def _wgt(nx, ny, ow, weighted):
    """Peso de la tinta: más grueso en el lado que mira lejos de la luz."""
    if not weighted:
        return ow
    d = -(nx * LIGHT_V[0] + ny * LIGHT_V[1])
    return ow * (0.6 + 1.0 * clamp(d, 0.0, 1.0))


def ribbon_pts(frame, keys_l, keys_r, k, grow=0.0, kk=1.0, shift=0.0, weighted=False):
    """Contorno de una cinta de radio variable (miembro orgánico continuo) con tapas redondas."""
    P, U, Nn = frame
    if not isinstance(keys_l[0], (int, float)):          # perfil por claves → radios por vértice
        keys_l = [_smooth_keys(keys_l, u) for u in U]
        keys_r = [_smooth_keys(keys_r, u) for u in U]
    left, right = [], []
    rl_end = rr_end = rl0 = rr0 = 0.0
    kq = k * kk
    for (cx, cy), rl, rr, (nx, ny) in zip(P, keys_l, keys_r, Nn):
        rl *= kq
        rr *= kq
        if shift:
            d = nx * LIGHT_V[0] + ny * LIGHT_V[1]
            sx, sy = nx * d * shift * (rl + rr) * 0.5, ny * d * shift * (rl + rr) * 0.5
        else:
            sx = sy = 0.0
        gl = _wgt(nx, ny, grow, weighted) if grow else 0.0
        gr = _wgt(-nx, -ny, grow, weighted) if grow else 0.0
        left.append((cx + sx + nx * (rl + gl), cy + sy + ny * (rl + gl)))
        right.append((cx + sx - nx * (rr + gr), cy + sy - ny * (rr + gr)))
        rl_end, rr_end = rl + gl, rr + gr
        if len(left) == 1:
            rl0, rr0 = rl + gl, rr + gr
    # tapas: semicírculo en cada extremo
    def cap(c, n, r, sgn):
        out = []
        tx, ty = n[1], -n[0]               # tangente real: la tapa final avanza, la inicial retrocede
        for j in range(1, 5):
            th = math.pi * j / 5
            ca, sa = math.cos(th), math.sin(th)
            out.append((c[0] + (n[0] * ca + tx * sa) * r * sgn, c[1] + (n[1] * ca + ty * sa) * r * sgn))
        return out
    ce, ne = (left[-1][0] + right[-1][0]) * 0.5, (left[-1][1] + right[-1][1]) * 0.5
    cs, ns = (left[0][0] + right[0][0]) * 0.5, (left[0][1] + right[0][1]) * 0.5
    capb = cap((ce, ne), Nn[-1], (rl_end + rr_end) * 0.5, 1)
    capa = cap((cs, ns), Nn[0], (rl0 + rr0) * 0.5, -1)
    return left + capb + right[::-1] + capa


def brush_pts(pts, w, taper=True, per=4):
    """Pincelada: trazo suavizado que engorda en el centro y se afila en las puntas."""
    P = catmull(pts, per) if len(pts) > 2 else catmull(pts, per)
    if len(P) < 2:
        return []
    n = len(P)
    left, right = [], []
    for a in range(n):
        b0, b1 = P[max(0, a - 1)], P[min(n - 1, a + 1)]
        tx, ty = _norm(b1[0] - b0[0], b1[1] - b0[1])
        t = a / (n - 1)
        hw = w * 0.5 * ((0.2 + 0.8 * math.sin(math.pi * t) ** 0.6) if taper else 1.0)
        left.append((P[a][0] - ty * hw, P[a][1] + tx * hw))
        right.append((P[a][0] + ty * hw, P[a][1] - tx * hw))
    return left + right[::-1]


def lock_pts(base, ctrl, tip, w0):
    """Mechón de pelo: base ancha, curva por un punto de control, punta afilada."""
    P = catmull([base, ctrl, tip], 5)
    n = len(P)
    left, right = [], []
    for a in range(n):
        b0, b1 = P[max(0, a - 1)], P[min(n - 1, a + 1)]
        tx, ty = _norm(b1[0] - b0[0], b1[1] - b0[1])
        t = a / (n - 1)
        hw = w0 * 0.5 * (1 - t) ** 0.8
        left.append((P[a][0] - ty * hw, P[a][1] + tx * hw))
        right.append((P[a][0] + ty * hw, P[a][1] - tx * hw))
    return left + right[::-1]


# ─────────────────────────────────────────────────────────────────────────────
#  FIGURA: lista de piezas por grupo de profundidad
# ─────────────────────────────────────────────────────────────────────────────
class Figure:
    """Lista de piezas. Cada pieza recuerda su material (para la luz de la GPU) y si es un surco."""
    MATS = {"ink": 0, "skin": 1, "cloth": 2, "satin": 3, "leather": 4, "hair": 5, "eye": 6}
    # Tejidos diseñados (los pinta la GPU en el espacio propio de cada pieza, orientados a su eje)
    PATS = {"plain": 0, "judo": 1, "twill": 2, "canvas": 3, "knit": 4, "damask": 5, "linen": 6, "belt": 7,
            "satin": 8, "rib": 9, "leather": 10, "neoprene": 11, "hair": 12, "skin": 13, "stitch": 14,
            "cotton": 15, "belt_worn": 16}
    MAT_PAT = {"skin": "skin", "cloth": "cotton", "satin": "satin", "leather": "leather", "hair": "hair"}

    def __init__(self):
        self.parts: List[tuple] = []
        self.pats: List[int] = []
        self.mat = "cloth"
        self.pat = "cotton"
        self.groove = False
        self.zone_pat: dict = {}

    def zone(self, **pats):
        """Tejido por material para la zona que se dibuja (prenda de arriba, de abajo, cabeza...)."""
        self.zone_pat = pats

    def m(self, mat, groove=False, pat=None):
        self.mat = mat
        self.groove = groove
        self.pat = pat or self.zone_pat.get(mat) or self.MAT_PAT.get(mat, "plain")

    def _add(self, g, kind, geo, color, outline, shade_, sheen, edge, groove=False):
        self.parts.append((g, kind, geo, color, outline, shade_, sheen, edge, self.mat, groove))
        self.pats.append(self.PATS[self.pat])

    def tube(self, g, a, b, ra, rb, color, bulge=0.0, bt=0.45, side=0.0, outline=True, shade_=True, sheen=1.0, edge=False):
        self._add(g, "tube", (a, b, ra, rb, bulge, bt, side), color, outline, shade_, sheen, edge)

    def ribbon(self, g, C, keys_l, keys_r, color, outline=True, shade_=True, sheen=1.0, edge=False):
        """Miembro continuo por una spline: C son las articulaciones; keys_* perfiles [(u, radio)]."""
        self._add(g, "ribbon", (list(C), keys_l, keys_r), color, outline, shade_, sheen, edge)

    def poly(self, g, pts, color, outline=True, shade_=True, sheen=1.0, edge=False, smooth=1):
        if len(pts) >= 3:
            if smooth and len(pts) >= 4:
                pts = chaikin(pts, smooth)
            self._add(g, "poly", pts, color, outline, shade_, sheen, edge)

    def circle(self, g, c, r, color, outline=True, shade_=True, sheen=1.0, edge=False):
        self._add(g, "circle", (c, r), color, outline, shade_, sheen, edge)

    def line(self, g, pts, color, w, closed=False, taper=None):
        if taper is None:
            taper = w < 0.8 and not closed
        self._add(g, "line", (pts, w, closed, taper), color, False, False, 0.0, False, self.groove)

    def dot(self, g, c, r, color):
        self._add(g, "dot", (c, r), color, False, False, 0.0, False, self.groove)


def _geom(kind, geo, T, k, fac, cache, key):
    """Geometría en el lienzo de una pieza (cacheada por pasada)."""
    if kind == "ribbon":
        fr = cache.get(key)
        if fr is None:
            C, kl, kr = geo
            frame = ribbon_frame([T(q) for q in C])
            if fac != 1:
                kl, kr = kr, kl
            U = frame[1]
            # radios evaluados una vez por pasada: contorno, relleno, luces y mapa de datos los comparten
            fr = (frame, ([_smooth_keys(kl, u) for u in U], [_smooth_keys(kr, u) for u in U]))
            cache[key] = fr
        return fr
    return None


def _outline_part(surf, kind, geo, T, k, ow, ocol, fac, cache, key):
    """Contorno de peso variable. La geometría queda en la caché para que el mapa de datos la reutilice."""
    lx, ly = LIGHT
    sh = (-lx * ow * 0.35, -ly * ow * 0.35)      # tinta desplazada hacia la sombra: trazo de peso variable
    if kind == "circle":
        c, r = geo
        cc = T(c)
        o = ("c", (cc[0] + sh[0], cc[1] + sh[1]), r * k + ow * 0.8)
        pygame.draw.circle(surf, ocol, (int(o[1][0]), int(o[1][1])), int(o[2]))
        cache[("o", key)] = o
        return
    if kind == "tube":
        a, b, ra, rb, bu, bt, side = geo
        P = tube_pts(T(a), T(b), ra * k, rb * k, bu * k, bt, side * fac, grow=ow * 0.8, off=sh)
    elif kind == "ribbon":
        frame, (kl, kr) = _geom(kind, geo, T, k, fac, cache, key)
        P = ribbon_pts(frame, kl, kr, k, grow=ow, weighted=True)
    elif kind == "poly":
        P = _offset_weighted([T(q) for q in geo], ow)
    else:
        return
    pygame.draw.polygon(surf, ocol, P)
    cache[("o", key)] = ("p", P)


def _draw_cached_outline(surf, o, inv, col):
    if o[0] == "c":
        pygame.draw.circle(surf, col, (int(o[1][0] * inv), int(o[1][1] * inv)), max(1, int(o[2] * inv)))
    else:
        pygame.draw.polygon(surf, col, [(x * inv, y * inv) for x, y in o[1]])


def _offset_weighted(pts, ow):
    """Desplazamiento del contorno con peso variable según la orientación respecto a la luz."""
    n = len(pts)
    if n < 3:
        return pts
    s = 1.0 if poly_area(pts) > 0 else -1.0
    out = []
    for i in range(n):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        e1 = _norm(x1 - x0, y1 - y0)
        e2 = _norm(x2 - x1, y2 - y1)
        nx, ny = (e1[1] + e2[1]) * s, (-e1[0] - e2[0]) * s
        ln = math.hypot(nx, ny)
        if ln < 1e-6:
            nx, ny = e1[1] * s, -e1[0] * s
        else:
            nx, ny = nx / ln, ny / ln
        d = _wgt(nx, ny, ow, True)
        out.append((x1 + nx * d, y1 + ny * d))
    return out


def _render(fig: Figure, surf, T, k, ow, ocol, flat=None, ss=2, fac=1, baked=True, cache=None):
    """Pinta la figura en `surf`. T: local→lienzo; k: píxeles de lienzo por cm."""
    lx, ly = LIGHT
    groups: Dict[int, list] = {}
    for idx, p in enumerate(fig.parts):
        groups.setdefault(p[0], []).append((idx, p))
    edge_w = max(1, int(ss * 0.8))
    if cache is None:
        cache = {}
    for g in sorted(groups):
        ps = groups[g]
        # 1) contorno del grupo, con peso de pincel
        for idx, (_, kind, geo, col, outline, _s, _sh, _e, _m, _gr) in ps:
            if outline:
                _outline_part(surf, kind, geo, T, k, ow, ocol, fac, cache, idx)
        # 2) relleno (sombreado de tres tonos sólo en el renderer CPU)
        for idx, (_, kind, geo, col, outline, shd, sheen, edge, mat, groove) in ps:
            if flat is not None:
                col = flat
                shd = False
            if not baked:
                shd = False
                if groove:
                    continue          # en la GPU, músculos y pliegues son relieve, no trazo
            if kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                A, B = T(a), T(b)
                ra_, rb_, bu_ = ra * k, rb * k, bu * k
                sd = side * fac
                if shd and (ra_ + rb_) > 3:
                    ux, uy = _norm(B[0] - A[0], B[1] - A[1])
                    d = lx * ux + ly * uy
                    px, py = lx - d * ux, ly - d * uy
                    r = (ra_ + rb_) * 0.5
                    pygame.draw.polygon(surf, shade(col, 0.68), tube_pts(A, B, ra_, rb_, bu_, bt, sd))
                    pygame.draw.polygon(surf, col, tube_pts(A, B, ra_, rb_, bu_, bt, sd, k=0.8, off=(px * r * 0.2, py * r * 0.2)))
                    if sheen > 0:
                        pygame.draw.polygon(surf, shade(col, 1.1 + 0.08 * sheen), tube_pts(A, B, ra_, rb_, bu_, bt, sd, k=0.28, off=(px * r * 0.5, py * r * 0.5)))
                else:
                    Pf = tube_pts(A, B, ra_, rb_, bu_, bt, sd)
                    pygame.draw.polygon(surf, col, Pf)
                    cache[("f", idx)] = Pf
                if edge:
                    pygame.draw.polygon(surf, shade(col, 0.55), tube_pts(A, B, ra_, rb_, bu_, bt, sd), edge_w)
            elif kind == "ribbon":
                frame, (kl, kr) = _geom(kind, geo, T, k, fac, cache, idx)
                if shd:
                    pygame.draw.polygon(surf, shade(col, 0.68), ribbon_pts(frame, kl, kr, k))
                    pygame.draw.polygon(surf, col, ribbon_pts(frame, kl, kr, k, kk=0.8, shift=0.25))
                    if sheen > 0:
                        pygame.draw.polygon(surf, shade(col, 1.1 + 0.08 * sheen), ribbon_pts(frame, kl, kr, k, kk=0.3, shift=0.55))
                else:
                    Pf = ribbon_pts(frame, kl, kr, k)
                    pygame.draw.polygon(surf, col, Pf)
                    cache[("f", idx)] = Pf
                if edge:
                    pygame.draw.polygon(surf, shade(col, 0.55), cache.get(("f", idx)) or ribbon_pts(frame, kl, kr, k), edge_w)
            elif kind == "poly":
                P = [T(q) for q in geo]
                if shd:
                    xs = [q[0] for q in P]
                    ys = [q[1] for q in P]
                    span = min(max(xs) - min(xs), max(ys) - min(ys))
                    pygame.draw.polygon(surf, shade(col, 0.7), P)
                    if span > 6:
                        cx = sum(xs) / len(xs)
                        cy = sum(ys) / len(ys)
                        m = [(cx + (q[0] - cx) * 0.86 + lx * span * 0.08, cy + (q[1] - cy) * 0.9 + ly * span * 0.08) for q in P]
                        pygame.draw.polygon(surf, col, m)
                        if sheen > 0 and span > 14:
                            h = [(cx + (q[0] - cx) * 0.45 + lx * span * 0.2, cy + (q[1] - cy) * 0.6 + ly * span * 0.2) for q in P]
                            pygame.draw.polygon(surf, shade(col, 1.08 + 0.06 * sheen), h)
                    else:
                        pygame.draw.polygon(surf, col, P)
                else:
                    pygame.draw.polygon(surf, col, P)
                if edge:
                    pygame.draw.polygon(surf, shade(col, 0.55), P, edge_w)
            elif kind == "circle":
                c, r = geo
                cc = T(c)
                rr = r * k
                if shd and rr > 3:
                    pygame.draw.circle(surf, shade(col, 0.68), (int(cc[0]), int(cc[1])), int(rr))
                    pygame.draw.circle(surf, col, (int(cc[0] + lx * rr * 0.16), int(cc[1] + ly * rr * 0.16)), int(rr * 0.8))
                    if sheen > 0:
                        pygame.draw.circle(surf, shade(col, 1.1 + 0.08 * sheen),
                                           (int(cc[0] + lx * rr * 0.45), int(cc[1] + ly * rr * 0.45)), max(1, int(rr * 0.3)))
                else:
                    pygame.draw.circle(surf, col, (int(cc[0]), int(cc[1])), max(1, int(rr)))
                if edge:
                    pygame.draw.circle(surf, shade(col, 0.55), (int(cc[0]), int(cc[1])), max(1, int(rr)), edge_w)
            elif kind == "line":
                pts, w, closed, taper = geo
                P = [T(q) for q in pts]
                wpx = w * k
                if len(P) >= 2:
                    if wpx >= 2.0 and (taper or (not closed and wpx >= 3)):     # bajo 2 px el afilado no se ve
                        bp = brush_pts(P, wpx, taper=taper)
                        if len(bp) >= 3:
                            pygame.draw.polygon(surf, col, bp)
                            cache[("f", idx)] = bp
                    else:
                        pygame.draw.lines(surf, col, closed, P, max(1, int(wpx)))
            elif kind == "dot":
                c, r = geo
                cc = T(c)
                pygame.draw.circle(surf, col, (int(cc[0]), int(cc[1])), max(1, int(r * k)))


GROUP_H = {0: 46, 1: 66, 2: 106, 3: 126, 4: 150, 5: 176, 6: 210}


def _axis(P):
    """Centro y eje principal (análisis de componentes) de una nube de puntos; ancho menor en px."""
    n = len(P)
    cx = sum(q[0] for q in P) / n
    cy = sum(q[1] for q in P) / n
    sxx = syy = sxy = 0.0
    for x, y in P:
        dx, dy = x - cx, y - cy
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    sxx, syy, sxy = sxx / n, syy / n, sxy / n
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    lmin = max(0.0, tr * 0.5 - math.sqrt(max(0.0, tr * tr * 0.25 - det)))
    return (cx, cy), ang, 3.4 * math.sqrt(lmin)


def _render_data(fig: Figure, surf, T, k, ow, fac=1, shared=None, inv=1.0, id_base=1, pieces=None):
    """Mapa de datos para la luz por píxel: R altura, G pieza, B brillo.

    Cada grupo de profundidad tiene su altura base; cada pieza añade una cúpula interior y los
    surcos (músculos, pliegues, nudillos) la rebajan. La GPU deriva normales de esta altura.
    G identifica la pieza (0 = tinta): `pieces` recibe su ancla, eje, material y tejido para que la GPU
    pinte el tejido en el espacio propio de cada pieza, orientado a lo largo de ella y sin resbalar.
    Las cintas (brazos, piernas) se parten en dos piezas por la articulación central.
    """
    groups: Dict[int, list] = {}
    for idx, p in enumerate(fig.parts):
        groups.setdefault(p[0], []).append((idx, p))
    M = Figure.MATS
    cache: dict = {}
    if pieces is None:
        pieces = []
    state = {"last": 0}

    def piece(anchor, ang, w_px, mat, pat, idx):
        pid = id_base + len(pieces)
        if pid > 254:                     # sin hueco en la tabla: hereda la última pieza
            return state["last"]
        seed = math.fmod(idx * 0.6180339 + id_base * 0.1372, 1.0) * 37.0
        pieces.append((anchor[0], anchor[1], ang, k, float(M.get(mat, 2)), float(pat), seed, w_px / max(1e-6, k)))
        state["last"] = pid
        return pid

    for g in sorted(groups):
        ps = groups[g]
        h0 = GROUP_H.get(g, 120)
        ink = (max(0, h0 - 34), 0, 0)
        for idx, (_, kind, geo, col, outline, _s, _sh, _e, _m, _gr) in ps:
            if outline:
                o = shared.get(("o", idx)) if shared is not None else None
                if o is not None:
                    _draw_cached_outline(surf, o, inv, ink)        # misma tinta que el albedo, reescalada
                else:
                    _outline_part(surf, kind, geo, T, k, ow, ink, fac, cache, idx)
            if kind == "ribbon" and shared is not None and idx in shared and idx not in cache:
                (P, U, Nn), radii = shared[idx]
                cache[idx] = (([(x * inv, y * inv) for x, y in P], U, Nn), radii)
        for idx, (_, kind, geo, col, outline, shd, sheen, edge, mat, groove) in ps:
            pat = fig.pats[idx]
            spec = int(clamp(sheen * 55, 0, 255))
            pf = shared.get(("f", idx)) if shared is not None else None
            pid2 = None
            if groove:
                pid = state["last"]
            elif kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                A, B = T(a), T(b)
                pid = piece(_lerp2(A, B, 0.5), math.atan2(B[1] - A[1], B[0] - A[0]), (ra + rb) * k, mat, pat, idx)
            elif kind == "ribbon":
                frame, (kl, kr) = _geom(kind, geo, T, k, fac, cache, idx)
                P = frame[0]
                nC = len(geo[0])
                mcut = (nC - 2) * 6 if nC >= 3 else None
                segs = [(0, mcut), (mcut, len(P) - 1)] if mcut else [(0, len(P) - 1)]
                ids = []
                for i0, i1 in segs:
                    sub = P[i0:i1 + 1]
                    cx = sum(q[0] for q in sub) / len(sub)
                    cy = sum(q[1] for q in sub) / len(sub)
                    ang = math.atan2(P[i1][1] - P[i0][1], P[i1][0] - P[i0][0])
                    rr = (kl[(i0 + i1) // 2] + kr[(i0 + i1) // 2]) * k
                    ids.append(piece((cx, cy), ang, rr, mat, pat, idx + 1000 * len(ids)))
                pid = ids[0]
                pid2 = (ids[1], mcut, len(P)) if len(ids) > 1 else None
            elif kind == "poly":
                anchor, ang, wpx = _axis([T(q) for q in geo])
                pid = piece(anchor, ang, wpx, mat, pat, idx)
            elif kind in ("circle", "dot"):
                c, r = geo
                pid = piece(T(c), 0.0, 2 * r * k, mat, pat, idx)
            else:                                                 # pincelada
                pts, w, closed, taper = geo
                A, B = T(pts[0]), T(pts[-1])
                pid = piece(_lerp2(A, B, 0.5), math.atan2(B[1] - A[1], B[0] - A[0]), w * k, mat, pat, idx)
            base = (h0, pid, spec)
            dome = (min(255, h0 + 18), pid, spec)

            def split_draw(pts, h):
                """Cinta: el polígono entero con la pieza de arriba y la mitad baja con la de abajo."""
                pygame.draw.polygon(surf, (h, pid, spec), pts)
                if pid2 is not None:
                    id2, m_, n_ = pid2
                    low = pts[m_:2 * n_ + 4 - m_]
                    if len(low) >= 3:
                        pygame.draw.polygon(surf, (h, id2, spec), low)

            if kind == "ribbon":
                frame, (kl, kr) = _geom(kind, geo, T, k, fac, cache, idx)
                split_draw([(x * inv, y * inv) for x, y in pf] if pf is not None else ribbon_pts(frame, kl, kr, k), h0)
                split_draw(ribbon_pts(frame, kl, kr, k, kk=0.5), min(255, h0 + 18))
            elif kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                A, B = T(a), T(b)
                if pf is not None:
                    pygame.draw.polygon(surf, base, [(x * inv, y * inv) for x, y in pf])     # misma silueta que el albedo
                else:
                    pygame.draw.polygon(surf, base, tube_pts(A, B, ra * k, rb * k, bu * k, bt, side * fac))
                if (ra + rb) * k > 4:
                    pygame.draw.polygon(surf, dome, tube_pts(A, B, ra * k, rb * k, bu * k, bt, side * fac, k=0.5))
            elif kind == "poly":
                P = [T(q) for q in geo]
                pygame.draw.polygon(surf, base, P)
                if outline:
                    cx = sum(q[0] for q in P) / len(P)
                    cy = sum(q[1] for q in P) / len(P)
                    pygame.draw.polygon(surf, dome, [(cx + (q[0] - cx) * 0.6, cy + (q[1] - cy) * 0.66) for q in P])
            elif kind == "circle":
                c, r = geo
                cc = T(c)
                pygame.draw.circle(surf, base, (int(cc[0]), int(cc[1])), max(1, int(r * k)))
                if r * k > 3:
                    pygame.draw.circle(surf, dome, (int(cc[0]), int(cc[1])), max(1, int(r * k * 0.55)))
            elif kind == "line":
                pts, w, closed, taper = geo
                P = [T(q) for q in pts]
                if len(P) >= 2:
                    # surco: pliegue o músculo ancho y poco profundo (una arruga, no un corte)
                    gd = 12 if mat == "skin" else 8
                    val = (max(0, h0 - gd), pid, spec) if groove else (h0 + 4, pid, spec)
                    wpx = max(1.0, w * k * ((2.2 if mat == "skin" else 2.8) if groove else 1.0))
                    if pf is not None and not groove:
                        bp = [(x * inv, y * inv) for x, y in pf]
                    else:
                        bp = brush_pts(P, wpx, taper=taper) if not closed and wpx >= 2.0 else []
                    if len(bp) >= 3:
                        pygame.draw.polygon(surf, val, bp)
                    else:
                        pygame.draw.lines(surf, val, closed, P, max(1, int(wpx)))
            elif kind == "dot":
                c, r = geo
                cc = T(c)
                val = (max(0, h0 - 10), pid, spec) if groove else (h0 + 4, pid, spec)
                pygame.draw.circle(surf, val, (int(cc[0]), int(cc[1])), max(1, int(r * k)))
    return pieces


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTRUCCIÓN DE CADA LUCHADOR
# ─────────────────────────────────────────────────────────────────────────────
def _interp(arr, t):
    ts = SPINE_T
    if t <= ts[0]:
        return arr[0]
    for i in range(1, len(ts)):
        if t <= ts[i]:
            u = (t - ts[i - 1]) / (ts[i] - ts[i - 1])
            return arr[i - 1] + (arr[i] - arr[i - 1]) * u
    return arr[-1]


FACE = [(0.30, -0.92), (0.60, -0.72), (0.78, -0.46), (0.86, -0.30), (0.84, -0.14),
        (0.87, -0.06), (1.10, 0.20), (1.05, 0.27), (0.90, 0.30),
        (0.94, 0.40), (0.88, 0.46), (0.92, 0.52), (0.85, 0.59),
        (0.86, 0.72), (0.74, 0.86), (0.50, 0.92), (0.12, 0.80), (-0.10, 0.62), (-0.32, 0.46)]


def _face_points(look):
    pts = list(FACE)
    nose = look["nose"]
    if nose == "ancha":
        pts[6], pts[7], pts[8] = (1.08, 0.21), (1.04, 0.30), (0.92, 0.33)
    elif nose == "fina":
        pts[6], pts[7] = (1.06, 0.18), (1.02, 0.25)
    elif nose == "rota":
        pts[5] = (0.97, 0.02)          # caballete roto dos veces
        pts[6], pts[7] = (1.10, 0.24), (1.03, 0.31)
    j = look["jaw"]
    for i in range(13, len(pts)):
        x, y = pts[i]
        pts[i] = (0.4 + (x - 0.4) * j, 0.5 + (y - 0.5) * j)
    if "papada" in look["extras"]:
        pts[15:15] = [(0.55, 1.04), (0.25, 1.0)]
    # arco del cráneo: de la nuca, por detrás y por encima, hasta la frente
    arc = [(-0.08 + math.cos(math.radians(a)), -0.05 + math.sin(math.radians(a))) for a in range(118, 292, 12)]
    return pts + arc


def _expression(f, look):
    st = f.state
    if st in (Fighter.HIT, Fighter.KNOCK, Fighter.STUN, Fighter.THROWN, Fighter.KO):
        return ("sonrisa" if look is LOOKS.get("matias") and st == Fighter.HIT else "dolor")
    if st == Fighter.KIAI or (st == Fighter.ATTACK and f.move is not None and f.move_key in ("heavy", "special")):
        return "grito"
    if st in (Fighter.ATTACK, Fighter.THROWING, Fighter.GROUND):
        return "esfuerzo"
    if st == Fighter.WIN:
        return "sonrisa"
    if getattr(f, "exhausted", False):
        return "jadeo"
    e = look["expression"]
    return "sonrisa" if e in ("sonrisa", "picara") else "neutra"


_LEGS: Dict[str, Tuple[float, float]] = {}


def _leg_lengths(fd):
    """Muslo y pierna (rodilla → apoyo del pie) de cada luchador, medidos en su guardia."""
    key = fd["id"]
    if key not in _LEGS:
        try:
            p = _stance(fd)
            d = lambda a, b: math.hypot(b[0] - a[0], b[1] - a[1])
            _LEGS[key] = ((d(p["hip"], p["kn_l"]) + d(p["hip"], p["kn_r"])) / 2, (d(p["kn_l"], p["ft_l"]) + d(p["kn_r"], p["ft_r"])) / 2)
        except Exception:
            _LEGS[key] = (46.0, 48.0)
    return _LEGS[key]


def _leg_ik(H, ft0, T, S, front, up, stretch=1.2):
    """Pierna de dos huesos fijos hacia el pie que pide la pose. La rodilla dobla siempre hacia delante (o hacia
    arriba en las patadas), nunca hacia atrás; si el pie no llega, la pierna se estira hasta un 20 % (estirón de
    animación) y el pie se queda donde alcanza. Con el pie en el suelo y la pierna casi recta, el pie no se mueve."""
    dx, dy = ft0[0] - H[0], ft0[1] - H[1]
    D = math.hypot(dx, dy) or 1e-6
    ux, uy = dx / D, dy / D
    k = clamp(D / ((T + S) * 0.985), 1.0, stretch)
    T, S = T * k, S * k
    D = clamp(D, abs(T - S) + 2.0, (T + S) * 0.995)
    ft = (H[0] + ux * D, H[1] + uy * D)
    a = (T * T + D * D - S * S) / (2 * D)
    h = math.sqrt(max(0.0, T * T - a * a))
    px, py = -uy, ux
    score = lambda qx, qy: qx * front[0] + qy * front[1] + 0.5 * (qx * up[0] + qy * up[1])
    sg = 1.0 if score(px, py) >= score(-px, -py) else -1.0
    return (H[0] + ux * a + px * h * sg, H[1] + uy * a + py * h * sg), ft


def build_figure(f, pose, rig, mono=False, raw=False) -> Figure:
    fd = f.fd
    lk = LOOKS[fd["id"]]
    B = BODIES[lk["body"]]
    fig = Figure()
    key, amb = rig["key"], rig["ambient"]
    fac, sc = f.facing, f.scale
    t_anim = f.anim_t
    sweat = getattr(f, "sweat", 0.0)
    red = getattr(f, "redness", 0.0)
    dish = getattr(f, "dishevel", 0.0)
    vxl = getattr(f, "vx", 0.0) * fac          # velocidad hacia delante en espacio local
    vy = getattr(f, "vy", 0.0)

    def C(c, x=0.0, depth=1.0):
        c = hex2rgb(c) if isinstance(c, str) else c
        if mono or raw:
            return c if not (raw and depth < 1.0) else shade(c, 0.9)
        kk = clamp(0.55 - x * fac * sc / 220.0, 0.0, 1.0)
        base = tint(c, key, 0.35 * kk)
        base = mixc(base, amb, 0.32 * (1 - kk))
        return shade(base, (0.8 + 0.38 * kk) * depth)

    skin = hex2rgb(lk["skin"])
    cloth = lk.get("cloth", "#FFFFFF")
    skin_sheen = 0.6 + 2.2 * sweat
    J = pose
    H, Cc0, Hd0 = J["hip"], J["chest"], J["head"]
    # el esqueleto de poses deja los hombros bajos y la cabeza lejos (36 cm): subimos la cintura
    # escapular y acercamos la cabeza para un cuello real de ~8 cm visibles
    lift = ((Hd0[0] - Cc0[0]) * 0.15, (Hd0[1] - Cc0[1]) * 0.15)
    J = dict(J)
    for jn, w in (("chest", 1.0), ("sh_l", 0.55), ("sh_r", 0.55), ("el_l", 0.2), ("el_r", 0.2)):
        J[jn] = (J[jn][0] + lift[0] * w, J[jn][1] + lift[1] * w)
    Cc = J["chest"]
    Hd = _lerp2(Cc0, Hd0, 0.64)

    # ── columna y torso ──────────────────────────────────────────────────────
    N = _lerp2(Cc, Hd, 0.06)
    ax, ay = N[0] - H[0], N[1] - H[1]
    L = math.hypot(ax, ay) or 1.0
    nx, ny = -ay / L, ax / L             # normal hacia el pecho (delante)

    def S(t, w):
        return (H[0] + ax * t + nx * w, H[1] + ay * t + ny * w)

    breath = 1.0 + 0.025 * math.sin(t_anim * 2.2) * (0.6 + getattr(f, "breath", 0.0))
    jig = 0.0
    if lk["body"] == "sumo" and f.state in (Fighter.HIT, Fighter.STUN, Fighter.KNOCK):
        jig = 0.07 * math.sin(f.timer * 1.3) * max(0.0, 1 - f.timer / 30)
    front = [w * (breath if 0.45 <= t <= 0.85 else 1.0) * (1 + jig * (1 if t < 0.5 else 0.3)) for t, w in zip(SPINE_T, B["front"])]
    back = list(B["back"])

    def torso(fr, bk, ts=SPINE_T, grow_f=0.0, grow_b=0.0):
        F = [S(t, w + grow_f) for t, w in zip(ts, fr)]
        K = [S(t, -(w + grow_b)) for t, w in zip(ts, bk)]
        return F + K[::-1]

    def wf(t):
        return _interp(front, t)

    def wb(t):
        return _interp(back, t)

    # ── piernas ──────────────────────────────────────────────────────────────
    bottom = lk["bottom"]
    top = lk["top"]
    bot_pat = {"gi": "twill", "abada": "cotton", "track": "satin", "shorts_sambo": "cotton", "shorts_thai": "satin",
               "singlet": "satin", "hakama": "twill", "mawashi": "rib"}.get(bottom, "cotton")
    top_pat = {"gi": "judo", "kurtka": "canvas", "changshan": "damask", "keikogi": "linen", "tank": "knit",
               "singlet": "satin"}.get(top, "cotton")
    top_pat = {"yeon": "twill", "ines": "canvas"}.get(fd["id"], top_pat)
    fig.zone(cloth=bot_pat, satin=bot_pat)
    feet = lk.get("feet", "descalzo")
    th0, th1, thb = B["thigh"]
    ca0, ca1, cab = B["calf"]
    def dist(a, b):
        return math.hypot(b[0] - a[0], b[1] - a[1])

    def leg_keys(L1, L2, add=0.0):
        """Perfil anatómico continuo: cuádriceps delante, glúteo-isquio detrás, rodilla, gemelo, tobillo."""
        uk = L1 / max(1e-6, L1 + L2)
        back = [(0.0, th0 + add), (uk * 0.4, th0 * 0.93 + add), (uk * 0.85, th1 * 1.05 + add), (uk, th1 * 0.95 + add),
                (uk + (1 - uk) * 0.28, ca0 * 0.92 + cab + add), (uk + (1 - uk) * 0.75, ca1 * 1.12 + add), (1.0, ca1 + add)]
        front = [(0.0, th0 * 0.95 + add), (uk * 0.35, th0 * 0.9 + thb + add), (uk * 0.82, th1 * 1.02 + thb * 0.35 + add),
                 (uk, th1 + 0.3 + add), (uk + (1 - uk) * 0.3, ca0 * 0.9 + add), (uk + (1 - uk) * 0.75, ca1 * 1.05 + add), (1.0, ca1 + add)]
        return back, front

    def cloth_keys(L1, L2, r0, rk, r1, bag=0.0):
        uk = L1 / max(1e-6, L1 + L2)
        back = [(0.0, r0 + bag), (uk * 0.55, (r0 + rk) * 0.5 + bag * 1.4), (uk, rk + bag), (1.0, r1)]
        front = [(0.0, r0), (uk * 0.6, (r0 + rk) * 0.5 - 0.3), (uk, rk + 0.4), (1.0, r1)]
        return back, front

    # piernas de huesos fijos: el esqueleto de poses estira muslo y pierna a voluntad (de 31 a 66 cm el muslo,
    # de 30 a 70 la pierna: 135 cm de pierna en la patada alta); aquí miden lo que en la guardia de cada uno
    T_, S_ = _leg_lengths(fd)
    ux_, uy_ = _norm(Cc[0] - H[0], Cc[1] - H[1])
    for side in ("l", "r"):
        J["kn_" + side], J["ft_" + side] = _leg_ik(H, J["ft_" + side], T_, S_, (-uy_, ux_), (ux_, uy_))

    for side, g, depth in (("l", 1, 0.84), ("r", 3, 1.0)):
        kn, ft = J["kn_" + side], J["ft_" + side]
        sx, sy = _norm(ft[0] - kn[0], ft[1] - kn[1])
        if sx == 0 and sy == 0:
            sy = 1.0
        ankle = (ft[0] - sx * 6, ft[1] - sy * 6)
        fdx, fdy = sy, -sx                   # el pie, perpendicular a la espinilla
        x_m = kn[0]
        sk = C(skin, x_m, depth)
        L1, L2 = dist(H, kn), dist(kn, ankle)
        if bottom != "hakama":
            fig.m("skin")
            kb, kf = leg_keys(L1, L2)
            fig.ribbon(g, [H, kn, ankle], kb, kf, sk, sheen=skin_sheen)
            if lk["body"] in ("atleta", "fibroso", "pesado", "atleta_f") and bottom in ("shorts_thai", "shorts_sambo", "singlet", "mawashi"):
                fig.m("skin", True)
                fig.line(g, [_lerp2(kn, ankle, 0.12), _lerp2(kn, ankle, 0.42)], shade(sk, 0.8), 0.35)     # gemelo
        # pie
        def F_(a, b):
            return (ankle[0] + fdx * a + sx * b, ankle[1] + fdy * a + sy * b)
        if feet in ("botas", "botas_altas", "zapatillas"):
            bc = C(lk.get("boots", "#F2F2F2") if feet != "zapatillas" else "#EDEDED", x_m, depth)
            foot = [F_(-5, -3), F_(-5.5, 6.5), F_(17.5, 6.5), F_(18.5, 3.5), F_(12, 0.5), F_(4, -3.5)]
            fig.m("leather")
            fig.poly(g, foot, bc, sheen=1.5, smooth=2)
            sole = C("#1E1A18" if feet != "zapatillas" else "#222222", x_m, depth)
            fig.line(g, [F_(-5.3, 5.9), F_(6, 6.1), F_(17.7, 5.6)], sole, 1.4, taper=False)
            if feet == "zapatillas":
                fig.line(g, [F_(1, 2.8), F_(5, 1.5), F_(10, 1.0)], C("#1A1A1A", x_m, depth), 0.9)
                fig.line(g, [F_(4, 4.0), F_(8, 2.6), F_(13, 2.2)], C("#1A1A1A", x_m, depth), 0.9)
            else:
                btop = 0.62 if feet == "botas" else 0.32
                b0 = _lerp2(kn, ankle, btop)
                rb = ca1 + 1.5 + (1.0 if feet == "botas_altas" else 0)
                fig.ribbon(g, [b0, _lerp2(b0, ankle, 0.5), ankle], [(0, rb), (1, ca1 + 1.6)], [(0, rb - 0.3), (1, ca1 + 1.5)], bc, sheen=1.4)
                fig.m("leather", True)
                for q in (0.2, 0.45, 0.7):
                    pq = _lerp2(b0, ankle, q)
                    fig.line(g, [(pq[0] - 2, pq[1]), (pq[0] + 2.5, pq[1] + 1.2)], shade(bc, 1.4), 0.35)       # cordones
        else:
            foot = [F_(-3.5, -1.5), F_(-4.8, 3.5), F_(-3.5, 6.0), F_(6, 6.3), F_(12.5, 6.0), F_(16.8, 5.2), F_(16.6, 3.2), F_(9, 1.3), F_(3, -1.6)]
            fig.m("skin")
            fig.poly(g, foot, sk, sheen=skin_sheen * 0.5, smooth=2)
            fig.m("skin", True)
            fig.line(g, [F_(12.8, 5.4), F_(13.3, 3.9)], shade(sk, 0.72), 0.3)
            fig.line(g, [F_(14.8, 5.3), F_(15.2, 3.8)], shade(sk, 0.72), 0.25)
            fig.line(g, [F_(-1.5, 1.0), F_(2.5, 0.0), F_(6, 0.8)], shade(sk, 0.82), 0.3)                    # tendón
        # ropa de las piernas
        if bottom == "gi":
            pc = C(lk.get("pants", cloth), x_m, depth)
            hem = _lerp2(kn, ankle, 0.86)
            fig.m("cloth")
            kb, kf = cloth_keys(L1, dist(kn, hem), th0 + 3.2, th1 + 3.5, ca1 + 4.3, bag=0.8)
            fig.ribbon(g, [H, kn, hem], kb, kf, pc, sheen=0.5, edge=True)
            fig.m("cloth", True)
            fig.line(g, [_lerp2(H, kn, 0.8), _lerp2(kn, hem, 0.05), _lerp2(kn, hem, 0.28)], shade(pc, 0.78), 0.5)
            fig.line(g, [_lerp2(H, kn, 0.55), _lerp2(H, kn, 0.72)], shade(pc, 0.8), 0.4)
            fig.line(g, [_lerp2(kn, hem, 0.62), _lerp2(kn, hem, 0.9)], shade(pc, 0.8), 0.4)
        elif bottom == "abada":
            pc = C(cloth, x_m, depth)
            fig.m("cloth")
            kb, kf = cloth_keys(L1, L2, th0 + 2.4, th1 + 2.8, ca1 + 1.3, bag=0.6)
            fig.ribbon(g, [H, kn, ankle], kb, kf, pc, sheen=0.5, edge=True)
            fig.m("cloth", True)
            fig.line(g, [_lerp2(H, kn, 0.7), _lerp2(kn, ankle, 0.1), _lerp2(kn, ankle, 0.3)], shade(pc, 0.8), 0.45)
        elif bottom == "track":
            pc = C(lk["pants"], x_m, depth)
            hem = _lerp2(kn, ankle, 0.94)
            fig.m("satin")
            kb, kf = cloth_keys(L1, dist(kn, hem), th0 + 2.4, th1 + 2.4, ca1 + 2.6, bag=0.4)
            fig.ribbon(g, [H, kn, hem], kb, kf, pc, sheen=0.8, edge=True)
            fig.line(g, catmull([H, kn, hem], 3), C("#FFFFFF", x_m, depth), 0.9, taper=False)
        elif bottom == "shorts_sambo":
            fig.m("cloth")
            e = _lerp2(H, kn, 0.5)
            fig.ribbon(g, [H, _lerp2(H, e, 0.5), e], [(0, th0 + 2.2), (1, th0 + 1.4)], [(0, th0 + 1.8), (1, th0 + 1.2)],
                       C(lk["shorts"], x_m, depth), sheen=0.7, edge=True)
        elif bottom == "shorts_thai":
            sc_ = C(cloth, x_m, depth)
            fig.m("satin")
            e = _lerp2(H, kn, 0.5)
            fig.ribbon(g, [H, _lerp2(H, e, 0.5), e], [(0, th0 + 1.8), (1, th0 + 2.4)], [(0, th0 + 1.4), (1, th0 + 2.1)], sc_, sheen=3.0, edge=True)
            fig.line(g, [_lerp2(H, kn, 0.1), _lerp2(H, kn, 0.3), _lerp2(H, kn, 0.5)], C(lk["accent"], x_m, depth), 0.5)
        elif bottom == "singlet":
            fig.m("satin")
            e = _lerp2(H, kn, 0.32)
            fig.ribbon(g, [H, _lerp2(H, e, 0.5), e], [(0, th0 + 0.7), (1, th0 + 0.3)], [(0, th0 + 0.6), (1, th0 + 0.3)],
                       C(cloth, x_m, depth), sheen=1.8, edge=True)
            fig.m("leather", pat="neoprene")
            pad = [_lerp2(kn, H, 0.12), kn, _lerp2(kn, ankle, 0.12)]
            fig.ribbon(g, pad, [(0, ca0 + 1.3), (0.5, ca0 + 1.9), (1, ca0 + 1.3)], [(0, ca0 + 1.6), (0.5, ca0 + 2.3), (1, ca0 + 1.6)],
                       C("#1C1C1F", x_m, depth), sheen=1.0)       # rodillera de neopreno

    # ── torso (piel) ─────────────────────────────────────────────────────────
    tx = (wf(0.5) - wb(0.5)) * 0.5
    sk_t = C(skin, tx)
    fig.m("skin")
    fig.poly(2, torso(front, back), sk_t, sheen=skin_sheen, smooth=2)
    ex = lk["extras"]
    if top == "none":
        dk = shade(sk_t, 0.78)
        if "definido" in ex:
            fig.m("skin", True)
            fig.line(2, [S(0.66, wf(0.66) * 0.98), S(0.6, wf(0.6) * 0.55), S(0.66, wf(0.66) * 0.15)], dk, 0.45)
            for t in (0.34, 0.42, 0.5):
                fig.line(2, [S(t, wf(t) * 0.94), S(t + 0.01, wf(t) * 0.62)], dk, 0.4)
            fig.line(2, [S(0.28, wf(0.28) * 0.8), S(0.58, wf(0.58) * 0.82)], dk, 0.35)
            fig.line(2, [S(0.55, -wb(0.55) * 0.2), S(0.7, -wb(0.7) * 0.55)], dk, 0.35)      # dorsal
        if lk["body"] == "sumo":
            fig.m("skin", True)
            fig.line(2, [S(0.05, wf(0.05) * 0.95), S(0.1, wf(0.1) * 0.55), S(0.02, wf(0.02) * 0.1)], dk, 0.6)
            fig.line(2, [S(0.66, wf(0.66) * 0.95), S(0.6, wf(0.6) * 0.5)], dk, 0.5)
        fig.dot(2, S(0.24, wf(0.24) * 0.9), 0.5, shade(sk_t, 0.6))                    # ombligo
        if "sak_yant" in ex:
            fig.m("skin")
            ink = mixc((30, 34, 52), sk_t, 0.35)
            base_t, w0 = 0.58, -wb(0.7) * 0.62
            for i in range(5):
                tt = 0.62 + i * 0.04
                fig.line(2, [S(tt, -wb(tt) * 0.35), S(tt + 0.1, -wb(tt + 0.1) * 0.55)], ink, 0.3)
            fig.line(2, [S(0.58, -wb(0.58) * 0.3), S(0.6, -wb(0.6) * 0.8), S(0.78, -wb(0.78) * 0.85), S(0.82, -wb(0.82) * 0.4)], ink, 0.3)
            for i in range(4):
                fig.dot(2, S(0.84 + i * 0.025, -wb(0.85) * (0.4 + 0.12 * i)), 0.35, ink)
            _ = (base_t, w0)

    # ── ropa del torso (grupo 4: sobre la pierna delantera) ──────────────────
    fig.zone(cloth=top_pat, satin=top_pat)
    def band(t0, t1, gf, gb):
        return [S(t0, wf(t0) + gf), S(t1, wf(t1) + gf), S(t1, -(wb(t1) + gb)), S(t0, -(wb(t0) + gb))]

    if top in ("gi", "kurtka", "changshan", "keikogi"):
        grow = {"gi": 2.6, "kurtka": 2.2, "changshan": 1.6, "keikogi": 2.4}[top]
        skirt = {"gi": (-0.44, -0.26), "kurtka": (-0.3, -0.16), "changshan": (-0.32, -0.18), "keikogi": (0.12, 0.14)}[top]
        ts = [skirt[0], skirt[1]] + [t for t in SPINE_T if t > skirt[1]]
        fr = [wf(0.0) + 6.5, wf(0.0) + 4.5] + [w + grow for t, w in zip(SPINE_T, front) if t > skirt[1]]
        bk = [wb(0.0) + 6.0, wb(0.0) + 4.0] + [w + grow for t, w in zip(SPINE_T, back) if t > skirt[1]]
        if top == "keikogi":
            fr[0] = fr[1] = wf(0.12) + grow
            bk[0] = bk[1] = wb(0.12) + grow
        fig.m("cloth")
        jc = C(cloth, tx)
        F = [S(t, w) for t, w in zip(ts, fr)]
        K = [S(t, -w) for t, w in zip(ts, bk)]
        fig.poly(4, F + K[::-1], jc, sheen=0.5, edge=True, smooth=2)
        # pliegues de la tela
        fig.m("cloth", True)
        fd_ = shade(jc, 0.8)
        fig.line(4, [S(0.55, wf(0.55) * 0.3), S(0.32, wf(0.32) * 0.65 + 1)], fd_, 0.4)
        fig.line(4, [S(0.7, -wb(0.7) * 0.5), S(0.4, -wb(0.4) * 0.2)], fd_, 0.4)
        fig.m("cloth")
        if top != "changshan":
            apex = {"gi": 0.58, "kurtka": 0.74, "keikogi": 0.66}[top] - 0.16 * dish
            if fd["id"] == "yeon":
                apex = 0.82 - 0.1 * dish
            v_col = C("#2B2B2B", tx) if fd["id"] == "ines" else C(skin, tx)
            vtri = [S(1.0, 0.4), S(1.0, wf(1.0) + grow), S(apex, wf(apex) + grow - 0.6)]
            fig.m("skin")
            fig.poly(4, vtri, v_col, outline=False, sheen=skin_sheen)
            fig.m("cloth", pat="stitch")
            cc = C(lk.get("collar", cloth), tx)
            cw = 2.6 if fd["id"] == "yeon" else 2.2
            fig.line(4, [S(1.0, wf(1.0) + grow - 0.6), S(apex, wf(apex) + grow - 1.2)], cc, cw)
            fig.line(4, [S(1.0, 0.0), S(1.0, -wb(1.0) - grow + 0.5)], cc, cw)
        else:
            # cuello mao y botones de lazo granate
            nk = [S(1.0, wf(1.0) + 1.8), S(1.0, -wb(1.0) - 1.8), _lerp2(S(1.0, -wb(1.0) - 1.4), Hd, 0.14), _lerp2(S(1.0, wf(1.0) + 1.4), Hd, 0.14)]
            fig.poly(4, nk, jc, sheen=0.5, edge=True)
            ac = C(lk["accent"], tx)
            for t in (0.92, 0.8, 0.68, 0.56, 0.44):
                p = S(t, wf(t) + grow - 0.6)
                q = S(t, wf(t) * 0.4)
                fig.line(4, [p, q], ac, 0.6)
                fig.dot(4, p, 0.9, ac)
        if top == "kurtka":
            ep = C("#8E1B1B", tx)
            fig.line(4, [S(1.0, wf(1.0)), S(0.97, -wb(0.97))], ep, 2.2)                 # charreteras
        if "bordado" in ex:
            ac = C(lk["accent"], tx)
            p = S(0.74, wf(0.74) * 0.55)
            for dx, dy, ex_, ey in ((0, 0, 2.5, 0), (1.2, -1.5, 1.2, 2.2), (0.2, 1.5, 2.6, 1.2)):
                fig.line(4, [(p[0] + dx, p[1] + dy), (p[0] + dx + ex_, p[1] + dy + ey)], ac, 0.45)
        if "parches" in ex:
            pc = [S(0.72, wf(0.72) * 0.25), S(0.72, wf(0.72) * 0.85), S(0.62, wf(0.62) * 0.85), S(0.62, wf(0.62) * 0.25)]
            fig.poly(4, pc, C("#F4F4F4", tx), outline=False, shade_=False)
            fig.line(4, [_lerp2(pc[0], pc[3], 0.5), _lerp2(pc[1], pc[2], 0.5)], C("#1E6FB3", tx), 0.5)
        # cinturón con nudo y puntas que se mueven
        if top in ("gi", "kurtka"):
            fig.m("cloth", pat="belt_worn" if lk.get("belt_style") == "deshilachado" else "belt")
            bc = C(lk.get("belt", "#111111"), tx)
            fig.poly(4, band(0.17, 0.26, grow + 0.4, grow + 0.4), bc, sheen=0.6)
            knot = S(0.215, wf(0.215) + grow + 1.0)
            sw = math.sin(t_anim * 4.2 + f.x * 0.01) * 2.5 - vxl * 1.6
            ln_ = 24 + 12 * dish
            e1 = (knot[0] + 2 + sw, knot[1] + ln_)
            e2 = (knot[0] + 6 + sw * 0.7, knot[1] + ln_ * 0.9)
            fig.tube(4, knot, e1, 1.6, 1.5, bc, sheen=0.5)
            fig.tube(4, knot, e2, 1.6, 1.5, bc, sheen=0.5)
            fig.circle(4, knot, 2.4, bc, sheen=0.6)
            if lk.get("belt_style") == "deshilachado":
                for e in (e1, e2):
                    for d in (-1.0, 0.0, 1.0):
                        fig.line(4, [e, (e[0] + d, e[1] + 2.2)], shade(bc, 1.8), 0.25)
            if lk.get("belt_style") == "grados":
                bar0, bar1 = _lerp2(knot, e1, 0.62), _lerp2(knot, e1, 0.92)
                fig.line(4, [bar0, bar1], C("#9A1C1C", tx), 2.6)
                for i in range(4):
                    p = _lerp2(bar0, bar1, 0.2 + i * 0.2)
                    fig.line(4, [(p[0] - 1.5, p[1]), (p[0] + 1.5, p[1])], C("#F2F2F2", tx), 0.45)
    elif top == "tank":
        fig.m("cloth")
        tc = C(cloth, tx)
        ts = [0.0, 0.12, 0.28, 0.45, 0.62, 0.78, 0.86]
        fr = [wf(t) + 1.0 for t in ts]
        fr[-1] = wf(0.86) * 0.35
        bk = [wb(t) + 1.0 for t in ts]
        F = [S(t, w) for t, w in zip(ts, fr)]
        K = [S(t, -w) for t, w in zip(ts, bk)]
        fig.poly(4, F + K[::-1], tc, sheen=1.0 + 1.5 * sweat, edge=True)
        fig.line(4, [S(0.86, wf(0.86) * 0.3), S(1.0, wf(1.0) * 0.6)], tc, 2.4)
        fig.line(4, [S(0.86, -wb(0.86)), S(1.0, -wb(1.0) * 0.4)], tc, 2.4)
    elif top == "singlet":
        fig.m("satin")
        tc = C(cloth, tx)
        ts = [-0.1, 0.0, 0.12, 0.28, 0.45, 0.62, 0.74]
        F = [S(t, wf(t) + 0.7) for t in ts]
        K = [S(t, -(wb(t) + 0.7)) for t in ts]
        fig.poly(4, F + K[::-1], tc, sheen=2.0, edge=True)
        fig.line(4, [S(0.74, wf(0.74) * 0.5), S(1.0, wf(1.0) * 0.5)], tc, 2.2)
        fig.line(4, [S(0.74, -wb(0.74) * 0.6), S(1.0, -wb(1.0) * 0.5)], tc, 2.2)
        fig.line(4, [S(-0.08, 0.0), S(0.72, 0.0)], C(lk["accent"], tx), 1.6)

    # partes bajas que se ven sobre la cadera
    fig.zone(cloth=bot_pat, satin=bot_pat)
    if bottom == "shorts_thai":
        sc_ = C(cloth, tx)
        fig.m("satin")
        fig.poly(4, band(-0.12, 0.08, 2.4, 2.4), sc_, sheen=3.0, edge=True)
        ac = C(lk["accent"], tx)
        fig.m("cloth")
        fig.poly(4, band(0.03, 0.11, 2.6, 2.6), ac, sheen=1.0)
        for i in range(3):
            p = S(0.07, wf(0.07) * (0.2 + 0.25 * i))
            fig.line(4, [(p[0] - 0.8, p[1]), (p[0] + 0.8, p[1])], C(cloth, tx), 0.4)
    elif bottom == "abada":
        fig.m("cloth")
        fig.poly(4, band(-0.12, 0.08, 2.2, 2.2), C(cloth, tx), sheen=0.5, edge=True)
        y_, g_ = lk["cord"]
        fig.poly(4, band(0.04, 0.09, 2.5, 2.5), C(y_, tx), sheen=1.0)
        k0 = S(0.065, wf(0.065) + 2.6)
        sw = math.sin(t_anim * 5) * 2 - vxl
        fig.line(4, [k0, (k0[0] + 1 + sw, k0[1] + 16)], C(y_, tx), 0.9)
        fig.line(4, [k0, (k0[0] + 3 + sw, k0[1] + 15)], C(g_, tx), 0.9)
    elif bottom == "track":
        fig.m("satin")
        fig.poly(4, band(-0.12, 0.06, 2.4, 2.4), C(lk["pants"], tx), sheen=0.8, edge=True)
    elif bottom == "mawashi":
        fig.m("satin")
        mc = C(cloth, tx)
        fig.poly(4, band(-0.12, 0.2, 2.6, 2.6), mc, sheen=1.6, edge=True)
        fig.m("satin", True)
        fig.line(4, [S(0.04, wf(0.04) + 2.7), S(0.04, -wb(0.04) - 2.7)], shade(mc, 0.7), 0.4)
        fig.line(4, [S(0.12, wf(0.12) + 2.7), S(0.12, -wb(0.12) - 2.7)], shade(mc, 0.7), 0.4)
        fig.m("satin")
        top_ = S(0.16, wf(0.16) + 2.0)
        bot_ = S(-0.12, wf(-0.1) + 1.0)
        fig.tube(4, top_, bot_, 3.2, 2.6, mc, sheen=1.6)
        sw = math.sin(t_anim * 3) * 1.5 - vxl * 1.2
        for i in range(7):
            p = S(0.02, wf(0.02) + 2.4 - i * 1.6)
            fig.line(4, [p, (p[0] + sw * (0.6 + i * 0.08), p[1] + 20 + (i % 2) * 2)], shade(mc, 1.25), 0.45)
        kn_ = S(0.2, -(wb(0.2) + 3.5))
        fig.circle(4, kn_, 3.4, mc, sheen=1.4)
    elif bottom == "hakama":
        fig.m("cloth")
        hc = C(lk["hakama"], tx)
        # hakama: falda-pantalón dividida. Cada pernera es ancha y se abre perpendicular a su pierna.
        waist = [S(0.24, wf(0.24) + 4.5), S(0.24, -(wb(0.24) + 5.5)), S(0.04, wf(0.04) + 7), S(0.04, -(wb(0.04) + 8))]
        legs = []
        for side in ("l", "r"):
            kn, ft = J["kn_" + side], J["ft_" + side]
            tx_, ty_ = _norm(kn[0] - H[0], kn[1] - H[1])
            sx, sy = _norm(ft[0] - kn[0], ft[1] - kn[1])
            ank = (ft[0] - sx * 9, ft[1] - sy * 9)
            pts = list(waist) + [(kn[0] - ty_ * 12, kn[1] + tx_ * 12), (kn[0] + ty_ * 12, kn[1] - tx_ * 12),
                                 (ank[0] - sy * 19, ank[1] + sx * 19), (ank[0] + sy * 19, ank[1] - sx * 19)]
            legs.append((side, convex_hull(pts), ank))
        for side, hull, ank in legs:
            shade_k = 0.82 if side == "l" else 1.0
            fig.poly(4, hull, shade(hc, shade_k), sheen=0.4, edge=True, smooth=2)
            # pliegues: líneas de la cintura al bajo de cada pernera
            fig.m("cloth", True)
            hem = sorted(hull, key=lambda q: -((q[0] - H[0]) ** 2 + (q[1] - H[1]) ** 2))[:2]
            if len(hem) == 2:
                for i in range(4):
                    u = (i + 0.5) / 4
                    hcx = sum(q[0] for q in hull) / len(hull)
                    hcy = sum(q[1] for q in hull) / len(hull)
                    fig.line(4, [S(0.2, wf(0.2) * (1 - 2 * u) - (wb(0.2) * (2 * u - 1) if u > 0.5 else 0)),
                                 _lerp2(_lerp2(hem[0], hem[1], 0.15 + 0.7 * u), (hcx, hcy), 0.12)], shade(hc, 1.7), 0.35)
        fig.m("cloth")
        fig.poly(4, band(0.16, 0.24, 4.8, 5.6), hc, sheen=0.6, edge=True)
        fig.poly(4, [S(0.12, -(wb(0.12) + 6)), S(0.3, -(wb(0.3) + 4.5)), S(0.3, -(wb(0.3) + 1)), S(0.12, -(wb(0.12) + 2))],
                 shade(hc, 1.3), sheen=0.6)                      # koshi-ita

    # ── cuello y cabeza (grupo 5) ────────────────────────────────────────────
    fig.zone(cloth="rib")
    R = B["head"]
    ang = math.atan2(Hd[0] - Cc[0], -(Hd[1] - Cc[1]))

    def HP(x, y):
        rx, ry = _rot(x * R, y * R, ang)
        return (Hd[0] + rx, Hd[1] + ry)

    face_c = C(skin, Hd[0])
    if "rubor" in ex or red > 0.05:
        face_c = mixc(face_c, (205, 85, 75), min(0.45, 0.12 + 0.25 * getattr(f, "breath", 0.0) + 0.3 * red))
    hair_c = C(lk["hair_c"], Hd[0])
    hair = lk["hair"]
    sw_h = math.sin(t_anim * 3.1) * 0.12 - vxl * 0.05 + vy * 0.01

    # coletas y trenzas detrás de la cabeza
    fig.m("hair")
    if hair == "coleta":
        # coleta alta: sale de la coronilla, cae por su peso y sigue cada giro con retardo
        lift = clamp(-vy * 0.03, -0.3, 0.6)
        b0 = HP(-0.72, -0.84)
        mid = HP(-1.45 - sw_h * 1.8, -0.25 - lift)
        tip = HP(-1.32 - sw_h * 3.6, 1.4 - lift * 0.5)
        fig.poly(5, lock_pts(b0, mid, tip, 0.5 * R), hair_c, sheen=1.6, smooth=0)
        fig.poly(5, lock_pts(b0, HP(-1.3 - sw_h * 1.5, -0.1 - lift), HP(-1.12 - sw_h * 3.0, 1.1 - lift * 0.5), 0.22 * R), hair_c, sheen=1.6, smooth=0)
        fig.m("hair", True)
        fig.line(5, [HP(-1.0, -0.7), HP(-1.4 - sw_h * 1.8, -0.1 - lift), HP(-1.35 - sw_h * 3.0, 0.9)], shade(hair_c, 1.5), 0.04 * R)
        fig.m("hair")
    if hair == "trenza":
        prev = HP(-0.9, -0.2)
        for i in range(7):
            q = HP(-1.0 - 0.06 * i - sw_h * (0.3 + 0.2 * i), 0.2 + 0.3 * i)
            fig.circle(5, q, (0.2 - 0.012 * i) * R, hair_c, sheen=1.2)
            prev = q
        _ = prev
    fig.m("skin")
    neck_end = HP(-0.2, 0.5)
    nr = B["neck"]
    fig.tube(5, N, neck_end, nr * 1.05, nr, C(skin, N[0]), sheen=skin_sheen)
    if lk["body"] == "sumo":
        fig.tube(5, N, HP(0.3, 0.8), nr * 1.1, nr * 0.9, C(skin, N[0]), sheen=skin_sheen)
    head_pts = [HP(x, y) for x, y in _face_points(lk)]
    fig.poly(5, head_pts, face_c, sheen=0.4 + skin_sheen * 0.5)
    # oreja
    ear_c = shade(face_c, 0.92)
    ear = [HP(-0.12 + 0.17 * math.cos(a), 0.06 + 0.27 * math.sin(a)) for a in [i * math.pi / 6 for i in range(12)]]
    if "coliflor" in ex:
        fig.poly(5, ear, mixc(ear_c, (190, 110, 100), 0.25), outline=False, sheen=0.3)
        for dx, dy in ((-0.1, -0.08), (-0.05, 0.1), (-0.16, 0.12)):
            fig.dot(5, HP(dx, dy), 0.09 * R, shade(ear_c, 1.08))
    else:
        fig.poly(5, ear, ear_c, outline=False, sheen=0.3)
        fig.line(5, [HP(-0.08, -0.08), HP(-0.18, 0.04), HP(-0.1, 0.2)], shade(ear_c, 0.7), 0.06 * R)

    fig.m("eye")
    expr = _expression(f, lk)
    dark = (22, 14, 12)
    iris = hex2rgb(lk.get("eyes", "#3A2618"))
    # ojo: párpado superior entintado y grueso, iris con brillo, línea inferior fina
    if expr == "dolor":
        fig.line(5, [HP(0.46, -0.12), HP(0.6, -0.08), HP(0.74, -0.12)], dark, 0.06 * R)
        fig.line(5, [HP(0.5, -0.04), HP(0.62, -0.02), HP(0.72, -0.05)], shade(face_c, 0.7), 0.03 * R)
    else:
        open_ = 1.15 if expr == "grito" else 1.0
        eye = [HP(0.47, -0.11), HP(0.58, -0.18 * open_), HP(0.72, -0.13), HP(0.75, -0.08), HP(0.6, -0.04)]
        fig.poly(5, eye, (236, 232, 222), outline=False, shade_=False, smooth=1)
        fig.dot(5, HP(0.65, -0.1), 0.068 * R, iris)
        fig.dot(5, HP(0.665, -0.1), 0.036 * R, dark)
        fig.dot(5, HP(0.635, -0.125), 0.02 * R, (255, 255, 250))
        fig.line(5, [HP(0.45, -0.1), HP(0.57, -0.19 * open_), HP(0.7, -0.15), HP(0.79, -0.1)], dark, 0.075 * R)
        fig.line(5, [HP(0.52, -0.04), HP(0.64, -0.025), HP(0.73, -0.06)], shade(face_c, 0.72), 0.022 * R)
    # ceja de pincel
    fig.m("hair")
    bw = 0.075 * R * lk["brow"]
    if expr in ("grito", "esfuerzo") or lk["expression"] == "feroz":
        brow = [HP(0.4, -0.33), HP(0.6, -0.3), HP(0.8, -0.2)]
    elif expr == "dolor":
        brow = [HP(0.4, -0.27), HP(0.62, -0.35), HP(0.8, -0.31)]
    else:
        brow = [HP(0.4, -0.3), HP(0.62, -0.35), HP(0.82, -0.28)]
    fig.line(5, brow, shade(hair_c, 0.85) if hair not in ("mono",) else (205, 205, 200), bw)
    # nariz: aleta y sombra bajo la punta
    fig.m("skin", True)
    fig.line(5, [HP(0.86, 0.22), HP(0.93, 0.29), HP(1.0, 0.28)], shade(face_c, 0.68), 0.035 * R)
    fig.line(5, [HP(0.83, -0.02), HP(0.9, 0.12)], shade(face_c, 0.82), 0.025 * R)
    # pómulo y rubor
    fig.m("skin")
    fig.dot(5, HP(0.55, 0.2), 0.15 * R, mixc(face_c, (215, 110, 100), 0.16 + 0.3 * getattr(f, "breath", 0.0)))
    # boca
    lip = shade(face_c, 0.6)
    if expr in ("grito", "dolor", "jadeo"):
        o = 0.13 if expr == "grito" else 0.07
        mouth = [HP(0.71, 0.46), HP(0.92, 0.44), HP(0.9, 0.5 + o), HP(0.8, 0.54 + o), HP(0.73, 0.52 + o * 0.8)]
        fig.poly(5, mouth, (72, 26, 26), outline=False, shade_=False, smooth=1)
        if expr == "grito":
            fig.line(5, [HP(0.74, 0.47), HP(0.9, 0.45)], (238, 232, 222), 0.035 * R, taper=False)
            fig.dot(5, HP(0.8, 0.52 + o), 0.04 * R, (150, 60, 60))
    elif expr == "sonrisa":
        fig.line(5, [HP(0.64, 0.42), HP(0.76, 0.5), HP(0.93, 0.47)], lip, 0.05 * R)
        fig.dot(5, HP(0.82, 0.55), 0.03 * R, shade(face_c, 1.08))
    else:
        fig.line(5, [HP(0.68, 0.49), HP(0.8, 0.5), HP(0.93, 0.47)], lip, 0.045 * R)
        fig.dot(5, HP(0.82, 0.56), 0.03 * R, shade(face_c, 1.08))
    # pelo
    fig.m("hair")
    def arc(r, a0, a1, step=10):
        out = []
        a = a0
        st = step if a1 > a0 else -step
        while (a <= a1) if st > 0 else (a >= a1):
            out.append(HP(-0.08 + r * math.cos(math.radians(a)), -0.05 + r * math.sin(math.radians(a))))
            a += st
        return out

    cap_inner = [HP(-0.66, 0.36), HP(-0.5, 0.08), HP(-0.22, -0.3), HP(0.12, -0.6), HP(0.42, -0.76)]
    if hair in ("corto", "trenza", "coleta", "trenza_nuca", "rastas", "chonmage"):
        r_ = {"corto": 1.08, "trenza": 1.05, "coleta": 1.05, "trenza_nuca": 1.06, "rastas": 1.1, "chonmage": 1.05}[hair]
        fig.poly(5, arc(r_, -62, -212) + cap_inner, hair_c, sheen=2.4 if hair == "chonmage" else 1.0, edge=True)
    elif hair == "rapado":
        fig.poly(5, arc(1.02, -62, -212) + cap_inner, mixc(face_c, hair_c, 0.45), outline=False, sheen=0.3)
    elif hair == "melena":
        pts = [HP(0.8, -0.34), HP(0.74, -0.86)] + arc(1.13, -55, -195) + [HP(-1.08, 0.8), HP(-0.42, 0.88), HP(-0.3, 0.32), HP(0.1, -0.06), HP(0.46, -0.3)]
        fig.poly(5, pts, hair_c, sheen=1.6, edge=True)
        fig.line(5, [HP(0.78, -0.35), HP(0.46, -0.32)], shade(hair_c, 1.4), 0.04 * R)
    elif hair == "rizado":
        fig.poly(5, arc(1.04, -62, -212) + cap_inner, hair_c, sheen=0.8, edge=True)
        for a in range(-66, -220, -22):
            q = HP(-0.08 + 1.02 * math.cos(math.radians(a)), -0.05 + 1.02 * math.sin(math.radians(a)))
            fig.circle(5, q, 0.24 * R, hair_c, sheen=0.9)
    elif hair == "mono":
        fig.poly(5, arc(1.05, -98, -212) + [HP(-0.66, 0.36), HP(-0.5, 0.08), HP(-0.3, -0.42), HP(-0.06, -0.86)], hair_c, sheen=0.8, edge=True)
        fig.circle(5, HP(-0.82, -0.8), 0.3 * R, hair_c, sheen=1.0)
        fig.line(5, [HP(-0.66, -0.66), HP(-0.6, -0.98)], shade(hair_c, 0.7), 0.05 * R)
    if hair == "chonmage":
        tk = [HP(-0.45, -1.02), HP(-0.1, -1.32), HP(0.42, -1.28), HP(0.5, -1.12), HP(0.05, -1.08)]
        fig.poly(5, tk, hair_c, sheen=3.0, edge=True)
        fig.line(5, [HP(-0.32, -1.02), HP(-0.18, -1.2)], (220, 220, 215), 0.06 * R)
    if hair == "coleta":
        fig.circle(5, HP(-0.78, -0.72), 0.14 * R, C("#1B1B1B", Hd[0]), sheen=0.6)
    if hair == "trenza_nuca":
        crown = arc(0.98, -80, -200, 14)
        fig.line(5, crown, shade(hair_c, 0.85), 0.16 * R)
        for i, q in enumerate(crown):
            if i % 2 == 0:
                fig.dot(5, q, 0.07 * R, shade(hair_c, 1.15))
        for i in range(3):
            fig.circle(5, HP(-0.95 - 0.06 * i - sw_h * 0.3, 0.35 + 0.25 * i), (0.17 - 0.03 * i) * R, hair_c, sheen=1.4)
    if hair == "rastas":
        for i, a in enumerate(range(-80, -235, -17)):
            base = HP(-0.08 + 0.98 * math.cos(math.radians(a)), -0.05 + 0.98 * math.sin(math.radians(a)))
            dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
            ln = 0.55 + 0.25 * ((i * 7) % 3) / 2
            tip = HP(-0.08 + (0.98 + ln) * dx - sw_h * (1 + i * 0.1), -0.05 + (0.98 + ln) * dy + 0.45)
            fig.tube(5, base, tip, 0.13 * R, 0.1 * R, hair_c, sheen=0.9)
    # mechones: el pelo deja de ser un casco y tiene forma
    fig.m("hair")

    def lock(a_deg, r0, length, bend, w, down=0.0):
        a = math.radians(a_deg)
        b = math.radians(a_deg + bend)
        base = HP(-0.08 + r0 * math.cos(a), -0.05 + r0 * math.sin(a))
        ctrl = HP(-0.08 + (r0 + length * 0.55) * math.cos((a + b) * 0.5), -0.05 + (r0 + length * 0.55) * math.sin((a + b) * 0.5) + down * 0.5)
        tip = HP(-0.08 + (r0 + length) * math.cos(b) - sw_h * 0.6, -0.05 + (r0 + length) * math.sin(b) + down)
        fig.poly(5, lock_pts(base, ctrl, tip, w * R), hair_c, sheen=1.2, smooth=0)

    if hair == "corto":
        for a_deg, ln, bend in ((-78, 0.32, -22), (-100, 0.36, -24), (-124, 0.34, -26), (-150, 0.3, -24), (-176, 0.26, -20)):
            lock(a_deg, 0.98, ln, bend, 0.42)
        fig.poly(5, lock_pts(HP(0.3, -0.92), HP(0.58, -0.86), HP(0.72, -0.64), 0.32 * R), hair_c, sheen=1.2, smooth=0)
    elif hair in ("trenza", "trenza_nuca", "coleta"):
        # mechones sueltos en la sien y el flequillo
        fig.poly(5, lock_pts(HP(0.38, -0.86), HP(0.62, -0.7), HP(0.64, -0.42), 0.2 * R), hair_c, sheen=1.2, smooth=0)
        fig.poly(5, lock_pts(HP(0.1, -0.8), HP(0.18, -0.4), HP(0.12 - sw_h, -0.02), 0.14 * R), hair_c, sheen=1.2, smooth=0)
    elif hair == "melena":
        for x0, x1 in ((-1.02, -1.08), (-0.82, -0.86), (-0.6, -0.62)):
            fig.poly(5, lock_pts(HP(x0, 0.5), HP(x0 - 0.02, 0.7), HP(x1 - sw_h * 0.4, 0.98), 0.24 * R), hair_c, sheen=1.4, smooth=0)
        for x0 in (0.5, 0.64):
            fig.poly(5, lock_pts(HP(x0, -0.8), HP(x0 + 0.08, -0.55), HP(x0 + 0.1, -0.34), 0.16 * R), hair_c, sheen=1.4, smooth=0)
    elif hair == "mono":
        for a_deg in (-120, -150, -178):
            lock(a_deg, 1.0, 0.18, -14, 0.22)
    if hair not in ("rapado",):
        # banda de brillo del pelo (en la GPU la pone la luz; en CPU se pinta)
        fig.m("hair", True)
        fig.line(5, [HP(-0.5, -0.82), HP(-0.1, -1.0), HP(0.3, -0.92)], shade(hair_c, 1.6), 0.05 * R)

    # rasgos
    fig.m("skin")
    if "cicatriz" in ex:
        fig.line(5, [HP(0.6, -0.44), HP(0.7, -0.24)], mixc(face_c, (245, 220, 210), 0.6), 0.03 * R)
    fig.m("hair")
    if "barba" in ex:
        beard = [HP(0.9, 0.36), HP(0.95, 0.5), HP(0.9, 0.7), HP(0.76, 0.92), HP(0.48, 1.0), HP(0.08, 0.86), HP(-0.14, 0.56),
                 HP(-0.16, 0.34), HP(0.1, 0.42), HP(0.5, 0.56), HP(0.7, 0.44)]
        fig.poly(5, beard, hair_c, sheen=0.4, edge=True)
        fig.poly(5, [HP(0.72, 0.47), HP(0.9, 0.47), HP(0.88, 0.53), HP(0.74, 0.54)], (80, 34, 30), outline=False, shade_=False)
    fig.m("hair")
    if "bigote" in ex:
        fig.line(5, [HP(0.66, 0.42), HP(0.8, 0.37), HP(0.94, 0.38)], hair_c, 0.06 * R)
    fig.m("skin", True)
    if "ojeras" in ex:
        fig.line(5, [HP(0.5, -0.01), HP(0.62, 0.04), HP(0.72, 0.0)], shade(face_c, 0.75), 0.035 * R)
    fig.m("skin")
    if "pecas" in ex:
        for dx, dy in ((0.5, 0.1), (0.58, 0.16), (0.66, 0.1), (0.44, 0.2), (0.62, 0.24), (0.72, 0.2)):
            fig.dot(5, HP(dx, dy), 0.025 * R, shade(face_c, 0.72))
    fig.m("skin", True)
    if "arrugas" in ex:
        for dy in (-0.02, 0.06):
            fig.line(5, [HP(0.38, -0.12 + dy), HP(0.46, -0.1 + dy * 1.4)], shade(face_c, 0.72), 0.02 * R)
        for y in (-0.56, -0.48):
            fig.line(5, [HP(0.25, y), HP(0.5, y - 0.02), HP(0.7, y + 0.04)], shade(face_c, 0.8), 0.02 * R)
        fig.line(5, [HP(0.62, 0.3), HP(0.66, 0.5)], shade(face_c, 0.75), 0.02 * R)
    fig.m("hair")
    if "barba_rala" in ex:
        bc = (226, 224, 218)
        for i, (x0_, y0_) in enumerate(((0.84, 0.66), (0.76, 0.8), (0.62, 0.88), (0.5, 0.86))):
            fig.line(5, [HP(x0_, y0_), HP(x0_ - 0.08 - sw_h * 0.4, y0_ + 0.42 - 0.05 * i)], bc, 0.035 * R)
        fig.line(5, [HP(0.7, 0.4), HP(0.94, 0.4)], bc, 0.03 * R)
    fig.m("skin")
    if "manchas" in ex:
        for dx, dy in ((0.1, -0.5), (0.3, -0.2), (-0.2, -0.6)):
            fig.dot(5, HP(dx, dy), 0.035 * R, shade(face_c, 0.8))
    fig.m("cloth")
    if "mongkol" in ex:
        mc = C(lk["accent"], Hd[0])
        ring = [HP(0.68, -0.6), HP(0.2, -0.9), HP(-0.5, -0.82), HP(-1.0, -0.42)]
        fig.line(5, ring, mc, 0.16 * R)
        for q in ring[1:-1]:
            fig.dot(5, q, 0.07 * R, shade(mc, 0.75))
        tail = HP(-1.0, -0.42)
        fig.line(5, [tail, HP(-1.35 - sw_h, 0.1), HP(-1.45 - sw_h * 1.5, 0.55)], mc, 0.1 * R)
    fig.m("cloth")
    if getattr(f, "state", None) == Fighter.INTRO and fd["id"] == "matias" and f.timer < 90:
        # pañuelo albiceleste al cuello, que lanza al público al empezar
        sc_ = C("#75AADB", N[0])
        fig.poly(5, [S(1.0, wf(1.0) + 2.5), S(1.0, -wb(1.0) - 2.5), _lerp2(S(1.0, -wb(1.0)), Hd, 0.2), _lerp2(S(1.0, wf(1.0)), Hd, 0.2)], sc_)
        fig.tube(5, S(0.98, wf(0.98) + 2), (S(0.98, wf(0.98))[0] + 4 + sw_h * 20, S(0.98, 0)[1] + 22), 2.4, 2.0, sc_, sheen=1.2)
        fig.line(5, [S(0.98, wf(0.98) + 2), (S(0.98, wf(0.98))[0] + 4 + sw_h * 20, S(0.98, 0)[1] + 22)], C("#FFFFFF", N[0]), 0.7)

    # ── brazos ───────────────────────────────────────────────────────────────
    fig.zone(cloth=top_pat)
    up0, up1, upb = B["upper"]
    fo0, fo1, fob = B["fore"]
    sleeve = lk.get("sleeve", 0.0) if top in ("gi", "kurtka", "changshan", "keikogi") else 0.0
    open_hand = (f.state == Fighter.THROWING or (f.state == Fighter.ATTACK and f.move is not None and f.move.anim == "grab")
                 or (lk.get("guard_open") and f.state in (Fighter.IDLE, Fighter.WALK, Fighter.INTRO)))
    def hand_frame(el, hd):
        fx_, fy_ = _norm(hd[0] - el[0], hd[1] - el[1])
        if fx_ == 0 and fy_ == 0:
            fx_ = 1.0
        px_, py_ = -fy_, fx_
        return lambda a, b: (hd[0] + fx_ * a + px_ * b, hd[1] + fy_ * a + py_ * b)

    def arm_ik(sh, el0, hd0):
        """Brazo de huesos fijos (cinemática inversa de dos huesos). El esqueleto de poses coloca codo y mano
        libremente (de 30 a 85 cm de brazo según la pose); aquí el brazo y el antebrazo miden lo que miden,
        el codo dobla hacia donde lo pide la pose y, si la mano no llega, el hombro se adelanta y el brazo se
        estira un poco, como en la animación de lucha."""
        U, F = B.get("arm", (30.0, 25.5))
        dx, dy = hd0[0] - sh[0], hd0[1] - sh[1]
        D = math.hypot(dx, dy) or 1e-6
        ux, uy = dx / D, dy / D
        reach = (U + F) * 0.985
        if D > reach:                                  # protracción del hombro hacia el objetivo
            pro = min(3.0, D - reach)
            sh = (sh[0] + ux * pro, sh[1] + uy * pro)
            D -= pro
        k = clamp(D / reach, 1.0, 1.2)                 # y un estirón de animación
        U, F = U * k, F * k
        D = clamp(D, abs(U - F) + 2.0, (U + F) * 0.995)
        hd = (sh[0] + ux * D, sh[1] + uy * D)
        a = (U * U + D * D - F * F) / (2 * D)          # distancia del hombro al pie de la perpendicular
        h = math.sqrt(max(0.0, U * U - a * a))
        side_ = (el0[0] - sh[0]) * (-uy) + (el0[1] - sh[1]) * ux     # ¿a qué lado dobla el codo de la pose?
        if abs(side_) < 1.0:
            side_ = 1.0 if ux >= 0 else -1.0                         # recto en la pose: codo hacia abajo
        s_ = 1.0 if side_ > 0 else -1.0
        el = (sh[0] + ux * a - uy * h * s_, sh[1] + uy * a + ux * h * s_)
        return sh, el, hd

    def bend_sides(sh, el, hd, flex, ext):
        """Perfiles (izquierdo, derecho) de la cinta: el flexor va por dentro del doblez y el extensor fuera."""
        cr = (el[0] - sh[0]) * (hd[1] - el[1]) - (el[1] - sh[1]) * (hd[0] - el[0])
        return (flex, ext) if cr >= 0 else (ext, flex)

    for side, g, depth in (("l", 0, 0.84), ("r", 6, 1.0)):
        sh, el, hd = arm_ik(J["sh_" + side], J["el_" + side], J["hd_" + side])
        fig.m("skin")
        sk = C(skin, el[0], depth)
        fore_sk = mixc(sk, (205, 80, 70), min(0.4, 0.4 * red)) if not mono else sk
        L1, L2 = dist(sh, el), dist(el, hd)
        fx_, fy_ = _norm(el[0] - sh[0], el[1] - sh[1])
        # raíz del brazo: hacia atrás y un poco hacia arriba, dentro del torso, para que la manga o el deltoides
        # cubran la parte alta del pecho y la espalda como un hombro, y no nazca un tubo pegado al borde
        # (el brazo de detrás ya queda tapado por el torso: su raíz va hacia dentro, no asoma por la espalda)
        back_ = (-1.0, 0.12) if side == "r" else (0.55, 0.2)
        cap = (sh[0] + back_[0] * B["delt"] * 1.05, sh[1] + back_[1] * B["delt"] * 1.05)
        if sleeve > 0:
            # antebrazo que asoma por la manga
            fig.ribbon(g, [el, _lerp2(el, hd, 0.5), hd], [(0, fo0 + fob * 0.5), (0.3, fo0 + fob * 0.6), (0.85, fo1 * 0.95), (1, fo1 * 0.9)],
                       [(0, fo0), (0.3, fo0 + fob), (0.85, fo1 * 0.95), (1, fo1 * 0.9)], fore_sk, sheen=skin_sheen)
            fig.m("cloth")
            slc = C(cloth, el[0], depth)
            cut = _lerp2(el, hd, sleeve)
            wide = 3.4 if top != "changshan" else 1.4
            Ls = dist(cap, sh) + L1 + dist(el, cut)
            us, ue = dist(cap, sh) / Ls, (dist(cap, sh) + L1) / Ls
            # manga de caída: hombro redondo, sin bulto en el codo, se abre hacia el puño y cuelga por debajo
            # por dentro del codo la tela se recoge; por fuera se tensa y marca la punta del codo
            # la raíz se estrecha dentro del torso: el contorno se curva hacia el pecho como una costura raglán
            inner = [(0, B["delt"] * 0.3), (us, B["delt"] + 0.4), (ue * 0.75, up0 + 1.9), (ue, up1 + 1.6), (1, fo1 + wide - 0.2)]
            outer = [(0, B["delt"] * 0.3), (us, B["delt"] + 0.6), (ue * 0.75, up0 + 2.2), (ue, up1 + 2.9), (1, fo1 + wide + 0.6)]
            kl_, kr_ = bend_sides(sh, el, hd, inner, outer)
            fig.ribbon(g, [cap, sh, el, cut], kl_, kr_, slc, sheen=0.5, edge=True)
            # puño de la manga: refuerzo con pespuntes
            fig.m("cloth", pat="stitch")
            c0 = _lerp2(el, cut, 0.84)
            fig.ribbon(g, [c0, _lerp2(c0, cut, 0.5), cut], [(0, fo1 + wide + 0.5), (1, fo1 + wide + 0.75)],
                       [(0, fo1 + wide - 0.2), (1, fo1 + wide - 0.05)], shade(slc, 0.95), sheen=0.4)
            fig.m("cloth", True)
            # pliegues del codo, del lado de dentro del doblez
            cr = (el[0] - sh[0]) * (hd[1] - el[1]) - (el[1] - sh[1]) * (hd[0] - el[0])
            nx_, ny_ = _norm(-(el[1] - sh[1]), el[0] - sh[0])
            sgn = 1.0 if cr >= 0 else -1.0
            for o_, w_ in ((0.45, 0.8), (0.15, 0.6)):
                off = (nx_ * sgn * up1 * o_, ny_ * sgn * up1 * o_)
                fig.line(g, [(p_[0] + off[0], p_[1] + off[1]) for p_ in (_lerp2(sh, el, 0.7), el, _lerp2(el, cut, 0.25))],
                         shade(slc, 0.8), w_)
            if top == "changshan":
                fig.m("cloth")
                fig.line(g, [_lerp2(el, cut, 0.9), cut], C(lk["accent"], el[0], depth), 0.5)
        else:
            fig.m("skin")
            arm_c = fore_sk if red > 0.15 else sk
            ue = L1 / max(1e-6, L1 + L2)
            # flexores por dentro del doblez (bíceps, pliegue del codo, braquiorradial) y extensores por fuera
            # (tríceps, punta del codo, antebrazo que se afina hacia la muñeca)
            flex = [(0.0, up0 * 0.95), (ue * 0.5, up0 * 0.9 + upb), (ue * 0.9, up1 * 0.82), (ue, up1 * 0.72),
                    (ue + (1 - ue) * 0.2, fo0 * 0.95 + fob * 0.85), (ue + (1 - ue) * 0.7, fo1 * 1.06), (1.0, fo1 * 0.86)]
            extn = [(0.0, up0 * 1.02), (ue * 0.38, up0 * 0.98 + upb * 0.65), (ue * 0.85, up1 * 0.98), (ue, up1 * 1.06),
                    (ue + (1 - ue) * 0.16, fo0 * 1.0 + fob * 0.45), (ue + (1 - ue) * 0.7, fo1 * 1.06), (1.0, fo1 * 0.86)]
            kl_, kr_ = bend_sides(sh, el, hd, flex, extn)
            fig.ribbon(g, [sh, el, hd], kl_, kr_, arm_c, sheen=skin_sheen)
            # deltoides: la cápsula del hombro que cubre la articulación
            dtip = _lerp2(sh, el, 0.45)
            fig.ribbon(g, [cap, sh, dtip], [(0, B["delt"] * 0.6), (0.35, B["delt"] * 0.92), (1, up0 * 0.55)],
                       [(0, B["delt"] * 0.55), (0.35, B["delt"] * 0.85), (1, up0 * 0.5)], arm_c, sheen=skin_sheen)
            fig.m("skin", True)
            cr = (el[0] - sh[0]) * (hd[1] - el[1]) - (el[1] - sh[1]) * (hd[0] - el[0])
            nx_, ny_ = _norm(-(el[1] - sh[1]), el[0] - sh[0])
            sgn = 1.0 if cr >= 0 else -1.0
            fig.line(g, [(el[0] + nx_ * sgn * up1 * 0.45, el[1] + ny_ * sgn * up1 * 0.45),
                         _lerp2(el, (el[0] + nx_ * sgn * up1 * 0.7, el[1] + ny_ * sgn * up1 * 0.7), 1.0)], shade(sk, 0.8), 0.4)   # pliegue del codo
            if "definido" in ex or lk["body"] in ("atleta", "pesado", "fibroso"):
                fig.line(g, [_lerp2(sh, el, 0.3), _lerp2(sh, el, 0.55), _lerp2(sh, el, 0.75)], shade(sk, 0.82), 0.5)
                fig.line(g, [_lerp2(el, hd, 0.1), _lerp2(el, hd, 0.4)], shade(sk, 0.84), 0.45)
        if "prajied" in ex:
            fig.m("cloth")
            c1, c2 = lk["band"]
            pa = _lerp2(sh, el, 0.34)
            pb = _lerp2(sh, el, 0.44)
            # cordón trenzado que abraza el bíceps: una banda, no un disco
            nx_, ny_ = -fy_, fx_
            ra_, rb_ = up0 + 0.45, up0 + 0.35
            band = [(pa[0] + nx_ * ra_, pa[1] + ny_ * ra_), (pb[0] + nx_ * rb_, pb[1] + ny_ * rb_),
                    (pb[0] - nx_ * rb_, pb[1] - ny_ * rb_), (pa[0] - nx_ * ra_, pa[1] - ny_ * ra_)]
            fig.poly(g, band, C(c1, el[0], depth), sheen=0.8, smooth=0)
            fig.m("cloth", True)
            for q_ in (0.3, 0.7):
                m_ = _lerp2(pa, pb, q_)
                fig.line(g, [(m_[0] + nx_ * ra_, m_[1] + ny_ * ra_), (m_[0] - nx_ * ra_, m_[1] - ny_ * ra_)], shade(C(c1, el[0], depth), 0.8), 0.3)
            fig.m("cloth")
            fig.line(g, [_lerp2(pa, pb, 0.5), (pa[0] - 2 - vxl, pa[1] + 5), (pa[0] - 4 - vxl * 1.5, pa[1] + 10)], C(c2, el[0], depth), 0.8)
        # mano
        HF = hand_frame(el, hd)
        hs = B["hand"] / 4.3
        nail = mixc(fore_sk, (238, 210, 200), 0.35)
        if "guantes" in ex:
            fig.m("leather")
            gc = C("#1A1A1A", hd[0], depth)
            glove = [HF((a - 3) * hs * 1.35, b * hs * 1.35) for a, b in ((-2, -4.2), (3.5, -5.0), (8.2, -3.6), (9, 0), (8.2, 3.8), (3, 4.8), (-2, 4.0))]
            fig.poly(g, glove, gc, sheen=2.4, smooth=2)
            fig.line(g, [HF(-5.8 * hs, -4.4 * hs), HF(-5.8 * hs, 4.4 * hs)], C("#3A3A3A", hd[0], depth), 1.6, taper=False)
            fig.m("leather", True)
            fig.line(g, [HF(6.0 * hs, -3.0 * hs), HF(7.4 * hs, 0), HF(6.0 * hs, 3.0 * hs)], shade(gc, 1.6), 0.3)
        elif open_hand:
            fig.m("skin")
            H_ = lambda a, b: HF(a * hs, b * hs)
            palm = [H_(a, b) for a, b in ((-1.5, -2.8), (3.5, -3.7), (8.6, -3.3), (9.4, -1.0), (9.2, 1.6), (8.4, 3.3), (3.5, 3.4), (-1.5, 2.7))]
            fig.poly(g, palm, fore_sk, sheen=skin_sheen, smooth=2)
            # dedos delante de la palma, de meñique a índice: dos falanges con curva y contorno propio
            for bk, ln, rr in ((2.45, 5.6, 0.82), (0.82, 7.0, 0.9), (-0.82, 7.6, 0.93), (-2.45, 7.0, 0.9)):
                k0 = H_(7.6, bk)
                k1 = H_(7.6 + ln * 0.6, bk + 0.3 + ln * 0.05)
                k2 = H_(7.6 + ln * 1.0, bk + 1.2 + ln * 0.12)
                fig.tube(g, k0, k1, rr * hs, rr * 0.93 * hs, fore_sk, sheen=skin_sheen * 0.6)
                fig.tube(g, k1, k2, rr * 0.93 * hs, rr * 0.78 * hs, fore_sk, sheen=skin_sheen * 0.6)
            # pulgar: sale de la base de la palma y se abre hacia delante
            t0, t1, t2 = H_(2.0, -2.6), H_(5.4, -4.9), H_(8.4, -5.4)
            fig.tube(g, t0, t1, 1.35 * hs, 1.15 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.tube(g, t1, t2, 1.12 * hs, 0.9 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.m("skin", True)
            fig.line(g, [H_(3.0, 0.8), H_(6.0, 0.2), H_(8.6, 0.9)], shade(fore_sk, 0.8), 0.45)        # línea de la palma
        else:
            fig.m("skin")
            H_ = lambda a, b: HF((a - 2.6) * hs, b * hs)
            fist = [H_(a, b) for a, b in ((-1.6, -3.4), (3.0, -4.1), (6.6, -3.9), (7.9, -2.4), (8.2, 0.6), (7.6, 3.3), (5.4, 4.3), (1.2, 4.0), (-1.6, 3.2))]
            fig.poly(g, fist, fore_sk, sheen=skin_sheen, smooth=2)
            # dedos plegados sobre la cara del puño: cuatro falanges redondeadas, cada una con su contorno
            for i, bk in enumerate((-2.55, -0.85, 0.85, 2.55)):
                r_ = (0.98 - 0.06 * abs(i - 1.5)) * hs
                fig.tube(g, H_(8.0, bk), H_(5.2, bk + 0.5), r_, r_ * 0.95, fore_sk, sheen=skin_sheen * 0.6)
            # pulgar cruzado sobre índice y corazón
            fig.tube(g, H_(1.0, -3.4), H_(4.4, -2.3), 1.3 * hs, 1.15 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.tube(g, H_(4.4, -2.3), H_(6.6, -0.2), 1.12 * hs, 0.95 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.m("skin")
            fig.dot(g, H_(6.6, -0.2), 0.42 * hs, nail)
        if "manchas" in ex:
            fig.dot(g, (hd[0] - 1, hd[1] - 1), 0.5, shade(fore_sk, 0.8))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  RENDER CON SUPERMUESTREO
# ─────────────────────────────────────────────────────────────────────────────
def render_figure(f, pose, U, rig, ss=2, outline=OUTLINE, ow_px=2.0, flat=None, maps=False, id_base=1, pieces=None):
    """Dibuja la figura en una superficie propia. U: píxeles finales por cm.

    Devuelve (superficie, x_raíz, y_raíz). Con maps=True devuelve (albedo, datos, x_raíz, y_raíz):
    color plano sin luz horneada más el mapa de altura y material para la iluminación por píxel.
    """
    fig = build_figure(f, pose, rig, mono=flat is not None, raw=maps)
    sc, fac = f.scale, f.facing
    xs = [p[0] for p in pose.values()]
    ys = [p[1] for p in pose.values()]
    pad = 52
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad - 12, max(ys) + pad * 0.6
    k = sc * U * ss
    w = max(2, int((maxx - minx) * k) + 2)
    h = max(2, int((maxy - miny) * k) + 2)
    canvas = pygame.Surface((w, h), pygame.SRCALPHA)
    if fac == 1:
        def T(p):
            return ((p[0] - minx) * k, (p[1] - miny) * k)
        rx = -minx * k
    else:
        def T(p):
            return ((maxx - p[0]) * k, (p[1] - miny) * k)
        rx = maxx * k
    ry = -miny * k
    shared: dict = {}
    _render(fig, canvas, T, k, ow_px * ss, outline, flat=flat, ss=ss, fac=fac, baked=not maps, cache=shared)
    size = (max(1, int(w / ss)), max(1, int(h / ss))) if ss > 1 else (w, h)
    data = None
    if maps:
        # el mapa de datos va directo a resolución final: la GPU lo desenfoca igualmente
        data = pygame.Surface(size, pygame.SRCALPHA)
        k1 = k / ss
        if fac == 1:
            def T1(p):
                return ((p[0] - minx) * k1, (p[1] - miny) * k1)
        else:
            def T1(p):
                return ((maxx - p[0]) * k1, (p[1] - miny) * k1)
        _render_data(fig, data, T1, k1, ow_px, fac=fac, shared=shared, inv=1.0 / ss, id_base=id_base, pieces=pieces)
    if ss > 1:
        canvas = pygame.transform.smoothscale(canvas, size)
        rx, ry = rx / ss, ry / ss
    if maps:
        return canvas, data, rx, ry
    return canvas, rx, ry


def draw_fighter(surf, f: Fighter, cam: Camera, rig: dict, cfg: dict, debug=False, lut_bw=False, data_surf=None, pieces=None):
    """Luchador en pantalla: sombra (renderer CPU), figura supermuestreada y cajas de depuración."""
    pose = fighter_pose(f)
    if f.squash > 0.02 and f.y >= 0:
        q = 1 - 0.12 * f.squash
        pose = {k: (v[0] * (2 - q), v[1] * q) for k, v in pose.items()}
    f.lastpose = pose
    sc = f.scale
    if cfg.get("shadows", True):
        sx, sy = cam.P(f.x, 0)
        hgt = max(0.0, -f.y)
        sw = cam.S(70 * sc * f.width) * (1 - min(0.5, hgt / 400))
        sh = cam.S(14) * (1 - min(0.5, hgt / 400))
        shadow = pygame.Surface((int(sw * 2) + 2, int(sh * 2) + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (10, 6, 4, int(90 * (1 - min(0.6, hgt / 300)))), shadow.get_rect())
        surf.blit(shadow, (sx - sw + cam.S(12), sy - sh))
    active = f.state in (Fighter.ATTACK, Fighter.KIAI) and f.move is not None and f.move.startup <= f.timer < f.move.startup + f.move.active
    rim = (1.0 if active else f.rim) if cfg.get("rim_ink", True) else 0.0
    ocol = mixc(OUTLINE, f.player_color, min(1.0, rim)) if not lut_bw else OUTLINE
    ow = (2.0 + 1.4 * min(1.0, rim)) * RS
    # supermuestreo adaptativo: con el luchador grande en pantalla basta con menos muestras
    ss = max(1.0, min(cfg.get("ss", 2), cfg.get("ss_px", 2.7) / max(0.1, cam.S(1))))
    x0, y0 = cam.P(f.x, f.y)
    if data_surf is not None:
        mine: list = []
        base = len(pieces) + 1 if pieces is not None else 1
        img, data, rx, ry = render_figure(f, pose, cam.S(1), rig, ss=ss, outline=ocol, ow_px=ow, maps=True,
                                          id_base=base, pieces=mine)
        ox, oy = int(x0 - rx), int(y0 - ry)
        data_surf.blit(data, (ox, oy))
        if pieces is not None:      # anclas de las piezas en coordenadas de pantalla
            pieces.extend((ax + ox, ay + oy) + tuple(rest) for ax, ay, *rest in mine)
    else:
        img, rx, ry = render_figure(f, pose, cam.S(1), rig, ss=ss, outline=ocol, ow_px=ow)
    drawn = surf.blit(img, (int(x0 - rx), int(y0 - ry)))
    # estela: cuando el luchador registra una nueva pose de estela, se guarda su silueta real
    if f.trail and getattr(f, "_trail_seen", None) is not f.trail[-1]:
        f._trail_seen = f.trail[-1]
        sil = pygame.mask.from_surface(img, 100).to_surface(setcolor=(*f.player_color, 150), unsetcolor=(0, 0, 0, 0))
        imgs = getattr(f, "_trail_imgs", [])
        imgs.append((sil, f.x, f.y, rx, ry, f.anim_t))
        f._trail_imgs = imgs[-5:]
    if debug:
        hb = f.hurtbox()
        p1 = cam.P(hb.left, hb.top)
        p2 = cam.P(hb.right, hb.bottom)
        pygame.draw.rect(surf, (80, 200, 255), (p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]), 1)
        at = f.hitbox()
        if at:
            p1 = cam.P(at.left, at.top)
            p2 = cam.P(at.right, at.bottom)
            pygame.draw.rect(surf, (255, 60, 60), (p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]), 2)
    return drawn.inflate(int(cam.S(40)), int(cam.S(40))) if debug else drawn


def draw_trail(layer, f: Fighter, cam: Camera):
    """Estelas (afterimages): siluetas de las poses recientes en el color del jugador, que se desvanecen."""
    imgs = [t for t in getattr(f, "_trail_imgs", []) if f.anim_t - t[5] < 0.26]
    f._trail_imgs = imgs
    rects = []
    for sil, wx, wy, rx, ry, t0 in imgs:
        age = (f.anim_t - t0) / 0.26
        x0, y0 = cam.P(wx, wy)
        sil.set_alpha(int(105 * (1 - age)))
        rects.append(layer.blit(sil, (int(x0 - rx), int(y0 - ry))))
    if imgs or not f.trail:
        return rects
    sc = f.scale
    for pose, x, y, fac, life in f.trail:
        a = int(70 * life / 14)
        c = (*f.player_color, a)
        J = {k: cam.P(x + v[0] * fac * sc, y + v[1] * sc) for k, v in pose.items()}
        r = max(1, int(cam.S(7 * sc * f.width)))
        for a_, b_ in (("sh_l", "el_l"), ("el_l", "hd_l"), ("sh_r", "el_r"), ("el_r", "hd_r"), ("hip", "kn_l"), ("kn_l", "ft_l"), ("hip", "kn_r"), ("kn_r", "ft_r"), ("hip", "chest")):
            rects.append(pygame.draw.line(layer, c, J[a_], J[b_], r * 2))
        rects.append(pygame.draw.circle(layer, c, (int(J["head"][0]), int(J["head"][1])), int(cam.S(13 * sc))))
    return rects


# ─────────────────────────────────────────────────────────────────────────────
#  RETRATOS Y FIGURAS DE MENÚ
# ─────────────────────────────────────────────────────────────────────────────
class PoseDummy:
    """Lo mínimo que necesitan fighter_pose y build_figure para dibujar fuera de un combate."""

    def __init__(self, fd, facing=1, t=0.0, state=None):
        self.fd = fd
        self.facing = facing
        self.anim_t = t
        self.state = Fighter.IDLE if state is None else state
        self.timer = 200
        self.breath = 0.0
        self.scale = fd["h"]
        self.width = fd["w"]
        self.x = self.y = self.vx = self.vy = 0.0
        self.crouching = False
        self.move = None
        self.move_key = ""
        self.stun_len = 10
        self.throw_partner = None
        self.throw_phase = 0.0
        self.ground_attacker = False
        self.kiai_seq = []
        self.kiai_idx = 0
        self.sweat = 0.15
        self.redness = 0.0
        self.dishevel = 0.0
        self.exhausted = False
        self.squash = 0.0
        self.rim = 0.0
        self.player_color = (255, 170, 80)


_PORTRAITS: Dict[tuple, pygame.Surface] = {}


def draw_figure_preview(surf, fd, feet_x, feet_y, height_px, facing=1, t=0.0, rig=None, ss=2, state=None):
    """Figura de cuerpo entero para menús (selección, VS, resultados)."""
    rig = rig or LIGHT_RIGS[1]
    d = PoseDummy(fd, facing, t, state)
    pose = fighter_pose(d)
    U = height_px / (190.0 * fd["h"])
    img, rx, ry = render_figure(d, pose, U, rig, ss=ss)
    surf.blit(img, (int(feet_x - rx), int(feet_y - ry)))


def draw_portrait(surf, fd: dict, rect: pygame.Rect, rig: dict, selected=False, locked=False):
    """Retrato de busto: la misma figura del combate, encuadrada en cabeza y hombros. Se cachea."""
    key = (fd["id"], rect.w, rect.h, locked)
    img = _PORTRAITS.get(key)
    if img is None:
        pal = [hex2rgb(c) for c in fd["pal"]]
        img = pygame.Surface(rect.size)
        top_c = shade(mixc(pal[0], (40, 30, 24), 0.55), 0.5) if not locked else (22, 22, 24)
        for i in range(rect.h):
            img.fill(mixc(top_c, (10, 8, 8), i / rect.h), (0, i, rect.w, 1))
        glow = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.circle(glow, (255, 220, 170, 40), (int(rect.w * 0.3), int(rect.h * 0.3)), int(rect.w * 0.55))
        img.blit(glow, (0, 0))
        d = PoseDummy(fd, 1, 0.0)
        pose = fighter_pose(d)
        U = rect.h / 62.0
        flat = (14, 12, 12) if locked else None
        fig_img, rx, ry = render_figure(d, pose, U, LIGHT_RIGS[1], ss=2, flat=flat)
        hx_, hy_ = pose["head"]
        head_x = rx + hx_ * d.scale * U
        head_y = ry + hy_ * d.scale * U
        img.blit(fig_img, (int(rect.w * 0.46 - head_x), int(rect.h * 0.4 - head_y)))
        _PORTRAITS[key] = img
    surf.blit(img, rect.topleft)
    pygame.draw.rect(surf, (255, 220, 160) if selected else (60, 50, 44), rect, 3 if selected else 1)
