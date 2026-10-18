import math
import time

from .config import *
from .fighter import Fighter

# ─────────────────────────────────────────────────────────────────────────────
#  CÁMARA
# ─────────────────────────────────────────────────────────────────────────────
class Camera:
    def __init__(self):
        self.x = 0.0
        self.zoom = 1.0
        self.tx = 0.0
        self.tzoom = 1.0
        self.yoff = 0.0
        self.tilt = 0.0
        self.shake_x = 0.0
        self.shake_y = 0.0
        self.override = None   # (x, zoom) para cinemáticas / modo foto
        self.punch = 0.0       # zoom de impacto, decae solo
        self.ty = 0.0          # desplazamiento vertical objetivo (saltos)

    def follow(self, a: Fighter, b: Fighter, tension: float):
        if self.override is not None:
            self.tx, self.tzoom = self.override
        else:
            mid = (a.x + b.x) / 2
            dist = abs(a.x - b.x)
            # encuadre de juego de lucha moderno: los cuerpos llenan la mitad de la pantalla
            self.tzoom = clamp(1280.0 / ((dist + 250) * 0.95), 1.3, 2.1)
            air = max(0.0, -a.y, -b.y)
            if air > 0:
                self.tzoom = min(self.tzoom, 2.1 - air * 0.0045)
            half = 640.0 / (self.tzoom * 0.95)
            lim = max(0.0, SAFE_HALF + 80 - half)
            self.tx = clamp(mid, -lim, lim)
            self.ty = air * self.tzoom * 0.95 * 0.5
        self.x = lerp(self.x, self.tx, 0.08)
        if self.override is None:
            self.yoff = lerp(self.yoff, self.ty, 0.1)
        self.punch *= 0.86
        self.zoom = lerp(self.zoom, self.tzoom + self.punch, 0.06)
        self.tilt = lerp(self.tilt, tension * 1.6 * math.sin(time.time() * 0.7), 0.05)

    def P(self, wx, wy):
        """Mundo (cm, y hacia arriba negativo) → pantalla."""
        return (W / 2 + (wx - self.x) * self.zoom * 0.95 + self.shake_x,
                FLOOR_Y + wy * self.zoom * 0.95 + self.yoff + self.shake_y + cam_base_off(self.zoom))

    def S(self, l):
        return l * self.zoom * 0.95
