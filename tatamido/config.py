import json
import math
import os
from typing import Tuple

import pygame

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTES GLOBALES
# ─────────────────────────────────────────────────────────────────────────────
TITLE = "TATAMI: DŌ — Edición 2026"
W, H = 1280, 720
FPS = 60
ROUND_TIME = 99
VIT_MAX = 1000
STAM_MAX = 100.0
KIAI_MAX = 100.0
TATAMI_HALF = 400          # cm; el tatami mide 8 m
SAFE_HALF = 520            # zona roja de seguridad
FLOOR_Y = int(H * 0.80)    # línea del plano de combate en pantalla (zoom 1)
GRAVITY = 0.9
SAVE_PATH = os.path.join(os.path.expanduser("~"), ".tatamydo_save.json")

PRESETS = {
    "portatil": dict(ss=1, particles=0.3, beams=False, grain=False, bloom=False, tilt=False, dust=20,  blur=False, shadows=False),
    "bajo":     dict(ss=1, particles=0.5, beams=True,  grain=False, bloom=False, tilt=False, dust=40,  blur=False, shadows=True),
    "medio":    dict(ss=2, particles=0.8, beams=True,  grain=False, bloom=False, tilt=True,  dust=80,  blur=False, shadows=True),
    "alto":     dict(ss=2, particles=1.0, beams=True,  grain=True,  bloom=True,  tilt=True,  dust=140, blur=False, shadows=True),
    "ultra":    dict(ss=2, particles=1.4, beams=True,  grain=True,  bloom=True,  tilt=True,  dust=220, blur=True,  shadows=True),
    "cine":     dict(ss=3, particles=2.0, beams=True,  grain=True,  bloom=True,  tilt=True,  dust=320, blur=True,  shadows=True),
}
PRESET_ORDER = ["portatil", "bajo", "medio", "alto", "ultra", "cine"]

# ─────────────────────────────────────────────────────────────────────────────
#  UTILIDADES
# ─────────────────────────────────────────────────────────────────────────────
def clamp(v, a, b):
    return a if v < a else b if v > b else v


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def hex2rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mixc(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)), int(lerp(c1[2], c2[2], t)))


def shade(c, f):
    """Multiplica un color por un factor (f>1 aclara)."""
    return (int(clamp(c[0] * f, 0, 255)), int(clamp(c[1] * f, 0, 255)), int(clamp(c[2] * f, 0, 255)))


def tint(c, light, amount):
    """Mezcla un color hacia el color de la luz."""
    return mixc(c, (c[0] * light[0] // 255, c[1] * light[1] // 255, c[2] * light[2] // 255), amount)


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def rot(px, py, ang):
    ca, sa = math.cos(ang), math.sin(ang)
    return px * ca - py * sa, px * sa + py * ca


def load_save() -> dict:
    try:
        with open(SAVE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"okabe": False, "wins": 0, "preset": "alto", "volume": 0.8, "controls": "moderno",
                "access": {"flashes": False, "shake": True, "contrast": False, "daltonism": "off"},
                "arcade_clears": []}


def write_save(d: dict):
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  ILUMINACIÓN NARRATIVA POR ASALTO
# ─────────────────────────────────────────────────────────────────────────────
LIGHT_RIGS = {
    1: dict(name="17:30 · tarde dorada", sky_top=(214, 140, 70), sky_bot=(252, 208, 140), key=(255, 214, 160),
            ambient=(120, 90, 70), tint=(255, 236, 205), beam=(255, 210, 140), beam_a=34, lanterns=0.0,
            shoji=(246, 226, 190), floor=(206, 178, 120), wall=(92, 64, 44), rain=False, vignette=110),
    2: dict(name="19:00 · hora azul", sky_top=(28, 42, 90), sky_bot=(110, 120, 170), key=(170, 190, 240),
            ambient=(70, 75, 110), tint=(205, 215, 245), beam=(170, 190, 250), beam_a=18, lanterns=0.8,
            shoji=(170, 185, 220), floor=(150, 140, 125), wall=(55, 46, 48), rain=False, vignette=150),
    3: dict(name="20:30 · noche", sky_top=(6, 8, 20), sky_bot=(20, 24, 48), key=(255, 170, 90),
            ambient=(50, 32, 26), tint=(230, 190, 150), beam=(255, 160, 80), beam_a=0, lanterns=1.0,
            shoji=(60, 55, 70), floor=(118, 92, 66), wall=(36, 26, 22), rain=False, vignette=200),
    4: dict(name="Desempate · lluvia", sky_top=(8, 10, 18), sky_bot=(30, 36, 52), key=(240, 170, 100),
            ambient=(44, 34, 32), tint=(220, 195, 170), beam=(255, 160, 80), beam_a=0, lanterns=1.0,
            shoji=(58, 58, 70), floor=(110, 88, 66), wall=(34, 26, 24), rain=True, vignette=215),
}

PHOTO_LUTS = ["Natural", "Caravaggio", "Hammershøi", "Kurosawa B/N", "Daido Moriyama"]

# Parámetros de luz del renderer GPU por asalto (se interpolan al cambiar de asalto).
# light_uv: posición de la fuente de los rayos en coordenadas de pantalla normalizadas (puede estar fuera).
# shadow: (desplazamiento x, compresión y, suavidad, intensidad) de la sombra proyectada.
GPU_RIGS = {
    1: dict(key3=(-0.72, 0.5, 0.48), exposure=-0.25, key_i=1.15, amb_i=1.0, sky_fill=0.16, beam_i=1.0, rays=0.45, rays_col=(1.0, 0.72, 0.42),
            light_uv=(-0.3, -0.9), refl=0.10, wet=0.0, rim=1.1, emissive=1.5, light_dir=(-0.75, -0.66),
            shadow=(0.55, 0.30, 0.010, 0.62), lift=(0.008, 0.004, 0.0), gain=(1.04, 1.0, 0.93), sat=1.2, power=1.14, vig=0.4),
    2: dict(key3=(-0.45, 0.72, 0.52), exposure=0.15, key_i=0.9, amb_i=1.3, sky_fill=0.25, beam_i=0.45, rays=0.25, rays_col=(0.55, 0.68, 1.0),
            light_uv=(-0.2, -0.95), refl=0.2, wet=0.0, rim=0.8, emissive=1.4, light_dir=(-0.45, -0.89),
            shadow=(0.30, 0.42, 0.018, 0.45), lift=(0.0, 0.012, 0.03), gain=(0.97, 1.0, 1.06), sat=1.12, power=1.04, vig=0.45),
    3: dict(key3=(0.0, 0.86, 0.5), exposure=0.55, key_i=0.45, amb_i=1.15, sky_fill=0.15, beam_i=0.0, rays=0.12, rays_col=(1.0, 0.6, 0.3),
            light_uv=(0.5, -0.6), refl=0.32, wet=0.0, rim=0.35, emissive=2.4, light_dir=(0.0, -1.0),
            shadow=(0.10, 0.55, 0.030, 0.5), lift=(0.0, 0.012, 0.022), gain=(1.08, 0.98, 0.88), sat=1.22, power=1.08, vig=0.6),
    4: dict(key3=(0.0, 0.86, 0.5), exposure=0.5, key_i=0.4, amb_i=1.1, sky_fill=0.15, beam_i=0.0, rays=0.1, rays_col=(0.8, 0.7, 0.6),
            light_uv=(0.5, -0.6), refl=0.42, wet=1.0, rim=0.35, emissive=2.4, light_dir=(0.0, -1.0),
            shadow=(0.10, 0.55, 0.035, 0.45), lift=(0.0, 0.016, 0.03), gain=(1.02, 0.98, 0.94), sat=1.12, power=1.08, vig=0.65),
}


def lerp_tree(a, b, t):
    """Interpola números, tuplas y diccionarios anidados."""
    if isinstance(a, dict):
        return {k: lerp_tree(a[k], b[k], t) for k in a}
    if isinstance(a, (tuple, list)):
        return tuple(lerp_tree(x, y, t) for x, y in zip(a, b))
    return a + (b - a) * t
