import math
import random
from typing import List, Optional

import pygame

from .camera import Camera
from .config import *
from .ui import ScaledFonts, draw, panel

W, H = LW, LH       # carteles y destellos van en la interfaz (resolución lógica)

# ─────────────────────────────────────────────────────────────────────────────
#  EFECTOS: partículas, tinta sumi-e, ondas, rótulos
# ─────────────────────────────────────────────────────────────────────────────
class Effects:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.particles: List[list] = []   # [x, y, vx, vy, life, max, size, (r,g,b), kind]
        self.inks: List[dict] = []
        self.rings: List[list] = []       # [x, y, r, max_r, life, color]
        self.popups: List[dict] = []
        self.flash = 0.0
        self.flash_color = (255, 255, 255)
        self.sparks: List[list] = []
        self.waves: List[list] = []       # ondas de choque para la GPU: [x, y, t, t_max, fuerza]
        self.ca = 0.0                     # aberración cromática extra tras un golpe fuerte
        self.impact = 0                   # frames de impacto (negativo estilo anime)

    def mul(self):
        return self.cfg.get("particles", 1.0)

    def dust(self, x, y, n, power=1.0):
        for _ in range(int(n * self.mul())):
            a = random.uniform(-math.pi, 0)
            sp = random.uniform(0.6, 2.6) * power
            self.particles.append([x + random.uniform(-30, 30), y - random.uniform(0, 8), math.cos(a) * sp * 1.6, math.sin(a) * sp * 0.8,
                                   0, random.randint(30, 70), random.uniform(4, 11) * power, (190, 170, 130), "dust"])

    def sweat(self, x, y, n, facing):
        for _ in range(int(n * self.mul())):
            self.particles.append([x, y, -facing * random.uniform(1, 5) + random.uniform(-1, 1), random.uniform(-4, -1),
                                   0, random.randint(18, 32), random.uniform(1.5, 3), (240, 240, 250), "sweat"])

    def spark(self, x, y, n, color=(255, 240, 200)):
        for _ in range(int(n * self.mul())):
            a = random.uniform(0, math.pi * 2)
            sp = random.uniform(4, 11)
            self.sparks.append([x, y, math.cos(a) * sp, math.sin(a) * sp, 0, random.randint(6, 14), color])

    def ring(self, x, y, max_r=120, color=(255, 255, 255)):
        self.rings.append([x, y, 6, max_r, 0, color])
        if max_r >= 120:
            self.waves.append([x, y, 0.0, 16.0, min(1.0, max_r / 220.0)])
            self.waves = self.waves[-4:]
            self.ca = min(2.5, self.ca + max_r / 120.0)

    def salt(self, x, y):
        for _ in range(int(60 * self.mul())):
            self.particles.append([x, y, random.uniform(-2.5, 2.5), random.uniform(-7, -3), 0, random.randint(40, 70),
                                   random.uniform(1.2, 2.4), (255, 250, 240), "salt"])

    def ink(self, x, y, facing, size=1.0):
        """Pincelada sumi-e: curva de puntos con grosor que decrece; se disuelve en el aire."""
        pts = []
        n = 14
        ang = random.uniform(-0.6, 0.6)
        dx = facing                    # sigue al golpe, por detrás de quien lo recibe: hacia atrás cruzaba la cara del atacante
        for i in range(n):
            t = i / (n - 1)
            px = x + dx * t * 150 * size + math.sin(t * 5 + ang) * 18 * size
            py = y - t * 70 * size * math.sin(ang + 1) + math.cos(t * 7) * 10 * size
            pts.append((px, py, (1 - t) ** 1.3 * 16 * size + 1))
        self.inks.append(dict(pts=pts, life=0, max=random.randint(34, 50), drift=random.uniform(-0.3, 0.3)))
        # salpicaduras
        for _ in range(int(6 * size)):
            # gotas pequeñas que salen despedidas (las grandes y redondas manchaban caras y piernas como discos)
            a = random.uniform(0, math.pi * 2)
            d = random.uniform(40, 110) * size
            self.inks.append(dict(pts=[(x + math.cos(a) * d, y + math.sin(a) * d * 0.6, random.uniform(0.8, 2.2) * size)], life=0, max=random.randint(26, 40), drift=0))

    def enso(self, x, y, r=130):
        """Círculo ensō tras el Muro de puños de Inés."""
        pts = []
        n = 40
        for i in range(n):
            t = i / (n - 1)
            a = -math.pi / 2 + t * math.pi * 1.9
            th = (math.sin(t * math.pi) * 10 + 2)
            pts.append((x + math.cos(a) * r, y + math.sin(a) * r * 0.9, th))
        self.inks.append(dict(pts=pts, life=0, max=90, drift=0))

    def popup(self, text, color=(255, 240, 220), size=64, y=H * 0.3, life=90, sub=None):
        self.popups.append(dict(text=text, color=color, size=size, y=y, life=0, max=life, sub=sub))

    def do_flash(self, strength=1.0, color=(255, 255, 255)):
        self.flash = max(self.flash, strength)
        self.flash_color = color

    def update(self, tscale=1.0):
        for p in self.particles:
            p[0] += p[2] * tscale
            p[1] += p[3] * tscale
            if p[8] == "dust":
                p[2] *= 0.96
                p[3] = p[3] * 0.96 - 0.02 * tscale
                p[6] += 0.08 * tscale
            elif p[8] in ("sweat", "salt"):
                p[3] += 0.35 * tscale
            p[4] += tscale
        self.particles = [p for p in self.particles if p[4] < p[5]]
        for s in self.sparks:
            s[0] += s[2] * tscale
            s[1] += s[3] * tscale
            s[3] += 0.4 * tscale
            s[4] += tscale
        self.sparks = [s for s in self.sparks if s[4] < s[5]]
        for r in self.rings:
            r[4] += tscale
            r[2] = lerp(6, r[3], smoothstep(r[4] / 20))
        self.rings = [r for r in self.rings if r[4] < 20]
        for k in self.inks:
            k["life"] += tscale
            if k["drift"]:
                k["pts"] = [(x + k["drift"], y - 0.35 * tscale, th) for x, y, th in k["pts"]]
        self.inks = [k for k in self.inks if k["life"] < k["max"]]
        for p in self.popups:
            p["life"] += tscale
        self.popups = [p for p in self.popups if p["life"] < p["max"]]
        self.flash = max(0.0, self.flash - 0.07 * tscale)
        for wv in self.waves:
            wv[2] += tscale
        self.waves = [wv for wv in self.waves if wv[2] < wv[3]]
        self.ca *= 0.9
        if self.impact > 0:
            self.impact -= 1

    def draw_world(self, surf, cam: Camera, layer):
        """Dibuja partículas en coordenadas del mundo sobre la capa alpha `layer`."""
        for p in self.particles:
            a = int(255 * (1 - p[4] / p[5]))
            sx, sy = cam.P(p[0], p[1])
            r = max(1, int(cam.S(p[6])))
            if p[8] == "dust":
                pygame.draw.circle(layer, (*p[7], int(a * 0.28)), (int(sx), int(sy)), r)
            else:
                pygame.draw.circle(layer, (*p[7], a), (int(sx), int(sy)), max(1, r // 2))
        for s in self.sparks:
            a = int(255 * (1 - s[4] / s[5]))
            sx, sy = cam.P(s[0], s[1])
            ex, ey = cam.P(s[0] - s[2] * 2, s[1] - s[3] * 2)
            pygame.draw.line(layer, (*s[6], a), (sx, sy), (ex, ey), max(2, int(2 * RS)))
        for r in self.rings:
            a = int(200 * (1 - r[4] / 20))
            sx, sy = cam.P(r[0], r[1])
            rr = max(2, int(cam.S(r[2])))
            pygame.draw.ellipse(layer, (*r[5], a), (sx - rr, sy - rr * 0.6, rr * 2, rr * 1.2), max(1, int(cam.S(3))))
        for k in self.inks:
            t = k["life"] / k["max"]
            a = int(235 * (1 - t) ** 1.5)
            pts = [(*cam.P(x, y), max(1.0, cam.S(th) * (1 + 0.3 * t))) for x, y, th in k["pts"]]
            # trazo continuo: entre punto y punto, sellos a menos de medio radio (antes quedaban discos sueltos)
            for (x0, y0, r0), (x1, y1, r1) in zip(pts, pts[1:]):
                n = max(1, int(math.hypot(x1 - x0, y1 - y0) / max(1.0, min(r0, r1) * 0.45)))
                for j in range(n):
                    u = j / n
                    pygame.draw.circle(layer, (16, 12, 14, a), (int(x0 + (x1 - x0) * u), int(y0 + (y1 - y0) * u)),
                                       max(1, int(r0 + (r1 - r0) * u)))
            x_, y_, r_ = pts[-1]
            pygame.draw.circle(layer, (16, 12, 14, a), (int(x_), int(y_)), max(1, int(r_)))

    def draw_screen(self, surf, fonts, reduce_flash=False):
        fonts = fonts.at(surf)
        for p in self.popups:
            t = p["life"] / p["max"]
            a = 255 if t < 0.75 else int(255 * (1 - (t - 0.75) / 0.25))
            scale = 1.0 + 0.25 * (1 - smoothstep(t * 6))
            font = fonts.get(int(p["size"] * scale))
            txt = font.render(p["text"], True, p["color"])
            txt.set_alpha(a)
            sh = font.render(p["text"], True, (0, 0, 0))
            sh.set_alpha(a // 2)
            x = W // 2 - txt.get_width() // 2
            surf.blit(sh, (x + 4, p["y"] + 4))
            surf.blit(txt, (x, p["y"]))
            if p["sub"]:
                f2 = fonts.get(22)
                s2 = f2.render(p["sub"], True, (235, 225, 210))
                s2.set_alpha(a)
                surf.blit(s2, (W // 2 - s2.get_width() // 2, p["y"] + txt.get_height() + 4))
        if self.flash > 0.02:
            panel(surf, (0, 0, W, H), (*self.flash_color, int(self.flash * (90 if reduce_flash else 220))))


# ─────────────────────────────────────────────────────────────────────────────
#  FUENTES
# ─────────────────────────────────────────────────────────────────────────────
class Fonts:
    def __init__(self):
        import warnings
        warnings.filterwarnings("ignore", category=UserWarning)
        self.cache = {}
        self.cjk_name = None
        for name in ("notosanscjkjp", "notoserifcjkjp", "notosanscjk", "sourcehansans", "droidsansfallback", "wenquanyimicrohei", "ipagothic", "takaogothic", "msgothic", "hiraginosans", "pingfangsc", "simsun"):
            try:
                if not pygame.font.match_font(name):
                    continue
                f = pygame.font.SysFont(name, 40)
                a = pygame.image.tobytes(f.render("道", True, (255, 255, 255)), "RGB")
                b = pygame.image.tobytes(f.render("\u0378", True, (255, 255, 255)), "RGB")
                if a != b:
                    self.cjk_name = name
                    break
            except Exception:
                continue
        self.serif_name = None
        for name in ("dejavuserif", "liberationserif", "georgia", "timesnewroman", "notoserif"):
            try:
                if pygame.font.match_font(name):
                    self.serif_name = name
                    break
            except Exception:
                pass

    def get(self, size, serif=False):
        key = (size, serif)
        if key not in self.cache:
            name = self.serif_name if serif else None
            try:
                self.cache[key] = pygame.font.SysFont(name, size) if name else pygame.font.Font(None, size)
            except Exception:
                self.cache[key] = pygame.font.Font(None, size)
        return self.cache[key]

    def cjk(self, size):
        key = ("cjk", size)
        if key not in self.cache:
            self.cache[key] = pygame.font.SysFont(self.cjk_name, size) if self.cjk_name else None
        return self.cache[key]

    def at(self, surf):
        """Las fuentes adecuadas para pintar en `surf`: en una interfaz a escala k, glifos a tamaño ×k."""
        k = getattr(surf, "k", 1)
        if k == 1:
            return self
        key = ("scaled", k)
        if key not in self.cache:
            self.cache[key] = ScaledFonts(self, k)
        return self.cache[key]


def draw_kanji_do(surf, x, y, size, color=(20, 16, 18), fonts: Optional[Fonts] = None):
    """Dibuja 道 con fuente CJK si existe; si no, una caligrafía de trazos aproximada."""
    fonts = fonts.at(surf) if fonts else None
    f = fonts.cjk(size) if fonts else None
    if f:
        img = f.render("道", True, color)
        surf.blit(img, (x - img.get_width() // 2, y - img.get_height() // 2))
        return
    s = size / 100.0
    def L(pts, w):
        draw.lines(surf, color, False, [(x + px * s, y + py * s) for px, py in pts], max(1, int(w * s)))
    # 首: dos puntos, techo largo y el bloque 自 con dos trazos interiores
    L([(-12, -47), (-5, -37)], 7); L([(14, -47), (7, -37)], 7)
    L([(-26, -31), (34, -31)], 8)
    L([(-12, -20), (24, -20), (24, 22), (-12, 22), (-12, -20)], 5)
    L([(-12, -6), (24, -6)], 3); L([(-12, 8), (24, 8)], 3)
    # 辶: punto, trazo quebrado y barrido final con gancho
    L([(-42, -30), (-34, -20)], 7)
    L([(-38, -8), (-30, 0), (-40, 14)], 6)
    L([(-46, 26), (-34, 40), (-16, 46), (40, 44), (46, 38)], 9)
