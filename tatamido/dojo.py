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
                r[1] += r[2]
                r[0] += 1.5
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
    def draw(self, surf, cam: Camera, fonts: Fonts, lut_bw=False):
        rig = self.rig
        zoom = cam.zoom
        bgz = 0.75 + zoom * 0.25            # parallax de zoom del fondo
        par = -(cam.x) * 0.35 * zoom         # parallax horizontal
        # --- cielo (visible a través de los shōji) ---
        wall_top = int(FLOOR_Y - 420 * bgz + cam.yoff + (1 - zoom) * 90)
        floor_back = int(FLOOR_Y - 190 * bgz + cam.yoff + (1 - zoom) * 90)
        for i in range(0, floor_back, 4):
            t = i / max(1, floor_back)
            pygame.draw.rect(surf, mixc(rig["sky_top"], rig["sky_bot"], t), (0, i, W, 4))
        # --- pared de madera ---
        wall_c = rig["wall"]
        pygame.draw.rect(surf, wall_c, (0, wall_top, W, floor_back - wall_top))
        # vigas horizontales
        for yy in (wall_top + 6, floor_back - 10):
            pygame.draw.rect(surf, shade(wall_c, 0.7), (0, yy, W, 7))
        # --- shōji (pared norte) ---
        panel_w = 110 * bgz
        n_panels = 12
        x0 = W / 2 + par - panel_w * n_panels / 2
        shoji_c = rig["shoji"]
        frame_c = shade(wall_c, 0.55)
        glow_c = mixc(shoji_c, rig["sky_bot"], 0.35)
        for i in range(n_panels):
            px = x0 + i * panel_w
            if i in (5, 6):
                continue  # hueco del tokonoma
            rect = pygame.Rect(int(px), wall_top + 18, int(panel_w) - 6, floor_back - wall_top - 40)
            pygame.draw.rect(surf, glow_c, rect)
            # sombra del pino exterior que se mece con el viento
            if rig["lanterns"] < 0.6:
                sway = self.wind * 14 * bgz
                branch = pygame.Surface(rect.size, pygame.SRCALPHA)
                for b in range(3):
                    by = rect.h * (0.15 + 0.3 * b)
                    pts = [(0 + sway + b * 20, by), (rect.w * 0.4 + sway, by - 14 + b * 6), (rect.w * 0.95 + sway * 1.3, by + 10 - b * 8)]
                    pygame.draw.lines(branch, (60, 40, 30, 70), False, pts, 5)
                    for k in range(6):
                        qx, qy = pts[1][0] + k * 12 - 30, pts[1][1] + math.sin(k) * 10
                        pygame.draw.circle(branch, (50, 40, 30, 55), (int(qx), int(qy)), 9)
                surf.blit(branch, rect.topleft)
            # celosía
            for k in range(1, 4):
                pygame.draw.line(surf, frame_c, (rect.x + rect.w * k / 4, rect.y), (rect.x + rect.w * k / 4, rect.bottom), 2)
            for k in range(1, 6):
                pygame.draw.line(surf, frame_c, (rect.x, rect.y + rect.h * k / 6), (rect.right, rect.y + rect.h * k / 6), 2)
            pygame.draw.rect(surf, frame_c, rect, 4)
        # --- tokonoma: pergamino 道 y rama de arce ---
        tx = int(x0 + 5 * panel_w)
        tw = int(panel_w * 2 - 6)
        trect = pygame.Rect(tx, wall_top + 18, tw, floor_back - wall_top - 40)
        pygame.draw.rect(surf, shade(wall_c, 0.45), trect)
        pygame.draw.rect(surf, shade(wall_c, 0.9), trect, 5)
        scroll = pygame.Rect(trect.centerx - int(26 * bgz), trect.y + 14, int(52 * bgz), trect.h - 60)
        pygame.draw.rect(surf, mixc((238, 228, 205), rig["ambient"], 0.3), scroll)
        pygame.draw.rect(surf, (90, 70, 50), (scroll.x - 4, scroll.y - 4, scroll.w + 8, 6))
        pygame.draw.rect(surf, (90, 70, 50), (scroll.x - 4, scroll.bottom - 2, scroll.w + 8, 6))
        draw_kanji_do(surf, scroll.centerx, scroll.centery, int(44 * bgz), (24, 18, 20), fonts)
        # rama de arce rojo
        bx, by = trect.x + int(22 * bgz), trect.bottom - int(40 * bgz)
        pygame.draw.lines(surf, (70, 45, 30), False, [(bx, by), (bx + 18 * bgz, by - 30 * bgz), (bx + 44 * bgz, by - 48 * bgz)], 3)
        leaf_c = mixc((178, 34, 34), rig["ambient"], 0.3)
        for lx, ly in ((bx + 12, by - 24), (bx + 28, by - 42), (bx + 44 * bgz, by - 52 * bgz)):
            if self.leaf is not None and (lx, ly) == (bx + 44 * bgz, by - 52 * bgz):
                continue
            pygame.draw.polygon(surf, leaf_c, [(lx, ly - 8 * bgz), (lx + 7 * bgz, ly), (lx, ly + 6 * bgz), (lx - 7 * bgz, ly)])
        if self.leaf is not None:
            lx, ly = cam.P(self.leaf[0], self.leaf[1])
            pts = [rot(0, -8, self.leaf[2]), rot(7, 0, self.leaf[2]), rot(0, 6, self.leaf[2]), rot(-7, 0, self.leaf[2])]
            pygame.draw.polygon(surf, leaf_c, [(lx + px * zoom, ly + py * zoom) for px, py in pts])
        # --- farolillos ---
        if rig["lanterns"] > 0.05:
            for i, lx in enumerate((x0 + panel_w * 1.5, x0 + panel_w * 4.5, x0 + panel_w * 7.5, x0 + panel_w * 10.5)):
                flick = 0.85 + 0.15 * math.sin(self.t * 9 + i * 1.7) * math.sin(self.t * 3.3 + i)
                ly = wall_top + 40
                lw, lh = int(26 * bgz), int(40 * bgz)
                lan = pygame.Surface((lw * 6, lh * 6))
                k = rig["lanterns"] * flick
                for r in range(3, 0, -1):
                    g = int(16 * k * (4 - r) / 3)
                    pygame.draw.circle(lan, (g * 2, int(g * 1.2), g // 2), (lw * 3, lh * 3), int(lw * (1.0 + r * 0.8)))
                surf.blit(lan, (lx - lw * 3, ly - lh * 3 + lh // 2), special_flags=pygame.BLEND_ADD)
                pygame.draw.line(surf, (40, 30, 24), (lx, wall_top), (lx, ly), 2)
                pygame.draw.ellipse(surf, mixc((250, 190, 110), (255, 230, 180), flick - 0.8), (lx - lw // 2, ly, lw, lh))
                for k in range(1, 4):
                    pygame.draw.line(surf, (150, 90, 50), (lx - lw // 2, ly + lh * k // 4), (lx + lw // 2, ly + lh * k // 4), 1)
        # --- suelo: tatami en perspectiva ---
        self.draw_floor(surf, cam, floor_back)
        # --- público en seiza (detrás, delante de la pared) ---
        self.draw_audience(surf, cam, floor_back)

    def floor_pt(self, cam, wx, d):
        """Punto del suelo: wx en cm, d profundidad 0 (plano de combate) .. 1 (pared)."""
        k = 1 - 0.42 * d
        sx = W / 2 + (wx - cam.x) * cam.zoom * 0.95 * k + cam.shake_x
        sy = FLOOR_Y + cam.yoff + cam.shake_y + (1 - cam.zoom) * 90 - d * 190 * (0.75 + cam.zoom * 0.25) + 70 * cam.zoom * (1 - d) * 0.0
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

    def draw_audience(self, surf, cam, floor_back):
        rig = self.rig
        for a in self.audience:
            px, py = self.floor_pt(cam, a["x"], 0.82)
            h = 52 * a["h"] * (0.75 + cam.zoom * 0.25)
            lean = a["lean"] * 6
            c = mixc(a["c"], rig["ambient"], 0.3)
            # cuerpo sentado
            pygame.draw.polygon(surf, c, [(px - h * 0.45, py), (px + h * 0.45, py), (px + h * 0.3 + lean, py - h * 0.7), (px - h * 0.3 + lean, py - h * 0.7)])
            pygame.draw.circle(surf, shade(c, 1.15), (int(px + lean * 1.3), int(py - h * 0.95)), int(h * 0.19))
            # rim light del shōji en la cabeza
            pygame.draw.arc(surf, mixc(rig["key"], c, 0.5), (px + lean * 1.3 - h * 0.19, py - h * 0.95 - h * 0.19, h * 0.38, h * 0.38), math.pi * 0.4, math.pi * 1.1, 2)
            if a["arms"] > 0.1:
                ang = math.sin(self.t * 14 + a["bob"]) * 0.3
                pygame.draw.line(surf, c, (px, py - h * 0.5), (px + math.cos(ang) * h * 0.3 * a["arms"], py - h * 0.9 - h * 0.2 * a["arms"]), 3)
                pygame.draw.line(surf, c, (px, py - h * 0.5), (px - math.cos(ang) * h * 0.3 * a["arms"], py - h * 0.9 - h * 0.2 * a["arms"]), 3)

    def draw_beams(self, surf, cam, layer, intensity_override=None):
        """Haces volumétricos de los shōji y polvo en suspensión (capa aditiva)."""
        rig = self.rig
        a = rig["beam_a"] if intensity_override is None else intensity_override
        if not self.cfg.get("beams", True):
            a = 0
        bgz = 0.75 + cam.zoom * 0.25
        wall_top = int(FLOOR_Y - 420 * bgz + cam.yoff + (1 - cam.zoom) * 90)
        if a > 1:
            for i in range(4):
                x = W * 0.12 + i * W * 0.22 - cam.x * 0.3 * cam.zoom
                sway = self.wind * 10
                poly = [(x - 20 + sway, wall_top), (x + 70 + sway, wall_top), (x + 420, FLOOR_Y + 60), (x + 160, FLOOR_Y + 60)]
                pygame.draw.polygon(layer, (*rig["beam"], int(a)), poly)
                pygame.draw.polygon(layer, (*rig["beam"], int(a * 0.5)), [(x + sway, wall_top), (x + 50 + sway, wall_top), (x + 330, FLOOR_Y + 60), (x + 250, FLOOR_Y + 60)])
        if self.single_beam > 0.05:
            # un único haz cenital (Kokyū nage / Kiai)
            cx = W / 2
            pygame.draw.polygon(layer, (255, 230, 190, int(70 * self.single_beam)), [(cx - 60, -10), (cx + 60, -10), (cx + 260, FLOOR_Y + 40), (cx - 260, FLOOR_Y + 40)])
        # polvo
        dust_a = int(min(160, 90 + a * 2) * (0.5 + 0.5 * (1 if a > 1 else rig["lanterns"])))
        for d in self.dust:
            sx, sy = cam.P(d[0], d[1])
            if 0 <= sx < W and 0 <= sy < H:
                r = 1 if d[2] < 0.6 else 2
                dc = rig["beam"] if a > 1 else (255, 200, 150)
                pygame.draw.circle(layer, (dc[0], dc[1], dc[2], int(dust_a * d[2])), (int(sx), int(sy)), r)

    def draw_rain(self, surf):
        if not self.rig["rain"]:
            return
        for r in self.rain:
            pygame.draw.line(surf, (170, 180, 200), (r[0], r[1]), (r[0] - 2, r[1] - r[2] * 1.6), 1)

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
