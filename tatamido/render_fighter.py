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
from .ui import draw as ui_draw, native as ui_native

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
        self.joints: dict = {}            # articulaciones finales (tras la cinemática inversa): tests de huesos
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

    def gloss(self, g, c, r, sheen):
        """Zona de brillo (frente, nariz, pómulo, hombro, nudillos): no se pinta en color; en el mapa de datos
        sube el brillo de la piel que ya hay debajo, sin tocar la altura."""
        self._add(g, "gloss", (c, r), (0, 0, 0), False, False, sheen, False, False)

    def bump(self, g, c, ra, rb, ang, h, sheen):
        """Relieve suave (vientre de un músculo): sólo en el mapa de datos, anillos elípticos de altura creciente
        hacia el centro sobre la pieza de debajo. `ra` a lo largo de `ang`, `rb` de través, `h` en unidades de altura."""
        self._add(g, "bump", (c, ra, rb, ang, h), (0, 0, 0), False, False, sheen, False, False)


# ─────────────────────────────────────────────────────────────────────────────
#  GRABADOR: los mismos trazos, en triángulos para la GPU
# ─────────────────────────────────────────────────────────────────────────────
def _zipper(pts):
    """Cremallera entre las dos mitades de un polígono (de su primer vértice al más lejano): triángulos en O(n)
    para lo que no es estrellado (miembros doblados, mechones curvos: dos lados y dos tapas)."""
    n = len(pts)
    x0, y0 = pts[0]
    i1 = max(range(n), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
    A = pts[:i1 + 1]
    B = [pts[0]] + pts[:i1 - 1:-1] if i1 > 0 else [pts[0]]
    a = b = 0
    la, lb = len(A) - 1, len(B) - 1
    out = []
    while a < la or b < lb:
        pa, pb = A[a], B[b]
        if b >= lb or (a < la and (A[a + 1][0] - pb[0]) ** 2 + (A[a + 1][1] - pb[1]) ** 2 <=
                       (B[b + 1][0] - pa[0]) ** 2 + (B[b + 1][1] - pa[1]) ** 2):
            q = A[a + 1]
            a += 1
        else:
            q = B[b + 1]
            b += 1
        out += (pa, pb, q)
    return out


class Recorder:
    """Hace de superficie para _render/_render_data: en vez de rasterizar, guarda los trazos en coordenadas de
    pantalla y al final los convierte en triángulos (x, y, r, g, b, a), en el mismo orden, para que la GPU los
    pinte de una vez. Rellenos: abanico desde el centroide si el polígono es estrellado respecto a él (cabezas,
    manos, torsos, cápsulas), calculado con numpy para todos a la vez; si no, cremallera. Los rellenos son
    opacos: si en un doblez muy cerrado dos triángulos se solapan, no se nota."""

    def __init__(self, data=False):
        self.ops = []
        self.data = data

    @staticmethod
    def _c(col):
        if len(col) > 3:
            return (col[0] / 255.0, col[1] / 255.0, col[2] / 255.0, col[3] / 255.0)
        return (col[0] / 255.0, col[1] / 255.0, col[2] / 255.0, 1.0)

    def polygon(self, col, pts, width=0):
        if not isinstance(pts, list):
            pts = list(pts)
        if len(pts) < 3:
            if len(pts) == 2:
                self.ops.append((1, pts, self._c(col), max(1, width), False))
            return
        if width:
            self.ops.append((1, pts, self._c(col), width, True))
        else:
            self.ops.append((0, pts, self._c(col)))

    def circle(self, col, c, r, width=0):
        r = float(r)
        n = int(clamp(r * 0.9 + 8, 10, 48))
        pts = [(c[0] + math.cos(6.283185307 * i / n) * r, c[1] + math.sin(6.283185307 * i / n) * r) for i in range(n)]
        if width:
            self.ops.append((1, pts, self._c(col), width, True))
        else:
            self.ops.append((0, pts, self._c(col)))

    def lines(self, col, closed, pts, width=1):
        pts = list(pts)
        if len(pts) >= 2:
            self.ops.append((1, pts, self._c(col), width, closed))

    def triangles(self):
        """Todos los triángulos como array (n, 6) float32, en orden de pintado, calculados en bloque."""
        return Recorder.triangles_of([self])[0]

    @staticmethod
    def triangles_of(recs):
        """Los triángulos de varios grabadores en una sola pasada de numpy (el color y los datos de una figura):
        devuelve una lista de arrays, uno por grabador."""
        import numpy as np
        ops = [o for r in recs for o in r.ops]
        bounds_ = np.cumsum([0] + [len(r.ops) for r in recs])
        if not ops:
            return [np.zeros((0, 6), dtype="f4") for _ in recs]
        from itertools import chain
        kinds = np.fromiter((o[0] for o in ops), dtype=np.int64, count=len(ops))
        lens = np.fromiter((len(o[1]) for o in ops), dtype=np.int64, count=len(ops))
        M = int(lens.sum())
        P = np.fromiter(chain.from_iterable(chain.from_iterable(o[1] for o in ops)), dtype="f8", count=2 * M).reshape(M, 2)
        off = np.concatenate(([0], np.cumsum(lens)[:-1]))
        opi = np.repeat(np.arange(len(ops)), lens)
        last = off + lens - 1
        prev = np.arange(len(P)) - 1
        prev[off] = last
        cols = np.array([o[2] for o in ops], dtype="f4")
        # rellenos estrellados: abanico desde el centroide (un triángulo por vértice)
        C = np.add.reduceat(P, off, axis=0) / lens[:, None]
        Pc, Pp = P - C[opi], P[prev] - C[opi]
        cr = Pp[:, 0] * Pc[:, 1] - Pp[:, 1] * Pc[:, 0]
        mx = np.maximum.reduceat(np.abs(cr), off)
        tol = (mx * 0.02)[opi]                             # las mellas del contorno entintado no cuentan
        neg = np.maximum.reduceat((cr < -tol).astype(np.int8), off) > 0
        pos = np.maximum.reduceat((cr > tol).astype(np.int8), off) > 0
        star = ~(neg & pos)
        fan_v = (kinds[opi] == 0) & star[opi]
        fan_xy = np.stack((C[opi], P[prev], P), axis=1)[fan_v].reshape(-1, 2)
        n_fan = np.bincount(opi[fan_v], minlength=len(ops)) * 3
        # trazos: dos triángulos por segmento (los abiertos no cierran del último al primero)
        closed = np.array([bool(o[4]) if o[0] == 1 else False for o in ops])
        nxt = np.arange(len(P)) + 1
        nxt[last] = off
        sv = (kinds[opi] == 1) & (closed[opi] | (np.arange(len(P)) != last[opi]))
        A, B = P[sv], P[nxt][sv]
        d = B - A
        ln = np.hypot(d[:, 0], d[:, 1])
        good = ln > 1e-6
        sop = opi[sv][good]
        A, B, d, ln = A[good], B[good], d[good], ln[good]
        width = np.array([max(0.5, float(o[3]) * 0.5) if o[0] == 1 else 0.0 for o in ops])
        nrm = np.stack((-d[:, 1], d[:, 0]), axis=1) / ln[:, None] * width[sop][:, None]
        a, b, c, e = A + nrm, B + nrm, B - nrm, A - nrm
        seg_xy = np.stack((a, b, c, a, c, e), axis=1).reshape(-1, 2)
        n_seg = np.bincount(sop, minlength=len(ops)) * 6
        # rellenos no estrellados: cremallera (pocos)
        zip_xy, n_zip = [], np.zeros(len(ops), dtype=np.int64)
        memo = {}
        for k in np.nonzero((kinds == 0) & ~star)[0]:
            pts = ops[k][1]
            z = memo.get(id(pts))
            if z is None:
                z = memo[id(pts)] = _zipper(pts)          # el mismo polígono en el color y en los datos
            zip_xy.extend(z)
            n_zip[k] = len(z)
        zip_xy = np.array(zip_xy, dtype="f8").reshape(-1, 2)
        # ensamblar en el orden de las operaciones: cada una toma sus triángulos de una sola fuente
        allxy = np.concatenate((fan_xy, seg_xy, zip_xy))
        cnt = n_fan + n_seg + n_zip
        src_start = np.where(n_fan > 0, np.cumsum(n_fan) - n_fan,
                             np.where(n_seg > 0, len(fan_xy) + np.cumsum(n_seg) - n_seg,
                                      len(fan_xy) + len(seg_xy) + np.cumsum(n_zip) - n_zip))
        tot = int(cnt.sum())
        dst_start = np.cumsum(cnt) - cnt
        idx = np.arange(tot) - np.repeat(dst_start, cnt) + np.repeat(src_start, cnt)
        out = np.empty((tot, 6), dtype="f4")
        out[:, :2] = allxy[idx]
        out[:, 2:] = np.repeat(cols, cnt, axis=0)
        ends = np.concatenate((dst_start, [tot]))
        return [out[ends[bounds_[i]]:ends[bounds_[i + 1]]] for i in range(len(recs))]

    def bounds(self):
        xs = [q[0] for o in self.ops for q in o[1]]
        ys = [q[1] for o in self.ops for q in o[1]]
        if not xs:
            return None
        x0, y0 = int(min(xs)) - 4, int(min(ys)) - 4
        return pygame.Rect(x0, y0, int(max(xs)) + 5 - x0, int(max(ys)) + 5 - y0)


class _D:
    """pygame.draw, o el grabador si la superficie es un Recorder."""

    @staticmethod
    def polygon(surf, col, pts, width=0):
        if isinstance(surf, Recorder):
            return surf.polygon(col, pts, width)
        return pygame.draw.polygon(surf, col, pts, width)

    @staticmethod
    def circle(surf, col, c, r, width=0):
        if isinstance(surf, Recorder):
            return surf.circle(col, c, r, width)
        return pygame.draw.circle(surf, col, c, r, width)

    @staticmethod
    def lines(surf, col, closed, pts, width=1):
        if isinstance(surf, Recorder):
            return surf.lines(col, closed, pts, width)
        return pygame.draw.lines(surf, col, closed, pts, width)


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
        _D.circle(surf, ocol, (int(o[1][0]), int(o[1][1])), int(o[2]))
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
    _D.polygon(surf, ocol, P)
    cache[("o", key)] = ("p", P)


def _draw_cached_outline(surf, o, inv, col):
    if o[0] == "c":
        _D.circle(surf, col, (int(o[1][0] * inv), int(o[1][1] * inv)), max(1, int(o[2] * inv)))
    else:
        _D.polygon(surf, col, o[1] if inv == 1.0 else [(x * inv, y * inv) for x, y in o[1]])


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
                    _D.polygon(surf, shade(col, 0.68), tube_pts(A, B, ra_, rb_, bu_, bt, sd))
                    _D.polygon(surf, col, tube_pts(A, B, ra_, rb_, bu_, bt, sd, k=0.8, off=(px * r * 0.2, py * r * 0.2)))
                    if sheen > 0:
                        _D.polygon(surf, shade(col, 1.1 + 0.08 * sheen), tube_pts(A, B, ra_, rb_, bu_, bt, sd, k=0.28, off=(px * r * 0.5, py * r * 0.5)))
                else:
                    Pf = tube_pts(A, B, ra_, rb_, bu_, bt, sd)
                    _D.polygon(surf, col, Pf)
                    cache[("f", idx)] = Pf
                if edge:
                    _D.polygon(surf, shade(col, 0.55), tube_pts(A, B, ra_, rb_, bu_, bt, sd), edge_w)
            elif kind == "ribbon":
                frame, (kl, kr) = _geom(kind, geo, T, k, fac, cache, idx)
                if shd:
                    _D.polygon(surf, shade(col, 0.68), ribbon_pts(frame, kl, kr, k))
                    _D.polygon(surf, col, ribbon_pts(frame, kl, kr, k, kk=0.8, shift=0.25))
                    if sheen > 0:
                        _D.polygon(surf, shade(col, 1.1 + 0.08 * sheen), ribbon_pts(frame, kl, kr, k, kk=0.3, shift=0.55))
                else:
                    Pf = ribbon_pts(frame, kl, kr, k)
                    _D.polygon(surf, col, Pf)
                    cache[("f", idx)] = Pf
                if edge:
                    _D.polygon(surf, shade(col, 0.55), cache.get(("f", idx)) or ribbon_pts(frame, kl, kr, k), edge_w)
            elif kind == "poly":
                P = [T(q) for q in geo]
                if shd:
                    xs = [q[0] for q in P]
                    ys = [q[1] for q in P]
                    span = min(max(xs) - min(xs), max(ys) - min(ys))
                    _D.polygon(surf, shade(col, 0.7), P)
                    if span > 6:
                        cx = sum(xs) / len(xs)
                        cy = sum(ys) / len(ys)
                        m = [(cx + (q[0] - cx) * 0.86 + lx * span * 0.08, cy + (q[1] - cy) * 0.9 + ly * span * 0.08) for q in P]
                        _D.polygon(surf, col, m)
                        if sheen > 0 and span > 14:
                            h = [(cx + (q[0] - cx) * 0.45 + lx * span * 0.2, cy + (q[1] - cy) * 0.6 + ly * span * 0.2) for q in P]
                            _D.polygon(surf, shade(col, 1.08 + 0.06 * sheen), h)
                    else:
                        _D.polygon(surf, col, P)
                else:
                    _D.polygon(surf, col, P)
                if edge:
                    _D.polygon(surf, shade(col, 0.55), P, edge_w)
            elif kind == "circle":
                c, r = geo
                cc = T(c)
                rr = r * k
                if shd and rr > 3:
                    _D.circle(surf, shade(col, 0.68), (int(cc[0]), int(cc[1])), int(rr))
                    _D.circle(surf, col, (int(cc[0] + lx * rr * 0.16), int(cc[1] + ly * rr * 0.16)), int(rr * 0.8))
                    if sheen > 0:
                        _D.circle(surf, shade(col, 1.1 + 0.08 * sheen),
                                           (int(cc[0] + lx * rr * 0.45), int(cc[1] + ly * rr * 0.45)), max(1, int(rr * 0.3)))
                else:
                    _D.circle(surf, col, (int(cc[0]), int(cc[1])), max(1, int(rr)))
                if edge:
                    _D.circle(surf, shade(col, 0.55), (int(cc[0]), int(cc[1])), max(1, int(rr)), edge_w)
            elif kind == "line":
                pts, w, closed, taper = geo
                P = [T(q) for q in pts]
                wpx = w * k
                if len(P) >= 2:
                    if wpx >= 2.0 and (taper or (not closed and wpx >= 3)):     # bajo 2 px el afilado no se ve
                        bp = brush_pts(P, wpx, taper=taper)
                        if len(bp) >= 3:
                            _D.polygon(surf, col, bp)
                            cache[("f", idx)] = bp
                    else:
                        _D.lines(surf, col, closed, P, max(1, int(wpx)))
            elif kind == "dot":
                c, r = geo
                cc = T(c)
                _D.circle(surf, col, (int(cc[0]), int(cc[1])), max(1, int(r * k)))
            # "gloss": sólo existe en el mapa de datos


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

    Cada grupo de profundidad tiene su altura base y los surcos (músculos, pliegues, nudillos) la
    rebajan. El volumen de cada miembro lo calcula la GPU con la distancia al contorno.
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
    state = {"last": 0, "body": 0, "body_mat": None}
    TRIM = (Figure.PATS["stitch"],)

    def piece(anchor, ang, w_px, mat, pat, idx):
        pid = id_base + len(pieces)
        if pid > 254:                     # sin hueco en la tabla: hereda la última pieza (nunca la tinta, que no se ilumina)
            return state["body"] or state["last"] or 254
        seed = math.fmod(idx * 0.6180339 + id_base * 0.1372, 1.0) * 37.0
        pieces.append((anchor[0], anchor[1], ang, k, float(M.get(mat, 2)), float(pat), seed, w_px / max(1e-6, k)))
        state["last"] = pid
        if pat not in TRIM:
            state["body"] = pid           # los surcos van sobre la última pieza de cuerpo, no sobre un ribete
            state["body_mat"] = mat
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
            if groove or kind in ("gloss", "bump"):
                pid = state["body"] or state["last"]
            elif kind in ("line", "dot") and state["body"] and state["body_mat"] == mat:
                pid = state["body"]           # trazos y puntos del mismo material: no gastan entrada en la tabla
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
            elif kind == "poly" and g == 5 and not outline and mat in ("skin", "eye") and state["body"] and state["body_mat"] == "skin":
                pid = state["body"]           # rasgos de la cara (ojo, párpado, boca, oreja): parte de la cara, sin cúpula propia
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
            base = (h0, pid, spec)        # el volumen de cada pieza lo pone la GPU (distancia al contorno)

            def split_draw(pts, h):
                """Cinta: el polígono entero con la pieza de arriba y la mitad baja con la de abajo."""
                _D.polygon(surf, (h, pid, spec), pts)
                if pid2 is not None:
                    id2, m_, n_ = pid2
                    low = pts[m_:2 * n_ + 4 - m_]
                    if len(low) >= 3:
                        _D.polygon(surf, (h, id2, spec), low)

            if kind == "ribbon":
                frame, (kl, kr) = _geom(kind, geo, T, k, fac, cache, idx)
                split_draw((pf if inv == 1.0 else [(x * inv, y * inv) for x, y in pf]) if pf is not None else ribbon_pts(frame, kl, kr, k), h0)
            elif kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                A, B = T(a), T(b)
                if pf is not None:
                    _D.polygon(surf, base, pf if inv == 1.0 else [(x * inv, y * inv) for x, y in pf])     # misma silueta
                else:
                    _D.polygon(surf, base, tube_pts(A, B, ra * k, rb * k, bu * k, bt, side * fac))
            elif kind == "poly":
                P = [T(q) for q in geo]
                _D.polygon(surf, base, P)
            elif kind == "circle":
                c, r = geo
                cc = T(c)
                _D.circle(surf, base, (int(cc[0]), int(cc[1])), max(1, int(r * k)))
            elif kind == "line":
                pts, w, closed, taper = geo
                P = [T(q) for q in pts]
                if len(P) >= 2:
                    # surco: pliegue o músculo ancho y poco profundo (una arruga, no un corte)
                    gd = 12 if mat == "skin" else 8
                    # los rasgos de la cara (ojos, boca, arrugas) no tienen relieve: alzados proyectan rayas de sombra
                    flat = g == 5 and mat in ("skin", "eye")
                    val = (max(0, h0 - gd), pid, spec) if groove else (h0 + (0 if flat else 4), pid, spec)
                    wpx = max(1.0, w * k * ((2.2 if mat == "skin" else 2.8) if groove else 1.0))
                    if pf is not None and not groove:
                        bp = pf if inv == 1.0 else [(x * inv, y * inv) for x, y in pf]
                    else:
                        bp = brush_pts(P, wpx, taper=taper) if not closed and wpx >= 2.0 else []
                    if len(bp) >= 3:
                        _D.polygon(surf, val, bp)
                    else:
                        _D.lines(surf, val, closed, P, max(1, int(wpx)))
            elif kind == "dot":
                c, r = geo
                cc = T(c)
                val = (max(0, h0 - 10), pid, spec) if groove else (h0 + (0 if g == 5 and mat in ("skin", "eye") else 4), pid, spec)
                _D.circle(surf, val, (int(cc[0]), int(cc[1])), max(1, int(r * k)))
            elif kind == "bump":
                (cx_, cy_), ra, rb, ang, hb = geo
                ca_, sa_ = math.cos(ang), math.sin(ang)
                nr = max(6, int(hb) + 3)                 # un escalón de altura por anillo como mucho
                for j in range(nr):
                    f_ = 1.0 - j / nr
                    hv = min(255, h0 + int(round(hb * (1.0 - f_ * f_) ** 1.5)))
                    pts = []
                    for i in range(14):
                        a = i * math.pi / 7
                        u, v = math.cos(a) * ra * f_, math.sin(a) * rb * f_
                        pts.append(T((cx_ + u * ca_ - v * sa_, cy_ + u * sa_ + v * ca_)))
                    _D.polygon(surf, (hv, pid, spec), pts)
            elif kind == "gloss":
                c, r = geo
                cc = T(c)
                # sin escalón de altura (un disco alzado proyecta sombra) y con el brillo en degradado, no en moneda
                for j in range(6):
                    f_ = 1.0 - j / 6.0
                    _D.circle(surf, (h0, pid, int(spec * (0.22 + 0.78 * (1.0 - f_) ** 0.7))), (int(cc[0]), int(cc[1])),
                              max(1, int(r * k * f_)))
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


# Expresiones: apertura del párpado superior, subida del inferior, cejas (dentro: junto a la nariz; fuera: sien;
# positivo baja), surco nasogeniano y boca.
EXPR = {
    "neutra":   dict(lid=1.0, low=0.0, brow_in=0.0, brow_out=0.0, fold=False, mouth="line"),
    "foco":     dict(lid=0.86, low=0.18, brow_in=0.045, brow_out=-0.01, fold=False, mouth="firm"),
    "feroz":    dict(lid=0.84, low=0.22, brow_in=0.075, brow_out=-0.03, fold=False, mouth="firm"),
    "calma":    dict(lid=0.72, low=0.1, brow_in=0.0, brow_out=0.01, fold=False, mouth="line"),
    "sonrisa":  dict(lid=0.84, low=0.38, brow_in=-0.02, brow_out=0.0, fold=True, mouth="smile"),
    "picara":   dict(lid=0.78, low=0.3, brow_in=0.025, brow_out=-0.045, fold=True, mouth="smirk"),
    "grito":    dict(lid=1.18, low=0.0, brow_in=0.09, brow_out=-0.045, fold=True, mouth="shout"),
    "esfuerzo": dict(lid=0.62, low=0.38, brow_in=0.08, brow_out=-0.02, fold=True, mouth="teeth"),
    "dolor":    dict(lid=0.0, low=0.0, brow_in=-0.07, brow_out=0.025, fold=True, mouth="grimace"),
    "jadeo":    dict(lid=0.6, low=0.0, brow_in=-0.03, brow_out=0.02, fold=False, mouth="open"),
    "ko":       dict(lid=0.0, low=0.0, brow_in=-0.02, brow_out=0.02, fold=False, mouth="slack"),
}
BASE_EXPR = {"contenida": "foco", "seria": "foco", "concentrada": "foco", "feroz": "feroz", "calma": "calma",
             "serena": "calma", "sonrisa": "sonrisa", "picara": "picara"}


def _expression(f, look):
    st = f.state
    if st == Fighter.KO:
        return "ko"
    if st in (Fighter.HIT, Fighter.KNOCK, Fighter.STUN, Fighter.THROWN):
        return ("sonrisa" if look is LOOKS.get("matias") and st == Fighter.HIT else "dolor")
    if st == Fighter.KIAI or (st == Fighter.ATTACK and f.move is not None and f.move_key in ("heavy", "special")):
        return "grito"
    if st in (Fighter.ATTACK, Fighter.THROWING, Fighter.GROUND):
        return "esfuerzo"
    if st == Fighter.WIN:
        return "sonrisa"
    if getattr(f, "exhausted", False):
        return "jadeo"
    return BASE_EXPR.get(look["expression"], "neutra")


_LEGS: Dict[str, Tuple[float, float]] = {}
_NECK: Dict[str, float] = {}


def _neck_len(fd):
    """Distancia fija del pecho al centro de la cabeza, medida en la guardia (el esqueleto de poses la estira
    o la encoge de 29 a 37 cm según la pose: en las caídas el cuello desaparecía)."""
    key = fd["id"]
    if key not in _NECK:
        try:
            p = _stance(fd)
            c0, h0 = p["chest"], p["head"]
            _NECK[key] = math.hypot(h0[0] - c0[0], h0[1] - c0[1]) * (0.64 - 0.15) * 0.94
        except Exception:
            _NECK[key] = 16.5
    return _NECK[key]


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


class Secondary:
    """Animación secundaria de un luchador entre fotogramas: la tela que llega tarde y los smears.

    Guarda puntos en coordenadas del mundo (en unidades de pose), para que la manga se retrase tanto si se
    mueve el brazo como si el luchador entero avanza. Se actualiza una vez por fotograma de simulación: los
    redibujos del mismo fotograma (Modo Foto, reflejos) no la aceleran."""

    def __init__(self, f):
        self.fac = f.facing
        self.t = None
        self.pts = {}
        self.prev = {}
        self.fresh = False

    @staticmethod
    def of(f):
        st = getattr(f, "_secondary", None)
        if st is None or st.fac != f.facing:          # al darse la vuelta, la ropa empieza en su sitio
            st = Secondary(f)
            try:
                f._secondary = st
            except AttributeError:
                return st
        key = int(round(getattr(f, "anim_t", 0.0) * 60))
        st.fresh = st.t is not None and key != st.t
        st.t = key
        st.root = (getattr(f, "x", 0.0) * f.facing / max(0.5, f.scale), getattr(f, "y", 0.0) / max(0.5, f.scale))
        return st

    def lag(self, name, p, k=0.45, maxd=6.0):
        """El punto p (local) seguido con retraso: devuelve dónde está la tela que cuelga de él."""
        w = (p[0] + self.root[0], p[1] + self.root[1])
        q = self.pts.get(name)
        if q is None:
            q = w
        elif self.fresh:
            q = (q[0] + (w[0] - q[0]) * k, q[1] + (w[1] - q[1]) * k)
        dx, dy = q[0] - w[0], q[1] - w[1]
        d = math.hypot(dx, dy)
        if d > maxd:
            q = (w[0] + dx / d * maxd, w[1] + dy / d * maxd)
            dx, dy = q[0] - w[0], q[1] - w[1]
        self.pts[name] = q
        return (p[0] + dx, p[1] + dy)

    def spring(self, name, target, stiff=0.16, damp=0.82):
        """Muelle amortiguado de un grado de libertad (pelo, cinturón, flecos): persigue `target` con
        inercia, se pasa y vuelve. Un paso por fotograma de simulación."""
        st = getattr(self, "spr", None)
        if st is None:
            st = self.spr = {}
        x, v = st.get(name, (target, 0.0))
        if self.fresh:
            v = (v + (target - x) * stiff) * damp
            x += v
        st[name] = (x, v)
        return x

    def velocity(self, name, p):
        """Velocidad del punto p (local) en el mundo, en unidades de pose por fotograma."""
        q, _ = self.motion(name, p)
        return (p[0] - q[0], p[1] - q[1])

    def motion(self, name, p):
        """Dónde estaba el punto p (local) en el fotograma anterior y su velocidad en cm por fotograma."""
        w = (p[0] + self.root[0], p[1] + self.root[1])
        old, last = self.prev.get(name, (None, None))
        if self.fresh or old is None:
            old, last = last, w
        self.prev[name] = (old, last)
        if old is None:
            return p, 0.0
        q = (old[0] - self.root[0], old[1] - self.root[1])
        return q, math.hypot(w[0] - old[0], w[1] - old[1])


PUNCHES = {"jab": "r", "hook": "r", "cross": "l"}


def _strike(f, J, B):
    """Puñetazo en sus fotogramas activos: el puño dibujado tiene que llegar a donde el juego cuenta el golpe.
    Busca la superficie del rival (o el frente del golpe si falla); lo que el brazo no alcanza con sus huesos lo
    pone una zancada: pecho, cabeza y hombros adelante, la cadera un poco y el pie delantero avanza.
    Modifica J y devuelve (lado, mano objetivo) o None."""
    m = getattr(f, "move", None)
    if f.state != Fighter.ATTACK or m is None or m.anim not in PUNCHES:
        return None
    st, ac = getattr(m, "startup", 0), getattr(m, "active", 1)
    t = f.timer
    if t < st * 0.6 or t > st + ac + getattr(m, "recovery", 0) * 0.5:
        return None
    ext = smoothstep(clamp((t - st * 0.6) / max(1.0, st * 0.4), 0.0, 1.0)) * (
        1.0 - smoothstep(clamp((t - st - ac) / max(1.0, getattr(m, "recovery", 0) * 0.5), 0.0, 1.0)))
    side = PUNCHES[m.anim]
    sc = max(0.5, f.scale)
    front = 20 + getattr(m, "rng", 70)
    look = getattr(f, "look_at", None)
    if look is not None and look[0] * f.facing > 0:
        tip_w = min(front, abs(look[0]) - 20 + 4)                # el puño entra un poco en el cuerpo del rival
    else:
        tip_w = front - 8
    hand = tip_w / sc - 5.6 * B["hand"] / 4.3
    sh = J["sh_" + side]
    U, F_ = B.get("arm", (30.0, 25.5))
    natural = sh[0] + (U + F_) * 1.25 + 7.0
    lunge = clamp((hand - natural) / 0.82, 0.0, 52.0) * ext
    if lunge > 0:
        for k, w in (("chest", 0.82), ("head", 0.82), ("sh_l", 0.82), ("sh_r", 0.82), ("el_l", 0.82), ("el_r", 0.82),
                     ("hd_l", 0.82), ("hd_r", 0.82), ("hip", 0.5)):
            J[k] = (J[k][0] + lunge * w, J[k][1] + (lunge * 0.1 if k == "hip" else lunge * 0.04))
        fs = "ft_r" if J["ft_r"][0] >= J["ft_l"][0] else "ft_l"
        ks = "kn_" + fs[-1]
        J[fs] = (J[fs][0] + lunge * 0.85, J[fs][1])
        J[ks] = (J[ks][0] + lunge * 0.75, J[ks][1])
    hd = J["hd_" + side]
    target = (hd[0] + (hand - hd[0]) * ext, hd[1])
    J["hd_" + side] = target
    return side, ext


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
    J = dict(pose)
    strike = _strike(f, J, B)                # puñetazo activo: brazo que golpea y zancada hasta el rival
    sec = Secondary.of(f)
    H, Cc0, Hd0 = J["hip"], J["chest"], J["head"]
    hip_vx, _ = sec.velocity("spr_hip", H)          # para el cinturón, los cordones y los flecos con muelle
    # el esqueleto de poses deja los hombros bajos y la cabeza lejos (36 cm): subimos la cintura
    # escapular y acercamos la cabeza para un cuello real de ~8 cm visibles
    lift = ((Hd0[0] - Cc0[0]) * 0.15, (Hd0[1] - Cc0[1]) * 0.15)
    for jn, w in (("chest", 1.0), ("sh_l", 0.55), ("sh_r", 0.55), ("el_l", 0.2), ("el_r", 0.2)):
        J[jn] = (J[jn][0] + lift[0] * w, J[jn][1] + lift[1] * w)
    # respiración: la cintura escapular sube y baja con el pecho (más tras un intercambio)
    br_ph = math.sin(t_anim * 2.2)
    br_amp = 0.35 + 0.6 * getattr(f, "breath", 0.0)
    for jn, w in (("chest", 0.6), ("sh_l", 1.0), ("sh_r", 1.0), ("el_l", 0.4), ("el_r", 0.4)):
        J[jn] = (J[jn][0], J[jn][1] - br_ph * br_amp * w)
    Cc = J["chest"]
    hx_, hy_ = _norm(Hd0[0] - Cc[0], Hd0[1] - Cc[1])
    if hx_ == 0 and hy_ == 0:
        hy_ = -1.0
    nl = _neck_len(fd)
    Hd = (Cc[0] + hx_ * nl, Cc[1] + hy_ * nl)          # cuello de longitud fija en todas las poses

    # ── columna y torso ──────────────────────────────────────────────────────
    N = _lerp2(Cc, Hd, 0.06)
    ax, ay = N[0] - H[0], N[1] - H[1]
    L = math.hypot(ax, ay) or 1.0
    nx, ny = -ay / L, ax / L             # normal hacia el pecho (delante)

    # columna curva: la espalda se redondea al agacharse, al encajar un golpe al cuerpo o al rematar un
    # puñetazo; se arquea (pecho fuera) al recibir en la cara. bend > 0: la mitad del tronco va hacia el pecho.
    hit_t = 0.0
    if f.state in (Fighter.HIT, Fighter.STUN):
        hit_t = max(0.0, 1.0 - f.timer / 14.0)
    spine_bend = {Fighter.CROUCH: -0.075, Fighter.KNOCK: -0.05, Fighter.GROUND: -0.03, Fighter.ATTACK: -0.03,
                  Fighter.BLOCK: -0.045, Fighter.IDLE: -0.018, Fighter.WALK: -0.018}.get(f.state, 0.0)
    if getattr(f, "crouching", False):
        spine_bend = min(spine_bend, -0.07)
    spine_bend += 0.07 * hit_t - 0.004 * br_ph

    def S(t, w):
        c = spine_bend * 4.0 * t * (1.0 - t) * L
        dc = spine_bend * 4.0 * (1.0 - 2.0 * t)
        tx, ty = ax / L + nx * dc, ay / L + ny * dc
        tl = math.hypot(tx, ty) or 1.0
        mx, my = -ty / tl, tx / tl
        return (H[0] + ax * t + nx * c + mx * w, H[1] + ay * t + ny * c + my * w)

    breath = 1.0 + 0.025 * br_ph * (0.6 + getattr(f, "breath", 0.0))
    jig = 0.0
    if lk["body"] == "sumo" and f.state in (Fighter.HIT, Fighter.STUN, Fighter.KNOCK):
        jig = 0.07 * math.sin(f.timer * 1.3) * max(0.0, 1 - f.timer / 30)
    front = [w * (breath if 0.45 <= t <= 0.85 else 1.0) * (1 + jig * (1 if t < 0.5 else 0.3)) for t, w in zip(SPINE_T, B["front"])]
    back = list(B["back"])
    if hit_t > 0:
        # torsión: el golpe gira el tronco; el pecho se cierra de perfil y la espalda asoma
        front = [w * (1.0 - 0.08 * hit_t) for w in front]
        back = [w * (1.0 + 0.06 * hit_t) for w in back]

    def torso(fr, bk, ts=SPINE_T, grow_f=0.0, grow_b=0.0):
        F = [S(t, w + grow_f) for t, w in zip(ts, fr)]
        K = [S(t, -(w + grow_b)) for t, w in zip(ts, bk)]
        return F + K[::-1]

    def wf(t):
        return _interp(front, t)

    def wb(t):
        return _interp(back, t)

    up_ = (ax / L, ay / L)

    yokes = {}

    def yoke(g, col, mat, grow=0.0, sheen=0.5, pat=None):
        """Trapecio y hombros: la línea que baja de la base del cuello al deltoides, con la masa del trapecio
        detrás. Sin ella, el hombro era un bulto pegado al borde recto del torso. El hombro adelantado
        (puñetazo) arrastra el pecho: el contorno delantero se estira hacia él."""
        fig.m(mat, pat=pat)
        dl = B["delt"]
        for sh in (J["sh_l"], J["sh_r"]):
            th = dl + 0.6 + grow                          # tan alto como la manga o el deltoides
            top = (sh[0] + up_[0] * th, sh[1] + up_[1] * th)
            fr = (sh[0] + nx * (dl * 0.75 + grow) + up_[0] * dl * 0.15, sh[1] + ny * (dl * 0.75 + grow) + up_[1] * dl * 0.15)
            bk = (sh[0] - nx * (dl * 0.9 + grow) + up_[0] * dl * 0.45, sh[1] - ny * (dl * 0.9 + grow) + up_[1] * dl * 0.45)
            pts = [S(0.8, wf(0.8) + grow), fr, (top[0] + nx * dl * 0.3, top[1] + ny * dl * 0.3), top,
                   (top[0] - nx * dl * 0.6, top[1] - ny * dl * 0.6), bk, S(0.78, -(wb(0.78) + grow))]
            # el hombro echado atrás no saca el trapecio por la espalda
            s0 = S(0.84, 0.0)
            lim = -(wb(0.84) + grow + 0.6)
            for i, lm in ((2, lim + dl * 1.1), (3, lim + dl * 0.7), (4, lim + dl * 0.3), (5, lim)):
                q_ = pts[i]
                dn = (q_[0] - s0[0]) * nx + (q_[1] - s0[1]) * ny
                if dn < lm:
                    pts[i] = (q_[0] + nx * (lm - dn), q_[1] + ny * (lm - dn))
            fig.poly(g, pts, col, sheen=sheen, smooth=2)
            yokes[sh] = (pts, col, mat, sheen, pat)

    def shoulder_patch(g, sh, cap):
        """Sobre la raíz del brazo: un disco del color del canesú, sin contorno, en el remate redondo de la manga
        (o del deltoides). Tapa la costura interior sin tocar la silueta ni, al echar la cabeza atrás, el cuello."""
        if sh not in yokes:
            return
        pts, col, mat, sheen, pat = yokes[sh]
        fig.m(mat, pat=pat)
        fig.circle(g, _lerp2(cap, sh, 0.35), B["delt"] * 0.95, col, outline=False, sheen=sheen)

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

    def pant_leg(g, kn, hem, L1, r0, rk, r1, col, mat, sheen, band=False, cuff=False, stack=True):
        """Pernera que se comporta como tela: se recoge detrás de la rodilla al doblarla, se tensa sobre la
        rótula y marca líneas de tensión desde la ingle, cuelga por el lado que mira al suelo con la pierna
        levantada y se apila sobre el tobillo. Con `band`, dobladillo con pespuntes; con `cuff`, puño elástico."""
        hem = sec.lag("hem" + str(g), hem, k=0.5, maxd=5.0)     # el bajo de la pernera llega tarde
        Ls = dist(kn, hem)
        uk = L1 / max(1e-6, L1 + Ls)
        t1 = _norm(kn[0] - H[0], kn[1] - H[1])
        t2 = _norm(hem[0] - kn[0], hem[1] - kn[1])
        flex = clamp(math.acos(clamp(t1[0] * t2[0] + t1[1] * t2[1], -1.0, 1.0)) / 1.6, 0.0, 1.0)
        cr = t1[0] * t2[1] - t1[1] * t2[0]
        s_in = 1.0 if cr >= 0 else -1.0                  # lado de dentro del doblez (la corva): izquierda si cr ≥ 0
        nl1 = (-t1[1], t1[0])                            # normal izquierda del muslo
        nl2 = (-t2[1], t2[0])
        # gravedad: con el muslo tumbado, la tela cuelga hacia el lado que mira al suelo
        down_l = nl1[1]                                  # > 0: la izquierda mira hacia abajo
        sag = 2.4 * max(0.0, 1.0 - abs(t1[1]) * 1.4)
        sag_l, sag_r = (sag, 0.0) if down_l > 0 else (0.0, sag)
        inner = [(0.0, r0 + 0.4), (uk * 0.55, (r0 + rk) * 0.5 + 0.6), (uk * 0.9, rk + 0.5 + 2.0 * flex),
                 (uk, rk + 0.4 + 2.4 * flex), (uk + (1 - uk) * 0.14, rk * 0.95 + 1.1 * flex), (0.9, r1 - 0.2), (1.0, r1 + 0.4)]
        outer = [(0.0, r0), (uk * 0.6, (r0 + rk) * 0.5 - 0.4 - 0.9 * flex), (uk, rk + 0.3 - 0.7 * flex),
                 (uk + (1 - uk) * 0.3, rk * 0.92), (0.9, r1 - 0.3), (1.0, r1 + 0.3)]
        if cuff:                                         # chándal: puño elástico que se cierra en el tobillo
            inner[-1] = (1.0, r1 - 1.0)
            outer[-1] = (1.0, r1 - 1.0)
        kl, kr = (inner, outer) if s_in > 0 else (outer, inner)
        add = lambda ks, extra: [(u, v + extra * clamp(1.0 - abs(u - uk * 0.5) / max(uk * 0.6, 1e-3), 0.0, 1.0)) for u, v in ks]
        kl, kr = add(kl, sag_l), add(kr, sag_r)
        fig.m(mat)
        fig.ribbon(g, [H, kn, hem], kl, kr, col, sheen=sheen, edge=True)
        if band:
            # dobladillo: una banda recta que cruza el bajo (no una cinta corta, que se ve como un disco)
            fig.m(mat, pat="stitch")
            b0 = _lerp2(kn, hem, 0.9)
            ra = (r1 - 0.2, r1 - 0.1)                    # (izquierda, derecha) en el arranque de la banda
            rb = (r1 + 0.4, r1 + 0.3)
            fig.poly(g, [(b0[0] + nl2[0] * ra[0], b0[1] + nl2[1] * ra[0]), (hem[0] + nl2[0] * rb[0], hem[1] + nl2[1] * rb[0]),
                         (hem[0] - nl2[0] * rb[1], hem[1] - nl2[1] * rb[1]), (b0[0] - nl2[0] * ra[1], b0[1] - nl2[1] * ra[1])],
                     shade(col, 0.96), sheen=sheen * 0.8, smooth=0)
        fig.m(mat, True)
        dk = shade(col, 0.8)
        off = lambda p, n, d: (p[0] + n[0] * d, p[1] + n[1] * d)
        # corva: dos o tres pliegues que nacen del doblez, más largos cuanto más se dobla
        if flex > 0.12:
            for i, (u, w_) in enumerate(((0.0, 0.9), (-0.16, 0.7), (0.16, 0.6))):
                if i == 2 and flex < 0.45:
                    break
                c0 = off(_lerp2(H, kn, 1.0 + u * 0.5 - 0.08), nl1, s_in * rk * (0.5 + 0.1 * i))
                c1 = off(kn, nl2, s_in * rk * (0.85 - 0.1 * i))
                c2 = off(_lerp2(kn, hem, 0.08 + 0.06 * i + 0.1 * flex), nl2, s_in * rk * (0.55 + 0.1 * i))
                fig.line(g, [c0, c1, c2], dk, w_)
        # tensión: de la ingle a la rótula por delante del muslo cuando la pierna se dobla o se levanta
        lift = max(flex, 1.0 - abs(t1[1]))
        if lift > 0.3:
            fig.line(g, [off(_lerp2(H, kn, 0.18), nl1, s_in * r0 * 0.35), off(_lerp2(H, kn, 0.6), nl1, -s_in * rk * 0.15),
                         off(_lerp2(H, kn, 0.92), nl1, -s_in * rk * 0.55)], shade(col, 0.84), 0.6)
        # pliegue largo de caída en el muslo de apoyo
        if lift <= 0.3:
            fig.line(g, [_lerp2(H, kn, 0.3), _lerp2(H, kn, 0.7)], shade(col, 0.84), 0.45)
        # la tela se apila sobre el tobillo
        if stack:
            for u, w_ in ((0.72, 0.55), (0.84, 0.6)):
                a_ = off(_lerp2(kn, hem, u), nl2, s_in * r1 * 0.8)
                b_ = off(_lerp2(kn, hem, u + 0.05), nl2, -s_in * r1 * 0.75)
                fig.line(g, [a_, _lerp2(a_, b_, 0.5), b_], dk, w_)

    # piernas de huesos fijos: el esqueleto de poses estira muslo y pierna a voluntad (de 31 a 66 cm el muslo,
    # de 30 a 70 la pierna: 135 cm de pierna en la patada alta); aquí miden lo que en la guardia de cada uno
    T_, S_ = _leg_lengths(fd)
    ux_, uy_ = _norm(Cc[0] - H[0], Cc[1] - H[1])
    for side in ("l", "r"):
        J["kn_" + side], J["ft_" + side] = _leg_ik(H, J["ft_" + side], T_, S_, (-uy_, ux_), (ux_, uy_))

    # pies: apoyados, planos en el suelo; si la espinilla se vence sobre el pie más de lo que dobla el tobillo,
    # el talón sube y el pie pivota sobre la base de los dedos (que siguen en el suelo); en el aire, en punta
    foot_frames = {}
    for side in ("l", "r"):
        kn, ft = J["kn_" + side], J["ft_" + side]
        sx, sy = _norm(ft[0] - kn[0], ft[1] - kn[1])
        if sx == 0 and sy == 0:
            sy = 1.0
        grounded = ft[1] > -4.0 and f.state not in (Fighter.JUMP, Fighter.KNOCK, Fighter.THROWN)
        if grounded:
            lean = math.degrees(math.atan2(-sx, sy))            # > 0: la rodilla por delante del tobillo
            th = math.radians(clamp(lean - 26.0, 0.0, 55.0))     # lo que el tobillo ya no dobla: sube el talón
            if th > 0.01:
                ball = 12.0
                tgt = (ft[0] + ball * (1.0 - math.cos(th)) * 0.6, ft[1] - ball * math.sin(th) * 0.75)
                J["kn_" + side], J["ft_" + side] = _leg_ik(H, tgt, T_, S_, (-uy_, ux_), (ux_, uy_))
                kn, ft = J["kn_" + side], J["ft_" + side]
                sx, sy = _norm(ft[0] - kn[0], ft[1] - kn[1])
            fv = (math.cos(th), math.sin(th))                     # hacia delante (y abajo si sube el talón)
        else:
            px_, py_ = sy, -sx                                     # perpendicular a la espinilla
            k_ = 0.55                                              # empeine estirado
            fv = _norm(px_ * (1 - k_) + sx * k_, py_ * (1 - k_) + sy * k_)
            th = 0.0
        foot_frames[side] = (fv, th, grounded)

    fig.joints.update(hip=H, chest=Cc, head=Hd, kn_l=J["kn_l"], ft_l=J["ft_l"], kn_r=J["kn_r"], ft_r=J["ft_r"])
    for side, g, depth in (("l", 1, 0.84), ("r", 3, 1.0)):
        kn, ft = J["kn_" + side], J["ft_" + side]
        sx, sy = _norm(ft[0] - kn[0], ft[1] - kn[1])
        if sx == 0 and sy == 0:
            sy = 1.0
        ankle = (ft[0] - sx * 6, ft[1] - sy * 6)
        (fdx, fdy), heel_up, grounded = foot_frames[side]
        dvx, dvy = -fdy, fdx                 # del empeine a la planta
        x_m = kn[0]
        # smear de la patada: la pierna rápida deja la silueta estirada uno o dos fotogramas
        was, speed = sec.motion("ft_" + side, ankle)
        if speed > 10.0 and f.state in (Fighter.ATTACK, Fighter.KIAI):
            covered = bottom in ("gi", "abada", "track", "hakama")
            fig.m("cloth" if covered else "skin")
            smc = C(lk.get("pants", cloth) if covered else skin, x_m, depth)
            k_ = clamp((speed - 10.0) / 14.0, 0.0, 1.0)
            rr = ca1 * (1.6 if covered else 1.0)
            fig.tube(g, _lerp2(ankle, was, 0.85 * k_), _lerp2(kn, ankle, 0.75), rr * 0.5, rr, smc, outline=False, sheen=0.3)
        sk = C(skin, x_m, depth)
        L1, L2 = dist(H, kn), dist(kn, ankle)
        if bottom != "hakama":
            fig.m("skin")
            kb, kf = leg_keys(L1, L2)
            fig.ribbon(g, [H, kn, ankle], kb, kf, sk, sheen=skin_sheen)
            if bottom in ("shorts_thai", "shorts_sambo", "singlet", "mawashi"):
                # cuádriceps delante del muslo (vasto medial sobre la rodilla) y gemelo detrás de la pierna
                dm = {"sumo": 0.55, "anciano": 0.5, "menuda": 0.65, "atleta_f": 0.8}.get(lk["body"], 1.0) * (1.25 if "definido" in lk["extras"] else 1.0)
                tdx, tdy = _norm(kn[0] - H[0], kn[1] - H[1])
                ldx, ldy = _norm(ankle[0] - kn[0], ankle[1] - kn[1])
                th_ = B["thigh"][0] if isinstance(B["thigh"], (tuple, list)) else B["thigh"]
                cf_ = B["calf"][0] if isinstance(B["calf"], (tuple, list)) else B["calf"]
                qc = _lerp2(H, kn, 0.5)
                fig.bump(g, (qc[0] + tdy * th_ * 0.3, qc[1] - tdx * th_ * 0.3), 0.3 * L1, th_ * 0.5, math.atan2(tdy, tdx), 6 * dm, skin_sheen)
                vm = _lerp2(H, kn, 0.85)
                fig.bump(g, (vm[0] + tdy * th_ * 0.25, vm[1] - tdx * th_ * 0.25), 0.1 * L1, th_ * 0.3, math.atan2(tdy, tdx), 4 * dm, skin_sheen)
                gc = _lerp2(kn, ankle, 0.3)
                fig.bump(g, (gc[0] - ldy * cf_ * 0.3, gc[1] + ldx * cf_ * 0.3), 0.2 * L2, cf_ * 0.5, math.atan2(ldy, ldx), 6 * dm, skin_sheen)
                v_ = (getattr(f, "marks", None) or {}).get("espinilla", 0.0)
                if v_ > 0.05 and side == "r":
                    sc_ = _lerp2(kn, ankle, 0.42)
                    fig.dot(g, (sc_[0] + ldy * cf_ * 0.5, sc_[1] - ldx * cf_ * 0.5), 2.0 + v_, mixc(sk, (110, 70, 105), 0.12 + 0.2 * v_))
        # pie
        def F_(a, b, toe=11.0):
            # la parte de los dedos (a > toe) vuelve a quedar plana en el suelo cuando el talón está subido
            if heel_up > 0.01 and a > toe:
                bx, by = ankle[0] + fdx * toe + dvx * b, ankle[1] + fdy * toe + dvy * b
                return (bx + (a - toe), by)
            return (ankle[0] + fdx * a + dvx * b, ankle[1] + fdy * a + dvy * b)
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
            # pie descalzo: talón redondo, arco que se levanta entre talón y metatarso, dedo gordo con uña,
            # dedos pequeños asomando detrás y el maléolo (hueso del tobillo) en relieve
            foot = [F_(-3.5, -1.5), F_(-4.8, 3.5), F_(-3.8, 6.0), F_(0.5, 6.4), F_(5.0, 5.8), F_(9.5, 6.2), F_(13, 6.1),
                    F_(16.8, 5.3), F_(17.1, 3.7), F_(14.2, 2.7), F_(9, 1.3), F_(3, -1.6)]
            fig.m("skin")
            fig.poly(g, foot, sk, sheen=skin_sheen * 0.5, smooth=2)
            for i, (a_, b_) in enumerate(((15.2, 3.0), (14.1, 2.6), (12.9, 2.4))):
                fig.dot(g, F_(a_, b_), (0.95 - 0.12 * i), shade(sk, 0.96 - 0.03 * i))                        # dedos de detrás
            fig.tube(g, F_(12.8, 4.4), F_(16.5, 4.5), 1.3, 1.2, sk, sheen=skin_sheen * 0.5, outline=False)  # dedo gordo
            fig.dot(g, F_(16.3, 3.95), 0.5, mixc(sk, (238, 210, 200), 0.35))                               # uña
            fig.bump(g, F_(0.4, 0.7), 1.4, 1.1, 0.0, 4, skin_sheen * 0.5)                                    # maléolo
            fig.bump(g, F_(11.6, 5.0), 1.6, 1.1, 0.0, 3, skin_sheen * 0.5)                                   # bola del pie
            fig.m("skin", True)
            fig.line(g, [F_(12.8, 5.4), F_(13.3, 3.9)], shade(sk, 0.72), 0.3)
            fig.line(g, [F_(14.8, 5.3), F_(15.2, 3.8)], shade(sk, 0.72), 0.25)
            fig.line(g, [F_(1.5, 5.2), F_(5.0, 4.8), F_(9.0, 5.3)], shade(sk, 0.86), 0.35)                  # arco
            fig.line(g, [F_(-1.5, 1.0), F_(2.5, 0.0), F_(6, 0.8)], shade(sk, 0.9), 0.25)                     # tendón
        # ropa de las piernas
        if bottom == "gi":
            pc = C(lk.get("pants", cloth), x_m, depth)
            pant_leg(g, kn, _lerp2(kn, ankle, 0.86), L1, th0 + 3.2, th1 + 3.4, ca1 + 4.2, pc, "cloth", 0.5, band=True)
        elif bottom == "abada":
            pant_leg(g, kn, ankle, L1, th0 + 2.4, th1 + 2.6, ca1 + 1.4, C(cloth, x_m, depth), "cloth", 0.5, stack=False)
        elif bottom == "track":
            pc = C(lk["pants"], x_m, depth)
            hem = _lerp2(kn, ankle, 0.94)
            pant_leg(g, kn, hem, L1, th0 + 2.4, th1 + 2.3, ca1 + 2.5, pc, "satin", 0.8, cuff=True)
            fig.m("satin")
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
    yoke(2, sk_t, "skin", sheen=skin_sheen)
    ex = lk["extras"]
    if top == "none":
        dk = shade(sk_t, 0.78)
        # relieve muscular en el mapa de alturas: la luz dibuja el músculo, no una línea
        ang_s = math.atan2(ay, ax)

        def mus(t, wfr, ra, rb, h, da=0.0):
            w_ = wf(t) * wfr if wfr >= 0 else wb(t) * wfr
            fig.bump(2, S(t, w_), ra, rb, ang_s + da, h, skin_sheen)
        if lk["body"] == "sumo":
            mus(0.3, 0.35, 0.22 * L, wf(0.3) * 0.45, 8)                       # vientre
            mus(0.7, 0.45, 7.0, wf(0.7) * 0.4, 6)                            # pecho
            mus(0.45, -0.5, 9.0, 3.0, 4)                                     # rollo de la espalda
        else:
            dm = 1.25 if "definido" in ex else 0.8
            mus(0.7, 0.5, 5.5, wf(0.7) * 0.45, 9 * dm)                       # pectoral
            for t in (0.3, 0.4, 0.5):
                mus(t, 0.78, 2.6, wf(t) * 0.25, 5 * dm)                      # recto abdominal
            mus(0.28, 0.25, 6.0, 3.0, 4 * dm, 0.3)                           # oblicuo
            for t in (0.56, 0.6, 0.64):
                mus(t, 0.18, 1.6, 1.2, 3 * dm, 0.5)                          # serrato
            mus(0.62, -0.35, 9.0, wb(0.62) * 0.3, 6 * dm)                    # dorsal ancho
            mus(0.32, -0.32, 7.0, 2.2, 3 * dm)                               # erectores
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
        yoke(4, jc, "cloth", grow=grow * 0.6)
        fig.m("cloth")
        # pliegues de la tela que responden al cuerpo: del cinturón a la axila del brazo que golpea (tensión),
        # de la espalda al hombro al levantar la guardia y abanico del faldón según la zancada y la velocidad
        fig.m("cloth", True)
        fd_ = shade(jc, 0.8)
        ua_, fa_ = B.get("arm", (30.0, 25.5))
        sh_f, hd_f = max(((J["sh_" + sd], J["hd_" + sd]) for sd in ("l", "r")), key=lambda p_: dist(*p_))
        reach = clamp((dist(sh_f, hd_f) / (ua_ + fa_) - 0.5) / 0.45, 0.0, 1.0)
        for o_, w_ in ((0.0, 0.45), (0.1, 0.35), (0.2, 0.28)):
            if o_ > 0.05 and reach < 0.3:
                continue
            a_ = S(0.28 + o_, wf(0.28 + o_) * (0.6 - o_))
            e_ = _lerp2(a_, sh_f, 0.45 + 0.25 * reach)
            m_ = _lerp2(a_, e_, 0.5)
            sag = 1.2 * (1.0 - reach)
            fig.line(4, [a_, (m_[0] - nx * sag, m_[1] - ny * sag), e_], shade(jc, 0.82 - 0.06 * reach), w_ * (0.7 + 0.6 * reach))
        raise_ = clamp((sh_f[1] - hd_f[1]) / 25.0, 0.0, 1.0)
        fig.line(4, [S(0.7, -wb(0.7) * 0.5), S(0.4 - 0.08 * raise_, -wb(0.4) * (0.2 + 0.2 * raise_))], fd_, 0.4 + 0.2 * raise_)
        if top != "keikogi":
            stride = (J["kn_r"][0] - J["kn_l"][0]) * 0.08 - vxl * 0.08
            for u in (0.25, 0.5, 0.75):
                w0_ = wf(0.14) * (1 - 2 * u) if u < 0.5 else -wb(0.14) * (2 * u - 1)
                p0 = S(0.14, w0_)
                p1 = S(skirt[0] + 0.06, w0_ * 1.15 + stride * (u - 0.5) * 6 + stride * 2)
                fig.line(4, [p0, _lerp2(p0, p1, 0.55), p1], fd_, 0.3)
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
            sw = sec.spring("belt", math.sin(t_anim * 4.2 + f.x * 0.01) * 0.8 + clamp(-hip_vx * 1.4, -6.0, 6.0), 0.14, 0.86)
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
        sw = sec.spring("cord", math.sin(t_anim * 5) * 0.8 + clamp(-hip_vx * 1.1, -5.0, 5.0), 0.16, 0.84)
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
        sw = sec.spring("sagari", math.sin(t_anim * 3) * 0.6 + clamp(-hip_vx * 1.2, -5.0, 5.0), 0.12, 0.85)
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
                    p0 = S(0.2, wf(0.2) * (1 - 2 * u) - (wb(0.2) * (2 * u - 1) if u > 0.5 else 0))
                    p1 = _lerp2(_lerp2(hem[0], hem[1], 0.15 + 0.7 * u), (hcx, hcy), 0.12)
                    # tabla: valle oscuro y, al lado, el canto planchado que coge la luz
                    fig.line(4, [p0, p1], shade(hc, 0.7), 0.45)
                    fig.m("cloth")
                    fig.line(4, [(p0[0] + 1.0, p0[1]), (p1[0] + 1.4, p1[1])], mixc(shade(hc, 1.5), (150, 150, 168), 0.22), 0.35)
                    fig.m("cloth", True)
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
    hair_c = shade(C(lk["hair_c"], Hd[0]), 1.0 - 0.12 * sweat)          # el sudor oscurece el pelo
    hair = lk["hair"]
    # pelo con física de muelle: la cabeza que se mueve deja atrás coletas, trenzas, rastas y flequillo,
    # que se pasan de largo al frenar y oscilan hasta asentarse
    hvx, hvy = sec.velocity("spr_head", Hd)
    sw_h = sec.spring("hair", math.sin(t_anim * 3.1) * 0.05 + clamp(hvx * 0.06, -0.3, 0.3) + clamp(hvy * 0.02, -0.15, 0.15))

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
    # impacto: los primeros fotogramas del golpe aplastan la mejilla y empujan la mandíbula hacia atrás
    hitk = 0.0
    if f.state in (Fighter.HIT, Fighter.STUN) and f.timer < 10:
        hitk = (1.0 - f.timer / 10.0) ** 1.5
    if hitk > 0:
        def squash(x, y):
            front = clamp((x - 0.2) / 0.9, 0.0, 1.0)
            return (x - 0.13 * hitk * front, y + 0.05 * hitk * front * (1.0 if y > 0 else -0.4))
        head_pts = [HP(*squash(x, y)) for x, y in _face_points(lk)]
    else:
        head_pts = [HP(x, y) for x, y in _face_points(lk)]
    fig.poly(5, head_pts, face_c, sheen=0.4 + skin_sheen * 0.5)
    # zonas de brillo de la cara: frente, puente y punta de la nariz, pómulo (más con el sudor)
    gl = 2.6 + 2.0 * sweat
    for (gx, gy), gr in (((0.52, -0.56), 0.2), ((0.86, -0.02), 0.07), ((1.02, 0.2), 0.07), ((0.62, 0.12), 0.12)):
        fig.gloss(5, HP(gx, gy), gr * R, gl)
    # oreja: más pequeña y más cálida, con hélice, concha y trago
    ear_c = mixc(shade(face_c, 0.93), (200, 92, 80), 0.12)
    ecx, ecy, erx, ery = -0.1, 0.07, 0.145, 0.235
    ear = [HP(ecx + erx * math.cos(a) * (1.0 - 0.18 * max(0.0, math.sin(a))), ecy + ery * math.sin(a))
           for a in [i * math.pi / 8 for i in range(16)]]
    fig.m("skin")
    if "coliflor" in ex:
        fig.poly(5, ear, mixc(ear_c, (190, 110, 100), 0.25), outline=False, sheen=0.3)
        for dx, dy in ((-0.1, -0.06), (-0.05, 0.1), (-0.15, 0.13)):
            fig.dot(5, HP(dx, dy), 0.085 * R, shade(ear_c, 1.08))
    else:
        fig.poly(5, ear, ear_c, outline=False, sheen=0.3)
    fig.line(5, [HP(-0.03, -0.1), HP(-0.15, -0.07), HP(-0.2, 0.06), HP(-0.15, 0.2), HP(-0.07, 0.26)], shade(ear_c, 0.72), 0.03 * R)
    fig.dot(5, HP(-0.09, 0.08), 0.055 * R, shade(ear_c, 0.8))                      # concha
    fig.dot(5, HP(0.0, 0.08), 0.035 * R, shade(ear_c, 1.04))                       # trago

    # ── expresión: cejas, párpados y boca coherentes ─────────────────────────
    expr = _expression(f, lk)
    E = EXPR.get(expr, EXPR["neutra"])
    lid, low = E["lid"], E["low"]
    if hitk > 0.3:
        lid, low = 0.0, 0.0
    dark = (22, 14, 12)
    iris = hex2rgb(lk.get("eyes", "#3A2618"))
    fem = lk["body"] in ("atleta_f", "menuda")
    # parpadeo: cada 3-5 s, unos 7 fotogramas, desfasado por luchador
    ph_ = (t_anim * 0.27 + (sum(map(ord, fd["id"])) % 17) / 17.0) % 1.0
    if expr not in ("dolor", "grito", "ko") and ph_ < 0.03:
        lid = 0.0
    # mirada: hacia el rival (un poco arriba o abajo si salta o se agacha)
    lx_, ly_ = getattr(f, "look_at", (100.0, 0.0))
    gaze = 0.0 if lx_ * fac >= 0 else -0.35            # con el rival a la espalda, los ojos se van atrás
    gaze_y = clamp(-ly_ / max(80.0, abs(lx_) + 40.0), -0.05, 0.05)
    fig.m("eye")
    ey = -0.105
    apex_y = ey - 0.088 * lid
    low_y = ey + 0.052 - 0.032 * low
    if lid < 0.12:
        # cerrado: la línea de las pestañas se curva hacia abajo (apretada en el dolor)
        sq = 0.03 if expr == "dolor" or hitk > 0.3 else 0.0
        fig.line(5, [HP(0.46, ey + 0.005), HP(0.6, ey + 0.035 + sq), HP(0.76, ey + 0.0)], dark, 0.065 * R)
        fig.line(5, [HP(0.48, ey - 0.04 - sq), HP(0.61, ey - 0.06 - sq), HP(0.73, ey - 0.035)], shade(face_c, 0.78), 0.025 * R)
    else:
        white = [HP(0.46, ey), HP(0.585, apex_y), HP(0.71, ey - 0.06 * lid), HP(0.765, ey + 0.018), HP(0.64, low_y), HP(0.52, ey + 0.035)]
        fig.poly(5, white, (230, 223, 212), outline=False, shade_=False, smooth=1)
        gx, gy = 0.668 + gaze * 0.2, ey - gaze_y + 0.004
        fig.dot(5, HP(gx, gy), 0.08 * R, shade(iris, 0.6))                          # borde del iris
        fig.dot(5, HP(gx, gy), 0.066 * R, iris)
        fig.dot(5, HP(gx + 0.004, gy + 0.03), 0.034 * R, mixc(iris, (235, 220, 190), 0.35))   # luz de abajo del iris
        fig.dot(5, HP(gx + 0.012, gy), 0.036 * R, dark)                              # pupila
        fig.dot(5, HP(gx - 0.022, gy - 0.032), 0.024 * R, (255, 255, 250))          # brillo húmedo
        fig.dot(5, HP(gx + 0.03, gy + 0.03), 0.011 * R, (255, 250, 240))
        if lid < 0.98:
            # el párpado cubre lo que el ojo no abre: piel desde la línea hasta la ceja
            fig.m("skin")
            fig.poly(5, [HP(0.45, ey - 0.002), HP(0.585, apex_y), HP(0.72, ey - 0.058 * lid), HP(0.78, ey + 0.012),
                         HP(0.8, ey - 0.16), HP(0.44, ey - 0.16)], face_c, outline=False, smooth=1)
            fig.m("eye")
        if low > 0.05:
            fig.m("skin")
            fig.poly(5, [HP(0.5, ey + 0.06), HP(0.64, low_y), HP(0.77, ey + 0.02), HP(0.76, ey + 0.09), HP(0.52, ey + 0.1)],
                     face_c, outline=False, smooth=1)
            fig.m("eye")
        # sombra del párpado sobre el blanco, línea del párpado (con ala y pestañas en ellas), pliegue y bajo
        fig.line(5, [HP(0.49, ey - 0.005), HP(0.585, apex_y + 0.022), HP(0.7, ey - 0.042 * lid)], (176, 164, 154), 0.03 * R)
        fig.line(5, [HP(0.445, ey + 0.004), HP(0.585, apex_y - 0.004), HP(0.71, ey - 0.062 * lid), HP(0.8, ey + 0.004)], dark,
                 (0.085 if fem else 0.072) * R)
        if fem:
            fig.line(5, [HP(0.76, ey - 0.01), HP(0.83, ey - 0.05)], dark, 0.035 * R)
            fig.line(5, [HP(0.71, ey - 0.045 * lid), HP(0.76, ey - 0.09 * lid - 0.02)], dark, 0.025 * R)
        fig.line(5, [HP(0.5, apex_y - 0.04), HP(0.61, apex_y - 0.06), HP(0.71, ey - 0.09 * lid - 0.02)], shade(face_c, 0.8), 0.02 * R)
        fig.line(5, [HP(0.53, low_y + 0.005), HP(0.64, low_y + 0.01), HP(0.74, ey + 0.035)], shade(face_c, 0.74), 0.02 * R)
    # ceja de pincel: gruesa junto a la nariz, se afila hacia la sien
    fig.m("hair")
    bw = 0.105 * R * lk["brow"]
    bi, bo = E["brow_in"], E["brow_out"]
    brow_c = shade(hair_c, 0.85) if hair not in ("mono",) else (205, 205, 200)
    fig.poly(5, lock_pts(HP(0.83, -0.28 + bi), HP(0.63, -0.355 + (bi + bo) * 0.4), HP(0.4, -0.3 + bo), bw), brow_c,
             outline=False, smooth=0)
    # nariz: aleta, orificio y sombra bajo la punta
    fig.m("skin", True)
    fig.line(5, [HP(0.85, 0.215), HP(0.92, 0.29), HP(0.99, 0.285)], shade(face_c, 0.66), 0.035 * R)
    fig.line(5, [HP(0.83, -0.02), HP(0.9, 0.12)], shade(face_c, 0.84), 0.025 * R)
    fig.m("skin")
    fig.dot(5, HP(0.95, 0.27), 0.03 * R, shade(face_c, 0.55))
    # definición: pómulo y ángulo de la mandíbula; surco nasogeniano al sonreír, gritar o con la edad
    # (líneas de color, no surcos: un surco en la mejilla se lee como cicatriz)
    if lk["jaw"] >= 1.05 or lk["body"] in ("atleta", "fibroso", "pesado", "anciano"):
        fig.line(5, [HP(0.64, 0.22), HP(0.5, 0.34), HP(0.36, 0.52)], shade(face_c, 0.94), 0.03 * R)
    if E["fold"] or "arrugas" in ex:
        fig.line(5, [HP(0.86, 0.3), HP(0.81, 0.38), HP(0.76, 0.47)], shade(face_c, 0.86), 0.02 * R)
    fig.m("skin")
    if "rubor" in ex or red > 0.05:
        fig.dot(5, HP(0.58, 0.2), 0.13 * R, mixc(face_c, (215, 110, 100), 0.1 + 0.25 * red))
    # boca
    lip_c = mixc(face_c, (176, 72, 72), 0.3)
    line_c = shade(face_c, 0.52)
    m = E["mouth"]
    if m in ("shout", "grimace", "open", "slack"):
        if m == "shout":
            pts = [(0.7, 0.44), (0.935, 0.42), (0.925, 0.6), (0.81, 0.69), (0.72, 0.6)]
        elif m == "grimace":
            pts = [(0.68, 0.47), (0.925, 0.43), (0.91, 0.53), (0.72, 0.57)]
        elif m == "open":
            pts = [(0.74, 0.465), (0.91, 0.455), (0.9, 0.56), (0.8, 0.6), (0.75, 0.55)]
        else:
            pts = [(0.75, 0.48), (0.9, 0.475), (0.88, 0.57), (0.79, 0.6)]
        fig.poly(5, [HP(x, y) for x, y in pts], (62, 22, 24), outline=False, shade_=False, smooth=1)
        if m in ("shout", "grimace"):
            fig.poly(5, [HP(0.73, 0.448), HP(0.92, 0.43), HP(0.915, 0.475), HP(0.74, 0.49)], (232, 226, 214), outline=False, shade_=False)
        if m in ("shout", "open"):
            fig.dot(5, HP(0.81, 0.62 if m == "shout" else 0.55), 0.06 * R, (150, 62, 62))
        fig.line(5, [HP(x, y) for x, y in pts[2:]] if m != "slack" else [HP(0.79, 0.62), HP(0.88, 0.6)], lip_c, 0.05 * R)
    elif m == "teeth":
        fig.poly(5, [HP(0.71, 0.465), HP(0.93, 0.452), HP(0.92, 0.52), HP(0.72, 0.53)], (226, 220, 208), outline=False, shade_=False)
        fig.line(5, [HP(0.71, 0.492), HP(0.925, 0.484)], (120, 100, 92), 0.015 * R)
        for x in (0.77, 0.83, 0.88):
            fig.line(5, [HP(x, 0.46), HP(x, 0.525)], (150, 132, 122), 0.012 * R)
        fig.line(5, [HP(0.7, 0.462), HP(0.935, 0.448)], line_c, 0.035 * R)
        fig.line(5, [HP(0.72, 0.535), HP(0.92, 0.525)], lip_c, 0.04 * R)
    else:
        corner = {"line": (0.7, 0.49), "firm": (0.705, 0.505), "smile": (0.665, 0.43), "smirk": (0.68, 0.45)}.get(m, (0.7, 0.49))
        fig.poly(5, [HP(0.75, 0.505), HP(0.905, 0.49), HP(0.885, 0.545 if m != "firm" else 0.53), HP(0.8, 0.56 if m != "firm" else 0.545)],
                 lip_c, outline=False, smooth=1)
        fig.line(5, [HP(*corner), HP(0.8, 0.5 if m != "smile" else 0.49), HP(0.935, 0.474)], line_c, 0.042 * R)
        fig.dot(5, HP(0.82, 0.59), 0.03 * R, shade(face_c, 0.88))                  # sombra bajo el labio
        if m == "smile":
            fig.m("skin", True)
            fig.line(5, [HP(0.8, 0.3), HP(0.7, 0.36), HP(0.655, 0.44)], shade(face_c, 0.78), 0.03 * R)
            fig.m("skin")
        if m == "smirk":
            fig.dot(5, HP(0.665, 0.43), 0.02 * R, shade(face_c, 0.72))

    # ── pelo: una base más oscura y mechones encima, cada uno con su eje ──────
    fig.m("hair")

    def arc(r, a0, a1, step=10):
        out = []
        a = a0
        st = step if a1 > a0 else -step
        while (a <= a1) if st > 0 else (a >= a1):
            out.append(HP(-0.08 + r * math.cos(math.radians(a)), -0.05 + r * math.sin(math.radians(a))))
            a += st
        return out

    def polar(r, a_deg, dx=0.0, dy=0.0):
        a = math.radians(a_deg)
        return HP(-0.08 + r * math.cos(a) + dx, -0.05 + r * math.sin(a) + dy)

    lock_i = [0]

    def strand(base, ctrl, tip, w, sheen=1.4):
        """Un mechón: ancho en la raíz, curvo, afilado; cada uno con un tono algo distinto para que se lean."""
        k_ = (0.94, 1.06, 0.99, 1.1, 0.96)[lock_i[0] % 5]
        lock_i[0] += 1
        fig.poly(5, lock_pts(base, ctrl, tip, w * R), shade(hair_c, k_), sheen=sheen, smooth=0)

    base_c = shade(hair_c, 0.8)
    cap_inner = [HP(-0.66, 0.36), HP(-0.5, 0.08), HP(-0.22, -0.3), HP(0.12, -0.6), HP(0.42, -0.76)]
    swing = sw_h * 0.6
    if hair in ("corto", "trenza", "coleta", "trenza_nuca", "rastas", "chonmage", "mono"):
        r_ = {"corto": 1.06, "trenza": 1.04, "coleta": 1.04, "trenza_nuca": 1.05, "rastas": 1.08, "chonmage": 1.04, "mono": 1.04}[hair]
        a0 = -98 if hair == "mono" else -62
        fig.poly(5, arc(r_, a0, -212) + cap_inner, base_c, sheen=0.8, edge=True)
    elif hair == "rapado":
        fig.poly(5, arc(1.02, -62, -212) + cap_inner, mixc(face_c, hair_c, 0.45), outline=False, sheen=0.3)
        fig.m("hair", True)
        for a in range(-70, -205, -18):                                         # nacimiento del pelo rapado
            fig.dot(5, polar(0.9, a), 0.05 * R, shade(hair_c, 0.9))
        fig.m("hair")
    elif hair == "melena":
        pts = [HP(0.8, -0.34), HP(0.74, -0.86)] + arc(1.13, -55, -195) + [HP(-1.08, 0.8), HP(-0.42, 0.88), HP(-0.3, 0.32), HP(0.1, -0.06), HP(0.46, -0.3)]
        fig.poly(5, pts, base_c, sheen=1.2, edge=True)
    elif hair == "rizado":
        fig.poly(5, arc(1.04, -62, -212) + cap_inner, base_c, sheen=0.6, edge=True)

    if hair == "corto":
        # mechones cortos que salen de la coronilla: los de delante caen hacia la frente, los de atrás a la nuca
        for i, a in enumerate(range(-46, -205, -16)):
            front_ = a > -95
            bend = 16 if front_ else -15
            ln = 1.12 + 0.08 * ((i * 5) % 3) / 2
            strand(polar(0.5, a), polar(0.92, a + bend * 0.5), polar(ln, a + bend, dx=-swing * (0 if front_ else 1)), 0.4)
        strand(HP(0.18, -0.9), HP(0.55, -0.88), HP(0.74, -0.6 + 0.04), 0.3)                       # flequillo
        strand(HP(0.05, -0.95), HP(0.35, -0.92), HP(0.5, -0.7), 0.24)
    elif hair in ("trenza", "trenza_nuca", "coleta", "chonmage", "mono"):
        # recogido: mechones largos desde el nacimiento del pelo, por encima del cráneo, hacia el moño o la coleta
        tie = {"coleta": (-0.78, -0.72), "trenza": (-0.9, -0.15), "trenza_nuca": (-0.95, 0.2), "chonmage": (-0.4, -1.0),
               "mono": (-0.82, -0.8)}[hair]
        for i, a in enumerate((-58, -72, -86, -100, -114, -128)):
            b_ = polar(1.0, a)
            mid = polar(1.06, (a + math.degrees(math.atan2(tie[1] + 0.05, tie[0] + 0.08))) * 0.5)
            strand(b_, mid, HP(tie[0] + 0.05 * (i % 2), tie[1] + 0.03 * i), 0.26, sheen=1.8)
        # mechones sueltos en la sien y el flequillo
        if hair != "chonmage" and hair != "mono":
            strand(HP(0.38, -0.86), HP(0.62, -0.7), HP(0.64, -0.42), 0.2)
            strand(HP(0.1, -0.8), HP(0.18, -0.4), HP(0.12 - sw_h, -0.02), 0.14)
    elif hair == "melena":
        for i, a in enumerate(range(-60, -200, -20)):
            b_ = polar(0.55, a)
            tip_ = polar(1.12, a)
            strand(b_, polar(1.05, a), (tip_[0], tip_[1] + 0.35 * R), 0.42)            # la melena cae por su peso
        for x0, x1 in ((-1.02, -1.08), (-0.82, -0.86), (-0.6, -0.62)):
            strand(HP(x0, 0.5), HP(x0 - 0.02, 0.7), HP(x1 - sw_h * 0.4, 0.98), 0.24)
        for x0 in (0.42, 0.56, 0.68):                                                     # flequillo
            strand(HP(x0 - 0.1, -0.92), HP(x0 + 0.06, -0.62), HP(x0 + 0.1, -0.36), 0.18)
    elif hair == "rizado":
        for ring, rad, n in ((1.02, 0.24, 8), (0.8, 0.2, 6)):
            for i, a in enumerate(range(-66, -220, -int(154 / n))):
                fig.circle(5, polar(ring, a), rad * R * (0.85 + 0.3 * ((i * 7) % 3) / 2), shade(hair_c, (0.92, 1.08, 1.0)[i % 3]), sheen=0.9)
        fig.m("hair", True)
        for i, a in enumerate(range(-70, -220, -30)):                                    # rizo: espiral de cada bucle
            c_ = polar(1.02, a)
            fig.line(5, [(c_[0] + math.cos(t) * 0.12 * R * (1 - t / 9), c_[1] + math.sin(t) * 0.12 * R * (1 - t / 9))
                         for t in [k * 0.9 for k in range(7)]], shade(hair_c, 0.7), 0.03 * R)
        fig.m("hair")
    if hair == "chonmage":
        tk = [HP(-0.45, -1.02), HP(-0.1, -1.32), HP(0.42, -1.28), HP(0.5, -1.12), HP(0.05, -1.08)]
        fig.poly(5, tk, hair_c, sheen=3.0, edge=True)
        fig.line(5, [HP(-0.32, -1.02), HP(-0.18, -1.2)], (220, 220, 215), 0.06 * R)
    if hair == "mono":
        fig.circle(5, HP(-0.82, -0.8), 0.3 * R, hair_c, sheen=1.2)
        fig.line(5, [HP(-0.66, -0.66), HP(-0.6, -0.98)], shade(hair_c, 0.7), 0.05 * R)
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
        for layer, (r0, lw_) in enumerate(((0.92, 0.12), (0.98, 0.13))):
            for i, a in enumerate(range(-80 - layer * 8, -235, -17)):
                base = polar(r0, a)
                dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
                ln = 0.55 + 0.25 * ((i * 7 + layer) % 3) / 2
                tip = HP(-0.08 + (r0 + ln) * dx - sw_h * (1 + i * 0.1), -0.05 + (r0 + ln) * dy + 0.45)
                fig.tube(5, base, tip, lw_ * R, lw_ * 0.8 * R, shade(hair_c, (0.9, 1.05)[(i + layer) % 2]), sheen=0.9)
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

    # ── desgaste del combate: marcas de los golpes, gotas de sudor y pelo revuelto ──
    marks = getattr(f, "marks", None) or {}
    v = marks.get("pomulo", 0.0)
    if v > 0.05:
        fig.m("skin")
        fig.dot(5, HP(0.56, 0.12), (0.12 + 0.05 * v) * R, mixc(face_c, (112, 72, 112), 0.12 + 0.22 * v))
        fig.bump(5, HP(0.56, 0.1), 0.16 * R, 0.12 * R, 0.0, 3 * v, skin_sheen)                     # hinchazón
    v = marks.get("ceja", 0.0)
    if v > 0.05:
        fig.m("skin")
        fig.bump(5, HP(0.6, -0.33), 0.15 * R, 0.08 * R, 0.0, 4 * v, skin_sheen)
        fig.line(5, [HP(0.53, -0.36), HP(0.6, -0.39), HP(0.67, -0.37)], (150, 28, 30), (0.02 + 0.02 * v) * R)  # brecha en la ceja
    v = marks.get("costillas", 0.0)
    if v > 0.05 and top == "none":
        fig.m("skin")
        fig.dot(2, S(0.5, wf(0.5) * 0.45), 3.0 + 1.5 * v, mixc(sk_t, (110, 70, 105), 0.1 + 0.2 * v))
    if sweat > 0.45:
        # gotas en la frente y la sien: puntos de brillo sobre la piel, más cuanto más sudor
        fig.m("skin")
        for i, (gx_, gy_) in enumerate(((0.62, -0.5), (0.45, -0.42), (0.78, -0.4), (0.3, -0.2), (0.7, -0.62))[:int(1 + sweat * 4)]):
            fig.dot(5, HP(gx_, gy_), 0.028 * R, mixc(face_c, (255, 250, 240), 0.35))
            fig.gloss(5, HP(gx_, gy_), 0.05 * R, 4.0)
    if dish > 0.25 and hair not in ("rapado",):
        # pelo revuelto: mechones sueltos que se salen del peinado
        fig.m("hair")
        for i in range(int(dish * 6)):
            a = -2.3 + i * 0.55
            r0 = 0.92
            p0 = HP(0.1 + r0 * math.cos(a) * 0.9, -0.05 + r0 * math.sin(a))
            p1 = HP(0.1 + (r0 + 0.25) * math.cos(a + 0.25 * (-1) ** i) * 0.9, -0.05 + (r0 + 0.22) * math.sin(a + 0.2))
            fig.line(5, [p0, _lerp2(p0, p1, 0.5), p1], hair_c, 0.035 * R)

    # ── brazos ───────────────────────────────────────────────────────────────
    fig.zone(cloth=top_pat)
    up0, up1, upb = B["upper"]
    fo0, fo1, fob = B["fore"]
    sleeve = lk.get("sleeve", 0.0) if top in ("gi", "kurtka", "changshan", "keikogi") else 0.0
    grabbing = f.state == Fighter.ATTACK and f.move is not None and f.move.anim == "grab"
    # en el agarre la mano va abierta mientras busca y se cierra en garra al hacer presa (y durante la proyección)
    claw = f.state == Fighter.THROWING or (grabbing and f.timer >= getattr(f.move, "startup", 0))
    open_hand = (grabbing and not claw) or (lk.get("guard_open") and f.state in (Fighter.IDLE, Fighter.WALK, Fighter.INTRO))
    def hand_frame(el, hd):
        fx_, fy_ = _norm(hd[0] - el[0], hd[1] - el[1])
        if fx_ == 0 and fy_ == 0:
            fx_ = 1.0
        px_, py_ = -fy_, fx_
        return lambda a, b: (hd[0] + fx_ * a + px_ * b, hd[1] + fy_ * a + py_ * b)

    def arm_ik(sh, el0, hd0, stretch=1.2, pro_max=3.0):
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
            pro = min(pro_max, D - reach)
            sh = (sh[0] + ux * pro, sh[1] + uy * pro)
            D -= pro
        k = clamp(D / reach, 1.0, stretch)             # y un estirón de animación
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
        hit_arm = strike is not None and strike[0] == side
        sh, el, hd = arm_ik(J["sh_" + side], J["el_" + side], J["hd_" + side],
                            *((1.2 + 0.05 * strike[1], 3.0 + 4.0 * strike[1]) if hit_arm else ()))
        fig.joints.update({"sh_" + side: sh, "el_" + side: el, "hd_" + side: hd})
        # smear: con el puño rápido, la silueta se estira entre donde estaba y donde está (1-2 fotogramas)
        was, speed = sec.motion("hd_" + side, hd)
        if speed > 9.0 and f.state in (Fighter.ATTACK, Fighter.KIAI):
            fig.m("skin")
            rr = B["hand"] * 0.75
            k_ = clamp((speed - 9.0) / 12.0, 0.0, 1.0)
            tail = _lerp2(hd, was, 0.85 * k_)
            fig.tube(g, tail, hd, rr * 0.55, rr, C(skin, hd[0], depth), outline=False, sheen=0.4)
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
        # sin salirse de la espalda: con el hombro echado atrás (el cross del otro brazo) asomaba como un bulto
        s0 = S(0.84, 0.0)
        lim = -(wb(0.84) - B["delt"] * 0.85)
        dn = (cap[0] - s0[0]) * nx + (cap[1] - s0[1]) * ny
        if dn < lim:
            cap = (cap[0] + nx * (lim - dn), cap[1] + ny * (lim - dn))
        if sleeve > 0:
            # antebrazo que asoma por la manga
            # (empieza ya dentro de la manga: desde el codo asomaba por la esquina de dentro al doblar el brazo)
            e0 = _lerp2(el, hd, max(0.0, sleeve - 0.3))
            fig.ribbon(g, [e0, _lerp2(e0, hd, 0.5), hd], [(0, fo0 + fob * 0.5), (0.3, fo0 + fob * 0.6), (0.85, fo1 * 0.95), (1, fo1 * 0.9)],
                       [(0, fo0), (0.3, fo0 + fob), (0.85, fo1 * 0.95), (1, fo1 * 0.9)], fore_sk, sheen=skin_sheen)
            fig.m("cloth")
            slc = C(cloth, el[0], depth)
            cut = sec.lag("cut_" + side, _lerp2(el, hd, sleeve), k=0.42, maxd=6.0)   # la manga llega tarde
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
            if side == "r":
                shoulder_patch(g, J["sh_r"], cap)
            # puño de la manga: refuerzo con pespuntes
            fig.m("cloth", pat="stitch")
            c0 = _lerp2(el, cut, 0.84)
            fig.ribbon(g, [c0, _lerp2(c0, cut, 0.5), cut], [(0, fo1 + wide + 0.5), (1, fo1 + wide + 0.75)],
                       [(0, fo1 + wide - 0.2), (1, fo1 + wide - 0.05)], shade(slc, 0.95), sheen=0.4)
            fig.m("cloth", True)
            # pliegues del codo, del lado de dentro del doblez: nacen al doblar el brazo (recto, la manga es lisa)
            cr = (el[0] - sh[0]) * (hd[1] - el[1]) - (el[1] - sh[1]) * (hd[0] - el[0])
            nx_, ny_ = _norm(-(el[1] - sh[1]), el[0] - sh[0])
            sgn = 1.0 if cr >= 0 else -1.0
            u1_, u2_ = _norm(el[0] - sh[0], el[1] - sh[1]), _norm(hd[0] - el[0], hd[1] - el[1])
            bend = clamp((1.0 - (u1_[0] * u2_[0] + u1_[1] * u2_[1])) / 0.8, 0.0, 1.0)
            if bend < 0.35:
                # brazo estirado: la manga se retuerce, pliegues en espiral del codo hacia el hombro
                tw = 1.0 - bend / 0.35
                for q0 in (0.25, 0.55):
                    pa = _lerp2(sh, el, q0)
                    pb = _lerp2(sh, el, q0 + 0.3)
                    rr_ = up1 + 1.6
                    fig.line(g, [(pa[0] + nx_ * rr_ * 0.7, pa[1] + ny_ * rr_ * 0.7), (pb[0] - nx_ * rr_ * 0.5, pb[1] - ny_ * rr_ * 0.5)],
                             shade(slc, 0.84), 0.45 * tw)
            if bend > 0.12:
                for o_, w_ in ((0.45, 0.8), (0.15, 0.6)):
                    off = (nx_ * sgn * up1 * o_, ny_ * sgn * up1 * o_)
                    fig.line(g, [(p_[0] + off[0], p_[1] + off[1]) for p_ in
                                 (_lerp2(sh, el, 1.0 - 0.3 * bend), el, _lerp2(el, cut, 0.25 * bend))], shade(slc, 0.8), w_ * (0.5 + 0.5 * bend))
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
            if side == "r" and top == "none":
                shoulder_patch(g, J["sh_r"], cap)
            fig.gloss(g, _lerp2(cap, sh, 0.7), B["delt"] * 0.45, 2.4 + 2.0 * sweat)       # brillo del hombro
            fig.m("skin", True)
            cr = (el[0] - sh[0]) * (hd[1] - el[1]) - (el[1] - sh[1]) * (hd[0] - el[0])
            nx_, ny_ = _norm(-(el[1] - sh[1]), el[0] - sh[0])
            sgn = 1.0 if cr >= 0 else -1.0
            fig.line(g, [(el[0] + nx_ * sgn * up1 * 0.45, el[1] + ny_ * sgn * up1 * 0.45),
                         _lerp2(el, (el[0] + nx_ * sgn * up1 * 0.7, el[1] + ny_ * sgn * up1 * 0.7), 1.0)], shade(sk, 0.8), 0.4)   # pliegue del codo
            # vientres musculares en relieve (bíceps del lado del doblez, tríceps detrás, extensores del antebrazo)
            dm = {"sumo": 0.5, "anciano": 0.45, "menuda": 0.6, "atleta_f": 0.75}.get(lk["body"], 1.0) * (1.3 if "definido" in ex else 1.0)
            au = math.atan2(el[1] - sh[1], el[0] - sh[0])
            af = math.atan2(hd[1] - el[1], hd[0] - el[0])
            flexion = clamp((1.0 - math.cos(af - au)) / 1.2, 0.0, 1.0)
            dm *= 1.0 + 0.45 * flexion                         # el bíceps se hincha al doblar el brazo
            bc = _lerp2(sh, el, 0.56)
            fig.bump(g, (bc[0] + nx_ * sgn * up1 * 0.3, bc[1] + ny_ * sgn * up1 * 0.3), 0.26 * L1, up1 * 0.5, au, 6 * dm, skin_sheen)
            tc = _lerp2(sh, el, 0.42)
            fig.bump(g, (tc[0] - nx_ * sgn * up1 * 0.35, tc[1] - ny_ * sgn * up1 * 0.35), 0.3 * L1, up1 * 0.45, au, 5 * dm, skin_sheen)
            fc = _lerp2(el, hd, 0.28)
            fig.bump(g, fc, 0.28 * L2, fo0 * 0.55, af, 5 * dm, skin_sheen)
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
        hs = B["hand"] / 4.3 * 1.08                     # las manos pequeñas restaban fuerza a los golpes
        nail = mixc(fore_sk, (238, 210, 200), 0.35)
        if "guantes" in ex:
            fig.m("leather")
            gc = C("#1A1A1A", hd[0], depth)
            glove = [HF((a - 3) * hs * 1.35, b * hs * 1.35) for a, b in ((-2, -4.2), (3.5, -5.0), (8.2, -3.6), (9, 0), (8.2, 3.8), (3, 4.8), (-2, 4.0))]
            fig.poly(g, glove, gc, sheen=2.4, smooth=2)
            fig.line(g, [HF(-5.8 * hs, -4.4 * hs), HF(-5.8 * hs, 4.4 * hs)], C("#3A3A3A", hd[0], depth), 1.6, taper=False)
            fig.m("leather", True)
            fig.line(g, [HF(6.0 * hs, -3.0 * hs), HF(7.4 * hs, 0), HF(6.0 * hs, 3.0 * hs)], shade(gc, 1.6), 0.3)
        elif claw:
            # garra: la palma empuja y los dedos se cierran en gancho sobre la solapa, nudillos marcados
            fig.m("skin")
            H_ = lambda a, b: HF(a * hs, b * hs)
            palm = [H_(a, b) for a, b in ((-1.5, -2.8), (3.5, -3.8), (8.4, -3.4), (9.6, -0.6), (9.4, 2.4), (7.6, 3.6), (3.5, 3.4), (-1.5, 2.7))]
            fig.poly(g, palm, fore_sk, sheen=skin_sheen, smooth=2)
            tips = []
            for i, (bk, ln, rr) in enumerate(((2.45, 5.6, 0.82), (0.82, 7.0, 0.9), (-0.82, 7.6, 0.93), (-2.45, 7.0, 0.9))):
                k0 = H_(7.8, bk * 0.85)
                k1 = H_(7.8 + ln * 0.42, bk * 0.85 + 0.6)                  # falange: adelante
                k2 = H_(7.8 + ln * 0.5, bk * 0.85 + 0.6 + ln * 0.36)       # media: hacia abajo
                k3 = H_(7.8 + ln * 0.3, bk * 0.85 + 0.6 + ln * 0.62)       # punta: vuelve hacia la palma
                fig.tube(g, k0, k1, rr * hs, rr * 0.95 * hs, fore_sk, sheen=skin_sheen * 0.6)
                fig.tube(g, k1, k2, rr * 0.95 * hs, rr * 0.85 * hs, fore_sk, sheen=skin_sheen * 0.6)
                fig.tube(g, k2, k3, rr * 0.85 * hs, rr * 0.72 * hs, fore_sk, sheen=skin_sheen * 0.6)
                tips.append((k0, k1, k2, k3))
            t0, t1, t2 = H_(2.0, -2.6), H_(5.6, -4.2), H_(8.6, -3.0)
            fig.tube(g, t0, t1, 1.35 * hs, 1.15 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.tube(g, t1, t2, 1.12 * hs, 0.9 * hs, fore_sk, sheen=skin_sheen * 0.6)
            # tinta fina entre dedos y nudillos en relieve
            crease = shade(fore_sk, 0.6)
            for (a0, a1, a2, a3), (b0, b1, b2, b3) in zip(tips, tips[1:]):
                fig.line(g, [_lerp2(a1, b1, 0.5), _lerp2(a2, b2, 0.5), _lerp2(a3, b3, 0.5)], crease, 0.16 * hs)
            fig.m("skin", True)
            fig.line(g, [tips[0][1], tips[1][1], tips[2][1], tips[3][1]], shade(fore_sk, 0.8), 0.3 * hs)
            fig.m("skin")
            for k0, k1, k2, k3 in tips:
                fig.bump(g, k1, 0.9 * hs, 0.75 * hs, 0.0, 4, skin_sheen)                                   # nudillos en relieve
                fig.dot(g, _lerp2(k2, k3, 0.75), 0.4 * hs, nail)
        elif open_hand:
            fig.m("skin")
            H_ = lambda a, b: HF(a * hs, b * hs)
            palm = [H_(a, b) for a, b in ((-1.5, -2.8), (3.5, -3.7), (8.6, -3.3), (9.4, -1.0), (9.2, 1.6), (8.4, 3.3), (3.5, 3.4), (-1.5, 2.7))]
            fig.poly(g, palm, fore_sk, sheen=skin_sheen, smooth=2)
            # dedos delante de la palma, de meñique a índice: dos falanges con curva, algo abiertos en abanico
            tips = []
            for bk, ln, rr in ((2.45, 5.6, 0.82), (0.82, 7.0, 0.9), (-0.82, 7.6, 0.93), (-2.45, 7.0, 0.9)):
                spread = bk * 0.1
                k0 = H_(7.6, bk)
                k1 = H_(7.6 + ln * 0.6, bk + 0.3 + ln * 0.05 + spread * ln * 0.6)
                k2 = H_(7.6 + ln * 1.0, bk + 1.2 + ln * 0.12 + spread * ln)
                fig.tube(g, k0, k1, rr * hs, rr * 0.93 * hs, fore_sk, sheen=skin_sheen * 0.6)
                fig.tube(g, k1, k2, rr * 0.93 * hs, rr * 0.78 * hs, fore_sk, sheen=skin_sheen * 0.6)
                tips.append((k0, k1, k2))
            # pulgar: sale de la base de la palma y se abre hacia delante
            t0, t1, t2 = H_(2.0, -2.6), H_(5.4, -4.9), H_(8.4, -5.4)
            fig.tube(g, t0, t1, 1.35 * hs, 1.15 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.tube(g, t1, t2, 1.12 * hs, 0.9 * hs, fore_sk, sheen=skin_sheen * 0.6)
            crease = shade(fore_sk, 0.6)
            for (a0, a1, a2), (b0, b1, b2) in zip(tips, tips[1:]):
                fig.line(g, [_lerp2(a0, b0, 0.5), _lerp2(a1, b1, 0.5), _lerp2(_lerp2(a1, b1, 0.5), _lerp2(a2, b2, 0.5), 0.6)], crease, 0.15 * hs)
            fig.line(g, [H_(2.6, -2.2), H_(5.0, -3.6)], crease, 0.15 * hs)                                    # base del pulgar
            fig.m("skin", True)
            fig.line(g, [H_(3.0, 0.8), H_(6.0, 0.2), H_(8.6, 0.9)], shade(fore_sk, 0.8), 0.45)        # línea de la palma
            fig.line(g, [tips[0][1], tips[1][1], tips[2][1], tips[3][1]], shade(fore_sk, 0.82), 0.25 * hs)    # pliegue de los dedos
            fig.m("skin")
            for (k0, k1, k2), (_, _, rr) in zip(tips, ((2.45, 5.6, 0.82), (0.82, 7.0, 0.9), (-0.82, 7.6, 0.93), (-2.45, 7.0, 0.9))):
                fig.dot(g, _lerp2(k1, k2, 0.82), rr * 0.5 * hs, nail)                                       # uñas
                fig.bump(g, k0, 0.9 * hs, 0.7 * hs, 0.0, 3, skin_sheen)                                    # nudillos
            fig.dot(g, _lerp2(t1, t2, 0.8), 0.5 * hs, nail)
        else:
            fig.m("skin")
            H_ = lambda a, b: HF((a - 2.6) * hs, b * hs)
            fist = [H_(a, b) for a, b in ((-1.6, -3.4), (3.0, -4.1), (6.6, -3.9), (7.9, -2.4), (8.2, 0.6), (7.6, 3.3), (5.4, 4.3), (1.2, 4.0), (-1.6, 3.2))]
            fig.poly(g, fist, fore_sk, sheen=skin_sheen, smooth=2)
            # dedos plegados sobre la cara del puño: cuatro falanges redondeadas
            bks = (-2.55, -0.85, 0.85, 2.55)
            for i, bk in enumerate(bks):
                r_ = (0.98 - 0.06 * abs(i - 1.5)) * hs
                fig.tube(g, H_(8.0, bk), H_(5.2, bk + 0.5), r_, r_ * 0.95, fore_sk, sheen=skin_sheen * 0.6)
            # pulgar cruzado sobre índice y corazón
            fig.tube(g, H_(1.0, -3.4), H_(4.4, -2.3), 1.3 * hs, 1.15 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.tube(g, H_(4.4, -2.3), H_(6.6, -0.2), 1.12 * hs, 0.95 * hs, fore_sk, sheen=skin_sheen * 0.6)
            fig.m("skin")
            fig.dot(g, H_(6.6, -0.2), 0.42 * hs, nail)
            # tinta fina: separación de los dedos plegados, borde del pulgar y fila de nudillos
            crease = shade(fore_sk, 0.6)
            for b0, b1 in zip(bks[1:], bks[2:]):
                m_ = (b0 + b1) * 0.5
                fig.line(g, [H_(8.1, m_), H_(6.4, m_ + 0.25), H_(5.0, m_ + 0.55)], crease, 0.15 * hs)
            fig.line(g, [H_(1.2, -2.3), H_(4.4, -1.2), H_(6.4, 0.6)], crease, 0.16 * hs)
            fig.m("skin", True)
            fig.line(g, [H_(8.6, -3.0), H_(8.9, 0.0), H_(8.4, 3.0)], shade(fore_sk, 0.8), 0.35 * hs)            # nudillos
            fig.m("skin")
            for bk in bks:
                fig.bump(g, H_(7.4, bk), 1.0 * hs, 0.8 * hs, 0.0, 4, skin_sheen)                           # cabeza de cada metacarpo
            fig.gloss(g, H_(8.2, -0.5), 1.4 * hs, 2.8 + 2.0 * sweat)
            if "vendas" in ex:
                # vendas de muay thai: muñeca, cruce en X por el dorso y una vuelta sobre los nudillos
                fig.m("cloth", pat="stitch")
                vc = C("#EDE6D6", hd[0], depth)
                vd = shade(vc, 0.82)
                fig.ribbon(g, [H_(-4.6, 0.0), H_(-2.2, 0.0), H_(0.4, 0.0)], [(0, 4.1 * hs), (1, 3.7 * hs)], [(0, 4.1 * hs), (1, 3.5 * hs)],
                           vc, sheen=0.4)
                fig.m("cloth")
                fig.line(g, [H_(0.2, -3.3), H_(3.4, 0.2), H_(6.2, 3.9)], vc, 1.1 * hs)
                fig.line(g, [H_(0.4, 3.4), H_(3.2, 0.0), H_(6.4, -3.6)], vc, 1.1 * hs)
                fig.line(g, [H_(6.9, -3.6), H_(7.1, 0.0), H_(6.8, 3.6)], vc, 1.2 * hs)
                fig.m("cloth", True)
                for a_ in (-3.8, -2.4, -1.0):
                    fig.line(g, [H_(a_, -3.9), H_(a_ + 0.5, 3.6)], vd, 0.18 * hs)                           # vueltas de la venda
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


def record_figure(f, pose, x0, y0, K, rig, ocol, ow, batch, ss=1.6):
    """La figura como triángulos en pantalla (raíz en x0, y0; K px por cm de pose) para que la GPU la rasterice:
    batch["albedo"] y batch["data"] reciben los mismos trazos que _render y _render_data, batch["pieces"] las
    piezas con su ancla en pantalla. Devuelve el rectángulo que ocupa."""
    fig = build_figure(f, pose, rig, raw=True)
    fac = f.facing
    Ks = K * f.scale
    if fac == 1:
        def T(p):
            return (x0 + p[0] * Ks, y0 + p[1] * Ks)
    else:
        def T(p):
            return (x0 - p[0] * Ks, y0 + p[1] * Ks)
    ra, rd = Recorder(), Recorder(data=True)
    shared: dict = {}
    _render(fig, ra, T, Ks, ow, ocol, ss=ss, fac=fac, baked=False, cache=shared)
    pieces = batch["pieces"]
    # la lista de piezas es la del fotograma (ya trae las del otro luchador): el número es su posición + 1
    _render_data(fig, rd, T, Ks, ow, fac=fac, shared=shared, inv=1.0, id_base=1, pieces=pieces)
    ta, td = Recorder.triangles_of([ra, rd])
    batch["albedo"].append(ta)
    batch["data"].append(td)
    return ra.bounds()


def squash_stretch(f, pose):
    """Aplastar y estirar (principio clásico de animación) sobre el esqueleto, con los pies como ancla:
    al aterrizar el cuerpo se aplasta; al encajar un golpe se comprime en el eje del impacto y se alarga;
    en los fotogramas activos de un ataque se estira hacia el rival. El volumen aparente se conserva."""
    sx = sy = 1.0
    if f.squash > 0.02 and f.y >= 0:
        q = 1 - 0.12 * f.squash
        sx, sy = 2 - q, q
    if f.state in (Fighter.HIT, Fighter.STUN) and f.timer < 8:
        k = (1.0 - f.timer / 8.0) ** 1.5
        sx *= 1.0 - 0.08 * k
        sy *= 1.0 + 0.035 * k
    elif f.state == Fighter.ATTACK and f.move is not None:
        st, ac = f.move.startup, f.move.active + 3
        if st <= f.timer < st + ac:
            k = 1.0 - (f.timer - st) / ac
            sx *= 1.0 + 0.05 * k
            sy *= 1.0 - 0.02 * k
    if sx == 1.0 and sy == 1.0:
        return pose
    return {k: (v[0] * sx, v[1] * sy) for k, v in pose.items()}


def draw_fighter(surf, f: Fighter, cam: Camera, rig: dict, cfg: dict, debug=False, lut_bw=False, data_surf=None, pieces=None,
                 batch=None):
    """Luchador en pantalla: sombra (renderer CPU), figura supermuestreada y cajas de depuración."""
    pose = squash_stretch(f, fighter_pose(f))
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
    if batch is not None:
        # la GPU rasteriza la figura (punto 16): aquí sólo se construyen sus triángulos
        batch["pieces"] = pieces if pieces is not None else batch.get("pieces", [])
        drawn = record_figure(f, pose, x0, y0, cam.S(1), rig, ocol, ow, batch) or pygame.Rect(int(x0), int(y0), 1, 1)
        if f.trail and getattr(f, "_trail_seen", None) is not f.trail[-1]:
            f._trail_seen = f.trail[-1]
            img, rx, ry = render_figure(f, pose, cam.S(1), rig, ss=1.0, outline=ocol, ow_px=ow)
            sil = pygame.mask.from_surface(img, 100).to_surface(setcolor=(*f.player_color, 150), unsetcolor=(0, 0, 0, 0))
            imgs = getattr(f, "_trail_imgs", [])
            imgs.append((sil, f.x, f.y, rx, ry, f.anim_t))
            f._trail_imgs = imgs[-5:]
        if debug:
            hb = f.hurtbox()
            p1, p2 = cam.P(hb.left, hb.top), cam.P(hb.right, hb.bottom)
            pygame.draw.rect(surf, (80, 200, 255), (p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]), 1)
            at = f.hitbox()
            if at:
                p1, p2 = cam.P(at.left, at.top), cam.P(at.right, at.bottom)
                pygame.draw.rect(surf, (255, 60, 60), (p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]), 2)
            return drawn.inflate(int(cam.S(40)), int(cam.S(40)))
        return drawn
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
        self.marks = {}
        self.exhausted = False
        self.squash = 0.0
        self.rim = 0.0
        self.player_color = (255, 170, 80)


_PORTRAITS: Dict[tuple, pygame.Surface] = {}


def draw_figure_preview(surf, fd, feet_x, feet_y, height_px, facing=1, t=0.0, rig=None, ss=2, state=None,
                        data_surf=None, pieces=None):
    """Figura de cuerpo entero para menús (selección). Con `data_surf` se dibuja como en el combate: albedo más
    mapa de altura y piezas, para que la GPU la ilumine por píxel y le pinte los tejidos."""
    rig = rig or LIGHT_RIGS[1]
    d = PoseDummy(fd, facing, t, state)
    pose = fighter_pose(d)
    U = height_px / (190.0 * fd["h"])
    if data_surf is None:
        img, rx, ry = render_figure(d, pose, U, rig, ss=ss)
        surf.blit(img, (int(feet_x - rx), int(feet_y - ry)))
        return
    mine: list = []
    img, data, rx, ry = render_figure(d, pose, U, rig, ss=ss, ow_px=2.0 * RS, maps=True,
                                      id_base=len(pieces) + 1, pieces=mine)
    ox, oy = int(feet_x - rx), int(feet_y - ry)
    surf.blit(img, (ox, oy))
    data_surf.blit(data, (ox, oy))
    pieces.extend((ax + ox, ay + oy) + tuple(r) for ax, ay, *r in mine)


PORTRAIT_BAKER = None      # Game lo apunta a gpu.bake_figure cuando hay renderer GPU


def draw_portrait(surf, fd: dict, rect: pygame.Rect, rig: dict, selected=False, locked=False):
    """Retrato de busto: la misma figura del combate, encuadrada en cabeza y hombros. Se cachea.
    En una interfaz a escala se compone a la resolución nativa del recuadro, no reescalado."""
    frame = rect
    rect = ui_native(surf, rect)[1]
    key = (fd["id"], rect.w, rect.h, locked)
    img = _PORTRAITS.get(key)
    if img is None:
        pal = [hex2rgb(c) for c in fd["pal"]]
        img = pygame.Surface(rect.size)
        top_c = shade(mixc(pal[0], (40, 30, 24), 0.55), 0.5) if not locked else (22, 22, 24)
        for i in range(rect.h):
            img.fill(mixc(top_c, (10, 8, 8), i / rect.h), (0, i, rect.w, 1))
        glow = pygame.Surface(rect.size, pygame.SRCALPHA)
        for i in range(10):                                  # halo suave detrás de la cabeza (no un disco)
            pygame.draw.circle(glow, (255, 220, 170, 7), (int(rect.w * 0.34), int(rect.h * 0.32)), int(rect.w * (0.75 - i * 0.065)))
        img.blit(glow, (0, 0))
        d = PoseDummy(fd, 1, 0.0)
        pose = fighter_pose(d)
        U = rect.h / 62.0
        flat = (14, 12, 12) if locked else None
        if PORTRAIT_BAKER is not None and not locked:
            # con GPU: la misma luz por píxel del combate (tejidos, piel, contraluz) y un farolillo de retrato
            # arriba y delante que pone el brillo en los ojos
            from .gpu import figure_light
            pcs: list = []
            alb, data, rx, ry = render_figure(d, pose, U, LIGHT_RIGS[1], ss=2, ow_px=max(1.5, U * 0.35), maps=True, pieces=pcs)
            hx_, hy_ = pose["head"]
            lan = (rx + (hx_ + 60) * d.scale * U, ry + (hy_ - 40) * d.scale * U, 0.5)
            fig_img = PORTRAIT_BAKER(alb, data, [(ax, ay) + tuple(v) for ax, ay, *v in pcs],
                                     figure_light(LIGHT_RIGS[1], GPU_RIGS[1]), (rx, ry), lantern=lan)
        else:
            fig_img, rx, ry = render_figure(d, pose, U, LIGHT_RIGS[1], ss=2, flat=flat)
        hx_, hy_ = pose["head"]
        head_x = rx + hx_ * d.scale * U
        head_y = ry + hy_ * d.scale * U
        img.blit(fig_img, (int(rect.w * 0.46 - head_x), int(rect.h * 0.4 - head_y)))
        _PORTRAITS[key] = img
    getattr(surf, "blit_native", surf.blit)(img, rect.topleft)
    ui_draw.rect(surf, (255, 220, 160) if selected else (60, 50, 44), frame, 3 if selected else 1)
