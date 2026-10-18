import math
import random
from typing import List, Tuple

import pygame

from .camera import Camera
from .config import *
from .fx import Fonts, draw_kanji_do

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

# ─────────────────────────────────────────────────────────────────────────────
#  EL DOJO: Kōyō-kan
# ─────────────────────────────────────────────────────────────────────────────
class Dojo:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rig = dict(LIGHT_RIGS[1])
        self.target_rig = dict(LIGHT_RIGS[1])
        self.gpu_rig = dict(GPU_RIGS[1])
        self.target_gpu = dict(GPU_RIGS[1])
        self.lantern_pos = []      # (sx, sy, intensidad) del último fotograma, para la GPU
        self.t = 0.0
        self.wind = 0.0
        self.dust = [[random.uniform(-700, 700), random.uniform(-420, -10), random.uniform(0.2, 1.0)] for _ in range(cfg.get("dust", 100))]
        self.rain = [[random.uniform(0, W), random.uniform(0, H), random.uniform(8, 14)] for _ in range(220)]
        self.decals: List[Tuple[float, float, float]] = []
        self.audience = [dict(x=-560 + i * 86 + random.uniform(-6, 6), lean=0.0, bob=random.uniform(0, 6.28), arms=0.0, h=random.uniform(0.9, 1.1), c=random.choice([(28, 24, 30), (40, 30, 26), (24, 28, 36), (46, 40, 34)])) for i in range(14)]
        self.leaf = None   # hoja de arce que cae en el asalto final
        self.leaf_timer = 0
        self.wall_tex = None
        self.react_t = 0.0
        self.applaud = 0.0
        self.single_beam = 0.0   # Kokyū nage / Kiai: la luz se reduce a un haz
        self.vign_cache = {}
        self.grain = []
        if cfg.get("grain"):
            for _ in range(4):
                g = pygame.Surface((W // 2, H // 2))
                arr = None
                if np is not None:
                    noise = np.random.randint(0, 16, (W // 2, H // 2, 3), dtype=np.uint8)
                    pygame.surfarray.blit_array(g, noise)
                else:
                    g.fill((6, 6, 6))
                self.grain.append(pygame.transform.scale(g, (W, H)))

    def set_round(self, n: int):
        self.target_rig = dict(LIGHT_RIGS[min(n, 4)])
        self.target_gpu = dict(GPU_RIGS[min(n, 4)])
        if n >= 3:
            self.leaf = None
            self.leaf_timer = 60 * 40  # cae a mitad del asalto final

    def update(self, dt, fight_intensity=0.0):
        self.t += dt
        for k in ("sky_top", "sky_bot", "key", "ambient", "tint", "beam", "shoji", "floor", "wall"):
            self.rig[k] = mixc(self.rig[k], self.target_rig[k], 0.03)
        for k in ("beam_a", "lanterns", "vignette"):
            self.rig[k] = lerp(self.rig[k], self.target_rig[k], 0.03)
        self.rig["rain"] = self.target_rig["rain"]
        self.gpu_rig = lerp_tree(self.gpu_rig, self.target_gpu, 0.03)
        self.plate_mix = min(1.0, getattr(self, "plate_mix", 1.0) + (1.0 - getattr(self, "plate_mix", 1.0)) * 0.03 + 1e-4)
        self.rig["name"] = self.target_rig["name"]
        self.wind = math.sin(self.t * 0.7) * 0.6 + math.sin(self.t * 1.9) * 0.3
        for d in self.dust:
            d[0] += (0.15 + self.wind * 0.3 + fight_intensity * random.uniform(-1, 1)) * d[2]
            d[1] += math.sin(self.t * 0.8 + d[0] * 0.01) * 0.15 * d[2]
            if d[0] > 720:
                d[0] = -720
            if d[0] < -720:
                d[0] = 720
            d[1] = clamp(d[1], -430, -5)
        if self.rig["rain"]:
            for r in self.rain:
                r[1] += r[2] * RS
                r[0] += 1.5 * RS
                if r[1] > H:
                    r[1] = -10
                    r[0] = random.uniform(-20, W)
        for a in self.audience:
            a["lean"] = lerp(a["lean"], 0.0, 0.05)
            a["arms"] = lerp(a["arms"], 1.0 if self.applaud > 0 else 0.0, 0.1)
        self.applaud = max(0.0, self.applaud - dt)
        self.single_beam = lerp(self.single_beam, 0.0, 0.04)
        if self.leaf_timer > 0:
            self.leaf_timer -= 1
            if self.leaf_timer == 0:
                self.leaf = [430.0, -250.0, 0.0]
        if self.leaf is not None:
            self.leaf[0] += 0.5 + self.wind * 0.6
            self.leaf[1] += 0.9
            self.leaf[2] += 0.08
            if self.leaf[1] >= -2:
                self.leaf[1] = -2

    def react(self, strength):
        for a in self.audience:
            a["lean"] += strength * random.uniform(0.6, 1.2)

    def add_decal(self, x):
        self.decals.append((x, random.uniform(-10, 10), random.uniform(0.8, 1.3)))
        if len(self.decals) > 40:
            self.decals.pop(0)

    # ----- dibujo -----
    PLATE_U = (-420.0, 1740.0)    # extensión de la lámina de pared en unidades de pared (≈ cm; un shōji = 110)
    PLATE_V = (-300.0, 236.0)     # del techo (negativo) al zócalo; 0 es el borde superior de la pared
    MAT = {"paper": (255, 0, 0), "wood": (0, 255, 0), "plaster": (0, 0, 255), "none": (0, 0, 0)}

    def wall_geom(self, cam):
        """Transformación pared → pantalla: x = x0 + u·bgz, y = wall_top + v·bgz (parallax incluido)."""
        zoom = cam.zoom
        bgz = (0.75 + zoom * 0.25) * RS
        par = -(cam.x) * 0.35 * zoom * RS
        base = FLOOR_Y + cam.yoff + cam_base_off(zoom)
        return W / 2 + par - 110 * bgz * 6, base - 420 * bgz, base - 190 * bgz, bgz

    def _draw_wall(self, surf, x0, wall_top, floor_back, bgz, fonts, sway=0.0, mat=None, rig=None):
        """Techo con vigas, pared de madera, doce shōji, tokonoma y farolillos, en cualquier escala.
        Con `mat` pinta a la vez la máscara de materiales para la GPU (R papel, G madera, B enlucido)."""
        rig = rig or self.rig
        k = bgz
        Wd = surf.get_width()
        M = self.MAT

        def R(rect, col, m, width=0):
            pygame.draw.rect(surf, col, rect, width)
            if mat is not None:
                pygame.draw.rect(mat, M[m], rect, width)

        def Ln(col, m, a, b, w):
            pygame.draw.line(surf, col, a, b, w)
            if mat is not None:
                pygame.draw.line(mat, M[m], a, b, w)

        wall_c = rig["wall"]
        frame_c = shade(wall_c, 0.55)
        glow_c = mixc(rig["shoji"], rig["sky_bot"], 0.35)
        lw = lambda v: max(1, int(round(v * k)))
        # --- techo: enlucido alto (kokabe) entre pilares, viga nageshi y tablas del techo en fuga ---
        top = int(wall_top - 300 * k)
        ceil_c = shade(wall_c, 0.36)
        R((0, min(0, top), Wd, int(wall_top - min(0, top)) + 1), ceil_c, "wood")
        cy0, cy1 = wall_top - 300 * k, wall_top - 122 * k
        cx = x0 + 660 * k
        for i in range(-28, 29):                                  # tablas: convergen hacia la pared
            xb = cx + i * 55 * k
            xt = cx + i * 55 * k * 1.9
            Ln(shade(wall_c, 0.28), "wood", (xt, cy0), (xb, cy1), lw(1.6))
        for j in range(7):                                        # listones (sao-buchi), más juntos al fondo
            t = (j / 6) ** 1.7
            y = cy1 - (cy1 - cy0) * t
            R((0, int(y - 2.5 * k), Wd, lw(4 + 3 * t)), shade(wall_c, 0.5 - 0.1 * t), "wood")
        R((0, int(cy1), Wd, int(wall_top - 14 * k - cy1)), mixc(shade(wall_c, 1.35), (196, 176, 146), 0.55), "plaster")
        for i in range(-4, 16):                                   # pilares (hashira) cada dos shōji
            R((int(x0 + i * 220 * k - 6 * k), int(cy1), lw(12), int(wall_top - cy1)), shade(wall_c, 0.62), "wood")
        R((0, int(wall_top - 16 * k), Wd, lw(16)), shade(wall_c, 0.66), "wood")                    # nageshi
        R((0, int(wall_top - 2 * k), Wd, lw(3)), shade(wall_c, 0.35), "wood")
        # --- pared de madera ---
        R((0, int(wall_top), Wd, int(floor_back - wall_top) + 1), wall_c, "wood")
        for yy in (wall_top + 5 * k, floor_back - 9 * k):
            R((0, int(yy), Wd, lw(6.5)), shade(wall_c, 0.7), "wood")
        # --- shōji (pared norte) ---
        panel_w = 110 * k
        for i in range(12):
            if i in (5, 6):
                continue  # hueco del tokonoma
            px = x0 + i * panel_w
            rect = pygame.Rect(int(px), int(wall_top + 16 * k), int(panel_w - 5.5 * k), int(floor_back - wall_top - 36 * k))
            R(rect, glow_c, "paper")
            # sombra del pino exterior que se mece con el viento
            if rig["lanterns"] < 0.6:
                # un solo pino detrás de la pared: su sombra cruza varios shōji (se recorta en cada uno)
                bkey = (int(1320 * k), rect.h)
                if getattr(self, "_branch_key", None) != bkey:
                    self._branch, self._branch_key = self._pine_shadow(int(1320 * k), rect.h, k), bkey
                ox = int(x0)
                surf.blit(self._branch, (rect.x, rect.y), area=pygame.Rect(rect.x - ox - int(sway), 0, rect.w, rect.h))
            # celosía (kumiko)
            for q in range(1, 4):
                Ln(frame_c, "wood", (rect.x + rect.w * q / 4, rect.y), (rect.x + rect.w * q / 4, rect.bottom), lw(1.8))
            for q in range(1, 6):
                Ln(frame_c, "wood", (rect.x, rect.y + rect.h * q / 6), (rect.right, rect.y + rect.h * q / 6), lw(1.8))
            R(rect, frame_c, "wood", lw(3.6))
        # --- tokonoma: pergamino 道 y rama de arce ---
        tx = int(x0 + 5 * panel_w)
        trect = pygame.Rect(tx, int(wall_top + 16 * k), int(panel_w * 2 - 5.5 * k), int(floor_back - wall_top - 36 * k))
        R(trect, shade(wall_c, 0.45), "wood")
        R(trect, shade(wall_c, 0.9), "wood", lw(4.5))
        scroll = pygame.Rect(trect.centerx - int(26 * k), trect.y + int(13 * k), int(52 * k), trect.h - int(55 * k))
        R(scroll, mixc((238, 228, 205), rig["ambient"], 0.3), "paper")
        R((scroll.x - 4 * k, scroll.y - 4 * k, scroll.w + 8 * k, 5.5 * k), (90, 70, 50), "wood")
        R((scroll.x - 4 * k, scroll.bottom - 2 * k, scroll.w + 8 * k, 5.5 * k), (90, 70, 50), "wood")
        draw_kanji_do(surf, scroll.centerx, scroll.centery, int(44 * k), (24, 18, 20), fonts)
        bx, by = trect.x + int(22 * k), trect.bottom - int(40 * k)
        pygame.draw.lines(surf, (70, 45, 30), False, [(bx, by), (bx + 18 * k, by - 30 * k), (bx + 44 * k, by - 48 * k)], lw(2.7))
        leaf_c = mixc((178, 34, 34), rig["ambient"], 0.3)
        for i, (lx, ly) in enumerate(((bx + 11 * k, by - 22 * k), (bx + 26 * k, by - 38 * k), (bx + 44 * k, by - 52 * k))):
            if i == 2 and self.leaf is not None and mat is None:
                continue
            pygame.draw.polygon(surf, leaf_c, [(lx, ly - 8 * k), (lx + 7 * k, ly), (lx, ly + 6 * k), (lx - 7 * k, ly)])
        # --- farolillos (cuerpo de papel; la luz la pone cada renderer) ---
        if rig["lanterns"] > 0.05:
            for lx in self.lantern_xs(x0, k):
                ly = wall_top + 36 * k
                lwid, lh = int(26 * k), int(40 * k)
                Ln((40, 30, 24), "none", (lx, wall_top - 16 * k), (lx, ly), lw(1.8))
                body = (lx - lwid // 2, ly, lwid, lh)
                pygame.draw.ellipse(surf, (250, 205, 135), body)
                if mat is not None:
                    pygame.draw.ellipse(mat, M["paper"], body)
                for q in range(1, 4):
                    pygame.draw.line(surf, (150, 90, 50), (lx - lwid // 2, ly + lh * q // 4), (lx + lwid // 2, ly + lh * q // 4), lw(0.9))

    @staticmethod
    def _pine_shadow(w, h, k):
        """Sombra de un pino de jardín (niwaki) sobre toda la fila de shōji: un tronco que cruza en diagonal,
        ramas que se afinan y nubes de agujas en almohadillas. Se pinta a un sexto y se amplía: el
        desenfoque de una sombra lejana sobre papel."""
        rng = random.Random(11)
        sc = 1 / 6
        sw, sh = max(8, int(w * sc)), max(8, int(h * sc))
        small = pygame.Surface((sw, sh), pygame.SRCALPHA)
        u = sw / 1320.0                          # px (pequeños) por unidad de pared
        col = (48, 34, 26)
        trunk = [(1450 - 120 * t - 40 * math.sin(t * 3), 260 - 300 * t) for t in [i / 10 for i in range(11)]]
        pads = []
        for i in range(10):
            pygame.draw.line(small, (*col, 110), (trunk[i][0] * u, trunk[i][1] * u), (trunk[i + 1][0] * u, trunk[i + 1][1] * u),
                             max(1, int((26 - 16 * i / 10) * u)))
        for bi, (t, side) in enumerate(((0.25, -1), (0.42, -1), (0.55, 1), (0.68, -1), (0.8, -1), (0.9, 1))):
            x0, y0 = trunk[int(t * 10)]
            ln = 260 + rng.uniform(0, 260)
            pts = []
            for j in range(7):
                q = j / 6
                pts.append((x0 + side * ln * q, y0 - 40 * q + math.sin(q * 3.1 + bi) * 18))
            for j in range(6):
                pygame.draw.line(small, (*col, 100), (pts[j][0] * u, pts[j][1] * u), (pts[j + 1][0] * u, pts[j + 1][1] * u),
                                 max(1, int((9 - 6 * j / 6) * u)))
            for j in (2, 4, 6):
                pads.append((pts[j][0], pts[j][1] - 6, 46 + rng.uniform(0, 30), 20 + rng.uniform(0, 10)))
        for cx, cy, rx, ry in pads:              # almohadilla: muchas agujas cortas dentro de una nube
            for _ in range(70):
                a = rng.uniform(0, 2 * math.pi)
                r = rng.random() ** 0.5
                px, py = cx + math.cos(a) * rx * r, cy + math.sin(a) * ry * r
                d = rng.uniform(-0.6, 0.6) + (-math.pi / 2 if py < cy else math.pi / 2)
                ln = rng.uniform(6, 13)
                pygame.draw.line(small, (*col, 46), (px * u, py * u), ((px + math.cos(d) * ln) * u, (py + math.sin(d) * ln * 0.6) * u),
                                 max(1, int(3 * u)))
            pygame.draw.ellipse(small, (*col, 34), ((cx - rx * 0.8) * u, (cy - ry * 0.7) * u, rx * 1.6 * u, ry * 1.4 * u))
        return pygame.transform.smoothscale(small, (w, h))

    @staticmethod
    def lantern_xs(x0, k):
        return [x0 + 110 * k * f for f in (1.5, 4.5, 7.5, 10.5)]

    @staticmethod
    def plate_key(r):
        return (tuple(r["wall"]), tuple(r["shoji"]), tuple(r["sky_bot"]), tuple(r["ambient"]),
                r["lanterns"] > 0.05, r["lanterns"] < 0.6, RS)

    def _bake(self, rig, fonts):
        u0, u1 = self.PLATE_U
        v0, v1 = self.PLATE_V
        # densidad: la del juego a 1440p basta (el shader pone el detalle fino); los carteles en 4K, más
        s = min(1.6 * RS, 2.4 if RS < 2.5 else 3.6)
        size = (int((u1 - u0) * s), int((v1 - v0) * s))
        col = pygame.Surface(size)
        mat = pygame.Surface(size)
        mat.fill((0, 0, 0))
        self._draw_wall(col, -u0 * s, -v0 * s, (230 - v0) * s, s, fonts, mat=mat, rig=rig)
        self._plate_v = getattr(self, "_plate_v", 0) + 1
        return dict(col=col, mat=mat, rect=(u0, v0, u1 - u0, v1 - v0), version=(id(self), self._plate_v),
                    key=self.plate_key(rig))

    def ensure_plate(self, fonts):
        """Lámina de pared a alta resolución para la GPU. Se hornea una sola vez por luz de asalto, con
        la luz de destino; durante el cambio de asalto el shader funde la anterior con la nueva al mismo
        ritmo al que se interpola la luz (sin rehornear cada fotograma)."""
        key = self.plate_key(self.target_rig)
        cur = getattr(self, "plate", None)
        if cur is None or cur["key"] != key:
            new = self._bake(self.target_rig, fonts)
            self.plate_from = cur
            self.plate_mix = 1.0 if cur is None or self.plate_key(self.rig) == key else 0.0
            self.plate = new
        return self.plate

    def draw(self, surf, cam: Camera, fonts: Fonts, lut_bw=False, gpu=False):
        """Con gpu=True la pared va en la lámina horneada (la proyecta el shader) y aquí sólo se dibuja
        lo que se mueve: público y hoja. Tampoco hay suelo ni halos (shader y bloom)."""
        rig = self.rig
        x0, wall_top, floor_back, bgz = self.wall_geom(cam)
        floor_back = int(floor_back)
        self.lantern_pos = []
        self.drawn = []              # lo dibujado sobre `surf` (para subir a la GPU sólo esa zona)
        if rig["lanterns"] > 0.05:
            for i, lx in enumerate(self.lantern_xs(x0, bgz)):
                flick = 0.85 + 0.15 * math.sin(self.t * 9 + i * 1.7) * math.sin(self.t * 3.3 + i)
                ly = wall_top + 36 * bgz
                lw_, lh = int(26 * bgz), int(40 * bgz)
                kk = rig["lanterns"] * flick
                self.lantern_pos.append((lx, ly + lh / 2, kk))
                if not gpu:
                    lan = pygame.Surface((lw_ * 6, lh * 6))
                    for r in range(3, 0, -1):
                        g = int(16 * kk * (4 - r) / 3)
                        pygame.draw.circle(lan, (g * 2, int(g * 1.2), g // 2), (lw_ * 3, lh * 3), int(lw_ * (1.0 + r * 0.8)))
                    surf.blit(lan, (lx - lw_ * 3, ly - lh * 3 + lh // 2), special_flags=pygame.BLEND_ADD)
        if gpu:
            self.ensure_plate(fonts)
        else:
            self._draw_wall(surf, x0, wall_top, floor_back, bgz, fonts, sway=self.wind * 14 * bgz)
        zoom = cam.zoom
        if self.leaf is not None:
            leaf_c = mixc((178, 34, 34), rig["ambient"], 0.3)
            lx, ly = cam.P(self.leaf[0], self.leaf[1])
            pts = [rot(0, -8, self.leaf[2]), rot(7, 0, self.leaf[2]), rot(0, 6, self.leaf[2]), rot(-7, 0, self.leaf[2])]
            self.drawn.append(pygame.draw.polygon(surf, leaf_c, [(lx + px * zoom * RS, ly + py * zoom * RS) for px, py in pts]))
        # --- suelo: tatami en perspectiva ---
        if not gpu:
            self.draw_floor(surf, cam, floor_back)
        # --- público en seiza (detrás, delante de la pared) ---
        self.draw_audience(surf, cam, floor_back)

    def floor_pt(self, cam, wx, d):
        """Punto del suelo: wx en cm, d profundidad 0 (plano de combate) .. 1 (pared)."""
        k = 1 - 0.42 * d
        sx = W / 2 + (wx - cam.x) * cam.zoom * 0.95 * k * RS + cam.shake_x
        sy = FLOOR_Y + cam.yoff + cam.shake_y + cam_base_off(cam.zoom) - d * 190 * (0.75 + cam.zoom * 0.25) * RS
        return sx, sy

    def draw_floor(self, surf, cam, floor_back):
        rig = self.rig
        fc = rig["floor"]
        # zona frontal (desde el plano de combate hasta el borde inferior): tatami que sigue hacia la cámara
        front = [self.floor_pt(cam, -1400, -0.9), self.floor_pt(cam, 1400, -0.9), self.floor_pt(cam, 1400, 1.0), self.floor_pt(cam, -1400, 1.0)]
        pygame.draw.polygon(surf, shade(fc, 0.78), front)
        # zona roja de seguridad
        red = mixc((150, 40, 36), rig["ambient"], 0.35)
        band = [self.floor_pt(cam, -SAFE_HALF, -0.6), self.floor_pt(cam, SAFE_HALF, -0.6), self.floor_pt(cam, SAFE_HALF, 0.95), self.floor_pt(cam, -SAFE_HALF, 0.95)]
        pygame.draw.polygon(surf, red, band)
        # esteras 8x8 (cada 100 cm) con variación de tono y desgaste central
        for i in range(8):
            for j in range(8):
                x1 = -TATAMI_HALF + i * 100
                x2 = x1 + 100
                d1 = -0.5 + j * (1.4 / 8)
                d2 = d1 + 1.4 / 8
                wear = 1 - 0.1 * math.exp(-((i - 3.5) ** 2 + (j - 3.5) ** 2) / 6)
                var = 0.96 + 0.04 * ((i * 7 + j * 3) % 5) / 4
                c = shade(fc, wear * var * (1 - 0.18 * max(0, d1)))
                quad = [self.floor_pt(cam, x1, d1), self.floor_pt(cam, x2, d1), self.floor_pt(cam, x2, d2), self.floor_pt(cam, x1, d2)]
                pygame.draw.polygon(surf, c, quad)
                pygame.draw.polygon(surf, shade(c, 0.82), quad, 1)
                # fibras de igusa (sólo en la parte delantera, donde se ven)
                if d1 < 0.25 and cam.zoom > 0.9 and self.cfg.get("particles", 1) >= 0.8:
                    for k in range(1, 4):
                        a = self.floor_pt(cam, x1 + 25 * k, d1)
                        b = self.floor_pt(cam, x1 + 25 * k, d2)
                        pygame.draw.line(surf, shade(c, 0.93), a, b, 1)
        # línea de jogai
        for xx in (-TATAMI_HALF, TATAMI_HALF):
            pygame.draw.line(surf, shade(fc, 0.5), self.floor_pt(cam, xx, -0.5), self.floor_pt(cam, xx, 0.9), 2)
        # decals: marcas de caída
        for dx, dd, ds in self.decals:
            px, py = self.floor_pt(cam, dx, 0.0)
            w, h = cam.S(90 * ds), cam.S(16 * ds)
            dec = pygame.Surface((int(w) + 2, int(h) + 2), pygame.SRCALPHA)
            pygame.draw.ellipse(dec, (40, 28, 16, 42), dec.get_rect())
            surf.blit(dec, (px - w / 2, py - h / 2 + dd * 0.2))

    # Con la GPU, el público se hornea con la luz de los luchadores (gpu.GPURenderer.bake_figure): Game lo
    # conecta aquí. Sin él, se tiñen sprites con la luz de la tarde.
    baker = None

    def _audience_sprite(self, look, facing, n, lit):
        """Figura en seiza con su apoyo (rx, ry) y altura de referencia. Con `lit`, horneada con la luz de
        destino del asalto: tejidos, rampa pintada, brillos de material, contraluz de los shōji y farolillo."""
        from .poses import pose_seiza
        from .render_fighter import PoseDummy, render_figure
        fd = dict(id=look, h=1.0, w=1.0, pal=["#FFFFFF"] * 4)
        d = PoseDummy(fd, facing, n * 0.7)
        d.sweat = 0.0
        ua = 1.1 * RS
        if not lit:
            img, rx, ry = render_figure(d, pose_seiza(fd), ua, LIGHT_RIGS[1], ss=2, ow_px=2.0 * RS)
            return img, rx, ry, ua * 125
        from .gpu import figure_light
        pieces: list = []
        img, data, rx, ry = render_figure(d, pose_seiza(fd), ua, self.target_rig, ss=2, ow_px=2.0 * RS, maps=True,
                                          pieces=pieces)
        r = self.target_rig
        # el farolillo de la pared les cae desde arriba y por detrás
        lan = (rx - 20 * ua * facing, ry - 230 * ua, 0.3 * r["lanterns"]) if r["lanterns"] > 0.05 else None
        light = figure_light(r, self.target_gpu)
        air = mixc(r["ambient"], r["sky_bot"], 0.5)
        haze = tuple(((v / 255.0) ** 2.2) * 0.55 for v in air) + (0.24,)
        lit_img = self.baker(img, data, [(ax, ay) + tuple(v) for ax, ay, *v in pieces], light, (rx, ry),
                             tex=self.cfg.get("tex", 1.0), lantern=lan, haze=haze)
        return lit_img, rx, ry, ua * 125

    def _audience_shadow(self, w):
        """Sombra de contacto: la mancha oscura y suave que asienta a cada figura en el tatami."""
        key = ("shadow", w)
        img = self._aud_scaled.get(key)
        if img is None:
            h = max(4, w // 5)
            img = pygame.Surface((w, h), pygame.SRCALPHA)
            for i in range(6, 0, -1):
                f = i / 6
                rw, rh = w * (0.45 + 0.55 * f), h * (0.45 + 0.55 * f)
                pygame.draw.ellipse(img, (14, 10, 8, int(34 * (1.15 - f))), ((w - rw) / 2, (h - rh) / 2, rw, rh))
            self._aud_scaled[key] = img
        return img

    def draw_audience(self, surf, cam, floor_back):
        """Alumnos y maestro sentados en seiza, dibujados con el mismo renderer que los luchadores. Con la GPU
        se hornean una vez por luz de asalto y, mientras cambia la luz, la versión anterior se funde con la nueva."""
        from .anatomy import AUDIENCE
        rig = self.rig
        if not hasattr(self, "_aud_base"):
            self._aud_base = {}
            self._aud_scaled = {}
        lit = self.baker is not None and getattr(self, "plate", None) is not None
        if lit:
            key = self.plate_key(self.target_rig)
            cur = getattr(self, "_aud_lit", None)
            if cur is None or cur["key"] != key:
                self._aud_lit_from = cur
                self._aud_v = getattr(self, "_aud_v", 0) + 1
                self._aud_lit = dict(key=key, v=self._aud_v, sprites={})
            sets = [self._aud_lit]
            mix = float(getattr(self, "plate_mix", 1.0))
            if self._aud_lit_from is not None and mix < 0.999:
                sets.insert(0, self._aud_lit_from)
            else:
                self._aud_lit_from = None
        else:
            tint = shade(mixc(rig["key"], rig["ambient"], 0.55), 1.12)
            sets = [dict(v=("tint",) + tuple(v // 8 for v in tint), sprites=self._aud_base, tint=tint)]
            mix = 1.0
        ids = list(AUDIENCE)
        budget = 1
        for n, a in enumerate(self.audience):
            look = ids[(n * 5 + 3) % len(ids)]
            facing = 1 if a["x"] < 0 else -1
            px, py = self.floor_pt(cam, a["x"], 0.82)
            target = 52 * a["h"] * (0.75 + cam.zoom * 0.25) * 1.25 * RS
            lean = a["lean"] * 6 * facing * RS
            bob = -abs(math.sin(self.t * 12 + a["bob"])) * 4 * a["arms"] * RS
            base_key = (look, facing)
            layers = []
            for st in sets:
                spr = st["sprites"].get(base_key)
                if spr is None and (budget > 0 or len(sets) == 1 or (st is sets[-1] and not layers)):
                    # con luz nueva se hornea una figura por fotograma (sin tirón); mientras, se ve la anterior
                    budget -= 1
                    spr = st["sprites"][base_key] = self._audience_sprite(look, facing, n, "tint" not in st)
                if spr is not None:
                    layers.append((st, spr))
            for si, (st, spr) in enumerate(layers):
                img, rx, ry, h0 = spr
                k = max(0.2, round(target / h0 * 12) / 12)
                if si == 0:
                    sh = self._audience_shadow(max(8, int(h0 * k * 0.95)))
                    self.drawn.append(surf.blit(sh, (int(px - sh.get_width() / 2 - facing * h0 * k * 0.05),
                                                     int(py - sh.get_height() * 0.55))))
                sk = (base_key, k, st["v"])
                sc_img = self._aud_scaled.get(sk)
                if sc_img is None:
                    sc_img = pygame.transform.smoothscale(img, (max(1, int(img.get_width() * k)), max(1, int(img.get_height() * k))))
                    if "tint" in st:
                        sc_img.fill((*st["tint"], 255), special_flags=pygame.BLEND_RGBA_MULT)
                    if len(self._aud_scaled) > 400:
                        self._aud_scaled.clear()
                    self._aud_scaled[sk] = sc_img
                if si == 1:
                    # fundido de la luz anterior a la nueva, en ocho escalones precalculados (el alfa de
                    # superficie sobre alfa por píxel es la vía lenta de pygame)
                    step = min(7, int(mix * 8))
                    fk = sk + (step,)
                    faded = self._aud_scaled.get(fk)
                    if faded is None:
                        faded = sc_img.copy()
                        faded.fill((255, 255, 255, int(255 * (step + 0.5) / 8)), special_flags=pygame.BLEND_RGBA_MULT)
                        self._aud_scaled[fk] = faded
                    sc_img = faded
                self.drawn.append(surf.blit(sc_img, (int(px + lean - rx * k), int(py + bob - ry * k))))

    def draw_beams(self, surf, cam, layer, intensity_override=None):
        """Haces volumétricos de los shōji y polvo en suspensión (capa aditiva)."""
        rig = self.rig
        a = rig["beam_a"] if intensity_override is None else intensity_override
        if not self.cfg.get("beams", True):
            a = 0
        bgz = (0.75 + cam.zoom * 0.25) * RS
        wall_top = int(FLOOR_Y - 420 * bgz + cam.yoff + cam_base_off(cam.zoom))
        R_ = RS
        if a > 1:
            for i in range(4):
                x = W * 0.12 + i * W * 0.22 - cam.x * 0.3 * cam.zoom * R_
                sway = self.wind * 10 * R_
                fy = FLOOR_Y + 60 * R_
                poly = [(x - 20 * R_ + sway, wall_top), (x + 70 * R_ + sway, wall_top), (x + 420 * R_, fy), (x + 160 * R_, fy)]
                pygame.draw.polygon(layer, (*rig["beam"], int(a)), poly)
                pygame.draw.polygon(layer, (*rig["beam"], int(a * 0.5)), [(x + sway, wall_top), (x + 50 * R_ + sway, wall_top), (x + 330 * R_, fy), (x + 250 * R_, fy)])
        if self.single_beam > 0.05 and intensity_override is None:
            # un único haz cenital (Kokyū nage / Kiai)
            cx = W / 2
            fy = FLOOR_Y + 40 * R_
            pygame.draw.polygon(layer, (255, 230, 190, int(70 * self.single_beam)), [(cx - 60 * R_, -10), (cx + 60 * R_, -10), (cx + 260 * R_, fy), (cx - 260 * R_, fy)])
        # polvo
        dust_a = int(min(160, 90 + a * 2) * (0.5 + 0.5 * (1 if a > 1 else rig["lanterns"])))
        for d in self.dust:
            sx, sy = cam.P(d[0], d[1])
            if 0 <= sx < W and 0 <= sy < H:
                r = max(1, int((1 if d[2] < 0.6 else 2) * RS))
                dc = rig["beam"] if a > 1 else (255, 200, 150)
                pygame.draw.circle(layer, (dc[0], dc[1], dc[2], int(dust_a * d[2])), (int(sx), int(sy)), r)

    def draw_rain(self, surf):
        if not self.rig["rain"]:
            return
        # trazos largos, finos y translúcidos (desenfoque de movimiento); los más rápidos, más cerca y más claros
        w = max(1, int(RS * 0.75))
        for r in self.rain:
            k = (r[2] - 8) / 6
            a = int(55 + 70 * k)
            ln = r[2] * (3.2 + 1.6 * k) * RS
            pygame.draw.line(surf, (190, 200, 222, a), (r[0], r[1]), (r[0] - ln * 0.12, r[1] - ln), w)

    def vignette(self, strength):
        key = int(strength) // 10
        if key not in self.vign_cache:
            v = pygame.Surface((W, H), pygame.SRCALPHA)
            cx, cy = W / 2, H / 2
            for i in range(14, 0, -1):
                a = int(key * 10 * (1 - i / 14) ** 2)
                rw, rh = W * (0.55 + i * 0.06), H * (0.6 + i * 0.07)
                pygame.draw.ellipse(v, (0, 0, 0, 0), (cx - rw, cy - rh, rw * 2, rh * 2))
            # construcción simple: anillos exteriores oscuros
            v.fill((0, 0, 0, 0))
            steps = 18
            for i in range(steps):
                t = i / steps
                a = int(key * 10 * t ** 1.8)
                rw = W * (0.95 - 0.35 * t)
                rh = H * (1.0 - 0.4 * t)
                ring = pygame.Surface((W, H), pygame.SRCALPHA)
                ring.fill((0, 0, 0, a))
                pygame.draw.ellipse(ring, (0, 0, 0, 0), (cx - rw, cy - rh, rw * 2, rh * 2))
                v.blit(ring, (0, 0))
            self.vign_cache[key] = v
        return self.vign_cache[key]
