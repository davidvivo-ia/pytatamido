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
    def draw(self, surf, cam: Camera, fonts: Fonts, lut_bw=False, gpu=False):
        """Con gpu=True no se dibuja el suelo (lo pinta el shader) ni los halos (los hace el bloom)."""
        rig = self.rig
        self.lantern_pos = []
        zoom = cam.zoom
        bgz = (0.75 + zoom * 0.25) * RS     # parallax de zoom del fondo (en píxeles de render)
        par = -(cam.x) * 0.35 * zoom * RS    # parallax horizontal
        r1 = max(1, int(round(RS)))          # grosor de línea de 1 px a escala
        # --- cielo (visible a través de los shōji) ---
        wall_top = int(FLOOR_Y - 420 * bgz + cam.yoff + cam_base_off(zoom))
        floor_back = int(FLOOR_Y - 190 * bgz + cam.yoff + cam_base_off(zoom))
        skey = (tuple(rig["sky_top"]), tuple(rig["sky_bot"]), floor_back)
        if getattr(self, "_sky_key", None) != skey:
            sky = pygame.Surface((W, max(1, floor_back)))
            for i in range(0, floor_back, 4):
                t = i / max(1, floor_back)
                sky.fill(mixc(rig["sky_top"], rig["sky_bot"], t), (0, i, W, 4))
            self._sky, self._sky_key = sky, skey
        surf.blit(self._sky, (0, 0))
        # --- pared de madera ---
        wall_c = rig["wall"]
        pygame.draw.rect(surf, wall_c, (0, wall_top, W, floor_back - wall_top))
        # vigas horizontales
        for yy in (wall_top + 6 * RS, floor_back - 10 * RS):
            pygame.draw.rect(surf, shade(wall_c, 0.7), (0, yy, W, 7 * RS))
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
            rect = pygame.Rect(int(px), wall_top + int(18 * RS), int(panel_w - 6 * RS), int(floor_back - wall_top - 40 * RS))
            pygame.draw.rect(surf, glow_c, rect)
            # sombra del pino exterior que se mece con el viento
            if rig["lanterns"] < 0.6:
                sway = self.wind * 14 * bgz
                bkey = (rect.size, RS)
                if getattr(self, "_branch_key", None) != bkey:
                    # sombra del pino: se dibuja una vez por tamaño de panel y se desplaza con el viento
                    m = int(20 * RS)
                    branch = pygame.Surface((rect.w + 2 * m, rect.h), pygame.SRCALPHA)
                    for b in range(3):
                        by = rect.h * (0.15 + 0.3 * b)
                        pts = [(m + b * m, by), (m + rect.w * 0.4, by + (-14 + b * 6) * RS), (m + rect.w * 0.95, by + (10 - b * 8) * RS)]
                        pygame.draw.lines(branch, (60, 40, 30, 70), False, pts, int(5 * RS))
                        for k in range(6):
                            qx, qy = pts[1][0] + (k * 12 - 30) * RS, pts[1][1] + math.sin(k) * 10 * RS
                            pygame.draw.circle(branch, (50, 40, 30, 55), (int(qx), int(qy)), int(9 * RS))
                    self._branch, self._branch_key = branch, bkey
                m = int(20 * RS)
                surf.blit(self._branch, (rect.x - m + int(sway), rect.y), area=pygame.Rect(m - int(sway), 0, rect.w, rect.h))
            # celosía
            for k in range(1, 4):
                pygame.draw.line(surf, frame_c, (rect.x + rect.w * k / 4, rect.y), (rect.x + rect.w * k / 4, rect.bottom), 2 * r1)
            for k in range(1, 6):
                pygame.draw.line(surf, frame_c, (rect.x, rect.y + rect.h * k / 6), (rect.right, rect.y + rect.h * k / 6), 2 * r1)
            pygame.draw.rect(surf, frame_c, rect, 4 * r1)
        # --- tokonoma: pergamino 道 y rama de arce ---
        tx = int(x0 + 5 * panel_w)
        tw = int(panel_w * 2 - 6 * RS)
        trect = pygame.Rect(tx, wall_top + int(18 * RS), tw, int(floor_back - wall_top - 40 * RS))
        pygame.draw.rect(surf, shade(wall_c, 0.45), trect)
        pygame.draw.rect(surf, shade(wall_c, 0.9), trect, 5 * r1)
        scroll = pygame.Rect(trect.centerx - int(26 * bgz), trect.y + int(14 * RS), int(52 * bgz), trect.h - int(60 * RS))
        pygame.draw.rect(surf, mixc((238, 228, 205), rig["ambient"], 0.3), scroll)
        pygame.draw.rect(surf, (90, 70, 50), (scroll.x - 4 * RS, scroll.y - 4 * RS, scroll.w + 8 * RS, 6 * RS))
        pygame.draw.rect(surf, (90, 70, 50), (scroll.x - 4 * RS, scroll.bottom - 2 * RS, scroll.w + 8 * RS, 6 * RS))
        draw_kanji_do(surf, scroll.centerx, scroll.centery, int(44 * bgz), (24, 18, 20), fonts)
        # rama de arce rojo
        bx, by = trect.x + int(22 * bgz), trect.bottom - int(40 * bgz)
        pygame.draw.lines(surf, (70, 45, 30), False, [(bx, by), (bx + 18 * bgz, by - 30 * bgz), (bx + 44 * bgz, by - 48 * bgz)], 3 * r1)
        leaf_c = mixc((178, 34, 34), rig["ambient"], 0.3)
        for lx, ly in ((bx + 12 * RS, by - 24 * RS), (bx + 28 * RS, by - 42 * RS), (bx + 44 * bgz, by - 52 * bgz)):
            if self.leaf is not None and (lx, ly) == (bx + 44 * bgz, by - 52 * bgz):
                continue
            pygame.draw.polygon(surf, leaf_c, [(lx, ly - 8 * bgz), (lx + 7 * bgz, ly), (lx, ly + 6 * bgz), (lx - 7 * bgz, ly)])
        if self.leaf is not None:
            lx, ly = cam.P(self.leaf[0], self.leaf[1])
            pts = [rot(0, -8, self.leaf[2]), rot(7, 0, self.leaf[2]), rot(0, 6, self.leaf[2]), rot(-7, 0, self.leaf[2])]
            pygame.draw.polygon(surf, leaf_c, [(lx + px * zoom * RS, ly + py * zoom * RS) for px, py in pts])
        # --- farolillos ---
        if rig["lanterns"] > 0.05:
            for i, lx in enumerate((x0 + panel_w * 1.5, x0 + panel_w * 4.5, x0 + panel_w * 7.5, x0 + panel_w * 10.5)):
                flick = 0.85 + 0.15 * math.sin(self.t * 9 + i * 1.7) * math.sin(self.t * 3.3 + i)
                ly = wall_top + 40 * RS
                lw, lh = int(26 * bgz), int(40 * bgz)
                k = rig["lanterns"] * flick
                self.lantern_pos.append((lx, ly + lh / 2, k))
                if not gpu:
                    lan = pygame.Surface((lw * 6, lh * 6))
                    for r in range(3, 0, -1):
                        g = int(16 * k * (4 - r) / 3)
                        pygame.draw.circle(lan, (g * 2, int(g * 1.2), g // 2), (lw * 3, lh * 3), int(lw * (1.0 + r * 0.8)))
                    surf.blit(lan, (lx - lw * 3, ly - lh * 3 + lh // 2), special_flags=pygame.BLEND_ADD)
                pygame.draw.line(surf, (40, 30, 24), (lx, wall_top), (lx, ly), 2 * r1)
                pygame.draw.ellipse(surf, mixc((250, 190, 110), (255, 230, 180), flick - 0.8), (lx - lw // 2, ly, lw, lh))
                for k in range(1, 4):
                    pygame.draw.line(surf, (150, 90, 50), (lx - lw // 2, ly + lh * k // 4), (lx + lw // 2, ly + lh * k // 4), r1)
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

    def draw_audience(self, surf, cam, floor_back):
        """Alumnos y maestro sentados en seiza, dibujados con el mismo renderer que los luchadores."""
        from .anatomy import AUDIENCE
        from .poses import pose_seiza
        from .render_fighter import PoseDummy, render_figure
        rig = self.rig
        if not hasattr(self, "_aud_base"):
            self._aud_base = {}
            self._aud_scaled = {}
        ids = list(AUDIENCE)
        tint = shade(mixc(rig["key"], rig["ambient"], 0.55), 1.12)
        tkey = tuple(v // 8 for v in tint)
        for n, a in enumerate(self.audience):
            look = ids[(n * 5 + 3) % len(ids)]
            facing = 1 if a["x"] < 0 else -1
            px, py = self.floor_pt(cam, a["x"], 0.82)
            target = 52 * a["h"] * (0.75 + cam.zoom * 0.25) * 1.25 * RS
            base_key = (look, facing)
            if base_key not in self._aud_base:
                fd = dict(id=look, h=1.0, w=1.0, pal=["#FFFFFF"] * 4)
                d = PoseDummy(fd, facing, n * 0.7)
                d.sweat = 0.0
                ua = 1.1 * RS
                img, rx, ry = render_figure(d, pose_seiza(fd), ua, LIGHT_RIGS[1], ss=2, ow_px=2.0 * RS)
                self._aud_base[base_key] = (img, rx, ry, ua * 125)
            img, rx, ry, h0 = self._aud_base[base_key]
            k = max(0.2, round(target / h0 * 12) / 12)
            sk = (base_key, k, tkey)
            sc_img = self._aud_scaled.get(sk)
            if sc_img is None:
                sc_img = pygame.transform.smoothscale(img, (max(1, int(img.get_width() * k)), max(1, int(img.get_height() * k))))
                sc_img.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
                if len(self._aud_scaled) > 400:
                    self._aud_scaled.clear()
                self._aud_scaled[sk] = sc_img
            lean = a["lean"] * 6 * facing * RS
            bob = -abs(math.sin(self.t * 12 + a["bob"])) * 4 * a["arms"] * RS
            surf.blit(sc_img, (int(px + lean - rx * k), int(py + bob - ry * k)))

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
