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
from .poses import fighter_pose

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


def tube_pts(a, b, ra, rb, bulge=0.0, bt=0.45, side=0.0, grow=0.0, k=1.0, off=(0.0, 0.0), n=8):
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
    for j in range(1, 6):
        th = math.pi * j / 6
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
#  FIGURA: lista de piezas por grupo de profundidad
# ─────────────────────────────────────────────────────────────────────────────
class Figure:
    """Lista de piezas. Cada pieza recuerda su material (para la luz de la GPU) y si es un surco."""
    MATS = {"ink": 0, "skin": 1, "cloth": 2, "satin": 3, "leather": 4, "hair": 5, "eye": 6}

    def __init__(self):
        self.parts: List[tuple] = []
        self.mat = "cloth"
        self.groove = False

    def m(self, mat, groove=False):
        self.mat = mat
        self.groove = groove

    def tube(self, g, a, b, ra, rb, color, bulge=0.0, bt=0.45, side=0.0, outline=True, shade_=True, sheen=1.0, edge=False):
        self.parts.append((g, "tube", (a, b, ra, rb, bulge, bt, side), color, outline, shade_, sheen, edge, self.mat, False))

    def poly(self, g, pts, color, outline=True, shade_=True, sheen=1.0, edge=False):
        if len(pts) >= 3:
            self.parts.append((g, "poly", pts, color, outline, shade_, sheen, edge, self.mat, False))

    def circle(self, g, c, r, color, outline=True, shade_=True, sheen=1.0, edge=False):
        self.parts.append((g, "circle", (c, r), color, outline, shade_, sheen, edge, self.mat, False))

    def line(self, g, pts, color, w, closed=False):
        self.parts.append((g, "line", (pts, w, closed), color, False, False, 0.0, False, self.mat, self.groove))

    def dot(self, g, c, r, color):
        self.parts.append((g, "dot", (c, r), color, False, False, 0.0, False, self.mat, self.groove))


def _render(fig: Figure, surf, T, k, ow, ocol, flat=None, ss=2, fac=1, baked=True):
    """Pinta la figura en `surf`. T: local→lienzo; k: píxeles de lienzo por cm."""
    lx, ly = LIGHT
    groups: Dict[int, list] = {}
    for p in fig.parts:
        groups.setdefault(p[0], []).append(p)
    edge_w = max(1, int(ss * 0.8))
    for g in sorted(groups):
        ps = groups[g]
        # 1) contorno del grupo
        for _, kind, geo, col, outline, _s, _sh, _e, _m, _gr in ps:
            if not outline:
                continue
            if kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                pygame.draw.polygon(surf, ocol, tube_pts(T(a), T(b), ra * k, rb * k, bu * k, bt, side * fac, grow=ow))
            elif kind == "poly":
                pygame.draw.polygon(surf, ocol, offset_poly([T(q) for q in geo], ow))
            elif kind == "circle":
                c, r = geo
                cc = T(c)
                pygame.draw.circle(surf, ocol, (int(cc[0]), int(cc[1])), int(r * k + ow))
        # 2) relleno con sombreado de tres tonos
        for _, kind, geo, col, outline, shd, sheen, edge, mat, groove in ps:
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
                        hi = shade(col, 1.1 + 0.08 * sheen)
                        pygame.draw.polygon(surf, hi, tube_pts(A, B, ra_, rb_, bu_, bt, sd, k=0.28, off=(px * r * 0.5, py * r * 0.5)))
                else:
                    pygame.draw.polygon(surf, col, tube_pts(A, B, ra_, rb_, bu_, bt, sd))
                if edge:
                    pygame.draw.polygon(surf, shade(col, 0.55), tube_pts(A, B, ra_, rb_, bu_, bt, sd), edge_w)
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
                pts, w, closed = geo
                P = [T(q) for q in pts]
                wpx = max(1, int(w * k))
                if len(P) >= 2:
                    pygame.draw.lines(surf, col, closed, P, wpx)
                    if wpx >= 4:
                        for q in (P[0], P[-1]):
                            pygame.draw.circle(surf, col, (int(q[0]), int(q[1])), wpx // 2)
            elif kind == "dot":
                c, r = geo
                cc = T(c)
                pygame.draw.circle(surf, col, (int(cc[0]), int(cc[1])), max(1, int(r * k)))


GROUP_H = {0: 46, 1: 66, 2: 106, 3: 126, 4: 150, 5: 176, 6: 210}


def _render_data(fig: Figure, surf, T, k, ow, fac=1):
    """Mapa de datos para la luz por píxel: R altura, G material, B brillo.

    Cada grupo de profundidad tiene su altura base; cada pieza añade una cúpula interior y los
    surcos (músculos, pliegues, nudillos) la rebajan. La GPU deriva normales de esta altura.
    """
    groups: Dict[int, list] = {}
    for p in fig.parts:
        groups.setdefault(p[0], []).append(p)
    M = Figure.MATS
    for g in sorted(groups):
        ps = groups[g]
        h0 = GROUP_H.get(g, 120)
        ink = (max(0, h0 - 34), 0, 0)
        for _, kind, geo, col, outline, _s, _sh, _e, _m, _gr in ps:
            if not outline:
                continue
            if kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                pygame.draw.polygon(surf, ink, tube_pts(T(a), T(b), ra * k, rb * k, bu * k, bt, side * fac, grow=ow))
            elif kind == "poly":
                pygame.draw.polygon(surf, ink, offset_poly([T(q) for q in geo], ow))
            elif kind == "circle":
                c, r = geo
                cc = T(c)
                pygame.draw.circle(surf, ink, (int(cc[0]), int(cc[1])), int(r * k + ow))
        for _, kind, geo, col, outline, shd, sheen, edge, mat, groove in ps:
            mid = M.get(mat, 2) * 36
            spec = int(clamp(sheen * 55, 0, 255))
            base = (h0, mid, spec)
            dome = (min(255, h0 + 18), mid, spec)
            if kind == "tube":
                a, b, ra, rb, bu, bt, side = geo
                A, B = T(a), T(b)
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
                pts, w, closed = geo
                P = [T(q) for q in pts]
                if len(P) >= 2:
                    val = (max(0, h0 - 22), mid, spec) if groove else (h0 + 4, mid, spec)
                    pygame.draw.lines(surf, val, closed, P, max(1, int(w * k * (1.6 if groove else 1.0))))
            elif kind == "dot":
                c, r = geo
                cc = T(c)
                val = (max(0, h0 - 22), mid, spec) if groove else (h0 + 4, mid, spec)
                pygame.draw.circle(surf, val, (int(cc[0]), int(cc[1])), max(1, int(r * k)))


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
    H, Cc, Hd0 = J["hip"], J["chest"], J["head"]
    # el esqueleto de poses deja 36 cm entre esternón y cabeza; un cuerpo real deja ~24
    Hd = _lerp2(Cc, Hd0, 0.68)

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
    feet = lk.get("feet", "descalzo")
    th0, th1, thb = B["thigh"]
    ca0, ca1, cab = B["calf"]
    for side, g, depth in (("l", 1, 0.84), ("r", 3, 1.0)):
        kn, ft = J["kn_" + side], J["ft_" + side]
        sx, sy = _norm(ft[0] - kn[0], ft[1] - kn[1])
        if sx == 0 and sy == 0:
            sy = 1.0
        ankle = (ft[0] - sx * 6, ft[1] - sy * 6)
        fdx, fdy = sy, -sx                   # el pie, perpendicular a la espinilla
        x_m = kn[0]
        sk = C(skin, x_m, depth)
        if bottom != "hakama":
            fig.m("skin")
            fig.tube(g, H, kn, th0, th1, sk, bulge=thb, bt=0.35, side=-1, sheen=skin_sheen)
            fig.tube(g, kn, ankle, ca0, ca1, sk, bulge=cab, bt=0.3, side=1, sheen=skin_sheen)
        # pie
        def F_(a, b):
            return (ankle[0] + fdx * a + sx * b, ankle[1] + fdy * a + sy * b)
        if feet in ("botas", "botas_altas", "zapatillas"):
            bc = C(lk.get("boots", "#F2F2F2") if feet != "zapatillas" else "#EDEDED", x_m, depth)
            foot = [F_(-5, -3), F_(-5.5, 6.5), F_(17.5, 6.5), F_(18, 3.5), F_(12, 0.5), F_(4, -3.5)]
            fig.m("leather")
            fig.poly(g, foot, bc, sheen=1.5)
            sole = C("#1E1A18" if feet != "zapatillas" else "#222222", x_m, depth)
            fig.line(g, [F_(-5.3, 5.8), F_(17.7, 5.8)], sole, 1.6)
            if feet == "zapatillas":
                fig.line(g, [F_(2, 2.5), F_(9, 0.5)], C("#1A1A1A", x_m, depth), 1.0)
                fig.line(g, [F_(5, 3.5), F_(12, 1.5)], C("#1A1A1A", x_m, depth), 1.0)
            else:
                top = 0.62 if feet == "botas" else 0.32
                fig.tube(g, _lerp2(kn, ankle, top), ankle, ca1 + 1.5 + (1.0 if feet == "botas_altas" else 0), ca1 + 1.6, bc, sheen=1.4)
                fig.line(g, [_lerp2(kn, ankle, top + 0.08), _lerp2(kn, ankle, 0.95)], shade(bc, 1.35), 0.6)
        else:
            foot = [F_(-3.5, -1.5), F_(-4.5, 3.5), F_(-3.5, 6.0), F_(12, 6.0), F_(16.5, 5.2), F_(16.2, 3.0), F_(9, 1.3), F_(3, -1.6)]
            fig.m("skin")
            fig.poly(g, foot, sk, sheen=skin_sheen * 0.5)
            fig.m("skin", True)
            fig.line(g, [F_(13.5, 5.2), F_(13.8, 3.6)], shade(sk, 0.75), 0.35)
        # ropa de las piernas
        if bottom == "gi":
            pc = C(lk.get("pants", cloth), x_m, depth)
            hem = _lerp2(kn, ankle, 0.86)
            fig.m("cloth")
            fig.tube(g, H, kn, th0 + 3.0, th1 + 3.4, pc, sheen=0.5, edge=True)
            fig.tube(g, kn, hem, th1 + 3.2, ca1 + 4.2, pc, sheen=0.5, edge=True)
            fig.m("cloth", True)
            fig.line(g, [_lerp2(kn, hem, 0.1), _lerp2(H, kn, 0.86), _lerp2(kn, hem, 0.25)], shade(pc, 0.78), 0.5)
        elif bottom == "abada":
            pc = C(cloth, x_m, depth)
            fig.m("cloth")
            fig.tube(g, H, kn, th0 + 2.2, th1 + 2.6, pc, sheen=0.5, edge=True)
            fig.tube(g, kn, ankle, th1 + 2.4, ca1 + 1.2, pc, sheen=0.5, edge=True)
        elif bottom == "track":
            pc = C(lk["pants"], x_m, depth)
            hem = _lerp2(kn, ankle, 0.94)
            fig.m("satin")
            fig.tube(g, H, kn, th0 + 2.4, th1 + 2.4, pc, sheen=0.8, edge=True)
            fig.tube(g, kn, hem, th1 + 2.3, ca1 + 2.6, pc, sheen=0.8, edge=True)
            fig.line(g, [H, kn, hem], C("#FFFFFF", x_m, depth), 0.9)
        elif bottom == "shorts_sambo":
            fig.m("cloth")
            fig.tube(g, H, _lerp2(H, kn, 0.5), th0 + 2.0, th0 + 1.2, C(lk["shorts"], x_m, depth), sheen=0.7, edge=True)
        elif bottom == "shorts_thai":
            sc_ = C(cloth, x_m, depth)
            fig.m("satin")
            fig.tube(g, H, _lerp2(H, kn, 0.5), th0 + 1.6, th0 + 2.2, sc_, sheen=3.0, edge=True)
            fig.line(g, [_lerp2(H, kn, 0.1), _lerp2(H, kn, 0.5)], C(lk["accent"], x_m, depth), 0.5)
        elif bottom == "singlet":
            fig.m("satin")
            fig.tube(g, H, _lerp2(H, kn, 0.32), th0 + 0.7, th0 + 0.3, C(cloth, x_m, depth), sheen=1.8, edge=True)
            fig.m("leather")
            fig.circle(g, kn, ca0 + 1.6, C("#1C1C1F", x_m, depth), sheen=1.0)       # rodillera de neopreno
        # extras de las piernas
        if "prajied" in lk["extras"] and False:
            pass

    # ── torso (piel) ─────────────────────────────────────────────────────────
    top = lk["top"]
    tx = (wf(0.5) - wb(0.5)) * 0.5
    sk_t = C(skin, tx)
    fig.m("skin")
    fig.poly(2, torso(front, back), sk_t, sheen=skin_sheen)
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
        fig.poly(4, F + K[::-1], jc, sheen=0.5, edge=True)
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
            fig.m("cloth")
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
            fig.m("cloth")
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
            fig.poly(4, hull, shade(hc, shade_k), sheen=0.4, edge=True)
            # pliegues: líneas de la cintura al bajo de cada pernera
            fig.m("cloth", True)
            hem = sorted(hull, key=lambda q: -((q[0] - H[0]) ** 2 + (q[1] - H[1]) ** 2))[:2]
            if len(hem) == 2:
                for i in range(4):
                    u = (i + 0.5) / 4
                    fig.line(4, [S(0.2, wf(0.2) * (1 - 2 * u) - (wb(0.2) * (2 * u - 1) if u > 0.5 else 0)),
                                 _lerp2(hem[0], hem[1], u)], shade(hc, 1.7), 0.35)
        fig.m("cloth")
        fig.poly(4, band(0.16, 0.24, 4.8, 5.6), hc, sheen=0.6, edge=True)
        fig.poly(4, [S(0.12, -(wb(0.12) + 6)), S(0.3, -(wb(0.3) + 4.5)), S(0.3, -(wb(0.3) + 1)), S(0.12, -(wb(0.12) + 2))],
                 shade(hc, 1.3), sheen=0.6)                      # koshi-ita

    # ── cuello y cabeza (grupo 5) ────────────────────────────────────────────
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
        pts_ = [HP(-0.72, -0.84), HP(-1.18 - sw_h * 0.8, -0.82 - lift * 0.4), HP(-1.42 - sw_h * 1.8, -0.2 - lift),
                HP(-1.42 - sw_h * 2.8, 0.6 - lift * 0.8), HP(-1.3 - sw_h * 3.6, 1.35 - lift * 0.5)]
        radii = [0.2, 0.27, 0.25, 0.17, 0.05]
        for i in range(4):
            fig.tube(5, pts_[i], pts_[i + 1], radii[i] * R, radii[i + 1] * R, hair_c, sheen=1.6)
        fig.line(5, [pts_[1], pts_[2], pts_[3]], shade(hair_c, 1.5), 0.04 * R)
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
    dark = (24, 16, 14)
    # ojo
    if expr in ("dolor",):
        fig.line(5, [HP(0.5, -0.12), HP(0.7, -0.08)], dark, 0.06 * R)
    else:
        eye = [HP(0.48, -0.1), HP(0.6, -0.17), HP(0.72, -0.1), HP(0.6, -0.04)]
        fig.poly(5, eye, (232, 226, 214), outline=False, shade_=False)
        fig.dot(5, HP(0.64, -0.1), 0.06 * R, dark)
        fig.line(5, [HP(0.47, -0.11), HP(0.6, -0.18), HP(0.73, -0.11)], dark, 0.04 * R)
    # ceja
    fig.m("hair")
    bw = 0.05 * R * lk["brow"]
    if expr in ("grito", "esfuerzo") or lk["expression"] == "feroz":
        brow = [HP(0.42, -0.34), HP(0.6, -0.31), HP(0.8, -0.22)]
    elif expr == "dolor":
        brow = [HP(0.42, -0.28), HP(0.62, -0.34), HP(0.8, -0.3)]
    else:
        brow = [HP(0.42, -0.31), HP(0.62, -0.34), HP(0.8, -0.28)]
    fig.line(5, brow, shade(hair_c, 0.9) if hair not in ("mono",) else (200, 200, 196), bw)
    # boca
    fig.m("skin")
    lip = shade(face_c, 0.62)
    if expr in ("grito", "dolor", "jadeo"):
        o = 0.12 if expr == "grito" else 0.07
        mouth = [HP(0.72, 0.46), HP(0.92, 0.44), HP(0.9, 0.5 + o), HP(0.74, 0.52 + o * 0.8)]
        fig.poly(5, mouth, (70, 28, 26), outline=False, shade_=False)
        if expr == "grito":
            fig.line(5, [HP(0.74, 0.47), HP(0.9, 0.45)], (236, 230, 220), 0.03 * R)
    elif expr == "sonrisa":
        fig.line(5, [HP(0.66, 0.44), HP(0.76, 0.5), HP(0.92, 0.48)], lip, 0.045 * R)
    else:
        fig.line(5, [HP(0.7, 0.5), HP(0.92, 0.48)], lip, 0.04 * R)
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
    up0, up1, upb = B["upper"]
    fo0, fo1, fob = B["fore"]
    sleeve = lk.get("sleeve", 0.0) if top in ("gi", "kurtka", "changshan", "keikogi") else 0.0
    open_hand = (f.state == Fighter.THROWING or (f.state == Fighter.ATTACK and f.move is not None and f.move.anim == "grab")
                 or (lk.get("guard_open") and f.state in (Fighter.IDLE, Fighter.WALK, Fighter.INTRO)))
    for side, g, depth in (("l", 0, 0.84), ("r", 6, 1.0)):
        sh, el, hd = J["sh_" + side], J["el_" + side], J["hd_" + side]
        fig.m("skin")
        sk = C(skin, el[0], depth)
        fore_sk = mixc(sk, (205, 80, 70), min(0.4, 0.4 * red)) if not mono else sk
        if sleeve > 0:
            slc = C(cloth, el[0], depth)
            fig.tube(g, el, hd, fo0, fo1, fore_sk, bulge=fob, bt=0.25, sheen=skin_sheen)
            fig.m("cloth")
            fig.circle(g, sh, B["delt"] + 2.0, slc, sheen=0.5)
            fig.tube(g, sh, el, up0 + 2.6, up1 + 2.8, slc, sheen=0.5, edge=True)
            cut = _lerp2(el, hd, sleeve)
            wide = 3.6 if top != "changshan" else 1.6
            fig.tube(g, el, cut, up1 + 2.7, fo1 + wide, slc, sheen=0.5, edge=True)
            fig.m("cloth", True)
            fig.line(g, [_lerp2(sh, el, 0.55), _lerp2(el, cut, 0.3)], shade(slc, 0.78), 0.4)
            if top == "changshan":
                fig.line(g, [_lerp2(el, cut, 0.92), cut], C(lk["accent"], el[0], depth), 0.5)
        else:
            fig.m("skin")
            dc = sk
            fig.circle(g, sh, B["delt"], dc, sheen=skin_sheen)
            fig.tube(g, sh, el, up0, up1, sk, bulge=upb, bt=0.5, side=1, sheen=skin_sheen)
            fig.tube(g, el, hd, fo0, fo1, fore_sk, bulge=fob, bt=0.25, side=-1, sheen=skin_sheen)
            if "definido" in ex or lk["body"] in ("atleta", "pesado"):
                fig.m("skin", True)
                fig.line(g, [_lerp2(sh, el, 0.2), _lerp2(sh, el, 0.62)], shade(sk, 0.82), 0.3)
        if "prajied" in ex:
            fig.m("cloth")
            c1, c2 = lk["band"]
            pa = _lerp2(sh, el, 0.3)
            pb = _lerp2(sh, el, 0.42)
            fig.tube(g, pa, pb, up0 + 0.9, up0 + 0.8, C(c1, el[0], depth), sheen=0.8)
            fig.line(g, [_lerp2(pa, pb, 0.5), (pa[0] - 4 - vxl, pa[1] + 9)], C(c2, el[0], depth), 0.8)
        # mano
        if "guantes" in ex:
            fig.m("leather")
            gc = C("#1A1A1A", hd[0], depth)
            fig.circle(g, hd, B["hand"] * 1.45, gc, sheen=2.4)
            fig.line(g, [_lerp2(el, hd, 0.78), _lerp2(el, hd, 0.86)], C("#3A3A3A", hd[0], depth), 2.2)
        elif open_hand:
            fig.m("skin")
            dx_, dy_ = _norm(hd[0] - el[0], hd[1] - el[1])
            tip = (hd[0] + dx_ * 7, hd[1] + dy_ * 7)
            fig.tube(g, hd, tip, B["hand"] * 0.85, B["hand"] * 0.6, fore_sk, sheen=skin_sheen)
            fig.line(g, [hd, (hd[0] - dy_ * 3 + dx_ * 3, hd[1] + dx_ * 3 + dy_ * 3)], shade(fore_sk, 0.75), 0.5)
        else:
            fig.m("skin")
            fig.circle(g, hd, B["hand"], fore_sk, sheen=skin_sheen)
            fig.m("skin", True)
            fig.line(g, [(hd[0] + B["hand"] * 0.3, hd[1] - B["hand"] * 0.7), (hd[0] + B["hand"] * 0.7, hd[1] + B["hand"] * 0.3)],
                     shade(fore_sk, 0.7), 0.3)
        if "manchas" in ex:
            fig.dot(g, (hd[0] - 1, hd[1] - 1), 0.5, shade(fore_sk, 0.8))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  RENDER CON SUPERMUESTREO
# ─────────────────────────────────────────────────────────────────────────────
def render_figure(f, pose, U, rig, ss=2, outline=OUTLINE, ow_px=2.0, flat=None, maps=False):
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
    _render(fig, canvas, T, k, ow_px * ss, outline, flat=flat, ss=ss, fac=fac, baked=not maps)
    size = (max(1, w // ss), max(1, h // ss)) if ss > 1 else (w, h)
    data = None
    if maps:
        # el mapa de datos va directo a resolución final: la GPU lo desenfoca igualmente
        data = pygame.Surface(size)
        k1 = k / ss
        if fac == 1:
            def T1(p):
                return ((p[0] - minx) * k1, (p[1] - miny) * k1)
        else:
            def T1(p):
                return ((maxx - p[0]) * k1, (p[1] - miny) * k1)
        _render_data(fig, data, T1, k1, ow_px, fac=fac)
    if ss > 1:
        canvas = pygame.transform.smoothscale(canvas, size)
        rx, ry = rx / ss, ry / ss
    if maps:
        return canvas, data, rx, ry
    return canvas, rx, ry


def draw_fighter(surf, f: Fighter, cam: Camera, rig: dict, cfg: dict, debug=False, lut_bw=False, data_surf=None):
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
    rim = 1.0 if active else f.rim
    ocol = mixc(OUTLINE, f.player_color, min(1.0, rim)) if not lut_bw else OUTLINE
    ow = 2.0 + 1.4 * min(1.0, rim)
    ss = cfg.get("ss", 2)
    x0, y0 = cam.P(f.x, f.y)
    if data_surf is not None:
        img, data, rx, ry = render_figure(f, pose, cam.S(1), rig, ss=ss, outline=ocol, ow_px=ow, maps=True)
        # el mapa de datos no tiene alfa: se compone sólo donde la figura es opaca
        mask = pygame.mask.from_surface(img, 127)
        dpos = (int(x0 - rx), int(y0 - ry))
        cut = mask.to_surface(setcolor=(255, 255, 255), unsetcolor=(0, 0, 0))
        data.blit(cut, (0, 0), special_flags=pygame.BLEND_MULT)
        data.set_colorkey((0, 0, 0))
        data_surf.blit(data, dpos)
    else:
        img, rx, ry = render_figure(f, pose, cam.S(1), rig, ss=ss, outline=ocol, ow_px=ow)
    surf.blit(img, (int(x0 - rx), int(y0 - ry)))
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


def draw_trail(layer, f: Fighter, cam: Camera):
    """Estelas (afterimages): siluetas de las poses recientes en el color del jugador, que se desvanecen."""
    imgs = [t for t in getattr(f, "_trail_imgs", []) if f.anim_t - t[5] < 0.26]
    f._trail_imgs = imgs
    for sil, wx, wy, rx, ry, t0 in imgs:
        age = (f.anim_t - t0) / 0.26
        x0, y0 = cam.P(wx, wy)
        sil.set_alpha(int(170 * (1 - age)))
        layer.blit(sil, (int(x0 - rx), int(y0 - ry)))
    if imgs or not f.trail:
        return
    sc = f.scale
    for pose, x, y, fac, life in f.trail:
        a = int(70 * life / 14)
        c = (*f.player_color, a)
        J = {k: cam.P(x + v[0] * fac * sc, y + v[1] * sc) for k, v in pose.items()}
        r = max(1, int(cam.S(7 * sc * f.width)))
        for a_, b_ in (("sh_l", "el_l"), ("el_l", "hd_l"), ("sh_r", "el_r"), ("el_r", "hd_r"), ("hip", "kn_l"), ("kn_l", "ft_l"), ("hip", "kn_r"), ("kn_r", "ft_r"), ("hip", "chest")):
            pygame.draw.line(layer, c, J[a_], J[b_], r * 2)
        pygame.draw.circle(layer, c, (int(J["head"][0]), int(J["head"][1])), int(cam.S(13 * sc)))


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
