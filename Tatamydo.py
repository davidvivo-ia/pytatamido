#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TATAMI: DŌ — Edición 2026  ·  TatamyDo
========================================
Juego de lucha 2.5D, uno contra uno, en un único fichero Python.
Doce luchadores, un dojo al atardecer y una luz que se apaga asalto a asalto.

Requisitos:  python >= 3.10, pygame-ce (o pygame >= 2.1), numpy (opcional, para el audio).
    pip install pygame-ce numpy
    python Tatamydo.py [--preset portatil|bajo|medio|alto|ultra|cine] [--photo-mode] [--debug-hitboxes]
                       [--ai-vs-ai] [--windowed] [--seed N]

Todo lo que ves es procedimental: no hay ficheros de arte ni de audio.
    「礼に始まり礼に終わる」 — Empieza con un saludo, termina con un saludo.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import pygame
except ImportError:  # pragma: no cover
    print("Necesitas pygame:  pip install pygame-ce numpy")
    sys.exit(1)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

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
    "portatil": dict(particles=0.3, beams=False, grain=False, bloom=False, tilt=False, dust=20,  blur=False, shadows=False),
    "bajo":     dict(particles=0.5, beams=True,  grain=False, bloom=False, tilt=False, dust=40,  blur=False, shadows=True),
    "medio":    dict(particles=0.8, beams=True,  grain=False, bloom=False, tilt=True,  dust=80,  blur=False, shadows=True),
    "alto":     dict(particles=1.0, beams=True,  grain=True,  bloom=True,  tilt=True,  dust=140, blur=False, shadows=True),
    "ultra":    dict(particles=1.4, beams=True,  grain=True,  bloom=True,  tilt=True,  dust=220, blur=True,  shadows=True),
    "cine":     dict(particles=2.0, beams=True,  grain=True,  bloom=True,  tilt=True,  dust=320, blur=True,  shadows=True),
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
#  DATOS DE LOS DOCE LUCHADORES
# ─────────────────────────────────────────────────────────────────────────────
# F fuerza · V velocidad · D defensa · A alcance · T técnica  (1–5)
# outfit: gi | shorts | kurtka | abada | mawashi | changshan | singlet | tank | hakama
# kicks: proporción de patadas en golpes medios/fuertes
FIGHTERS: List[dict] = [
    dict(id="haruki", name="Haruki Mori", title="El que espera el desequilibrio", style="Judo", nation="Japón · Osaka", age=28,
         F=3, V=3, D=4, A=2, T=5, arche="Proyector", outfit="gi", kicks=0.1, grappler=True, ground=False,
         pal=["#F2EFE6", "#1A1A1A", "#C99A6E", "#1A1A1A"], hair="short", h=1.0, w=1.0,
         kiai="Ippon Seoi Nage", kiai_kind="throw",
         intro="Se ajusta el cinturón y hace una reverencia lenta al tatami.",
         weak="Pocas herramientas a media distancia.",
         epilogue="Haruki vuelve a Osaka. En el dojo de su padre cuelga ahora un segundo cinturón; el suyo, deshilachado, sigue en su cintura."),
    dict(id="ines", name="Inés Albarrán", title="La que no da un paso atrás", style="Karate Kyokushin", nation="España · Cáceres", age=31,
         F=4, V=3, D=3, A=3, T=4, arche="Golpeadora equilibrada", outfit="gi", kicks=0.4, grappler=False, ground=False,
         pal=["#FFFFFF", "#111111", "#D8A47F", "#111111"], hair="braid", h=0.98, w=0.95,
         kiai="Muro de puños", kiai_kind="barrage6",
         intro="Un osu seco con los puños cruzados; el kiai hace vibrar el papel de los shōji.",
         weak="Agarre mediocre.",
         epilogue="Inés no celebra. Se venda los nudillos en silencio y pregunta a qué hora abre el dojo mañana."),
    dict(id="tomasz", name="Tomasz Krol", title="El oso del Báltico", style="Sambo", nation="Polonia · Gdansk", age=35,
         F=4, V=2, D=4, A=2, T=4, arche="Suelo y llaves", outfit="kurtka", kicks=0.2, grappler=True, ground=True,
         pal=["#B22222", "#1F3A93", "#E5BFA0", "#8C6A3F"], hair="shaved", h=1.04, w=1.2,
         kiai="Llave al tobillo", kiai_kind="groundlock",
         intro="Se golpea el pecho dos veces y el tatami tiembla al pisarlo.",
         weak="Lento de pie.",
         epilogue="Tomasz manda una foto al club de Gdansk. En el reverso escribe una sola palabra: Dziękuję."),
    dict(id="ayo", name="Ayo Okonkwo", title="El péndulo", style="Capoeira", nation="Nigeria/Brasil · Salvador", age=24,
         F=2, V=5, D=2, A=4, T=4, arche="Acróbata", outfit="abada", kicks=0.95, grappler=False, ground=False,
         pal=["#F7F7F2", "#E3B505", "#5C3A21", "#1A1410"], hair="locs", h=1.02, w=0.85,
         kiai="Meia Lua Invertida", kiai_kind="spinkick",
         intro="Un berimbau lejano. Entra haciendo la ginga, sin dejar de moverse.",
         weak="La defensa más baja.",
         epilogue="Ayo toca el berimbau en la playa de Itapuã. La roda se cierra a su alrededor como una ola."),
    dict(id="kanoa", name="Kanoa Pahia", title="La montaña que camina", style="Sumo", nation="Hawái · Maui", age=30,
         F=5, V=1, D=5, A=2, T=3, arche="Muro", outfit="mawashi", kicks=0.0, grappler=True, ground=False,
         pal=["#0F2A44", "#0F2A44", "#A86B43", "#0B0B0B"], hair="chonmage", h=1.08, w=1.6,
         kiai="Oshidashi", kiai_kind="charge",
         intro="Lanza sal al aire y pisa con shiko. La cámara tiembla; el público se sobresalta.",
         weak="El más lento.",
         epilogue="Kanoa vuelve a Maui con una bolsa de sal del dojo. La esparce en el mar, como le enseñó su abuela."),
    dict(id="lin", name="Lin Meixiu", title="La aguja", style="Wing Chun", nation="China · Foshan", age=26,
         F=2, V=5, D=4, A=1, T=5, arche="Contraataque", outfit="changshan", kicks=0.1, grappler=False, ground=False,
         pal=["#1B1B1B", "#9E2A2B", "#EBC9A8", "#1B1B1B"], hair="bob", h=0.92, w=0.8,
         kiai="Puños en cadena", kiai_kind="barrage10",
         intro="Saluda con el puño en la palma y queda inmóvil. El polvo flota a su alrededor.",
         weak="Alcance mínimo.",
         epilogue="Lin regresa a Foshan y golpea el muñeco de madera hasta el amanecer. No ha cambiado nada; eso era lo que buscaba."),
    dict(id="tito", name="Rafael \"Tito\" Duarte", title="La enredadera", style="Jiu-jitsu brasileño", nation="Brasil · Río", age=33,
         F=2, V=3, D=4, A=2, T=5, arche="Sumisión", outfit="gi", kicks=0.1, grappler=True, ground=True,
         pal=["#2C5AA0", "#111111", "#C68B59", "#3A2A1A"], hair="curly", h=0.98, w=1.0,
         kiai="Triángulo", kiai_kind="groundlock",
         intro="Choca la mano con el rival y se sienta en el tatami, invitándolo a bajar.",
         weak="De pie es el peor golpeador.",
         epilogue="Tito abre la Academia Maré a los niños del barrio. La primera clase es gratis. Las siguientes también."),
    dict(id="somchai", name="Somchai Prasert", title="El de las ocho armas", style="Muay Thai", nation="Tailandia · Isan", age=29,
         F=5, V=3, D=3, A=3, T=3, arche="Clinch", outfit="shorts", kicks=0.6, grappler=False, ground=False,
         pal=["#C1121F", "#FDF0D5", "#B07D4F", "#111111"], hair="short", h=1.0, w=0.92,
         kiai="Rodillas al clinch", kiai_kind="knees",
         intro="Ejecuta el wai kru completo mientras suenan el pi y los tambores.",
         weak="Defensa nula en el suelo.",
         epilogue="Somchai compra un búfalo para su familia en Isan. El mongkol cuelga ahora en el altar de la casa."),
    dict(id="yeon", name="Yeon Da-eun", title="El compás", style="Taekwondo", nation="Corea · Busan", age=22,
         F=3, V=4, D=2, A=5, T=3, arche="Patadas a distancia", outfit="gi", kicks=0.95, grappler=False, ground=False,
         pal=["#FFFFFF", "#000000", "#F1D3B3", "#2A1F1A"], hair="ponytail", h=1.05, w=0.85,
         kiai="Tornado de tres patadas", kiai_kind="tornado",
         intro="Tres patadas al aire, rápidas como látigos, que dejan estelas en el polvo.",
         weak="Sin respuesta si la cierran.",
         epilogue="Da-eun entrena al amanecer en la playa de Haeundae. Su madre la mira desde el paseo, sin decir nada."),
    dict(id="elsa", name="Elsa Hjelm", title="La avalancha", style="Lucha olímpica", nation="Noruega · Bergen", age=27,
         F=4, V=3, D=3, A=2, T=4, arche="Derribos", outfit="singlet", kicks=0.0, grappler=True, ground=True,
         pal=["#5A6470", "#F2C14E", "#F3D9C7", "#EDE6C8"], hair="braid", h=1.0, w=1.1,
         kiai="Derribo doble pierna", kiai_kind="tackle",
         intro="Se palmea los muslos y sonríe al rival.",
         weak="Cada derribo fallido le cuesta mucho aliento.",
         epilogue="Elsa cuelga la medalla junto a la de su abuelo. Llueve sobre Bergen, como siempre."),
    dict(id="matias", name="Matías Ferro", title="El martillo de Rosario", style="Boxeo + savate", nation="Argentina · Rosario", age=34,
         F=4, V=4, D=3, A=3, T=2, arche="Presión", outfit="tank", kicks=0.35, grappler=False, ground=False,
         pal=["#75AADB", "#FFFFFF", "#D2A07A", "#141414"], hair="short", h=1.0, w=1.0,
         kiai="Gancho de la casa", kiai_kind="liverhook",
         intro="Bob and weave frente al rival y un guiño a cámara.",
         weak="Técnica de proyección casi nula.",
         epilogue="Matías vuelve al gimnasio de Rosario. Sonríe cuando el chaval nuevo le pega en la cara."),
    dict(id="okabe", name="Sensei Okabe", title="El que no empuja", style="Aikido", nation="Japón · Kioto", age=71,
         F=1, V=3, D=5, A=2, T=5, arche="Redirector", outfit="hakama", kicks=0.0, grappler=True, ground=False,
         pal=["#101010", "#F5F5F0", "#D9B99B", "#E8E8E8"], hair="white", h=0.97, w=0.9, locked=True,
         kiai="Kokyū Nage", kiai_kind="kokyu",
         intro="No entra. Ya está sentado en seiza cuando empieza el combate.",
         weak="No puede ganar si el rival no ataca.",
         epilogue="Okabe barre el tatami al terminar. El arce del tokonoma ha perdido su última hoja."),
]
FIGHTER_BY_ID = {f["id"]: f for f in FIGHTERS}

EPILOGUE_FOOTER = "「礼に始まり礼に終わる」 — Empieza con un saludo, termina con un saludo."

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


# ─────────────────────────────────────────────────────────────────────────────
#  AUDIO SINTETIZADO (sin ficheros)
# ─────────────────────────────────────────────────────────────────────────────
class Audio:
    """Todo el sonido se sintetiza al arrancar con numpy. Sin numpy, el juego es mudo."""

    def __init__(self, volume=0.8):
        self.ok = False
        self.volume = volume
        self.sounds: Dict[str, pygame.mixer.Sound] = {}
        self.music_intensity = 0.0
        self.beat_t = 0.0
        self.bpm = 84
        self.heartbeat_only = False
        self.rain_ch = None
        self.muted_for_slowmo = False
        if np is None:
            return
        try:
            pygame.mixer.pre_init(44100, -16, 2, 512)
            pygame.mixer.init(44100, -16, 2, 512)
            pygame.mixer.set_num_channels(24)
            self.ok = True
        except Exception:
            self.ok = False
            return
        self.sr = 44100
        self._build()

    # ----- síntesis básica -----
    def _env(self, n, a=0.002, d=0.2, curve=4.0):
        t = np.linspace(0, 1, n)
        att = int(max(1, a * self.sr))
        env = np.exp(-curve * t)
        env[:att] *= np.linspace(0, 1, att)
        return env

    def _lowpass(self, x, alpha):
        y = np.empty_like(x)
        acc = 0.0
        # filtro IIR de un polo, vectorizado por bloques para no ser eterno
        b = 1 - alpha
        for i in range(0, len(x), 2048):
            blk = x[i:i + 2048]
            out = np.empty_like(blk)
            for j in range(len(blk)):
                acc = acc * alpha + blk[j] * b
                out[j] = acc
            y[i:i + 2048] = out
        return y

    def _to_sound(self, mono, gain=1.0, pan=0.0):
        mono = np.clip(mono * gain, -1, 1)
        left = mono * (1 - max(0, pan))
        right = mono * (1 + min(0, pan))
        stereo = np.stack([left, right], axis=1)
        arr = (stereo * 32000).astype(np.int16)
        return pygame.sndarray.make_sound(np.ascontiguousarray(arr))

    def _noise(self, dur):
        n = int(dur * self.sr)
        return np.random.uniform(-1, 1, n)

    def _tone(self, freq, dur, harm=(1.0,), vib=0.0):
        n = int(dur * self.sr)
        t = np.arange(n) / self.sr
        out = np.zeros(n)
        for k, a in enumerate(harm, start=1):
            out += a * np.sin(2 * math.pi * freq * k * t + vib * np.sin(2 * math.pi * 5.5 * t))
        return out / max(1e-6, sum(harm))

    def _build(self):
        S = {}
        rnd = np.random
        # golpe ligero: chasquido seco
        x = self._noise(0.12) * self._env(int(0.12 * self.sr), curve=14)
        S["hit_l"] = self._to_sound(self._lowpass(x, 0.6), 0.9)
        # golpe medio
        n = int(0.22 * self.sr)
        x = self._noise(0.22) * self._env(n, curve=10) + 0.6 * self._tone(140, 0.22) * self._env(n, curve=12)
        S["hit_m"] = self._to_sound(self._lowpass(x, 0.75), 1.0)
        # golpe fuerte: impacto grave con cuerpo
        n = int(0.4 * self.sr)
        t = np.arange(n) / self.sr
        sweep = np.sin(2 * math.pi * (110 - 70 * t / 0.4) * t)
        x = (self._noise(0.4) * 0.8 + sweep) * self._env(n, curve=7)
        S["hit_h"] = self._to_sound(self._lowpass(x, 0.85), 1.1)
        # caída sobre tatami: golpe sordo + fibra
        n = int(0.55 * self.sr)
        t = np.arange(n) / self.sr
        thud = np.sin(2 * math.pi * (70 - 40 * t) * t) * self._env(n, curve=6)
        fib = self._lowpass(self._noise(0.55), 0.5) * self._env(n, curve=9) * 0.5
        S["fall"] = self._to_sound(thud + fib, 1.2)
        # bloqueo
        x = self._lowpass(self._noise(0.09), 0.3) * self._env(int(0.09 * self.sr), curve=18)
        S["block"] = self._to_sound(x, 0.6)
        # parada perfecta: campanilla cristalina
        n = int(0.9 * self.sr)
        x = (self._tone(1760, 0.9, (1, 0.3, 0.15)) + 0.5 * self._tone(2637, 0.9)) * self._env(n, curve=5)
        S["parry"] = self._to_sound(x, 0.5)
        # whoosh
        n = int(0.18 * self.sr)
        x = self._lowpass(self._noise(0.18), 0.8) * np.sin(np.linspace(0, math.pi, n)) ** 2
        S["whoosh"] = self._to_sound(x, 0.45)
        # agarre: roce de algodón
        n = int(0.25 * self.sr)
        x = self._lowpass(self._noise(0.25), 0.4) * self._env(n, curve=6)
        S["grab"] = self._to_sound(x, 0.5)
        # taiko
        n = int(0.5 * self.sr)
        t = np.arange(n) / self.sr
        x = np.sin(2 * math.pi * (95 - 55 * np.minimum(t * 3, 1)) * t) * self._env(n, curve=6)
        x += self._lowpass(self._noise(0.5), 0.7) * self._env(n, curve=25) * 0.6
        S["taiko"] = self._to_sound(x, 1.0)
        n = int(0.25 * self.sr)
        t = np.arange(n) / self.sr
        x = np.sin(2 * math.pi * (240 - 120 * np.minimum(t * 6, 1)) * t) * self._env(n, curve=10)
        S["taiko_hi"] = self._to_sound(x, 0.5)
        # gong de ippon
        n = int(3.0 * self.sr)
        x = np.zeros(n)
        for f, a, c in ((196, 1.0, 1.2), (392, 0.5, 1.6), (587, 0.35, 2.0), (811, 0.2, 2.6), (1200, 0.1, 3.5)):
            x += a * self._tone(f, 3.0, vib=0.3) * self._env(n, curve=c)
        S["gong"] = self._to_sound(x, 0.9)
        # kiai: grito sintético (formantes)
        n = int(0.5 * self.sr)
        t = np.arange(n) / self.sr
        f0 = 180 + 60 * np.exp(-t * 6)
        src = np.sign(np.sin(2 * math.pi * f0 * t)) * 0.5 + np.sin(2 * math.pi * f0 * t) * 0.5
        x = self._lowpass(src, 0.6) * self._env(n, a=0.02, curve=6) + self._lowpass(self._noise(0.5), 0.5) * self._env(n, curve=8) * 0.3
        S["kiai"] = self._to_sound(x, 0.9)
        # público: inspiración contenida y aplauso
        n = int(0.8 * self.sr)
        x = self._lowpass(self._noise(0.8), 0.9) * np.sin(np.linspace(0, math.pi, n)) ** 3
        S["gasp"] = self._to_sound(x, 0.5)
        n = int(2.2 * self.sr)
        base = np.zeros(n)
        for _ in range(60):
            p = rnd.randint(0, n - 600)
            base[p:p + 600] += self._noise(600 / self.sr) * self._env(600, curve=30)
        S["applause"] = self._to_sound(self._lowpass(base, 0.3) * self._env(n, a=0.3, curve=2), 0.6)
        # latido
        n = int(0.6 * self.sr)
        t = np.arange(n) / self.sr
        x = np.sin(2 * math.pi * 55 * t) * (self._env(n, curve=14) + 0.6 * np.roll(self._env(n, curve=14), int(0.17 * self.sr)))
        S["heart"] = self._to_sound(x, 1.0)
        # UI
        S["ui_move"] = self._to_sound(self._tone(880, 0.06) * self._env(int(0.06 * self.sr), curve=12), 0.3)
        S["ui_ok"] = self._to_sound((self._tone(660, 0.18) + self._tone(990, 0.18)) * self._env(int(0.18 * self.sr), curve=8), 0.4)
        S["ui_back"] = self._to_sound(self._tone(330, 0.15) * self._env(int(0.15 * self.sr), curve=8), 0.3)
        # shakuhachi: aliento + tono
        for i, f in enumerate((293.66, 349.23, 392.0, 440.0, 523.25, 587.33)):
            n = int(1.8 * self.sr)
            x = self._tone(f, 1.8, (1, 0.4, 0.2, 0.1), vib=0.08) * self._env(n, a=0.25, curve=2.2)
            x += self._lowpass(self._noise(1.8), 0.92) * self._env(n, a=0.2, curve=2.5) * 0.25
            S[f"shaku{i}"] = self._to_sound(x, 0.35)
        # lluvia (loop)
        n = int(3.0 * self.sr)
        x = self._lowpass(self._noise(3.0), 0.55) * 0.5
        S["rain"] = self._to_sound(x, 0.5)
        # sal / pisotón de sumo
        n = int(0.7 * self.sr)
        t = np.arange(n) / self.sr
        S["stomp"] = self._to_sound(np.sin(2 * math.pi * (50 - 20 * t) * t) * self._env(n, curve=5), 1.2)
        # cuenta atrás
        S["tick"] = self._to_sound(self._tone(1320, 0.05) * self._env(int(0.05 * self.sr), curve=20), 0.4)
        self.sounds = S

    def play(self, name, vol=1.0, pan=0.0):
        if not self.ok or name not in self.sounds:
            return
        snd = self.sounds[name]
        ch = pygame.mixer.find_channel(True)
        if ch is None:
            return
        v = self.volume * vol
        ch.set_volume(v * (1 - max(0, pan)), v * (1 + min(0, pan)))
        ch.play(snd)

    def rain(self, on: bool):
        if not self.ok:
            return
        if on and self.rain_ch is None:
            self.rain_ch = pygame.mixer.Channel(23)
            self.rain_ch.set_volume(self.volume * 0.6)
            self.rain_ch.play(self.sounds["rain"], loops=-1)
        elif not on and self.rain_ch is not None:
            self.rain_ch.stop()
            self.rain_ch = None

    def music_tick(self, dt, intensity, last_second=False, silent=False):
        """Música generativa: taiko que sigue la intensidad del combate; en el último segundo, sólo el latido."""
        if not self.ok or silent:
            return
        self.music_intensity = lerp(self.music_intensity, intensity, 0.02)
        bpm = 72 + 60 * self.music_intensity
        self.beat_t += dt * bpm / 60.0
        if self.beat_t >= 1.0:
            self.beat_t -= 1.0
            self.beat_idx = getattr(self, "beat_idx", 0) + 1
            if last_second:
                self.play("heart", 0.9)
                return
            b = self.beat_idx % 8
            if b in (0, 4):
                self.play("taiko", 0.55 + 0.4 * self.music_intensity)
            elif b in (2, 6) and self.music_intensity > 0.35:
                self.play("taiko", 0.35)
            if b % 2 == 1 and self.music_intensity > 0.2:
                self.play("taiko_hi", 0.2 + 0.3 * self.music_intensity)
            if b == 3 and random.random() < 0.35:
                self.play(f"shaku{random.randrange(6)}", 0.5)


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRADA
# ─────────────────────────────────────────────────────────────────────────────
ACTIONS = ["up", "down", "left", "right", "light", "medium", "heavy", "grab", "block", "kiai"]
KEYMAP_P1 = dict(up=pygame.K_w, down=pygame.K_s, left=pygame.K_a, right=pygame.K_d,
                 light=pygame.K_u, medium=pygame.K_i, heavy=pygame.K_o, grab=pygame.K_j, block=pygame.K_k, kiai=pygame.K_l)
KEYMAP_P2 = dict(up=pygame.K_UP, down=pygame.K_DOWN, left=pygame.K_LEFT, right=pygame.K_RIGHT,
                 light=pygame.K_KP4, medium=pygame.K_KP5, heavy=pygame.K_KP6, grab=pygame.K_KP1, block=pygame.K_KP2, kiai=pygame.K_KP3)
# Mando (layout xbox estándar de SDL): X=2 Y=3 RB=5 A=0 B=1 RT=eje 5
PADMAP = dict(light=2, medium=3, heavy=5, grab=0, block=1)


class PlayerInput:
    """Estado de entrada de un jugador con histórico direccional para comandos clásicos."""

    def __init__(self, keymap: dict, joy_index: Optional[int] = None):
        self.keymap = keymap
        self.joy_index = joy_index
        self.held = {a: False for a in ACTIONS}
        self.prev = dict(self.held)
        self.pressed = {a: False for a in ACTIONS}
        self.history: List[Tuple[int, int]] = []  # (frame, dir)  dir en notación numérica 1-9
        self.frame = 0
        self.ai_cmd: Optional[dict] = None   # si no es None, la IA sustituye al teclado

    def poll(self, keys, joys):
        self.prev = dict(self.held)
        if self.ai_cmd is not None:
            for a in ACTIONS:
                self.held[a] = bool(self.ai_cmd.get(a, False))
        else:
            for a, k in self.keymap.items():
                self.held[a] = bool(keys[k])
            if self.joy_index is not None and self.joy_index < len(joys):
                j = joys[self.joy_index]
                try:
                    ax = j.get_axis(0)
                    ay = j.get_axis(1)
                    hx, hy = j.get_hat(0) if j.get_numhats() > 0 else (0, 0)
                    self.held["left"] |= ax < -0.45 or hx < 0
                    self.held["right"] |= ax > 0.45 or hx > 0
                    self.held["up"] |= ay < -0.5 or hy > 0
                    self.held["down"] |= ay > 0.5 or hy < 0
                    for a, b in PADMAP.items():
                        if b < j.get_numbuttons():
                            self.held[a] |= bool(j.get_button(b))
                    if j.get_numaxes() > 5:
                        self.held["kiai"] |= j.get_axis(5) > 0.3
                except Exception:
                    pass
        for a in ACTIONS:
            self.pressed[a] = self.held[a] and not self.prev[a]
        self.frame += 1
        d = self.direction()
        if not self.history or self.history[-1][1] != d:
            self.history.append((self.frame, d))
            if len(self.history) > 24:
                self.history.pop(0)

    def direction(self) -> int:
        x = (1 if self.held["right"] else 0) - (1 if self.held["left"] else 0)
        y = (1 if self.held["up"] else 0) - (1 if self.held["down"] else 0)
        return 5 + x + 3 * y

    def command(self, seq: List[int], window=18) -> bool:
        """Comprueba una secuencia de direcciones (ya orientada según facing) en el histórico reciente."""
        hist = [d for f, d in self.history if self.frame - f <= window]
        if len(hist) < len(seq):
            return False
        i = 0
        for d in hist:
            if d == seq[i]:
                i += 1
                if i == len(seq):
                    return True
        return False

    def double_tap(self, d: int) -> bool:
        hist = [(f, dd) for f, dd in self.history if self.frame - f <= 14]
        taps = [f for f, dd in hist if dd == d]
        return len(taps) >= 2 and self.held[("right" if d == 6 else "left")]


def mirror_dir(d: int, facing: int) -> int:
    """Convierte una dirección numérica a notación relativa al facing (6 = hacia delante)."""
    if facing == 1:
        return d
    col = (d - 1) % 3
    return d - col + (2 - col)


# ─────────────────────────────────────────────────────────────────────────────
#  MOVIMIENTOS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Move:
    name: str
    kind: str              # strike | grab | kiai
    startup: int
    active: int
    recovery: int
    damage: int
    stamina: float
    rng: float             # alcance en cm
    height: str = "mid"    # high | mid | low
    knockdown: bool = False
    hitstop: int = 4
    push: float = 6.0
    hitstun: int = 14
    blockstun: int = 10
    kiai_gain: float = 6.0
    kick: bool = False
    anim: str = "jab"
    reach_y: float = -110  # altura del golpe

    @property
    def total(self):
        return self.startup + self.active + self.recovery


def build_moveset(fd: dict) -> Dict[str, Move]:
    F, V, D, A, T = fd["F"], fd["V"], fd["D"], fd["A"], fd["T"]
    spd = (6 - V)            # 1..5 → más alto, más lento
    reach = 62 + A * 14
    kick_m = fd["kicks"] >= 0.5
    kick_h = fd["kicks"] >= 0.3
    ms = {
        "light": Move("Golpe ligero", "strike", 3 + spd // 2, 3, 7 + spd, 42 + F * 7, 5, reach * 0.85, "high",
                      hitstop=3, push=4, hitstun=12, blockstun=8, kiai_gain=4, anim="jab"),
        "medium": Move("Golpe medio", "strike", 7 + spd // 2, 4, 13 + spd, 72 + F * 13, 9, reach * (1.15 if kick_m else 1.0), "mid",
                       hitstop=5, push=7, hitstun=16, blockstun=12, kiai_gain=7, kick=kick_m, anim="kick_mid" if kick_m else "cross"),
        "heavy": Move("Golpe fuerte", "strike", 13 + spd, 5, 22 + spd, 112 + F * 22, 16, reach * (1.3 if kick_h else 1.1), "mid",
                      knockdown=True, hitstop=9, push=14, hitstun=24, blockstun=16, kiai_gain=12, kick=kick_h,
                      anim="kick_high" if kick_h else "hook"),
        "low": Move("Barrido", "strike", 8 + spd // 2, 3, 15 + spd, 60 + F * 9, 8, reach * 0.95, "low",
                    hitstop=4, push=5, hitstun=15, blockstun=11, kiai_gain=6, kick=True, anim="sweep", reach_y=-25),
        "grab": Move("Agarre", "grab", 6 + spd // 2, 3, 17 + spd, 95 + T * 17, 14, 58 + (6 if fd["grappler"] else 0), "mid",
                     knockdown=True, hitstop=10, push=0, hitstun=30, kiai_gain=14, anim="grab"),
        "air": Move("Golpe aéreo", "strike", 5, 5, 10, 68 + F * 10, 7, reach, "high", hitstop=4, push=8, hitstun=15,
                    blockstun=10, kiai_gain=6, kick=fd["kicks"] > 0.3, anim="air_kick" if fd["kicks"] > 0.3 else "air_punch"),
        "special": Move("Técnica de estilo", "strike", 10 + spd // 2, 6, 18 + spd, 95 + F * 16 + T * 6, 18, reach * 1.25, "mid",
                        knockdown=T >= 4, hitstop=7, push=12, hitstun=20, blockstun=14, kiai_gain=10, kick=fd["kicks"] > 0.5,
                        anim="special"),
        "kiai": Move(fd["kiai"], "kiai", 10, 6, 30, 230 + T * 14 + F * 10, 0, reach * 1.1, "mid", knockdown=True, hitstop=12,
                     push=22, hitstun=60, kiai_gain=0, anim="kiai"),
    }
    return ms


# ─────────────────────────────────────────────────────────────────────────────
#  LUCHADOR
# ─────────────────────────────────────────────────────────────────────────────
class Fighter:
    # Estados
    IDLE, WALK, CROUCH, JUMP, ATTACK, BLOCK, HIT, KNOCK, GETUP, THROWN, THROWING, KO, WIN, INTRO, KIAI, GROUND, PARRY, STUN = range(18)

    def __init__(self, fd: dict, side: int, pinput: PlayerInput, is_ai=False, difficulty=0.6):
        self.fd = fd
        self.side = side              # 0 izquierda (P1), 1 derecha (P2)
        self.inp = pinput
        self.is_ai = is_ai
        self.difficulty = difficulty
        self.moves = build_moveset(fd)
        self.pal = [hex2rgb(c) for c in fd["pal"]]
        self.scale = fd["h"]
        self.width = fd["w"]
        self.mass = 0.8 + 0.25 * fd["w"] + 0.1 * fd["F"]
        self.player_color = (255, 170, 80) if side == 0 else (90, 170, 255)
        self.reset_match()

    # ----- ciclo de vida -----
    def reset_match(self):
        self.wins = 0
        self.reset_round(full=True)

    def reset_round(self, full=True):
        self.x = -170.0 if self.side == 0 else 170.0
        self.y = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.facing = 1 if self.side == 0 else -1
        self.state = Fighter.INTRO
        self.timer = 0
        self.move: Optional[Move] = None
        self.move_key = ""
        self.hit_done = False
        self.vit = VIT_MAX
        self.stam = STAM_MAX
        if full:
            self.kiai = 0.0
        self.wazaari = 0
        self.jogai = 0
        self.crouching = False
        self.blocking = False
        self.block_timer = 0           # frames desde que empezó a bloquear (ventana de parada)
        self.exhausted = False
        self.combo = 0
        self.sweat = 0.0
        self.redness = 0.0
        self.dishevel = 0.0
        self.breath = 0.0
        self.anim_t = 0.0
        self.throw_partner: Optional["Fighter"] = None
        self.throw_phase = 0.0
        self.stun_frames = 0
        self.kiai_seq = []
        self.kiai_idx = 0
        self.ai_timer = 0
        self.ai_plan = {}
        self.ai_hold_block = 0
        self.last_hit_by = ""
        self.lastpose = None
        self.mash = 0
        self.rim = 0.0
        self.landed_hits = 0
        self.ground_attacker = False
        self.stun_len = 10

    # ----- geometría -----
    @property
    def height_cm(self):
        return 175 * self.scale * (0.62 if self.crouching and self.state in (Fighter.CROUCH, Fighter.BLOCK) else 1.0)

    def hurtbox(self) -> pygame.Rect:
        if self.state in (Fighter.KNOCK, Fighter.KO, Fighter.GROUND, Fighter.THROWN):
            return pygame.Rect(int(self.x - 55), -40, 110, 40)
        w = int(40 * self.width)
        h = int(self.height_cm)
        return pygame.Rect(int(self.x - w / 2), int(self.y - h), w, h)

    def hitbox(self) -> Optional[pygame.Rect]:
        if self.state not in (Fighter.ATTACK, Fighter.KIAI) or self.move is None:
            return None
        m = self.move
        if not (m.startup <= self.timer < m.startup + m.active):
            return None
        rng = m.rng
        yy = m.reach_y if m.height != "low" else -25
        if self.state == Fighter.JUMP or self.y < -5:
            yy = -90
        cx = self.x + self.facing * (20 + rng * 0.5)
        return pygame.Rect(int(cx - rng * 0.5), int(self.y + yy - 30), int(rng), 60)

    # ----- helpers -----
    def can_act(self):
        return self.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH) and self.stun_frames == 0

    def start_move(self, key: str, audio: "Audio"):
        m = self.moves[key]
        if self.stam < 4 and key != "kiai":
            return False
        self.state = Fighter.ATTACK if key != "kiai" else Fighter.KIAI
        self.move = m
        self.move_key = key
        self.timer = 0
        self.hit_done = False
        self.stam = max(0.0, self.stam - m.stamina * (1.4 if self.exhausted else 1.0))
        self.blocking = False
        if key in ("heavy", "special", "grab"):
            audio.play("whoosh", 0.6, pan=self.x / 600)
        return True

    def take_damage(self, dmg: int, source: str):
        if self.exhausted:
            pass
        self.vit = max(0, self.vit - int(dmg))
        self.redness = min(1.0, self.redness + dmg / 900)
        self.sweat = min(1.0, self.sweat + dmg / 1500)
        self.dishevel = min(1.0, self.dishevel + dmg / 1200)
        self.last_hit_by = source

    def damage_level(self) -> int:
        return 0 if self.vit > 700 else 1 if self.vit > 400 else 2 if self.vit > 150 else 3

    # ----- actualización -----
    def update(self, opp: "Fighter", match: "Match"):
        inp = self.inp
        audio = match.audio
        self.timer += 1
        self.anim_t += 1 / 60
        self.rim = max(0.0, self.rim - 0.08)
        if self.stun_frames > 0:
            self.stun_frames -= 1
        # orientación
        if self.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH, Fighter.BLOCK):
            self.facing = 1 if opp.x >= self.x else -1
        # aliento: regeneración
        regen = 0.22 if self.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH) else 0.12
        if self.state == Fighter.BLOCK:
            regen = 0.1
        if self.state not in (Fighter.KO, Fighter.INTRO):
            self.stam = min(STAM_MAX, self.stam + regen * (1.5 if self.exhausted else 1.0))
        if self.stam <= 0.5:
            self.exhausted = True
        if self.exhausted and self.stam > 45:
            self.exhausted = False
        self.sweat = min(1.0, self.sweat + 0.00025 + 0.0004 * (1 - self.stam / STAM_MAX))
        self.breath = (1 - self.stam / STAM_MAX)

        if self.state in (Fighter.INTRO, Fighter.KO, Fighter.WIN):
            self.vx = 0
            return
        if self.state == Fighter.GROUND:
            return

        # dirección relativa
        fwd = inp.held["right"] if self.facing == 1 else inp.held["left"]
        back = inp.held["left"] if self.facing == 1 else inp.held["right"]
        down = inp.held["down"]
        up = inp.held["up"]

        # ----- máquina de estados -----
        st = self.state
        if st in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH):
            self.crouching = bool(down) and self.y >= 0
            speed = (3.2 + 0.55 * self.fd["V"]) * (0.65 if self.exhausted else 1.0)
            if self.crouching:
                self.state = Fighter.CROUCH
                self.vx = 0
            else:
                if fwd:
                    self.vx = self.facing * speed
                    self.state = Fighter.WALK
                elif back:
                    self.vx = -self.facing * speed * 0.8
                    self.state = Fighter.WALK
                else:
                    self.vx = 0
                    self.state = Fighter.IDLE
            # dash (doble toque)
            if match.controls == "clasico" and (inp.double_tap(6) or inp.double_tap(4)) and self.stam > 10:
                self.vx *= 2.2
                self.stam -= 0.4
            # salto
            if inp.pressed["up"] and self.y >= 0 and not self.crouching and self.stam > 6:
                self.state = Fighter.JUMP
                self.vy = -(14 + self.fd["V"] * 0.9)
                self.vx = (self.facing * speed if fwd else -self.facing * speed * 0.8 if back else 0)
                self.stam -= 4
                self.timer = 0
            # bloqueo
            elif inp.held["block"]:
                self.state = Fighter.BLOCK
                self.block_timer = 0
                self.blocking = True
                self.vx = 0
                self.timer = 0
            # kiai final
            elif inp.pressed["kiai"] and self.kiai >= KIAI_MAX and match.kiai_allowed():
                self.start_kiai(opp, match)
            # ataques
            elif inp.pressed["grab"]:
                self.start_move("grab", audio)
            elif inp.pressed["heavy"]:
                if self.crouching:
                    self.start_move("low", audio)
                else:
                    self.start_move("heavy", audio)
            elif inp.pressed["medium"]:
                # técnica de estilo: moderno = atrás+medio; clásico = ↓↘→ + medio
                cmd = (inp.command([mirror_dir(d, self.facing) for d in (2, 3, 6)]) if match.controls == "clasico" else back)
                if cmd and self.stam > 12:
                    self.start_move("special", audio)
                elif self.crouching:
                    self.start_move("low", audio)
                else:
                    self.start_move("medium", audio)
            elif inp.pressed["light"]:
                self.start_move("light", audio)

        elif st == Fighter.BLOCK:
            self.block_timer += 1
            self.vx = 0
            self.crouching = bool(down)
            if not inp.held["block"]:
                self.state = Fighter.IDLE
                self.blocking = False
            elif inp.pressed["grab"]:
                self.blocking = False
                self.start_move("grab", audio)

        elif st == Fighter.JUMP:
            if inp.pressed["light"] or inp.pressed["medium"] or inp.pressed["heavy"]:
                if self.move is None or self.move_key != "air":
                    self.move = self.moves["air"]
                    self.move_key = "air"
                    self.timer = 0
                    self.hit_done = False
                    self.state = Fighter.ATTACK

        elif st == Fighter.ATTACK:
            m = self.move
            if self.y < 0 or self.move_key == "air":
                pass  # en el aire se mantiene la inercia
            else:
                self.vx *= 0.8
                # avance en el golpe
                if m.kind == "strike" and m.startup <= self.timer < m.startup + m.active:
                    self.vx = self.facing * (2.5 if m.kick else 1.8)
            if self.timer >= m.total:
                self.state = Fighter.IDLE if self.y >= 0 else Fighter.JUMP
                self.move = None
                self.move_key = ""

        elif st == Fighter.HIT:
            self.vx *= 0.82
            if self.timer >= self.stun_len:
                self.state = Fighter.IDLE

        elif st == Fighter.KNOCK:
            self.vx *= 0.9
            if self.y >= 0 and self.timer >= 40:
                self.state = Fighter.GETUP
                self.timer = 0

        elif st == Fighter.GETUP:
            self.vx = 0
            if self.timer >= 22:
                self.state = Fighter.IDLE

        elif st == Fighter.THROWN:
            # la posición la controla el que lanza
            if self.throw_partner is None or self.throw_partner.state != Fighter.THROWING:
                self.state = Fighter.KNOCK
                self.timer = 0
                self.vy = 0
                self.y = 0

        elif st == Fighter.THROWING:
            dur = 46
            p = self.timer / dur
            self.throw_phase = p
            self.vx = 0
            tgt = self.throw_partner
            if tgt is not None:
                ang = math.pi * smoothstep(p)
                r = 95 * self.scale
                tgt.x = self.x + self.facing * math.cos(ang) * r * 0.9
                tgt.y = -abs(math.sin(ang)) * 130
                tgt.facing = -self.facing
                if p >= 1.0:
                    tgt.y = 0
                    tgt.x = self.x - self.facing * 85
                    tgt.state = Fighter.KNOCK
                    tgt.timer = 0
                    tgt.vx = -self.facing * 2
                    match.on_throw_land(self, tgt)
                    self.state = Fighter.IDLE
                    self.throw_partner = None

        elif st == Fighter.STUN:
            self.vx = 0
            if self.timer >= self.stun_len:
                self.state = Fighter.IDLE

        elif st == Fighter.PARRY:
            self.vx = 0
            if self.timer >= 10:
                self.state = Fighter.IDLE

        elif st == Fighter.KIAI:
            self.update_kiai(opp, match)

        # ----- física -----
        if self.state not in (Fighter.THROWN, Fighter.GROUND):
            self.x += self.vx
            if self.y < 0 or self.vy != 0:
                self.vy += GRAVITY
                self.y += self.vy
                if self.y >= 0:
                    self.y = 0
                    self.vy = 0
                    if self.state == Fighter.JUMP or (self.state == Fighter.ATTACK and self.move_key == "air"):
                        self.state = Fighter.IDLE
                        self.move = None
                        self.move_key = ""
                        match.fx.dust(self.x, 0, 6, 0.6)
                    elif self.state == Fighter.KNOCK:
                        match.fx.dust(self.x, 0, 14, 1.0)
                        match.add_decal(self.x)
                        match.audio.play("fall", 0.9, pan=self.x / 600)
                        match.shake(5)
        # límites de la zona
        self.x = clamp(self.x, -SAFE_HALF - 40, SAFE_HALF + 40)

    # ----- recibir un golpe -----
    def on_hit(self, m: Move, attacker: "Fighter", match: "Match"):
        mult = 0.6 if attacker.exhausted else 1.0
        dmg = int(m.damage * mult * (0.85 + 0.03 * (6 - self.fd["D"])))
        if self.state in (Fighter.HIT,):
            dmg = int(dmg * 0.8)  # escalado de combo
        self.take_damage(dmg, attacker.fd["id"])
        self.kiai = min(KIAI_MAX, self.kiai + m.kiai_gain * 0.6)
        attacker.kiai = min(KIAI_MAX, attacker.kiai + m.kiai_gain)
        attacker.landed_hits += 1
        self.blocking = False
        self.move = None
        self.move_key = ""
        knock = m.knockdown or self.vit <= 0 or self.y < 0
        if knock:
            self.state = Fighter.KNOCK
            self.timer = 0
            self.vy = -7 - m.push * 0.25
            self.vx = -self.facing * (m.push * 0.9 + 2) / self.mass
            self.y = min(self.y, -1)
        else:
            self.state = Fighter.HIT
            self.timer = 0
            self.stun_len = m.hitstun
            self.vx = -self.facing * m.push / self.mass
        return dmg

    def on_block(self, m: Move, attacker: "Fighter", match: "Match"):
        self.stam = max(0.0, self.stam - m.stamina * 0.6)
        self.redness = min(1.0, self.redness + 0.04)
        self.kiai = min(KIAI_MAX, self.kiai + m.kiai_gain * 0.4)
        self.vx = -self.facing * m.push * 0.5 / self.mass
        self.stun_frames = m.blockstun
        self.state = Fighter.BLOCK
        self.block_timer = 99

    # ----- Kiai Final -----
    def start_kiai(self, opp: "Fighter", match: "Match"):
        self.kiai = 0.0
        self.state = Fighter.KIAI
        self.timer = 0
        self.move = self.moves["kiai"]
        self.move_key = "kiai"
        self.hit_done = False
        self.kiai_idx = 0
        kind = self.fd["kiai_kind"]
        # secuencia de impactos (frame, daño relativo)
        seqs = {
            "throw": [(26, 1.0)],
            "barrage6": [(14 + i * 7, 0.17) for i in range(6)],
            "barrage10": [(12 + i * 5, 0.1) for i in range(10)],
            "groundlock": [(22, 0.5), (46, 0.5)],
            "spinkick": [(24, 1.0)],
            "charge": [(20, 0.45), (34, 0.55)],
            "knees": [(16, 0.33), (28, 0.33), (40, 0.34)],
            "tornado": [(16, 0.3), (30, 0.3), (50, 0.4)],
            "tackle": [(22, 1.0)],
            "liverhook": [(24, 1.0)],
            "kokyu": [(22, 1.0)],
        }
        self.kiai_seq = seqs.get(kind, [(24, 1.0)])
        match.begin_kiai(self, opp)

    def update_kiai(self, opp: "Fighter", match: "Match"):
        self.vx = 0
        kind = self.fd["kiai_kind"]
        dur = self.kiai_seq[-1][0] + 34
        # ¿conecta? Se decide en el frame 8: debe estar en alcance y el rival no bloqueando.
        if self.timer == 8:
            dist = abs(opp.x - self.x)
            if dist > self.moves["kiai"].rng + 40 or (opp.state == Fighter.BLOCK and opp.blocking and kind not in ("throw", "kokyu", "tackle", "groundlock")):
                match.kiai_whiff(self)
                self.state = Fighter.ATTACK
                self.move = self.moves["heavy"]
                self.move_key = "heavy"
                self.timer = self.move.startup + self.move.active  # directo a recuperación
                return
            match.kiai_connect(self, opp)
            opp.state = Fighter.STUN
            opp.stun_len = dur + 10
            opp.timer = 0
            opp.facing = -self.facing
            opp.vx = 0
        if self.timer >= 8:
            # acercar al rival a distancia de impacto
            target = self.x + self.facing * 70
            if kind in ("charge", "tackle"):
                self.x += self.facing * 3.0
                target = self.x + self.facing * 70
            opp.x = lerp(opp.x, target, 0.3)
            if self.kiai_idx < len(self.kiai_seq) and self.timer >= self.kiai_seq[self.kiai_idx][0]:
                f, frac = self.kiai_seq[self.kiai_idx]
                self.kiai_idx += 1
                dmg = int(self.moves["kiai"].damage * frac)
                opp.take_damage(dmg, self.fd["id"])
                match.kiai_impact(self, opp, dmg, last=(self.kiai_idx == len(self.kiai_seq)))
            if kind in ("throw", "kokyu", "tackle") and self.timer >= self.kiai_seq[0][0] - 12 and self.timer <= self.kiai_seq[0][0] + 14:
                p = (self.timer - (self.kiai_seq[0][0] - 12)) / 26
                ang = math.pi * smoothstep(p)
                opp.x = self.x + self.facing * math.cos(ang) * 90
                opp.y = -abs(math.sin(ang)) * 150
            if self.timer >= dur:
                opp.y = 0
                opp.x = self.x + self.facing * (-80 if kind in ("throw", "kokyu") else 110)
                opp.state = Fighter.KNOCK
                opp.timer = 0
                opp.vy = 0
                self.state = Fighter.IDLE
                self.move = None
                self.move_key = ""
                match.end_kiai(self, opp, kind)

    # ----- IA -----
    def think(self, opp: "Fighter", match: "Match"):
        """IA por árbol de decisión sencillo con personalidad por estadísticas."""
        cmd = {a: False for a in ACTIONS}
        fd = self.fd
        if self.state in (Fighter.INTRO, Fighter.KO, Fighter.WIN):
            self.inp.ai_cmd = cmd
            return
        if self.state == Fighter.GROUND:
            # minijuego de sumisión: pulsa con ritmo
            if match.frame % max(2, 8 - fd["T"] - int(self.difficulty * 3)) == 0:
                cmd["grab"] = True
                cmd["block"] = True
            self.inp.ai_cmd = cmd
            return
        dist = abs(opp.x - self.x)
        toward = "right" if opp.x > self.x else "left"
        away = "left" if toward == "right" else "right"
        self.ai_timer -= 1
        diff = self.difficulty
        aggression = clamp(0.25 + 0.1 * (fd["F"] + fd["V"]) * 0.5 + (0.2 if fd["id"] in ("matias", "ines", "somchai") else 0) - (0.35 if fd["id"] == "okabe" else 0), 0.1, 0.95)
        reach = self.moves["medium"].rng
        opp_attacking = opp.state == Fighter.ATTACK and opp.move is not None and opp.timer < opp.move.startup + opp.move.active
        # reacción defensiva
        if opp_attacking and dist < opp.move.rng + 60 and self.can_act():
            if random.random() < 0.25 + 0.12 * fd["D"] * diff:
                self.ai_hold_block = 10 + random.randint(0, 12)
                if opp.move.kind == "grab" and random.random() < 0.5 * diff:
                    cmd["light"] = True   # golpe interrumpe agarre
                    self.ai_hold_block = 0
        if self.ai_hold_block > 0:
            self.ai_hold_block -= 1
            cmd["block"] = True
            if opp.state == Fighter.ATTACK and opp.move and opp.move.kind == "grab":
                cmd["block"] = False
                cmd[away] = True
            self.inp.ai_cmd = cmd
            return
        if self.ai_timer <= 0:
            self.ai_timer = random.randint(6, 18 - int(8 * diff))
            r = random.random()
            plan = {}
            if self.kiai >= KIAI_MAX and dist < reach + 30 and random.random() < 0.6 + 0.3 * diff:
                plan["kiai"] = True
            elif dist > reach * 1.6:
                if fd["id"] == "okabe":
                    plan[toward if random.random() < 0.3 else "wait"] = True
                elif fd["id"] == "ayo" and random.random() < 0.3:
                    plan[toward] = True
                    plan["up"] = True
                else:
                    plan[toward] = True
                    if fd["id"] == "yeon" and dist < reach * 1.9 and random.random() < 0.4:
                        plan["heavy"] = True
            elif dist > reach * 1.05:
                if random.random() < aggression:
                    plan[toward] = True
                    if fd["kicks"] > 0.5 and random.random() < 0.4:
                        plan["medium"] = True
                else:
                    plan[random.choice([toward, away, "wait"])] = True
            else:
                # en alcance
                if opp.state in (Fighter.KNOCK, Fighter.GETUP):
                    plan[away if random.random() < 0.4 else "wait"] = True
                elif opp.state == Fighter.BLOCK or (opp.blocking and random.random() < 0.6 * diff):
                    plan["grab"] = True  # agarre rompe bloqueo
                elif random.random() < aggression + 0.2 * diff:
                    if fd["grappler"] and random.random() < 0.45:
                        plan["grab"] = True
                    elif random.random() < 0.15 and fd["T"] >= 3:
                        plan["medium"] = True
                        plan["back_special"] = True
                    else:
                        plan[random.choices(["light", "medium", "heavy"], weights=[3, 3, 1 + fd["F"]])[0]] = True
                        if random.random() < 0.2:
                            plan["down"] = True
                else:
                    plan[random.choice(["block", away, "wait"])] = True
            self.ai_plan = plan
        p = self.ai_plan
        for k in ("left", "right", "up", "down", "block"):
            if p.get(k):
                cmd[k] = True
        if p.get("back_special"):
            cmd[away] = True
        # los botones son pulsaciones de un frame
        for k in ("light", "medium", "heavy", "grab", "kiai"):
            if p.get(k):
                cmd[k] = True
                p[k] = False
        if fd["id"] == "okabe" and opp_attacking and dist < 120 and random.random() < 0.55 + 0.4 * diff:
            cmd["block"] = True   # Okabe vive de la parada perfecta
        self.inp.ai_cmd = cmd


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

    def follow(self, a: Fighter, b: Fighter, tension: float):
        if self.override is not None:
            self.tx, self.tzoom = self.override
        else:
            mid = (a.x + b.x) / 2
            dist = abs(a.x - b.x)
            self.tx = clamp(mid, -SAFE_HALF + 300, SAFE_HALF - 300)
            self.tzoom = clamp(1200.0 / (dist + 620), 0.82, 1.28)
        self.x = lerp(self.x, self.tx, 0.08)
        self.zoom = lerp(self.zoom, self.tzoom, 0.06)
        self.tilt = lerp(self.tilt, tension * 1.6 * math.sin(time.time() * 0.7), 0.05)

    def P(self, wx, wy):
        """Mundo (cm, y hacia arriba negativo) → pantalla."""
        return (W / 2 + (wx - self.x) * self.zoom * 0.95 + self.shake_x,
                FLOOR_Y + wy * self.zoom * 0.95 + self.yoff + self.shake_y + (1 - self.zoom) * 90)

    def S(self, l):
        return l * self.zoom * 0.95


# ─────────────────────────────────────────────────────────────────────────────
#  POSES PROCEDIMENTALES
# ─────────────────────────────────────────────────────────────────────────────
# Cada pose es un diccionario de articulaciones en coordenadas locales (cm, mirando a la derecha,
# origen en la cadera, y negativo hacia arriba).  Interpolamos entre poses según el estado.
def pose_idle(t, fd, breath):
    bob = math.sin(t * 2.2) * (1.5 + 2.5 * breath)
    guard = {"gi": -20, "shorts": -30, "tank": -28, "changshan": -22, "mawashi": 6, "hakama": 2}.get(fd["outfit"], -18)
    return dict(hip=(0, -95 + bob), chest=(2, -140 + bob), head=(4, -176 + bob),
                sh_l=(-6, -138 + bob), sh_r=(8, -138 + bob),
                el_l=(14, -118 + bob), el_r=(28, -118 + bob),
                hd_l=(26, -128 + guard * 0.6 + bob), hd_r=(40, -128 + guard + bob),
                kn_l=(-12, -50), kn_r=(16, -50), ft_l=(-20, 0), ft_r=(22, 0))


def pose_walk(t, fd, back=False):
    s = math.sin(t * 9) * (-1 if back else 1)
    c = math.cos(t * 9)
    p = pose_idle(t, fd, 0)
    p["kn_l"] = (-12 + 14 * s, -52 - 6 * abs(c))
    p["kn_r"] = (14 - 14 * s, -52 - 6 * abs(c))
    p["ft_l"] = (-20 + 24 * s, -6 * max(0, c))
    p["ft_r"] = (22 - 24 * s, -6 * max(0, -c))
    return p


def pose_crouch(t, fd):
    return dict(hip=(0, -58), chest=(6, -98), head=(10, -132), sh_l=(0, -96), sh_r=(12, -96),
                el_l=(14, -82), el_r=(28, -80), hd_l=(28, -92), hd_r=(40, -96),
                kn_l=(-22, -36), kn_r=(26, -36), ft_l=(-26, 0), ft_r=(28, 0))


def pose_block(t, fd, crouch=False):
    p = pose_crouch(t, fd) if crouch else pose_idle(t, fd, 0)
    p["el_l"] = (12, p["sh_l"][1] + 20)
    p["el_r"] = (18, p["sh_r"][1] + 22)
    p["hd_l"] = (18, p["sh_l"][1] - 14)
    p["hd_r"] = (28, p["sh_r"][1] - 22)
    return p


def pose_jump(t, fd, vy):
    tuck = clamp(-vy / 16, -1, 1)
    return dict(hip=(0, -100), chest=(2, -145), head=(4, -181), sh_l=(-6, -142), sh_r=(8, -142),
                el_l=(4, -128), el_r=(26, -128), hd_l=(18, -150), hd_r=(40, -140),
                kn_l=(-6, -60 - 20 * tuck), kn_r=(20, -60 - 24 * tuck), ft_l=(-14, -24 * tuck), ft_r=(24, -30 * tuck))


def pose_attack(anim, phase, fd, kick=False):
    """phase 0..1 dentro del movimiento: startup (0..0.35), activo (..0.55), recovery."""
    base = pose_idle(0, fd, 0)
    ext = smoothstep(phase / 0.4) if phase < 0.4 else (1 - smoothstep((phase - 0.5) / 0.5)) if phase > 0.5 else 1.0
    lean = 10 * ext
    p = dict(base)
    if anim == "jab":
        p["hd_r"] = (lerp(40, 92, ext), -138)
        p["el_r"] = (lerp(28, 60, ext), -132)
        p["chest"] = (2 + lean * 0.6, -140)
        p["head"] = (4 + lean * 0.6, -176)
    elif anim == "cross":
        p["hd_l"] = (lerp(26, 100, ext), -140)
        p["el_l"] = (lerp(14, 66, ext), -136)
        p["hd_r"] = (lerp(40, 20, ext), -150)
        p["chest"] = (2 + lean, -140)
        p["head"] = (4 + lean, -176)
        p["hip"] = (lean * 0.5, -95)
    elif anim == "hook":
        p["hd_r"] = (lerp(40, 88, ext), lerp(-128, -150, ext))
        p["el_r"] = (lerp(28, 70, ext), lerp(-118, -128, ext))
        p["chest"] = (2 + lean * 1.4, -138)
        p["head"] = (4 + lean * 1.4, -174)
        p["hip"] = (lean * 0.8, -93)
        p["kn_r"] = (16 + lean, -50)
        p["ft_r"] = (22 + lean * 1.2, 0)
    elif anim == "kick_mid":
        p["kn_r"] = (lerp(16, 60, ext), lerp(-50, -95, ext))
        p["ft_r"] = (lerp(22, 118, ext), lerp(0, -100, ext))
        p["hip"] = (-6 * ext, -95)
        p["chest"] = (-10 * ext, -140)
        p["head"] = (-12 * ext, -176)
        p["hd_l"] = (18, -150)
    elif anim == "kick_high":
        p["kn_r"] = (lerp(16, 50, ext), lerp(-50, -120, ext))
        p["ft_r"] = (lerp(22, 108, ext), lerp(0, -160, ext))
        p["hip"] = (-10 * ext, -95)
        p["chest"] = (-18 * ext, -138)
        p["head"] = (-22 * ext, -172)
        p["hd_l"] = (-10, -150)
        p["hd_r"] = (30, -120)
    elif anim == "sweep":
        p = pose_crouch(0, fd)
        p["kn_r"] = (lerp(26, 60, ext), -34)
        p["ft_r"] = (lerp(28, 120, ext), -4)
        p["hd_l"] = (-20, -40)
    elif anim == "grab":
        p["hd_l"] = (lerp(26, 80, ext), -138)
        p["hd_r"] = (lerp(40, 84, ext), -118)
        p["el_l"] = (lerp(14, 50, ext), -130)
        p["el_r"] = (lerp(28, 56, ext), -116)
        p["chest"] = (2 + lean, -140)
        p["head"] = (4 + lean, -176)
        p["hip"] = (lean * 0.6, -95)
    elif anim in ("air_kick", "air_punch"):
        p = pose_jump(0, fd, 0)
        if anim == "air_kick":
            p["ft_r"] = (lerp(24, 110, ext), lerp(-30, -60, ext))
            p["kn_r"] = (lerp(20, 60, ext), -80)
        else:
            p["hd_r"] = (lerp(40, 90, ext), -120)
            p["el_r"] = (lerp(26, 60, ext), -124)
    elif anim == "special":
        if kick:
            p["kn_r"] = (lerp(16, 40, ext), lerp(-50, -130, ext))
            p["ft_r"] = (lerp(22, 120, ext), lerp(0, -150, ext))
            p["hip"] = (-14 * ext, -95)
            p["chest"] = (-26 * ext, -136)
            p["head"] = (-30 * ext, -170)
            p["hd_l"] = (-20 * ext, -150)
        else:
            p["hd_r"] = (lerp(40, 110, ext), -136)
            p["hd_l"] = (lerp(26, 96, ext), -124)
            p["el_r"] = (lerp(28, 70, ext), -130)
            p["el_l"] = (lerp(14, 60, ext), -122)
            p["chest"] = (2 + lean * 2, -138)
            p["head"] = (4 + lean * 2, -174)
            p["hip"] = (lean, -94)
            p["kn_r"] = (16 + lean * 1.5, -50)
            p["ft_r"] = (22 + lean * 2, 0)
    return p


def pose_hit(phase, fd):
    p = pose_idle(0, fd, 0)
    k = 1 - smoothstep(phase)
    p["head"] = (4 - 26 * k, -170 + 8 * k)
    p["chest"] = (2 - 14 * k, -138)
    p["hd_l"] = (12 - 10 * k, -120)
    p["hd_r"] = (30 - 20 * k, -110)
    p["el_l"] = (6, -118)
    p["el_r"] = (24, -112)
    return p


def pose_knock(phase, fd, airborne):
    """Volando hacia atrás y luego tumbado."""
    if airborne:
        return dict(hip=(0, -80), chest=(-30, -100), head=(-56, -112), sh_l=(-30, -100), sh_r=(-26, -96),
                    el_l=(-50, -120), el_r=(-44, -70), hd_l=(-70, -140), hd_r=(-60, -50),
                    kn_l=(30, -50), kn_r=(36, -70), ft_l=(60, -40), ft_r=(66, -70))
    return dict(hip=(0, -14), chest=(-40, -18), head=(-78, -16), sh_l=(-42, -22), sh_r=(-40, -12),
                el_l=(-66, -32), el_r=(-62, -8), hd_l=(-90, -24), hd_r=(-84, -6),
                kn_l=(34, -14), kn_r=(40, -22), ft_l=(70, -4), ft_r=(78, -14))


def pose_getup(phase, fd):
    a = pose_knock(1, fd, False)
    b = pose_crouch(0, fd)
    c = pose_idle(0, fd, 0)
    if phase < 0.6:
        return {k: (lerp(a[k][0], b[k][0], smoothstep(phase / 0.6)), lerp(a[k][1], b[k][1], smoothstep(phase / 0.6))) for k in a}
    t = smoothstep((phase - 0.6) / 0.4)
    return {k: (lerp(b[k][0], c[k][0], t), lerp(b[k][1], c[k][1], t)) for k in a}


def pose_throwing(phase, fd):
    p = pose_idle(0, fd, 0)
    k = math.sin(phase * math.pi)
    p["hip"] = (10 * k, -88 + 4 * k)
    p["chest"] = (18 * k, -134)
    p["head"] = (26 * k, -170)
    p["hd_l"] = (40 + 30 * k, -150 + 60 * phase)
    p["hd_r"] = (50 + 20 * k, -130 + 70 * phase)
    p["el_l"] = (24, -140 + 20 * phase)
    p["el_r"] = (36, -124 + 30 * phase)
    p["kn_l"] = (-20, -48)
    p["kn_r"] = (30, -48)
    p["ft_l"] = (-32, 0)
    p["ft_r"] = (40, 0)
    return p


def pose_thrown(phase, fd):
    """Rota en el aire: la pose se gira alrededor de la cadera."""
    ang = -math.pi * 1.1 * phase
    base = pose_jump(0, fd, 0)
    out = {}
    for k, (x, y) in base.items():
        rx, ry = rot(x, y + 100, ang)
        out[k] = (rx, ry - 60)
    return out


def pose_win(t, fd):
    p = pose_idle(t, fd, 0)
    k = (math.sin(t * 2) + 1) / 2
    p["hd_r"] = (30, -200 - 10 * k)
    p["el_r"] = (26, -170)
    p["hd_l"] = (-20, -120)
    p["el_l"] = (-6, -118)
    return p


def pose_bow(phase, fd):
    p = pose_idle(0, fd, 0)
    k = math.sin(clamp(phase, 0, 1) * math.pi) * 0.6
    for j in ("chest", "head", "sh_l", "sh_r", "el_l", "el_r", "hd_l", "hd_r"):
        x, y = p[j]
        y += 95
        rx, ry = rot(x, y, k)
        p[j] = (rx, ry - 95)
    p["hd_l"] = (p["hd_l"][0], p["hd_l"][1] + 20)
    p["hd_r"] = (p["hd_r"][0], p["hd_r"][1] + 20)
    return p


def pose_seiza(fd):
    return dict(hip=(0, -36), chest=(0, -82), head=(2, -118), sh_l=(-8, -80), sh_r=(8, -80),
                el_l=(-6, -58), el_r=(10, -58), hd_l=(6, -44), hd_r=(14, -44),
                kn_l=(28, -10), kn_r=(32, -14), ft_l=(-22, -2), ft_r=(-18, -6))


def pose_kiai(fd, phase, kind, idx):
    """Pose del Kiai Final: alterna golpes para las ráfagas, gira para las patadas."""
    if kind in ("barrage6", "barrage10", "knees", "liverhook"):
        anim = "cross" if idx % 2 == 0 else "jab"
        if kind == "knees":
            anim = "kick_mid"
        if kind == "liverhook":
            anim = "hook"
        return pose_attack(anim, 0.45, fd, kick=False)
    if kind in ("spinkick", "tornado"):
        base = pose_attack("kick_high", 0.45, fd, True)
        ang = phase * math.pi * 2 * (1 if kind == "spinkick" else 3)
        out = {}
        for k, (x, y) in base.items():
            if k in ("ft_l", "ft_r", "kn_l", "kn_r"):
                rx, ry = rot(x, y + 95, ang * 0.25)
                out[k] = (rx, ry - 95)
            else:
                out[k] = (x, y)
        return out
    if kind in ("throw", "kokyu", "tackle", "charge", "groundlock"):
        return pose_throwing(phase, fd)
    return pose_attack("special", 0.45, fd)


def blend(a, b, t):
    return {k: (lerp(a[k][0], b[k][0], t), lerp(a[k][1], b[k][1], t)) for k in a}


def fighter_pose(f: Fighter) -> dict:
    fd = f.fd
    st = f.state
    t = f.anim_t
    if st == Fighter.INTRO:
        if fd["id"] == "okabe":
            if f.timer < 110:
                return pose_seiza(fd)
            return blend(pose_seiza(fd), pose_idle(t, fd, 0), smoothstep((f.timer - 110) / 50))
        if f.timer < 40:
            return pose_idle(t, fd, 0)
        if f.timer < 140:
            return pose_bow((f.timer - 40) / 100, fd)
        return pose_idle(t, fd, 0)
    if st == Fighter.IDLE:
        return pose_idle(t, fd, f.breath)
    if st == Fighter.WALK:
        back = (f.vx * f.facing) < 0
        return pose_walk(t, fd, back)
    if st == Fighter.CROUCH:
        return pose_crouch(t, fd)
    if st == Fighter.BLOCK:
        return pose_block(t, fd, f.crouching)
    if st == Fighter.JUMP:
        return pose_jump(t, fd, f.vy)
    if st == Fighter.ATTACK and f.move:
        m = f.move
        ph = f.timer / max(1, m.total)
        return pose_attack(m.anim, ph, fd, m.kick)
    if st == Fighter.HIT:
        return pose_hit(f.timer / max(1, f.stun_len), fd)
    if st == Fighter.STUN:
        return pose_hit(0.3, fd)
    if st == Fighter.PARRY:
        return pose_block(t, fd)
    if st == Fighter.KNOCK or st == Fighter.KO:
        air = f.y < -2
        if st == Fighter.KO:
            air = False
        return pose_knock(1, fd, air)
    if st == Fighter.GETUP:
        return pose_getup(f.timer / 22, fd)
    if st == Fighter.THROWING:
        return pose_throwing(f.throw_phase, fd)
    if st == Fighter.THROWN:
        return pose_thrown(f.throw_partner.throw_phase if f.throw_partner else 0.5, fd)
    if st == Fighter.GROUND:
        return pose_crouch(t, fd) if f.ground_attacker else pose_knock(1, fd, False)
    if st == Fighter.WIN:
        return pose_win(t, fd)
    if st == Fighter.KIAI:
        dur = f.kiai_seq[-1][0] + 34 if f.kiai_seq else 60
        return pose_kiai(fd, f.timer / dur, fd["kiai_kind"], f.kiai_idx)
    return pose_idle(t, fd, 0)


# ─────────────────────────────────────────────────────────────────────────────
#  DIBUJO DEL LUCHADOR
# ─────────────────────────────────────────────────────────────────────────────
def draw_capsule(surf, c, a, b, r):
    if r < 1:
        r = 1
    pygame.draw.line(surf, c, a, b, int(r * 2))
    pygame.draw.circle(surf, c, (int(a[0]), int(a[1])), int(r))
    pygame.draw.circle(surf, c, (int(b[0]), int(b[1])), int(r))


def draw_fighter(surf, f: Fighter, cam: Camera, rig: dict, cfg: dict, debug=False, lut_bw=False):
    """Figura articulada con ropa, luz lateral, sudor, rojeces y rim light de jugador."""
    fd = f.fd
    pose = fighter_pose(f)
    f.lastpose = pose
    sc = f.scale
    fac = f.facing
    skin, hair_c = f.pal[2], f.pal[3]
    main, trim = f.pal[0], f.pal[1]
    key = rig["key"]
    amb = rig["ambient"]
    # dirección de la luz: entra por la izquierda (shōji al norte-oeste); la cara iluminada es la izquierda
    def lit(x_local):
        # x_local en mundo (ya con facing): más a la izquierda → más luz
        k = clamp(0.55 - x_local / 220.0, 0.0, 1.0)
        return k

    def col(c, xw, extra=1.0):
        k = lit(xw)
        base = tint(c, key, 0.35 * k)
        base = mixc(base, amb, 0.35 * (1 - k))
        base = shade(base, (0.78 + 0.42 * k) * extra)
        return base

    def WP(j):
        lx, ly = pose[j]
        return cam.P(f.x + lx * fac * sc, f.y + ly * sc)

    def WX(j):
        return pose[j][0] * fac * sc

    J = {k: WP(k) for k in pose}
    r_limb = cam.S(7.5 * sc * f.width)
    r_arm = cam.S(6.2 * sc * f.width)
    outfit = fd["outfit"]

    # sombra en el suelo
    if cfg.get("shadows", True):
        sx, sy = cam.P(f.x, 0)
        hgt = max(0.0, -f.y)
        sw = cam.S(70 * sc * f.width) * (1 - min(0.5, hgt / 400))
        sh = cam.S(14) * (1 - min(0.5, hgt / 400))
        shadow = pygame.Surface((int(sw * 2) + 2, int(sh * 2) + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (10, 6, 4, int(90 * (1 - min(0.6, hgt / 300)))), shadow.get_rect())
        surf.blit(shadow, (sx - sw + cam.S(12), sy - sh))

    # rim light del jugador cuando está activo
    active = f.state in (Fighter.ATTACK, Fighter.KIAI) and f.move is not None and f.move.startup <= f.timer < f.move.startup + f.move.active
    rim_a = 1.0 if active else f.rim
    if rim_a > 0.05 and not lut_bw:
        rc = f.player_color
        for a, b in (("sh_l", "el_l"), ("el_l", "hd_l"), ("sh_r", "el_r"), ("el_r", "hd_r"), ("hip", "kn_l"), ("kn_l", "ft_l"), ("hip", "kn_r"), ("kn_r", "ft_r"), ("hip", "chest")):
            draw_capsule(surf, mixc(rc, (255, 255, 255), 0.2), J[a], J[b], r_limb + cam.S(2.5))
        pygame.draw.circle(surf, rc, (int(J["head"][0]), int(J["head"][1])), int(cam.S(14 * sc) + cam.S(2.5)))

    # ----- piernas -----
    pants_c = {"gi": main, "abada": main, "changshan": trim, "tank": main, "hakama": main, "kurtka": trim}.get(outfit)
    for hipk, kn, ft, order in (("hip", "kn_l", "ft_l", 0), ("hip", "kn_r", "ft_r", 1)):
        depth = 0.86 if order == 0 else 1.0
        if outfit == "hakama":
            # falda pesada que barre el tatami
            hx, hy = J["hip"]
            kx, ky = J[kn]
            fx_, fy_ = J[ft]
            sway = math.sin(f.anim_t * 3) * cam.S(4)
            poly = [(hx - cam.S(22 * sc), hy - cam.S(6)), (hx + cam.S(22 * sc), hy - cam.S(6)),
                    (fx_ + cam.S(34 * sc) * (1 if order else -1) + sway, fy_ + cam.S(2)), (fx_ - cam.S(10 * sc) * (1 if order else -1) + sway, fy_ + cam.S(2))]
            pygame.draw.polygon(surf, col(main, WX(kn), depth), poly)
            pygame.draw.lines(surf, shade(col(main, WX(kn), depth), 1.35), False, [poly[0], poly[3]], max(1, int(cam.S(1.2))))
            draw_capsule(surf, col(skin, WX(ft), depth), (lerp(J[kn][0], J[ft][0], 0.7), lerp(J[kn][1], J[ft][1], 0.7)), J[ft], r_limb * 0.8)
            continue
        leg_c = col(skin, WX(kn), depth)
        if pants_c is not None:
            leg_c = col(pants_c, WX(kn), depth)
        thick = r_limb * (1.15 if outfit in ("gi", "hakama", "abada") else 1.0)
        draw_capsule(surf, leg_c, J[hipk], J[kn], thick)
        if outfit in ("shorts", "mawashi", "singlet"):
            # pantalón corto / mawashi: muslo de ropa, pantorrilla de piel
            mid = (lerp(J[hipk][0], J[kn][0], 0.5), lerp(J[hipk][1], J[kn][1], 0.5))
            c2 = main if outfit != "singlet" else main
            draw_capsule(surf, col(c2, WX(kn), depth), J[hipk], mid, thick * 1.1)
            draw_capsule(surf, col(skin, WX(ft), depth), J[kn], J[ft], r_limb * 0.9)
        else:
            draw_capsule(surf, leg_c, J[kn], J[ft], thick * 0.95)
        # pie
        fx_, fy_ = J[ft]
        foot_c = col(skin, WX(ft), depth)
        if outfit in ("kurtka", "singlet", "tank"):
            foot_c = col((40, 30, 24) if outfit != "tank" else (30, 30, 30), WX(ft), depth)
        pygame.draw.ellipse(surf, foot_c, (fx_ - cam.S(6), fy_ - cam.S(5), cam.S(20), cam.S(9)))
    # ----- torso -----
    hx, hy = J["hip"]
    cx, cy = J["chest"]
    slx, sly = J["sh_l"]
    srx, sry = J["sh_r"]
    wtor = cam.S(26 * sc * f.width)
    torso_poly = [(slx - cam.S(6) * fac, sly - cam.S(6)), (srx + cam.S(10) * fac, sry - cam.S(6)),
                  (hx + wtor * 0.75, hy + cam.S(8)), (hx - wtor * 0.75, hy + cam.S(8))]
    torso_c = {"gi": main, "kurtka": main, "changshan": main, "singlet": main, "tank": trim, "hakama": trim,
               "shorts": skin, "abada": skin, "mawashi": skin}[outfit]
    tc = col(torso_c, 0)
    pygame.draw.polygon(surf, tc, torso_poly)
    if outfit == "mawashi":
        belly = pygame.Rect(0, 0, int(wtor * 1.9), int(cam.S(80 * sc)))
        belly.center = (int(lerp(hx, cx, 0.4) + cam.S(4) * fac), int(lerp(hy, cy, 0.45)))
        pygame.draw.ellipse(surf, col(skin, 0), belly)
        # mawashi
        pygame.draw.rect(surf, col(main, 0), (hx - wtor * 0.9, hy - cam.S(12), wtor * 1.8, cam.S(26)))
        pygame.draw.rect(surf, col(main, 0, 1.2), (hx - cam.S(5), hy - cam.S(12), cam.S(10), cam.S(34)))
    if outfit in ("gi", "changshan", "kurtka", "hakama"):
        # solapa en V con desorden según daño
        d = f.dishevel * 10
        lap = [(cx + cam.S(4 + d) * fac, cy + cam.S(10)), (slx, sly - cam.S(4)), (srx, sry - cam.S(4))]
        pygame.draw.lines(surf, shade(tc, 0.72), False, [lap[1], lap[0], lap[2]], max(1, int(cam.S(2))))
        pygame.draw.line(surf, shade(tc, 1.2), (lerp(slx, cx, 0.3), sly), (hx - wtor * 0.1, hy), max(1, int(cam.S(1.5))))
        # cinturón (se afloja con el daño)
        belt_c = trim if outfit != "kurtka" else (40, 40, 40)
        if outfit != "hakama":
            pygame.draw.rect(surf, col(belt_c, 0), (hx - wtor * 0.8, hy - cam.S(4), wtor * 1.6, cam.S(7)))
            tail = cam.S(26 + 14 * f.dishevel) * sc
            sw = math.sin(f.anim_t * 4 + f.x * 0.01) * cam.S(5) + f.vx * cam.S(1.5)
            pygame.draw.line(surf, col(belt_c, 0), (hx, hy), (hx - cam.S(4) + sw, hy + tail), max(1, int(cam.S(3))))
            pygame.draw.line(surf, col(belt_c, 0), (hx, hy), (hx + cam.S(6) - sw * 0.6, hy + tail * 0.9), max(1, int(cam.S(3))))
        else:
            pygame.draw.rect(surf, col(main, 0), (hx - wtor * 0.85, hy - cam.S(6), wtor * 1.7, cam.S(10)))
    if outfit == "singlet":
        pygame.draw.polygon(surf, col(trim, 0), [(cx - cam.S(6) * fac, cy + cam.S(12)), (cx + cam.S(6) * fac, cy + cam.S(12)), (hx + cam.S(8) * fac, hy - cam.S(4)), (hx - cam.S(8) * fac, hy - cam.S(4))])
    if outfit == "abada":
        pygame.draw.rect(surf, col(trim, 0), (hx - wtor * 0.8, hy - cam.S(4), wtor * 1.6, cam.S(6)))
        pygame.draw.rect(surf, col(hex2rgb("#2E8B57"), 0), (hx - wtor * 0.8, hy + cam.S(2), wtor * 1.6, cam.S(3)))
    if outfit == "shorts":
        # mongkol/prajied: brazaletes
        pass
    if outfit == "tank":
        pygame.draw.line(surf, col(trim, 0), (slx, sly), (srx, sry), max(1, int(cam.S(3))))
    # tatuaje sak yant (Somchai) / pecas (Elsa) — detalle sutil
    if fd["id"] == "somchai" and fac == -1:
        pygame.draw.lines(surf, shade(tc, 0.6), False, [(cx, cy), (cx + cam.S(8), cy + cam.S(14)), (cx - cam.S(8), cy + cam.S(14)), (cx, cy)], 1)

    # ----- brazos -----
    sleeve = {"gi": 1.0, "kurtka": 1.0, "changshan": 1.0, "hakama": 1.0, "tank": 0.0, "singlet": 0.0, "shorts": 0.0, "abada": 0.0, "mawashi": 0.0}[outfit]
    if fd["id"] == "ines":
        sleeve = 0.75
    arm_c_cloth = main if outfit not in ("hakama",) else trim
    for sh, el, hd, depth in (("sh_l", "el_l", "hd_l", 0.86), ("sh_r", "el_r", "hd_r", 1.0)):
        upper_c = col(arm_c_cloth if sleeve > 0 else skin, WX(el), depth)
        draw_capsule(surf, upper_c, J[sh], J[el], r_arm * (1.2 if sleeve > 0 else 1.0))
        if sleeve >= 1.0:
            draw_capsule(surf, upper_c, J[el], J[hd], r_arm * 1.15)
        elif sleeve > 0:
            mid = (lerp(J[el][0], J[hd][0], sleeve - 0.2), lerp(J[el][1], J[hd][1], sleeve - 0.2))
            draw_capsule(surf, upper_c, J[el], mid, r_arm * 1.15)
            draw_capsule(surf, col(skin, WX(hd), depth), mid, J[hd], r_arm * 0.9)
        else:
            fore_c = col(skin, WX(hd), depth)
            if f.redness > 0.05 and not lut_bw:
                fore_c = mixc(fore_c, (200, 60, 50), 0.35 * f.redness)
            draw_capsule(surf, fore_c, J[el], J[hd], r_arm * 0.9)
        # mano / guantilla / prajied
        hxh, hyh = J[hd]
        hand_c = col(skin, WX(hd), depth)
        if fd["id"] == "matias":
            hand_c = (30, 30, 30)
        pygame.draw.circle(surf, hand_c, (int(hxh), int(hyh)), int(r_arm * 1.05))
        if fd["id"] == "somchai":
            ex, ey = J[el]
            pygame.draw.circle(surf, (250, 245, 230), (int(lerp(ex, J[sh][0], 0.3)), int(lerp(ey, J[sh][1], 0.3))), int(r_arm * 1.2), max(1, int(cam.S(2))))
    # ----- cabeza -----
    hdx, hdy = J["head"]
    rh = cam.S(13.5 * sc)
    head_c = col(skin, WX("head"))
    if f.redness > 0.1 and not lut_bw:
        head_c = mixc(head_c, (210, 90, 80), 0.18 * f.redness)
    pygame.draw.circle(surf, head_c, (int(hdx), int(hdy)), int(rh))
    # cuello
    pygame.draw.line(surf, col(skin, WX("head"), 0.9), (hdx, hdy + rh * 0.6), (cx, cy - cam.S(2)), max(1, int(cam.S(7 * sc))))
    # pelo
    hc = col(hair_c, WX("head"))
    style = fd["hair"]
    if style == "short":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.55)))
    elif style == "shaved":
        pygame.draw.arc(surf, mixc(head_c, hc, 0.35), (hdx - rh, hdy - rh, rh * 2, rh * 2), 0, math.pi, max(1, int(rh * 0.3)))
        # barba corta
        pygame.draw.arc(surf, mixc(head_c, hc, 0.4), (hdx - rh * 0.9, hdy - rh * 0.7, rh * 1.8, rh * 1.7), math.pi, 2 * math.pi, max(1, int(rh * 0.25)))
    elif style == "braid":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.5)))
        sw = math.sin(f.anim_t * 5) * cam.S(4) - f.vx * cam.S(1.2)
        pygame.draw.line(surf, hc, (hdx - rh * 0.6 * fac, hdy), (hdx - rh * 1.3 * fac + sw, hdy + rh * 2.6), max(2, int(rh * 0.35)))
    elif style == "ponytail":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.5)))
        sw = math.sin(f.anim_t * 6) * cam.S(6) - f.vx * cam.S(2.5)
        pts = [(hdx - rh * 0.5 * fac, hdy - rh * 0.7), (hdx - rh * 1.6 * fac + sw * 0.5, hdy - rh * 0.2), (hdx - rh * 2.4 * fac + sw, hdy + rh * 1.4)]
        pygame.draw.lines(surf, hc, False, pts, max(2, int(rh * 0.4)))
    elif style == "locs":
        for i in range(6):
            a = -math.pi * 0.1 + i * math.pi * 0.24
            sw = math.sin(f.anim_t * 7 + i) * cam.S(3)
            pygame.draw.line(surf, hc, (hdx + math.cos(a) * rh * 0.6, hdy - math.sin(a) * rh * 0.8),
                             (hdx + math.cos(a) * rh * 1.5 + sw, hdy - math.sin(a) * rh * 1.5 + rh * 0.6), max(2, int(rh * 0.25)))
    elif style == "chonmage":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.5)))
        pygame.draw.ellipse(surf, shade(hc, 1.6), (hdx - rh * 0.35, hdy - rh * 1.4, rh * 0.7, rh * 0.55))
    elif style == "bob":
        pygame.draw.arc(surf, hc, (hdx - rh * 1.08, hdy - rh * 1.1, rh * 2.16, rh * 2.3), 0, math.pi, max(2, int(rh * 0.7)))
        pygame.draw.rect(surf, hc, (hdx - rh * 1.08, hdy - rh * 0.2, rh * 0.45, rh * 1.1))
        pygame.draw.rect(surf, hc, (hdx + rh * 0.63, hdy - rh * 0.2, rh * 0.45, rh * 1.1))
    elif style == "curly":
        for i in range(7):
            a = i * math.pi / 6
            pygame.draw.circle(surf, hc, (int(hdx + math.cos(a) * rh * 0.8), int(hdy - math.sin(a) * rh * 0.9)), int(rh * 0.4))
    elif style == "white":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.4)))
        pygame.draw.circle(surf, hc, (int(hdx - rh * 0.3 * fac), int(hdy - rh * 1.1)), int(rh * 0.3))
        pygame.draw.arc(surf, mixc(head_c, hc, 0.5), (hdx - rh * 0.8, hdy - rh * 0.5, rh * 1.6, rh * 1.6), math.pi * 1.1, math.pi * 1.9, max(1, int(rh * 0.2)))
    # rostro: ojo y ceja (expresión por esfuerzo)
    ex = hdx + rh * 0.45 * fac
    ey = hdy - rh * 0.1
    eye_open = 1.0 if f.state not in (Fighter.HIT, Fighter.KNOCK, Fighter.KO, Fighter.STUN) else 0.3
    pygame.draw.ellipse(surf, (20, 14, 12), (ex - rh * 0.16, ey - rh * 0.12 * eye_open, rh * 0.32, rh * 0.24 * eye_open + 1))
    brow = -rh * 0.1 * f.breath - (rh * 0.12 if f.state in (Fighter.ATTACK, Fighter.KIAI) else 0)
    pygame.draw.line(surf, shade(head_c, 0.55), (ex - rh * 0.3, ey - rh * 0.32 + brow), (ex + rh * 0.3, ey - rh * 0.34 + brow * 0.5), max(1, int(cam.S(1.5))))
    # boca: jadeo si está agotado
    if f.exhausted or f.state in (Fighter.HIT, Fighter.KIAI):
        pygame.draw.ellipse(surf, (60, 30, 30), (hdx + rh * 0.4 * fac - rh * 0.12, hdy + rh * 0.4, rh * 0.24, rh * 0.22))
    if fd["id"] == "matias":
        pygame.draw.line(surf, shade(hc, 0.9), (hdx + rh * 0.15 * fac, hdy + rh * 0.38), (hdx + rh * 0.75 * fac, hdy + rh * 0.35), max(1, int(cam.S(1.6))))
    # sudor: brillo especular sobre piel
    if f.sweat > 0.05 and not lut_bw:
        glint = (255, 255, 250)
        a = int(140 * f.sweat)
        gl = pygame.Surface((int(rh * 2) + 4, int(rh * 2) + 4), pygame.SRCALPHA)
        pygame.draw.circle(gl, (*glint, a), (int(rh * 0.65), int(rh * 0.6)), max(1, int(rh * 0.28)))
        surf.blit(gl, (hdx - rh, hdy - rh))
        for sh, el, hd in (("sh_r", "el_r", "hd_r"),):
            if sleeve < 1.0:
                ax, ay = J[el]
                bx, by = J[hd]
                pygame.draw.line(gl, (*glint, a), (0, 0), (1, 1), 1)
                pygame.draw.line(surf, mixc(skin, glint, 0.6), (ax - cam.S(3), ay), (bx - cam.S(3), by), 1)
    # debug
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


def draw_portrait(surf, fd: dict, rect: pygame.Rect, rig: dict, selected=False, locked=False):
    """Retrato procedimental para la pantalla de selección: busto con luz lateral."""
    pal = [hex2rgb(c) for c in fd["pal"]]
    bg = pygame.Surface(rect.size)
    for i in range(rect.h):
        t = i / rect.h
        bg.fill(mixc(shade(pal[0], 0.25), (12, 10, 10), t), (0, i, rect.w, 1))
    cx, cy = rect.w // 2, int(rect.h * 0.62)
    r = int(rect.w * 0.19)
    skin = pal[2]
    if locked:
        skin = (40, 40, 42)
        pal = [(30, 30, 32)] * 4
    # hombros
    pygame.draw.ellipse(bg, shade(pal[0] if fd["outfit"] not in ("shorts", "abada", "mawashi") else skin, 0.9), (cx - r * 2.4, cy + r * 0.9, r * 4.8, r * 2.6))
    pygame.draw.polygon(bg, shade(pal[0] if fd["outfit"] not in ("shorts", "abada", "mawashi") else skin, 0.75), [(cx - r * 0.7, cy + r * 1.1), (cx + r * 0.7, cy + r * 1.1), (cx, cy + r * 2.2)])
    # luz lateral sobre la cabeza
    pygame.draw.circle(bg, shade(skin, 0.7), (cx, cy), r)
    hl = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(hl, (255, 230, 200, 110), (int(r * 0.7), int(r * 0.85)), int(r * 0.75))
    bg.blit(hl, (cx - r, cy - r))
    hc = pal[3]
    if fd["hair"] in ("short", "braid", "ponytail", "chonmage", "white", "shaved"):
        pygame.draw.arc(bg, hc if fd["hair"] != "shaved" else mixc(skin, hc, 0.4), (cx - r, cy - r * 1.05, r * 2, r * 2), 0, math.pi, max(2, int(r * 0.5)))
    elif fd["hair"] == "bob":
        pygame.draw.arc(bg, hc, (cx - r * 1.1, cy - r * 1.1, r * 2.2, r * 2.3), 0, math.pi, int(r * 0.7))
    elif fd["hair"] in ("locs", "curly"):
        for i in range(7):
            a = i * math.pi / 6
            pygame.draw.circle(bg, hc, (int(cx + math.cos(a) * r * 0.85), int(cy - math.sin(a) * r * 0.9)), int(r * 0.38))
    if not locked:
        pygame.draw.ellipse(bg, (20, 14, 12), (cx - r * 0.5, cy - r * 0.1, r * 0.3, r * 0.2))
        pygame.draw.ellipse(bg, (20, 14, 12), (cx + r * 0.2, cy - r * 0.1, r * 0.3, r * 0.2))
    surf.blit(bg, rect.topleft)
    pygame.draw.rect(surf, (255, 220, 160) if selected else (60, 50, 44), rect, 3 if selected else 1)


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

    def salt(self, x, y):
        for _ in range(int(60 * self.mul())):
            self.particles.append([x, y, random.uniform(-2.5, 2.5), random.uniform(-7, -3), 0, random.randint(40, 70),
                                   random.uniform(1.2, 2.4), (255, 250, 240), "salt"])

    def ink(self, x, y, facing, size=1.0):
        """Pincelada sumi-e: curva de puntos con grosor que decrece; se disuelve en el aire."""
        pts = []
        n = 14
        ang = random.uniform(-0.6, 0.6)
        dx = facing * random.choice([1, -1])
        for i in range(n):
            t = i / (n - 1)
            px = x + dx * t * 150 * size + math.sin(t * 5 + ang) * 18 * size
            py = y - t * 70 * size * math.sin(ang + 1) + math.cos(t * 7) * 10 * size
            pts.append((px, py, (1 - t) ** 1.3 * 16 * size + 1))
        self.inks.append(dict(pts=pts, life=0, max=random.randint(34, 50), drift=random.uniform(-0.3, 0.3)))
        # salpicaduras
        for _ in range(int(8 * size)):
            a = random.uniform(0, math.pi * 2)
            d = random.uniform(20, 90) * size
            self.inks.append(dict(pts=[(x + math.cos(a) * d, y + math.sin(a) * d * 0.6, random.uniform(2, 6) * size)], life=0, max=random.randint(26, 40), drift=0))

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
            pygame.draw.line(layer, (*s[6], a), (sx, sy), (ex, ey), 2)
        for r in self.rings:
            a = int(200 * (1 - r[4] / 20))
            sx, sy = cam.P(r[0], r[1])
            rr = max(2, int(cam.S(r[2])))
            pygame.draw.ellipse(layer, (*r[5], a), (sx - rr, sy - rr * 0.6, rr * 2, rr * 1.2), max(1, int(cam.S(3))))
        for k in self.inks:
            t = k["life"] / k["max"]
            a = int(235 * (1 - t) ** 1.5)
            for x, y, th in k["pts"]:
                sx, sy = cam.P(x, y)
                pygame.draw.circle(layer, (16, 12, 14, a), (int(sx), int(sy)), max(1, int(cam.S(th) * (1 + 0.3 * t))))

    def draw_screen(self, surf, fonts, reduce_flash=False):
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
            fl = pygame.Surface((W, H))
            fl.fill(self.flash_color)
            fl.set_alpha(int(self.flash * (90 if reduce_flash else 220)))
            surf.blit(fl, (0, 0))


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


def draw_kanji_do(surf, x, y, size, color=(20, 16, 18), fonts: Optional[Fonts] = None):
    """Dibuja 道 con fuente CJK si existe; si no, una caligrafía de trazos aproximada."""
    f = fonts.cjk(size) if fonts else None
    if f:
        img = f.render("道", True, color)
        surf.blit(img, (x - img.get_width() // 2, y - img.get_height() // 2))
        return
    s = size / 100.0
    def L(pts, w):
        pygame.draw.lines(surf, color, False, [(x + px * s, y + py * s) for px, py in pts], max(1, int(w * s)))
    # 首: dos puntos, techo largo y el bloque 自 con dos trazos interiores
    L([(-12, -47), (-5, -37)], 7); L([(14, -47), (7, -37)], 7)
    L([(-26, -31), (34, -31)], 8)
    L([(-12, -20), (24, -20), (24, 22), (-12, 22), (-12, -20)], 5)
    L([(-12, -6), (24, -6)], 3); L([(-12, 8), (24, 8)], 3)
    # 辶: punto, trazo quebrado y barrido final con gancho
    L([(-42, -30), (-34, -20)], 7)
    L([(-38, -8), (-30, 0), (-40, 14)], 6)
    L([(-46, 26), (-34, 40), (-16, 46), (40, 44), (46, 38)], 9)


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


# ─────────────────────────────────────────────────────────────────────────────
#  COMBATE
# ─────────────────────────────────────────────────────────────────────────────
class Match:
    INTRO, FIGHT, ROUND_END, REPLAY, MATCH_END = range(5)

    def __init__(self, f1: Fighter, f2: Fighter, audio: Audio, cfg: dict, controls="moderno", training=False, access=None):
        self.f = [f1, f2]
        self.audio = audio
        self.cfg = cfg
        self.controls = controls
        self.training = training
        self.access = access or {}
        self.fx = Effects(cfg)
        self.dojo = Dojo(cfg)
        self.cam = Camera()
        self.round = 0
        self.frame = 0
        self.timer = ROUND_TIME * 60
        self.state = Match.INTRO
        self.state_t = 0
        self.hitstop = 0
        self.slowmo = 1.0
        self.slowmo_t = 0
        self.shake_v = 0.0
        self.round_msg = ""
        self.round_result = None
        self.winner: Optional[Fighter] = None
        self.history: List[tuple] = []     # para la repetición del ippon
        self.replay_frames: List[tuple] = []
        self.replay_i = 0
        self.replay_angle = 0
        self.ground = None                  # fase de suelo
        self.kiai_active: Optional[Fighter] = None
        self.last_second_played = False
        self.intensity = 0.0
        self.debug = False
        self.frame_data_overlay = training
        self.start_round()

    # ----- flujo de asaltos -----
    def start_round(self):
        self.round += 1
        for f in self.f:
            f.reset_round(full=(self.round == 1))
        self.timer = ROUND_TIME * 60
        self.state = Match.INTRO
        self.state_t = 0
        self.dojo.set_round(self.round)
        self.audio.rain(self.dojo.target_rig["rain"])
        self.fx.popups.clear()
        self.history.clear()
        self.ground = None
        self.kiai_active = None
        self.cam.override = None
        self.last_second_played = False
        if self.round == 1:
            self.fx.popup(f"ASALTO {self.round}", (250, 230, 200), 54, H * 0.22, 150, sub=self.dojo.target_rig["name"])
        else:
            self.fx.popup(f"ASALTO {self.round}" if self.round < 4 else "DESEMPATE", (250, 230, 200), 54, H * 0.22, 150, sub=self.dojo.target_rig["name"])

    def kiai_allowed(self):
        return self.state == Match.FIGHT and self.kiai_active is None and self.ground is None

    def shake(self, v):
        if self.access.get("shake", True):
            self.shake_v = max(self.shake_v, v)

    def add_decal(self, x):
        self.dojo.add_decal(x)

    def tension(self):
        low = min(self.f[0].vit, self.f[1].vit) / VIT_MAX
        t = 0.0
        if low < 0.2:
            t += 0.5
        if self.timer < 600:
            t += 0.5
        return t if self.cfg.get("tilt") else 0.0

    # ----- actualización -----
    def update(self, keys, joys):
        self.frame += 1
        self.state_t += 1
        # IA
        for i, f in enumerate(self.f):
            if f.is_ai:
                f.think(self.f[1 - i], self)
            f.inp.poll(keys, joys)
        tscale = self.slowmo
        if self.slowmo_t > 0:
            self.slowmo_t -= 1
            if self.slowmo_t == 0:
                self.slowmo = 1.0
        # cámara
        if self.state == Match.REPLAY:
            self.update_replay()
        else:
            self.cam.follow(self.f[0], self.f[1], self.tension())
        self.cam.shake_x = random.uniform(-1, 1) * self.shake_v
        self.cam.shake_y = random.uniform(-1, 1) * self.shake_v * 0.6
        self.shake_v *= 0.85
        self.dojo.update(1 / 60, self.intensity)
        self.fx.update(tscale)
        self.intensity = lerp(self.intensity, 0.0, 0.01)

        if self.state == Match.INTRO:
            if self.state_t == 20:
                self.audio.play("taiko", 0.8)
            if self.state_t == 60:
                for f in self.f:
                    if f.fd["id"] == "kanoa":
                        self.fx.salt(f.x, -150)
                        self.audio.play("stomp", 1.0)
                        self.shake(6)
                        self.dojo.react(1.0)
                    if f.fd["id"] == "ines":
                        self.audio.play("kiai", 0.8, pan=f.x / 600)
            if self.state_t == 150:
                self.fx.popup("HAJIME!", (255, 236, 200), 72, H * 0.3, 70)
                self.audio.play("taiko", 1.0)
                self.audio.play("gong", 0.4)
            if self.state_t >= 160:
                self.state = Match.FIGHT
                self.state_t = 0
                for f in self.f:
                    f.state = Fighter.IDLE
            return

        if self.state == Match.FIGHT:
            if self.hitstop > 0:
                self.hitstop -= 1
                return
            # slow-mo: avanzamos la simulación sólo algunos frames
            if tscale < 1.0 and (self.frame % max(1, int(round(1 / tscale)))) != 0:
                return
            if self.ground is not None:
                self.update_ground()
                return
            if not self.training:
                self.timer -= 1
            if self.timer <= 60 and not self.last_second_played:
                self.last_second_played = True
            if self.timer in (180, 120, 60):
                self.audio.play("tick", 0.8)
            a, b = self.f
            a.update(b, self)
            b.update(a, self)
            self.resolve_collisions()
            self.resolve_hits()
            self.check_jogai()
            self.record_history()
            if self.training:
                for f in self.f:
                    if f.state in (Fighter.IDLE, Fighter.WALK) and f.vit < VIT_MAX:
                        f.vit = min(VIT_MAX, f.vit + 4)
                        f.stam = STAM_MAX
                        f.kiai = max(f.kiai, 60)
            self.check_round_end()
            return

        if self.state == Match.ROUND_END:
            for f in self.f:
                f.anim_t += 1 / 60
                if f.state == Fighter.KNOCK and f.y < 0:
                    f.vy += GRAVITY
                    f.y = min(0.0, f.y + f.vy)
            if self.state_t == 1:
                self.dojo.react(2.0)
            if self.state_t >= 170:
                if self.round_result == "ippon" and self.replay_frames:
                    self.state = Match.REPLAY
                    self.state_t = 0
                    self.replay_i = 0
                    self.replay_angle = 0
                    self.fx.popup("REPETICIÓN", (230, 230, 230), 30, H * 0.1, 400)
                else:
                    self.after_round()
            return

        if self.state == Match.REPLAY:
            if self.state_t > 60 * 6 or self.replay_i >= len(self.replay_frames) * 3 - 1:
                self.after_round()
            return

        if self.state == Match.MATCH_END:
            for f in self.f:
                f.anim_t += 1 / 60
            return

    def after_round(self):
        winner = self.winner
        if winner is not None:
            winner.wins += 1
        if winner is not None and winner.wins >= 2:
            self.state = Match.MATCH_END
            self.state_t = 0
            winner.state = Fighter.WIN
            loser = self.f[1 - self.f.index(winner)]
            loser.state = Fighter.KO
            self.cam.override = None
            self.audio.play("applause", 0.9)
            self.dojo.applaud = 3.0
        else:
            self.start_round()

    # ----- colisiones de cuerpos -----
    def resolve_collisions(self):
        a, b = self.f
        if a.state in (Fighter.THROWN, Fighter.THROWING, Fighter.KIAI) or b.state in (Fighter.THROWN, Fighter.THROWING, Fighter.KIAI):
            return
        if a.state in (Fighter.KNOCK, Fighter.KO) or b.state in (Fighter.KNOCK, Fighter.KO):
            return
        min_d = 30 * (a.width + b.width)
        d = b.x - a.x
        if abs(d) < min_d and abs(a.y - b.y) < 120:
            push = (min_d - abs(d)) / 2
            s = 1 if d >= 0 else -1
            a.x -= push * s
            b.x += push * s

    # ----- resolución del triángulo -----
    def resolve_hits(self):
        for i in (0, 1):
            att = self.f[i]
            df = self.f[1 - i]
            hb = att.hitbox()
            if hb is None or att.hit_done or att.move is None:
                continue
            if df.state in (Fighter.KNOCK, Fighter.KO, Fighter.THROWN, Fighter.GETUP, Fighter.GROUND, Fighter.KIAI):
                continue
            if not hb.colliderect(df.hurtbox()):
                continue
            m = att.move
            att.hit_done = True
            pan = df.x / 600
            if m.kind == "grab":
                # GOLPE gana a AGARRE: si el rival está en frames activos de golpe, el agarre falla
                if df.state == Fighter.ATTACK and df.move and df.move.kind == "strike":
                    att.state = Fighter.STUN
                    att.stun_len = 16
                    att.timer = 0
                    continue
                if df.y < -5:
                    continue
                # AGARRE rompe BLOQUEO (y agarra también en guardia baja)
                self.start_throw(att, df)
                continue
            # golpe
            blocked = df.state == Fighter.BLOCK and df.blocking
            if blocked and m.height == "low" and not df.crouching:
                blocked = False
            if blocked and m.height == "high" and df.crouching:
                blocked = False
            if blocked:
                # parada perfecta: bloqueo iniciado en los últimos 4 frames
                if df.block_timer <= 4 or (df.fd["id"] == "okabe" and df.block_timer <= 7):
                    self.perfect_parry(df, att, m)
                    continue
                df.on_block(m, att, self)
                self.audio.play("block", 0.8, pan=pan)
                self.fx.spark(df.x + df.facing * 20, m.reach_y if m.height != "low" else -25, 4, (200, 200, 220))
                self.hitstop = 2
                continue
            dmg = df.on_hit(m, att, self)
            self.intensity = min(1.0, self.intensity + 0.15)
            self.hitstop = m.hitstop
            self.shake(m.hitstop * 0.8)
            self.audio.play("hit_l" if m.damage < 70 else "hit_m" if m.damage < 120 else "hit_h", 1.0, pan=pan)
            y = df.y + (m.reach_y if m.height != "low" else -25)
            self.fx.spark(df.x, y, 8 + m.hitstop, (255, 230, 190))
            self.fx.sweat(df.x, y, 5 + m.hitstop, df.facing)
            if m.hitstop >= 7:
                self.fx.ring(df.x, y, 90 + m.hitstop * 6)
                self.dojo.react(0.6)
                self.audio.play("gasp", 0.4)
            if att.move_key == "special":
                self.fx.ink(df.x, y, att.facing, 0.8)
            att.rim = 1.0
            if df.vit <= 0:
                self.end_round(att, "ko")

    def perfect_parry(self, df: Fighter, att: Fighter, m: Move):
        self.hitstop = 8
        self.fx.do_flash(1.0, (255, 250, 235))
        self.audio.play("parry", 1.0, pan=df.x / 600)
        self.fx.ring(df.x + df.facing * 30, -120, 160, (255, 255, 240))
        self.fx.popup("PARADA PERFECTA", (255, 250, 220), 36, H * 0.42, 60)
        df.kiai = min(KIAI_MAX, df.kiai + 18)
        df.state = Fighter.PARRY
        df.timer = 0
        df.stam = min(STAM_MAX, df.stam + 10)
        att.state = Fighter.STUN
        att.stun_len = 26
        att.timer = 0
        att.move = None
        att.move_key = ""
        self.intensity = min(1.0, self.intensity + 0.3)
        if df.fd["id"] == "okabe":
            # Kokyū Nage pasivo: el rival vuela con su propia inercia
            self.dojo.single_beam = 1.0
            self.slowmo = 0.35
            self.slowmo_t = 50
            self.start_throw(df, att, kokyu=True)

    def start_throw(self, att: Fighter, df: Fighter, kokyu=False):
        att.state = Fighter.THROWING
        att.timer = 0
        att.throw_partner = df
        att.throw_phase = 0.0
        att.move = None
        att.move_key = ""
        df.state = Fighter.THROWN
        df.throw_partner = att
        df.blocking = False
        df.move = None
        df.move_key = ""
        df.timer = 0
        self.audio.play("grab", 0.9, pan=df.x / 600)
        att.rim = 1.0
        att.kiai = min(KIAI_MAX, att.kiai + 10)
        self.intensity = min(1.0, self.intensity + 0.25)
        if not kokyu:
            self.slowmo = 0.5
            self.slowmo_t = 30

    def on_throw_land(self, att: Fighter, df: Fighter):
        base = att.moves["grab"].damage
        dmg = base
        T = att.fd["T"]
        clean = random.random() < 0.08 + 0.07 * T or att.fd["id"] == "okabe"
        pan = df.x / 600
        self.fx.dust(df.x, 0, 18, 1.3)
        self.add_decal(df.x)
        self.audio.play("fall", 1.0, pan=pan)
        self.shake(8)
        self.dojo.react(1.0)
        self.fx.ring(df.x, -20, 160, (230, 210, 180))
        if clean and att.fd["grappler"]:
            df.wazaari_received = True
            att.wazaari += 1
            dmg = int(base * 1.35)
            if att.wazaari >= 2:
                df.take_damage(dmg, att.fd["id"])
                self.end_round(att, "ippon")
                return
            self.fx.popup("WAZA-ARI", (255, 215, 150), 58, H * 0.28, 80, sub=f"{att.fd['name']} · proyección limpia")
            self.audio.play("taiko", 1.0)
            # ippon directo si el rival ya estaba agotado
            if df.exhausted and random.random() < 0.3:
                df.take_damage(dmg, att.fd["id"])
                self.end_round(att, "ippon")
                return
        df.take_damage(dmg, att.fd["id"])
        if df.vit <= 0:
            self.end_round(att, "ko")
            return
        # fase de suelo para los grapplers de suelo
        if att.fd["ground"] and df.vit > 0 and random.random() < 0.7:
            self.begin_ground(att, df)

    # ----- fase de suelo -----
    def begin_ground(self, att: Fighter, df: Fighter):
        self.ground = dict(att=att, df=df, t=0, tension=0.0, dur=150)
        att.state = Fighter.GROUND
        df.state = Fighter.GROUND
        att.ground_attacker = True
        df.ground_attacker = False
        att.mash = 0
        df.mash = 0
        df.x = att.x + att.facing * 60
        df.y = 0
        self.cam.override = ((att.x + df.x) / 2, 1.45)
        self.fx.popup("SUMISIÓN", (255, 200, 170), 44, H * 0.18, 60, sub="¡Pulsa AGARRE / BLOQUEO con ritmo!")

    def update_ground(self):
        g = self.ground
        g["t"] += 1
        att, df = g["att"], g["df"]
        if att.inp.pressed["grab"]:
            att.mash += 1
            g["tension"] += 0.06 + 0.012 * att.fd["T"]
        if df.inp.pressed["block"]:
            df.mash += 1
            g["tension"] -= 0.05 + 0.012 * df.fd["D"]
        g["tension"] = clamp(g["tension"], -1, 1)
        if g["t"] % 20 == 0:
            self.audio.play("heart", 0.6)
        if g["t"] >= g["dur"] or abs(g["tension"]) >= 1:
            self.cam.override = None
            if g["tension"] > 0.15:
                dmg = int(120 + 25 * att.fd["T"] + 120 * g["tension"])
                df.take_damage(dmg, att.fd["id"])
                self.fx.popup("¡SUMISIÓN!", (255, 190, 150), 56, H * 0.28, 80, sub=att.fd["kiai"] if att.fd["kiai_kind"] == "groundlock" else "Control de suelo")
                self.audio.play("hit_h", 1.0)
                self.audio.play("gasp", 0.8)
                df.state = Fighter.KNOCK
                df.timer = 0
                att.state = Fighter.IDLE
                att.kiai = min(KIAI_MAX, att.kiai + 20)
                if df.vit <= 0:
                    self.end_round(att, "ko")
            else:
                self.fx.popup("ESCAPA", (200, 220, 255), 44, H * 0.28, 60)
                df.state = Fighter.GETUP
                df.timer = 0
                att.state = Fighter.IDLE
                att.stam = max(0.0, att.stam - 25)
                df.kiai = min(KIAI_MAX, df.kiai + 12)
            self.ground = None

    # ----- jogai -----
    def check_jogai(self):
        for i, f in enumerate(self.f):
            if abs(f.x) > TATAMI_HALF + 25 and f.y >= 0 and f.state in (Fighter.IDLE, Fighter.WALK, Fighter.HIT, Fighter.GETUP, Fighter.KNOCK, Fighter.BLOCK, Fighter.CROUCH):
                if f.state == Fighter.KNOCK and f.timer < 38:
                    continue
                f.jogai += 1
                self.audio.play("taiko_hi", 0.9)
                if f.jogai >= 3:
                    self.fx.popup("JOGAI × 3", (255, 170, 150), 56, H * 0.28, 90, sub=f"{f.fd['name']} pierde el asalto")
                    self.end_round(self.f[1 - i], "jogai")
                    return
                self.fx.popup("JOGAI", (255, 190, 170), 48, H * 0.28, 70, sub=f"{f.fd['name']} · {f.jogai}/3")
                # reposicionar a ambos
                s = -1 if f.x > 0 else 1
                f.x = s * -(TATAMI_HALF - 110)
                o = self.f[1 - i]
                o.x = f.x + s * 180
                for g in self.f:
                    g.state = Fighter.IDLE
                    g.vx = 0
                    g.move = None

    # ----- fin de asalto -----
    def check_round_end(self):
        if self.state != Match.FIGHT or self.training:
            return
        if self.timer <= 0:
            a, b = self.f
            if a.vit != b.vit:
                w = a if a.vit > b.vit else b
            elif a.wazaari != b.wazaari:
                w = a if a.wazaari > b.wazaari else b
            else:
                w = None
            self.end_round(w, "time")

    def end_round(self, winner: Optional[Fighter], how: str):
        if self.state != Match.FIGHT:
            return
        self.state = Match.ROUND_END
        self.state_t = 0
        self.winner = winner
        self.round_result = how
        self.ground = None
        self.cam.override = None
        self.slowmo = 1.0
        self.slowmo_t = 0
        loser = None if winner is None else self.f[1 - self.f.index(winner)]
        if how == "ko":
            self.fx.popup("K.O.", (255, 220, 190), 96, H * 0.26, 170, sub=f"{winner.fd['name']} gana el asalto")
            self.audio.play("gong", 0.9)
            self.audio.play("gasp", 0.9)
            self.slowmo = 0.25
            self.slowmo_t = 40
            if loser:
                loser.state = Fighter.KNOCK
                loser.timer = 0
                loser.vy = -8
                loser.y = -1
        elif how == "ippon":
            self.fx.popup("IPPON", (255, 230, 170), 110, H * 0.24, 170, sub=f"{winner.fd['name']} · proyección perfecta")
            self.audio.play("gong", 1.0)
            self.audio.play("applause", 0.8)
            self.dojo.applaud = 3.0
            self.replay_frames = list(self.history[-100:])
            if loser:
                loser.state = Fighter.KNOCK
                loser.timer = 0
        elif how == "jogai":
            self.audio.play("gong", 0.7)
        elif how == "time":
            self.fx.popup("TIEMPO", (230, 230, 230), 84, H * 0.26, 170, sub=("Asalto para " + winner.fd["name"]) if winner else "Asalto nulo")
            self.audio.play("gong", 0.8)
        if winner is not None:
            winner.rim = 1.0

    # ----- Kiai Final: cinemática -----
    def begin_kiai(self, att: Fighter, df: Fighter):
        self.kiai_active = att
        self.audio.play("kiai", 1.0, pan=att.x / 600)
        self.fx.popup(att.fd["kiai"].upper(), (255, 235, 200), 44, H * 0.14, 110, sub="Kiai Final")
        self.dojo.single_beam = 1.0

    def kiai_whiff(self, att: Fighter):
        self.kiai_active = None
        self.fx.popup("¡AL AIRE!", (220, 220, 220), 36, H * 0.4, 50)

    def kiai_connect(self, att: Fighter, df: Fighter):
        self.slowmo = 0.5
        self.slowmo_t = 120
        self.cam.override = ((att.x + df.x) / 2, 1.35)
        self.fx.do_flash(0.6, (255, 240, 220))
        self.intensity = 1.0

    def kiai_impact(self, att: Fighter, df: Fighter, dmg: int, last=False):
        y = -120
        pan = df.x / 600
        self.audio.play("hit_h" if last else "hit_m", 1.0, pan=pan)
        self.fx.ink(df.x, y, att.facing, 1.2 if last else 0.7)
        self.fx.spark(df.x, y, 16, (255, 240, 200))
        self.fx.sweat(df.x, y, 12, df.facing)
        self.hitstop = 10 if last else 3
        self.shake(10 if last else 4)
        self.dojo.react(0.8)
        if last:
            self.fx.ring(df.x, y, 220)
            self.fx.dust(df.x, 0, 26, 1.6)
            if att.fd["kiai_kind"] == "barrage6":
                self.fx.enso(att.x - att.facing * 40, -120)
            self.audio.play("gasp", 0.9)
            self.audio.play("kiai", 0.9, pan=att.x / 600)

    def end_kiai(self, att: Fighter, df: Fighter, kind: str):
        self.kiai_active = None
        self.cam.override = None
        self.slowmo = 1.0
        self.slowmo_t = 0
        self.add_decal(df.x)
        if kind in ("throw", "kokyu", "tackle") and att.fd["grappler"]:
            att.wazaari += 1
            if att.wazaari >= 2:
                self.end_round(att, "ippon")
                return
            self.fx.popup("WAZA-ARI", (255, 215, 150), 52, H * 0.3, 70)
        if df.vit <= 0:
            self.end_round(att, "ko")

    # ----- repetición -----
    def record_history(self):
        snap = tuple((f.x, f.y, f.facing, f.state, f.timer, f.anim_t, f.vy, f.throw_phase, f.kiai_idx, f.stun_len) for f in self.f)
        self.history.append(snap)
        if len(self.history) > 160:
            self.history.pop(0)

    def apply_snapshot(self, snap):
        for f, s in zip(self.f, snap):
            f.x, f.y, f.facing, f.state, f.timer, f.anim_t, f.vy, f.throw_phase, f.kiai_idx, f.stun_len = s

    def update_replay(self):
        n = len(self.replay_frames)
        if n == 0:
            return
        step = self.state_t // 3   # 1/3 de velocidad
        self.replay_i = step
        idx = step % n
        angle = step // n
        if angle != self.replay_angle:
            self.replay_angle = angle
        self.apply_snapshot(self.replay_frames[idx])
        a, b = self.f
        zooms = (1.0, 1.5, 1.25)
        self.cam.override = ((a.x + b.x) / 2 + (0, -60, 60)[angle % 3], zooms[angle % 3])
        self.cam.follow(a, b, 0)

    # ----- dibujo -----
    def draw(self, surf, fonts: Fonts, layer, photo=None):
        rig = self.dojo.rig
        lut = photo["lut"] if photo else "Natural"
        bw = lut in ("Kurosawa B/N", "Daido Moriyama")
        self.dojo.draw(surf, self.cam, fonts, bw)
        layer.fill((0, 0, 0, 0))
        self.dojo.draw_beams(surf, self.cam, layer)
        # los luchadores se dibujan de atrás hacia delante: el que ataca, delante
        order = sorted(self.f, key=lambda f: (f.state in (Fighter.ATTACK, Fighter.KIAI, Fighter.THROWING), -abs(f.y)))
        for f in order:
            draw_fighter(surf, f, self.cam, rig, self.cfg, self.debug, bw)
        self.fx.draw_world(surf, self.cam, layer)
        surf.blit(layer, (0, 0))
        self.dojo.draw_rain(surf)
        # tinte global de la iluminación
        tint_s = pygame.Surface((W, H))
        tint_s.fill(rig["tint"])
        surf.blit(tint_s, (0, 0), special_flags=pygame.BLEND_MULT)
        # haz único: oscurecer todo salvo el centro
        if self.dojo.single_beam > 0.05:
            dark = pygame.Surface((W, H), pygame.SRCALPHA)
            dark.fill((0, 0, 0, int(150 * self.dojo.single_beam)))
            cx = W // 2
            pygame.draw.polygon(dark, (0, 0, 0, 0), [(cx - 80, -10), (cx + 80, -10), (cx + 300, FLOOR_Y + 60), (cx - 300, FLOOR_Y + 60)])
            surf.blit(dark, (0, 0))
        surf.blit(self.dojo.vignette(rig["vignette"] + (60 if self.ground else 0)), (0, 0))

    def draw_hud(self, surf, fonts: Fonts):
        if self.state == Match.REPLAY:
            return
        a, b = self.f
        bar_w = 470
        y = 28
        for i, f in enumerate(self.f):
            x = 40 if i == 0 else W - 40 - bar_w
            # vitalidad
            pygame.draw.rect(surf, (20, 14, 12), (x - 2, y - 2, bar_w + 4, 22))
            frac = f.vit / VIT_MAX
            col = (236, 220, 190) if frac > 0.4 else (240, 160, 110) if frac > 0.15 else (230, 80, 70)
            w = int(bar_w * frac)
            rect = (x if i == 0 else x + bar_w - w, y, w, 18)
            pygame.draw.rect(surf, col, rect)
            pygame.draw.rect(surf, f.player_color, (x - 2, y - 2, bar_w + 4, 22), 2)
            # aliento
            sw = int((bar_w - 60) * f.stam / STAM_MAX)
            sc = (120, 200, 230) if not f.exhausted else (220, 90, 70)
            pygame.draw.rect(surf, (20, 14, 12), (x if i == 0 else x + 60, y + 26, bar_w - 60, 6))
            pygame.draw.rect(surf, sc, (x if i == 0 else x + bar_w - sw, y + 26, sw, 6))
            # nombre y estilo
            name = fonts.get(26, serif=True).render(f.fd["name"].upper(), True, (245, 238, 225))
            style = fonts.get(18).render(f.fd["style"], True, (200, 190, 175))
            if i == 0:
                surf.blit(name, (x, y + 38))
                surf.blit(style, (x, y + 64))
            else:
                surf.blit(name, (x + bar_w - name.get_width(), y + 38))
                surf.blit(style, (x + bar_w - style.get_width(), y + 64))
            # asaltos ganados, waza-ari y jogai
            for k in range(2):
                cx = (x + 10 + k * 22) if i == 0 else (x + bar_w - 10 - k * 22)
                pygame.draw.circle(surf, (255, 220, 150) if f.wins > k else (60, 50, 44), (cx, y + 96), 7)
                pygame.draw.circle(surf, (120, 100, 80), (cx, y + 96), 7, 1)
            for k in range(f.wazaari):
                cx = (x + 70 + k * 18) if i == 0 else (x + bar_w - 70 - k * 18)
                pygame.draw.polygon(surf, (255, 200, 120), [(cx, y + 88), (cx + 7, y + 96), (cx, y + 104), (cx - 7, y + 96)])
            for k in range(f.jogai):
                cx = (x + 130 + k * 14) if i == 0 else (x + bar_w - 130 - k * 14)
                pygame.draw.rect(surf, (230, 90, 80), (cx - 4, y + 92, 8, 8))
            # kiai (abajo)
            kw = 300
            kx = 40 if i == 0 else W - 40 - kw
            ky = H - 46
            pygame.draw.rect(surf, (20, 14, 12), (kx - 2, ky - 2, kw + 4, 14))
            kf = f.kiai / KIAI_MAX
            kc = (255, 200, 90) if kf >= 1 else (190, 140, 70)
            if kf >= 1 and (self.frame // 8) % 2 == 0:
                kc = (255, 240, 180)
            kwid = int(kw * kf)
            pygame.draw.rect(surf, kc, (kx if i == 0 else kx + kw - kwid, ky, kwid, 10))
            lab = fonts.get(16).render("KIAI" + ("  ·  ¡LISTO! (L / Num3)" if kf >= 1 else ""), True, (210, 190, 160))
            surf.blit(lab, (kx if i == 0 else kx + kw - lab.get_width(), ky - 20))
        # temporizador
        t = max(0, self.timer // 60) if not self.training else 0
        tc = (245, 235, 220) if t > 10 else (240, 110, 90)
        timg = fonts.get(56, serif=True).render(f"{t:02d}" if not self.training else "∞", True, tc)
        surf.blit(timg, (W // 2 - timg.get_width() // 2, 18))
        rimg = fonts.get(18).render(f"ASALTO {self.round}" if self.round < 4 else "DESEMPATE", True, (200, 190, 175))
        surf.blit(rimg, (W // 2 - rimg.get_width() // 2, 74))
        # fase de suelo: barra de tensión
        if self.ground:
            g = self.ground
            gx, gw = W // 2 - 200, 400
            pygame.draw.rect(surf, (20, 14, 12), (gx, H // 2 + 150, gw, 16))
            mid = gx + gw // 2
            t = g["tension"]
            if t >= 0:
                pygame.draw.rect(surf, g["att"].player_color, (mid, H // 2 + 150, int(t * gw / 2), 16))
            else:
                pygame.draw.rect(surf, g["df"].player_color, (mid + int(t * gw / 2), H // 2 + 150, int(-t * gw / 2), 16))
            pygame.draw.line(surf, (255, 255, 255), (mid, H // 2 + 146), (mid, H // 2 + 170), 2)
        # datos de frames (entrenamiento)
        if self.frame_data_overlay:
            yy = 120
            for f in self.f:
                st = ["IDLE", "WALK", "CROUCH", "JUMP", "ATTACK", "BLOCK", "HIT", "KNOCK", "GETUP", "THROWN", "THROWING", "KO", "WIN", "INTRO", "KIAI", "GROUND", "PARRY", "STUN"][f.state]
                line = f"{f.fd['id']}: {st}"
                if f.move:
                    m = f.move
                    ph = "startup" if f.timer < m.startup else "ACTIVO" if f.timer < m.startup + m.active else "recovery"
                    line += f"  {m.name}  s{m.startup}/a{m.active}/r{m.recovery}  [{ph} f{f.timer}]  dmg {m.damage}"
                img = fonts.get(18).render(line, True, (255, 240, 200))
                surf.blit(img, (40, yy))
                yy += 20
            hint = fonts.get(16).render("F1 hitboxes · F2 datos de frames · Esc menú", True, (180, 170, 160))
            surf.blit(hint, (40, yy))


# ─────────────────────────────────────────────────────────────────────────────
#  POSPROCESADO (grano, LUTs del Modo Foto, filtros de accesibilidad)
# ─────────────────────────────────────────────────────────────────────────────
def apply_lut(surf, lut: str, dojo: Dojo, grain_on=True, contrast_access=False, daltonism="off"):
    if lut == "Caravaggio":
        m = pygame.Surface((W, H)); m.fill((255, 215, 170)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((40, 25, 15)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        surf.blit(dojo.vignette(255), (0, 0))
    elif lut == "Hammershøi":
        g = pygame.transform.grayscale(surf) if hasattr(pygame.transform, "grayscale") else surf.copy()
        g.set_alpha(140)
        surf.blit(g, (0, 0))
        m = pygame.Surface((W, H)); m.fill((215, 225, 240)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((18, 18, 22)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_ADD)
    elif lut == "Kurosawa B/N":
        g = pygame.transform.grayscale(surf) if hasattr(pygame.transform, "grayscale") else surf
        surf.blit(g, (0, 0))
        m = pygame.Surface((W, H)); m.fill((30, 30, 30)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        m.fill((235, 235, 235)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((255, 255, 255)); m2 = surf.copy(); m2.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((160, 160, 160)); m2.blit(m, (0, 0), special_flags=pygame.BLEND_MULT); surf.blit(m2, (0, 0), special_flags=pygame.BLEND_ADD)
    elif lut == "Daido Moriyama":
        g = pygame.transform.grayscale(surf) if hasattr(pygame.transform, "grayscale") else surf
        surf.blit(g, (0, 0))
        m = pygame.Surface((W, H)); m.fill((45, 45, 45)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        g2 = surf.copy(); surf.blit(g2, (0, 0), special_flags=pygame.BLEND_ADD)
        if dojo.grain:
            for _ in range(3):
                g1, g2 = random.sample(dojo.grain, 2)
                surf.blit(g1, (0, 0), special_flags=pygame.BLEND_ADD)
                surf.blit(g2, (0, 0), special_flags=pygame.BLEND_SUB)
    if grain_on and dojo.grain and lut != "Daido Moriyama":
        g1, g2 = random.sample(dojo.grain, 2)
        surf.blit(g1, (0, 0), special_flags=pygame.BLEND_ADD)
        surf.blit(g2, (0, 0), special_flags=pygame.BLEND_SUB)
    if contrast_access:
        m = pygame.Surface((W, H)); m.fill((25, 25, 25)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        c = surf.copy(); m.fill((70, 70, 70)); c.blit(m, (0, 0), special_flags=pygame.BLEND_MULT); surf.blit(c, (0, 0), special_flags=pygame.BLEND_ADD)
    if daltonism != "off":
        # filtro sencillo: desplaza los canales para separar rojo/verde
        m = pygame.Surface((W, H))
        m.fill({"protanopia": (255, 230, 255), "deuteranopia": (230, 255, 255), "tritanopia": (255, 255, 220)}.get(daltonism, (255, 255, 255)))
        surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)


def bloom(surf, strength=0.5):
    """Bloom barato: reducimos, umbralizamos, ampliamos y sumamos."""
    small = pygame.transform.smoothscale(surf, (W // 6, H // 6))
    thr = pygame.Surface(small.get_size())
    thr.fill((150, 150, 150))
    small.blit(thr, (0, 0), special_flags=pygame.BLEND_SUB)
    if hasattr(pygame.transform, "box_blur"):
        small = pygame.transform.box_blur(small, 3)
    k = pygame.Surface(small.get_size())
    k.fill((int(255 * strength),) * 3)
    small.blit(k, (0, 0), special_flags=pygame.BLEND_MULT)
    big = pygame.transform.smoothscale(small, (W, H))
    surf.blit(big, (0, 0), special_flags=pygame.BLEND_ADD)


# ─────────────────────────────────────────────────────────────────────────────
#  JUEGO: escenas, menús, modos
# ─────────────────────────────────────────────────────────────────────────────
MENU_ITEMS = [("Versus local", "versus"), ("Versus contra la IA", "versus_ai"), ("Arcade", "arcade"), ("Torneo", "torneo"),
              ("Entrenamiento", "training"), ("Espectador (IA vs IA)", "spectate"), ("Opciones", "options"), ("Salir", "quit")]


class Game:
    def __init__(self, args):
        pygame.init()
        self.save = load_save()
        preset = args.get("preset") or self.save.get("preset", "alto")
        if preset not in PRESETS:
            preset = "alto"
        self.preset = preset
        self.cfg = dict(PRESETS[preset])
        flags = pygame.SCALED | (0 if args.get("windowed") else pygame.FULLSCREEN) if not args.get("headless") else 0
        try:
            self.screen = pygame.display.set_mode((W, H), flags, vsync=1)
        except Exception:
            self.screen = pygame.display.set_mode((W, H))
        pygame.display.set_caption(TITLE)
        self.audio = Audio(self.save.get("volume", 0.8))
        self.fonts = Fonts()
        self.clock = pygame.time.Clock()
        self.layer = pygame.Surface((W, H), pygame.SRCALPHA)
        self.world = pygame.Surface((W, H))
        self.joys = []
        try:
            pygame.joystick.init()
            self.joys = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
            for j in self.joys:
                j.init()
        except Exception:
            pass
        self.p1 = PlayerInput(KEYMAP_P1, 0 if self.joys else None)
        self.p2 = PlayerInput(KEYMAP_P2, 1 if len(self.joys) > 1 else None)
        self.scene = "title"
        self.scene_t = 0
        self.menu_i = 0
        self.mode = "versus"
        self.sel = [0, 1]
        self.sel_locked = [False, False]
        self.match: Optional[Match] = None
        self.photo = None
        self.paused = False
        self.pause_i = 0
        self.options_i = 0
        self.debug = args.get("debug", False)
        self.ai_vs_ai = args.get("ai_vs_ai", False)
        self.arcade_queue: List[str] = []
        self.arcade_idx = 0
        self.arcade_lost_round = False
        self.tourney: List[str] = []
        self.results_t = 0
        self.title_dojo = Dojo(self.cfg)
        self.title_cam = Camera()
        self.title_cam.zoom = 0.9
        self.bg_fighters = []
        self.last_access = dict(self.save.get("access", {}))
        self.quit = False
        self.frame = 0
        self.headless = args.get("headless", False)
        self.max_frames = args.get("max_frames", 0)
        if args.get("photo_mode"):
            self.setup_match("versus_ai", "haruki", "ines")
            self.scene = "fight"
            self.enter_photo()
        if self.ai_vs_ai:
            self.setup_match("spectate", random.choice(FIGHTERS)["id"], random.choice(FIGHTERS)["id"])
            self.scene = "fight"

    # ----- utilidades -----
    def access(self):
        return self.save.get("access", {})

    def available_fighters(self):
        return [f for f in FIGHTERS if not f.get("locked") or self.save.get("okabe")]

    def setup_match(self, mode, id1, id2, difficulty=None):
        self.mode = mode
        fd1, fd2 = FIGHTER_BY_ID[id1], FIGHTER_BY_ID[id2]
        ai1 = mode == "spectate"
        ai2 = mode in ("versus_ai", "arcade", "torneo", "spectate", "training")
        diff = difficulty if difficulty is not None else (0.35 if mode == "training" else 0.6)
        if mode == "arcade":
            diff = 0.35 + 0.06 * self.arcade_idx
        f1 = Fighter(fd1, 0, self.p1, ai1, diff)
        f2 = Fighter(fd2, 1, self.p2, ai2, diff)
        self.p1.ai_cmd = None
        self.p2.ai_cmd = None
        self.match = Match(f1, f2, self.audio, self.cfg, self.save.get("controls", "moderno"), training=(mode == "training"), access=self.access())
        self.match.debug = self.debug
        self.paused = False

    def enter_photo(self):
        if self.match is None:
            return
        self.photo = dict(lut="Natural", grain=True, vignette=True, dof=False, zoom=self.match.cam.zoom, x=self.match.cam.x, yoff=0.0, exposure=0, hud=False, saved_msg=0)

    # ----- bucle -----
    def run(self):
        while not self.quit:
            dt = self.clock.tick(FPS) / 1000.0
            self.frame += 1
            events = pygame.event.get()
            for e in events:
                if e.type == pygame.QUIT:
                    self.quit = True
                elif e.type == pygame.KEYDOWN:
                    self.on_key(e)
                elif e.type == pygame.JOYBUTTONDOWN:
                    self.on_joy(e)
            keys = pygame.key.get_pressed()
            self.update(keys)
            self.draw()
            pygame.display.flip()
            if self.max_frames and self.frame >= self.max_frames:
                self.quit = True
        self.save["preset"] = self.preset
        write_save(self.save)
        pygame.quit()

    # ----- entrada por eventos -----
    def on_joy(self, e):
        # mandos: A/✕ (0) acepta, B/○ (1) atrás, Select (6) modo foto
        fake = {0: pygame.K_RETURN, 1: pygame.K_ESCAPE, 6: pygame.K_F12}.get(e.button)
        if fake is not None:
            self.on_key(pygame.event.Event(pygame.KEYDOWN, key=fake, unicode="", mod=0))

    def on_key(self, e):
        k = e.key
        sc = self.scene
        if k == pygame.K_F11:
            pygame.display.toggle_fullscreen()
            return
        if k == pygame.K_F12 and self.match is not None and sc == "fight":
            if self.photo is None:
                self.enter_photo()
            else:
                self.photo = None
            return
        if self.photo is not None:
            self.photo_key(k)
            return
        if sc == "title":
            if k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j):
                self.scene = "menu"
                self.scene_t = 0
                self.audio.play("ui_ok")
            elif k == pygame.K_ESCAPE:
                self.quit = True
        elif sc == "menu":
            if k in (pygame.K_DOWN, pygame.K_s):
                self.menu_i = (self.menu_i + 1) % len(MENU_ITEMS)
                self.audio.play("ui_move")
            elif k in (pygame.K_UP, pygame.K_w):
                self.menu_i = (self.menu_i - 1) % len(MENU_ITEMS)
                self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j):
                self.audio.play("ui_ok")
                self.menu_select(MENU_ITEMS[self.menu_i][1])
            elif k == pygame.K_ESCAPE:
                self.scene = "title"
                self.audio.play("ui_back")
        elif sc == "select":
            self.select_key(k)
        elif sc == "options":
            self.options_key(k)
        elif sc == "fight":
            self.fight_key(k)
        elif sc in ("results", "epilogue", "vs"):
            if k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j, pygame.K_ESCAPE):
                self.advance_after_results()

    def menu_select(self, key):
        if key == "quit":
            self.quit = True
        elif key == "options":
            self.scene = "options"
            self.options_i = 0
        elif key == "spectate":
            ids = [f["id"] for f in self.available_fighters()]
            a, b = random.sample(ids, 2)
            self.setup_match("spectate", a, b)
            self.scene = "vs"
            self.scene_t = 0
        else:
            self.mode = key
            self.scene = "select"
            self.scene_t = 0
            self.sel_locked = [False, False]
            if key == "arcade":
                self.arcade_idx = 0
                self.arcade_lost_round = False
            if key == "torneo":
                self.arcade_idx = 0
                self.arcade_lost_round = False

    # ----- selección -----
    def select_key(self, k):
        avail = self.available_fighters()
        n = len(FIGHTERS)
        cols = 6
        two_players = self.mode == "versus"
        for p, (keys_h) in enumerate(((pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s, (pygame.K_u, pygame.K_j, pygame.K_RETURN)),
                                       (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, (pygame.K_KP4, pygame.K_KP1, pygame.K_KP_ENTER)))):
            if p == 1 and not two_players:
                # en modos de un jugador, P1 puede usar también las flechas
                keys_h = (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, (pygame.K_RETURN, pygame.K_u, pygame.K_j))
                p = 0
            if self.sel_locked[p]:
                continue
            L, R, U, D, OK = keys_h
            i = self.sel[p]
            if k == L:
                self.sel[p] = (i - 1) % n; self.audio.play("ui_move")
            elif k == R:
                self.sel[p] = (i + 1) % n; self.audio.play("ui_move")
            elif k == U:
                self.sel[p] = (i - cols) % n; self.audio.play("ui_move")
            elif k == D:
                self.sel[p] = (i + cols) % n; self.audio.play("ui_move")
            elif k in OK:
                fd = FIGHTERS[self.sel[p]]
                if fd.get("locked") and not self.save.get("okabe"):
                    self.audio.play("ui_back")
                    continue
                self.sel_locked[p] = True
                self.audio.play("ui_ok")
                self.audio.play("kiai", 0.5)
        if k == pygame.K_ESCAPE:
            if any(self.sel_locked):
                self.sel_locked = [False, False]
            else:
                self.scene = "menu"
            self.audio.play("ui_back")
            return
        # ¿listos?
        if self.sel_locked[0] and (self.sel_locked[1] or not two_players):
            id1 = FIGHTERS[self.sel[0]]["id"]
            if two_players:
                id2 = FIGHTERS[self.sel[1]]["id"]
            elif self.mode == "arcade":
                others = [f["id"] for f in FIGHTERS if f["id"] != id1 and (not f.get("locked") or self.save.get("okabe"))]
                random.shuffle(others)
                self.arcade_queue = others
                id2 = self.arcade_queue[0]
            elif self.mode == "torneo":
                others = [f["id"] for f in FIGHTERS if f["id"] != id1 and not f.get("locked")]
                random.shuffle(others)
                self.tourney = others[:3]   # cuartos, semifinal, final
                id2 = self.tourney[0]
            else:
                others = [f["id"] for f in self.available_fighters() if f["id"] != id1]
                id2 = random.choice(others)
            self.setup_match(self.mode, id1, id2)
            self.scene = "vs"
            self.scene_t = 0

    # ----- opciones -----
    OPTS = ["Preset gráfico", "Volumen", "Controles", "Reducir destellos", "Temblor de cámara", "Alto contraste", "Filtro daltonismo", "Volver"]

    def options_key(self, k):
        n = len(self.OPTS)
        if k in (pygame.K_DOWN, pygame.K_s):
            self.options_i = (self.options_i + 1) % n; self.audio.play("ui_move")
        elif k in (pygame.K_UP, pygame.K_w):
            self.options_i = (self.options_i - 1) % n; self.audio.play("ui_move")
        elif k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_a, pygame.K_d, pygame.K_RETURN, pygame.K_u, pygame.K_j):
            d = -1 if k in (pygame.K_LEFT, pygame.K_a) else 1
            o = self.OPTS[self.options_i]
            acc = self.save.setdefault("access", {})
            if o == "Preset gráfico":
                i = (PRESET_ORDER.index(self.preset) + d) % len(PRESET_ORDER)
                self.preset = PRESET_ORDER[i]
                self.cfg.clear(); self.cfg.update(PRESETS[self.preset])
                self.title_dojo = Dojo(self.cfg)
            elif o == "Volumen":
                self.audio.volume = clamp(round(self.audio.volume + 0.1 * d, 1), 0, 1)
                self.save["volume"] = self.audio.volume
            elif o == "Controles":
                self.save["controls"] = "clasico" if self.save.get("controls", "moderno") == "moderno" else "moderno"
            elif o == "Reducir destellos":
                acc["flashes"] = not acc.get("flashes", False)
            elif o == "Temblor de cámara":
                acc["shake"] = not acc.get("shake", True)
            elif o == "Alto contraste":
                acc["contrast"] = not acc.get("contrast", False)
            elif o == "Filtro daltonismo":
                opts = ["off", "protanopia", "deuteranopia", "tritanopia"]
                acc["daltonism"] = opts[(opts.index(acc.get("daltonism", "off")) + d) % 4]
            elif o == "Volver":
                self.scene = "menu"
            self.audio.play("ui_ok")
            write_save(self.save)
        elif k == pygame.K_ESCAPE:
            self.scene = "menu"
            self.audio.play("ui_back")

    # ----- combate -----
    def fight_key(self, k):
        m = self.match
        if k == pygame.K_ESCAPE or k == pygame.K_RETURN and self.paused:
            if k == pygame.K_ESCAPE:
                self.paused = not self.paused
                self.pause_i = 0
                self.audio.play("ui_back")
                return
        if self.paused:
            items = 3
            if k in (pygame.K_DOWN, pygame.K_s):
                self.pause_i = (self.pause_i + 1) % items; self.audio.play("ui_move")
            elif k in (pygame.K_UP, pygame.K_w):
                self.pause_i = (self.pause_i - 1) % items; self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_u, pygame.K_j, pygame.K_SPACE):
                self.audio.play("ui_ok")
                if self.pause_i == 0:
                    self.paused = False
                elif self.pause_i == 1:
                    self.setup_match(self.mode, m.f[0].fd["id"], m.f[1].fd["id"])
                else:
                    self.paused = False
                    self.audio.rain(False)
                    self.scene = "menu"
            return
        if k == pygame.K_F1:
            m.debug = not m.debug
        elif k == pygame.K_F2:
            m.frame_data_overlay = not m.frame_data_overlay
        elif m.state == Match.MATCH_END and k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j):
            self.go_results()

    def photo_key(self, k):
        ph = self.photo
        if k == pygame.K_ESCAPE:
            self.photo = None
            return
        if k in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            ph["lut"] = PHOTO_LUTS[k - pygame.K_1]
        elif k == pygame.K_g:
            ph["grain"] = not ph["grain"]
        elif k == pygame.K_v:
            ph["vignette"] = not ph["vignette"]
        elif k == pygame.K_b:
            ph["dof"] = not ph["dof"]
        elif k == pygame.K_h:
            ph["hud"] = not ph["hud"]
        elif k in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_e):
            ph["zoom"] = clamp(ph["zoom"] * 1.1, 0.5, 3.0)
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_q):
            ph["zoom"] = clamp(ph["zoom"] / 1.1, 0.5, 3.0)
        elif k == pygame.K_LEFTBRACKET:
            ph["exposure"] -= 1
        elif k == pygame.K_RIGHTBRACKET:
            ph["exposure"] += 1
        elif k == pygame.K_p:
            self.save_photo()
        elif k == pygame.K_SPACE:
            # avanzar un frame (fotograma a fotograma)
            keys = pygame.key.get_pressed()
            self.match.update(keys, self.joys)

    def save_photo(self):
        os.makedirs("fotos", exist_ok=True)
        name = os.path.join("fotos", f"tatami_{int(time.time())}.png")
        big = pygame.transform.smoothscale(self.world, (W * 2, H * 2))  # supermuestreo 2x
        pygame.image.save(big, name)
        self.photo["saved_msg"] = 120
        self.audio.play("ui_ok")

    # ----- resultados y modos -----
    def go_results(self):
        m = self.match
        winner = m.winner
        self.audio.rain(False)
        if self.mode in ("versus", "versus_ai", "spectate", "training"):
            if winner is not None and not winner.is_ai:
                self.save["wins"] = self.save.get("wins", 0) + 1
                write_save(self.save)
            self.scene = "results"
            self.scene_t = 0
            return
        player_won = winner is not None and winner is m.f[0]
        if m.f[1].wins > 0:
            self.arcade_lost_round = True
        if self.mode == "arcade":
            if not player_won:
                self.scene = "results"; self.scene_t = 0; return
            self.arcade_idx += 1
            if self.arcade_idx >= len(self.arcade_queue):
                cl = self.save.setdefault("arcade_clears", [])
                if m.f[0].fd["id"] not in cl:
                    cl.append(m.f[0].fd["id"])
                write_save(self.save)
                self.scene = "epilogue"; self.scene_t = 0
            else:
                self.scene = "results"; self.scene_t = 0
        elif self.mode == "torneo":
            if not player_won:
                self.scene = "results"; self.scene_t = 0; return
            self.arcade_idx += 1
            if self.arcade_idx >= len(self.tourney):
                if not self.arcade_lost_round and not self.save.get("okabe"):
                    self.save["okabe"] = True
                    write_save(self.save)
                    self.unlock_msg = True
                self.scene = "epilogue"; self.scene_t = 0
            else:
                self.scene = "results"; self.scene_t = 0

    def advance_after_results(self):
        m = self.match
        self.audio.play("ui_ok")
        if self.scene == "vs":
            self.scene = "fight"
            self.scene_t = 0
            return
        if self.scene == "epilogue":
            self.scene = "menu"
            return
        player_won = m is not None and m.winner is m.f[0]
        if self.mode == "arcade" and player_won and self.arcade_idx < len(self.arcade_queue):
            self.setup_match("arcade", m.f[0].fd["id"], self.arcade_queue[self.arcade_idx])
            self.scene = "vs"; self.scene_t = 0
        elif self.mode == "torneo" and player_won and self.arcade_idx < len(self.tourney):
            self.setup_match("torneo", m.f[0].fd["id"], self.tourney[self.arcade_idx])
            self.scene = "vs"; self.scene_t = 0
        else:
            self.scene = "menu"

    # ----- actualización -----
    def update(self, keys):
        self.scene_t += 1
        sc = self.scene
        if sc in ("title", "menu", "select", "options", "results", "epilogue"):
            self.title_dojo.update(1 / 60)
            if sc == "title" and self.frame % 240 == 0:
                self.audio.play(f"shaku{random.randrange(6)}", 0.4)
            return
        if sc == "vs":
            if self.scene_t > 170 or (self.headless and self.scene_t > 5):
                self.scene = "fight"
                self.scene_t = 0
            return
        if sc == "fight":
            m = self.match
            if self.photo is not None or self.paused:
                return
            m.update(keys, self.joys)
            last_sec = m.state == Match.FIGHT and m.timer <= 60
            silent = m.state in (Match.REPLAY,) or (m.slowmo < 1.0)
            self.audio.music_tick(1 / 60, m.intensity * 0.7 + 0.3 * (1 - min(m.f[0].vit, m.f[1].vit) / VIT_MAX) if m.state == Match.FIGHT else 0.0, last_sec, silent)
            if m.state == Match.MATCH_END and m.state_t > 240 and (self.mode in ("spectate",) or self.headless):
                self.go_results()
            if m.state == Match.MATCH_END and m.state_t > 600:
                self.go_results()

    # ----- dibujo -----
    def draw(self):
        sc = self.scene
        surf = self.world
        acc = self.access()
        if sc == "fight" and self.match is not None:
            m = self.match
            if self.photo is not None:
                ph = self.photo
                m.cam.override = (ph["x"], ph["zoom"])
                keys = pygame.key.get_pressed()
                if keys[pygame.K_LEFT]: ph["x"] -= 6 / ph["zoom"]
                if keys[pygame.K_RIGHT]: ph["x"] += 6 / ph["zoom"]
                if keys[pygame.K_UP]: ph["yoff"] += 4
                if keys[pygame.K_DOWN]: ph["yoff"] -= 4
                m.cam.x, m.cam.zoom = ph["x"], ph["zoom"]
                m.cam.yoff = ph["yoff"]
                m.draw(surf, self.fonts, self.layer, photo=ph)
                if ph["dof"] and hasattr(pygame.transform, "box_blur"):
                    # profundidad de campo falsa: desenfoque del fondo por encima del plano de combate
                    region = surf.subsurface((0, 0, W, int(FLOOR_Y * 0.62))).copy()
                    region = pygame.transform.box_blur(region, 4)
                    surf.blit(region, (0, 0))
                if ph["exposure"]:
                    e = pygame.Surface((W, H)); e.fill((abs(ph["exposure"]) * 18,) * 3)
                    surf.blit(e, (0, 0), special_flags=pygame.BLEND_ADD if ph["exposure"] > 0 else pygame.BLEND_SUB)
                apply_lut(surf, ph["lut"], m.dojo, ph["grain"] and self.cfg.get("grain", False), acc.get("contrast"), acc.get("daltonism", "off"))
                if not ph["vignette"]:
                    pass
                if ph["hud"]:
                    m.draw_hud(surf, self.fonts)
                self.draw_photo_ui(surf)
                m.cam.override = None
                m.cam.yoff = 0
            else:
                m.draw(surf, self.fonts, self.layer)
                if self.cfg.get("bloom") and m.state != Match.REPLAY:
                    bloom(surf, 0.35)
                if self.cfg.get("tilt") and abs(m.cam.tilt) > 0.05:
                    rotated = pygame.transform.rotozoom(surf, m.cam.tilt, 1.06)
                    surf.blit(rotated, (W // 2 - rotated.get_width() // 2, H // 2 - rotated.get_height() // 2))
                apply_lut(surf, "Natural", m.dojo, self.cfg.get("grain", False), acc.get("contrast"), acc.get("daltonism", "off"))
                m.draw_hud(surf, self.fonts)
                m.fx.draw_screen(surf, self.fonts, reduce_flash=acc.get("flashes", False))
                if m.state == Match.REPLAY:
                    self.draw_replay_frame(surf)
                if m.state == Match.MATCH_END and m.state_t > 60:
                    self.draw_match_end(surf)
                if self.paused:
                    self.draw_pause(surf)
        elif sc == "title":
            self.draw_title(surf)
        elif sc == "menu":
            self.draw_menu(surf)
        elif sc == "select":
            self.draw_select(surf)
        elif sc == "options":
            self.draw_options(surf)
        elif sc == "vs":
            self.draw_vs(surf)
        elif sc == "results":
            self.draw_results(surf)
        elif sc == "epilogue":
            self.draw_epilogue(surf)
        self.screen.blit(surf, (0, 0))

    def draw_backdrop(self, surf, darken=140):
        cam = self.title_cam
        cam.x = math.sin(self.frame * 0.002) * 120
        self.title_dojo.draw(surf, cam, self.fonts)
        self.layer.fill((0, 0, 0, 0))
        self.title_dojo.draw_beams(surf, cam, self.layer)
        surf.blit(self.layer, (0, 0))
        t = pygame.Surface((W, H)); t.fill(self.title_dojo.rig["tint"]); surf.blit(t, (0, 0), special_flags=pygame.BLEND_MULT)
        d = pygame.Surface((W, H), pygame.SRCALPHA); d.fill((0, 0, 0, darken)); surf.blit(d, (0, 0))
        surf.blit(self.title_dojo.vignette(180), (0, 0))

    def text(self, surf, s, size, x, y, color=(240, 232, 218), center=False, serif=False, alpha=255, shadow=True):
        f = self.fonts.get(size, serif)
        img = f.render(s, True, color)
        if alpha < 255:
            img.set_alpha(alpha)
        if center:
            x -= img.get_width() // 2
        if shadow:
            sh = f.render(s, True, (0, 0, 0)); sh.set_alpha(min(alpha, 150))
            surf.blit(sh, (x + 2, y + 2))
        surf.blit(img, (x, y))
        return img.get_height()

    def footer(self):
        return EPILOGUE_FOOTER if self.fonts.cjk_name else EPILOGUE_FOOTER.split("— ")[1]

    def draw_title(self, surf):
        self.draw_backdrop(surf, 90)
        band = pygame.Surface((W, 230), pygame.SRCALPHA); band.fill((8, 6, 6, 120)); surf.blit(band, (0, H // 2 - 10))
        pulse = (math.sin(self.frame * 0.05) + 1) / 2
        # caligrafía 道 grande, en tinta
        draw_kanji_do(surf, W // 2, H // 2 - 70, 230, (22, 16, 18), self.fonts)
        self.text(surf, "TATAMI: DŌ", 110, W // 2, H // 2 + 10, (248, 236, 215), center=True, serif=True)
        self.text(surf, "Edición 2026  ·  TatamyDo", 30, W // 2, H // 2 + 115, (220, 200, 170), center=True)
        self.text(surf, "Doce luchadores. Un dojo al atardecer. La luz se va apagando asalto a asalto.", 22, W // 2, H // 2 + 155, (200, 190, 175), center=True)
        self.text(surf, "PULSA INTRO", 28, W // 2, H - 110, (255, 240, 210), center=True, alpha=int(120 + 135 * pulse))
        self.text(surf, self.footer(), 18, W // 2, H - 50, (170, 160, 150), center=True)
        self.text(surf, f"Preset: {self.preset}   ·   F11 pantalla completa", 16, 20, H - 28, (150, 140, 130))

    def draw_menu(self, surf):
        self.draw_backdrop(surf, 150)
        self.text(surf, "TATAMI: DŌ", 64, 90, 70, (248, 236, 215), serif=True)
        self.text(surf, "Kōyō-kan · Kioto", 22, 92, 140, (200, 190, 175))
        y = 210
        for i, (label, key) in enumerate(MENU_ITEMS):
            sel = i == self.menu_i
            if sel:
                pygame.draw.rect(surf, (255, 220, 160), (76, y + 6, 6, 28))
            self.text(surf, label, 36 if sel else 32, 100, y, (255, 240, 215) if sel else (190, 180, 165), serif=sel)
            y += 52
        desc = {"versus": "Dos jugadores en el mismo teclado o con mandos. Al mejor de tres asaltos.",
                "versus_ai": "Elige luchador y enfréntate a un rival controlado por la IA.",
                "arcade": "Vence a los once rivales. Al final, el epílogo de tu luchador.",
                "torneo": "Cuartos, semifinal y final. Gana sin perder un asalto para desbloquear al Sensei Okabe.",
                "training": "Vitalidad infinita, datos de frames y hitboxes (F1 / F2).",
                "spectate": "Siéntate en seiza y mira a la IA combatir. Pulsa F12 para el Modo Foto.",
                "options": "Preset gráfico, volumen, controles y accesibilidad.",
                "quit": "Termina con un saludo."}[MENU_ITEMS[self.menu_i][1]]
        self.text(surf, desc, 20, 100, y + 20, (210, 200, 185))
        # recordatorio de controles
        box = pygame.Surface((420, 300), pygame.SRCALPHA); box.fill((10, 8, 8, 150)); surf.blit(box, (W - 470, 200))
        lines = ["CONTROLES", "", "J1: WASD mover · U/I/O golpes · J agarre", "    K bloqueo (parada: 4 frames) · L Kiai Final",
                 "J2: Flechas · Num 4/5/6 · Num 1/2/3", "", "Atrás + Medio: técnica de estilo", "Agacharse + Fuerte: barrido",
                 "Saltar + golpe: golpe aéreo", "", "F12 Modo Foto · F1 hitboxes · Esc pausa"]
        yy = 212
        for ln in lines:
            self.text(surf, ln, 20 if ln != "CONTROLES" else 26, W - 455, yy, (230, 220, 205) if ln == "CONTROLES" else (200, 190, 175), shadow=False)
            yy += 24
        okabe = "Sensei Okabe: DESBLOQUEADO" if self.save.get("okabe") else "Sensei Okabe: bloqueado (gana el Torneo sin perder un asalto)"
        self.text(surf, okabe, 16, 100, H - 40, (170, 160, 150))

    def draw_select(self, surf):
        self.draw_backdrop(surf, 160)
        self.text(surf, "ELIGE LUCHADOR", 44, W // 2, 24, (248, 236, 215), center=True, serif=True)
        cols = 6
        cw, ch = 150, 120
        x0 = W // 2 - cols * (cw + 10) // 2
        y0 = 90
        for i, fd in enumerate(FIGHTERS):
            r = pygame.Rect(x0 + (i % cols) * (cw + 10), y0 + (i // cols) * (ch + 10), cw, ch)
            locked = fd.get("locked") and not self.save.get("okabe")
            sel = (i == self.sel[0]) or (self.mode == "versus" and i == self.sel[1])
            draw_portrait(surf, fd, r, self.title_dojo.rig, sel, locked)
            if i == self.sel[0]:
                pygame.draw.rect(surf, (255, 170, 80), r.inflate(4, 4), 3)
            if self.mode == "versus" and i == self.sel[1]:
                pygame.draw.rect(surf, (90, 170, 255), r.inflate(10, 10), 3)
            nm = "???" if locked else fd["name"].split(" ")[0] if " " in fd["name"] else fd["name"]
            self.text(surf, nm, 18, r.centerx, r.bottom - 22, (240, 232, 218), center=True)
        # fichas
        for p in range(2 if self.mode == "versus" else 1):
            fd = FIGHTERS[self.sel[p]]
            locked = fd.get("locked") and not self.save.get("okabe")
            x = 60 if p == 0 else W - 480
            y = 360
            pygame.draw.rect(surf, (255, 170, 80) if p == 0 else (90, 170, 255), (x - 10, y, 4, 300))
            if locked:
                self.text(surf, "???", 40, x, y, (120, 110, 100), serif=True)
                self.text(surf, "Gana el Torneo sin perder un asalto.", 20, x, y + 50, (170, 160, 150))
                continue
            self.text(surf, fd["name"], 40, x, y, (248, 236, 215), serif=True)
            self.text(surf, f"«{fd['title']}»", 22, x, y + 48, (220, 200, 170))
            self.text(surf, f"{fd['style']}  ·  {fd['nation']}  ·  {fd['age']} años", 20, x, y + 78, (200, 190, 175))
            self.text(surf, f"Arquetipo: {fd['arche']}", 20, x, y + 104, (200, 190, 175))
            yy = y + 140
            for lab, key in (("Fuerza", "F"), ("Velocidad", "V"), ("Defensa", "D"), ("Alcance", "A"), ("Técnica", "T")):
                self.text(surf, lab, 18, x, yy, (190, 180, 165), shadow=False)
                for k in range(5):
                    pygame.draw.rect(surf, (255, 215, 150) if k < fd[key] else (60, 50, 44), (x + 110 + k * 22, yy + 4, 16, 10))
                yy += 22
            self.text(surf, f"Kiai Final: {fd['kiai']}", 20, x, yy + 6, (255, 225, 180))
            self.text(surf, f"Debilidad: {fd['weak']}", 18, x, yy + 32, (200, 170, 160))
            if self.sel_locked[p]:
                self.text(surf, "LISTO", 30, x, yy + 62, (255, 240, 200), serif=True)
        if self.mode != "versus":
            self.text(surf, "El rival lo elige el destino" if self.mode in ("versus_ai",) else "Arcade: once rivales" if self.mode == "arcade" else "Torneo: tres combates" if self.mode == "torneo" else "Entrenamiento", 22, W - 480, 360, (200, 190, 175))
        self.text(surf, "WASD / Flechas mover · U o Intro confirmar · Esc atrás", 18, W // 2, H - 32, (170, 160, 150), center=True)

    def draw_options(self, surf):
        self.draw_backdrop(surf, 170)
        self.text(surf, "OPCIONES", 54, 100, 60, (248, 236, 215), serif=True)
        acc = self.access()
        vals = {"Preset gráfico": self.preset, "Volumen": f"{int(self.audio.volume * 100)} %",
                "Controles": self.save.get("controls", "moderno"), "Reducir destellos": "sí" if acc.get("flashes") else "no",
                "Temblor de cámara": "sí" if acc.get("shake", True) else "no", "Alto contraste": "sí" if acc.get("contrast") else "no",
                "Filtro daltonismo": acc.get("daltonism", "off"), "Volver": ""}
        y = 150
        for i, o in enumerate(self.OPTS):
            sel = i == self.options_i
            self.text(surf, o, 32, 120, y, (255, 240, 215) if sel else (190, 180, 165), serif=sel)
            self.text(surf, f"◂ {vals[o]} ▸" if vals[o] else "", 28, 520, y + 2, (255, 225, 180) if sel else (170, 160, 150))
            y += 50
        p = PRESETS[self.preset]
        info = [f"Partículas ×{p['particles']}", f"Haces volumétricos: {'sí' if p['beams'] else 'no'}", f"Grano 35 mm: {'sí' if p['grain'] else 'no'}",
                f"Bloom: {'sí' if p['bloom'] else 'no'}", f"Inclinación de cámara: {'sí' if p['tilt'] else 'no'}", f"Polvo en suspensión: {p['dust']} motas"]
        yy = 160
        for ln in info:
            self.text(surf, ln, 20, 820, yy, (200, 190, 175)); yy += 26
        self.text(surf, "Moderno: Atrás+Medio = técnica de estilo.  Clásico: ↓↘→+Medio, doble toque = dash.", 18, 100, H - 70, (190, 180, 165))
        self.text(surf, "Los sonidos son sintetizados; sin numpy el juego es mudo.", 18, 100, H - 44, (170, 160, 150))

    def draw_vs(self, surf):
        m = self.match
        self.draw_backdrop(surf, 120)
        t = self.scene_t
        a, b = m.f
        slide = smoothstep(min(1, t / 40))
        for i, f in enumerate((a, b)):
            x = int(lerp(-500, 80, slide)) if i == 0 else int(lerp(W + 500, W - 560, slide))
            r = pygame.Rect(x, 150, 480, 300)
            draw_portrait(surf, f.fd, r, m.dojo.rig, True)
            self.text(surf, f.fd["name"], 44, x + 20, 470, (248, 236, 215), serif=True)
            self.text(surf, f"«{f.fd['title']}»", 22, x + 20, 520, (220, 200, 170))
            self.text(surf, f.fd["style"] + ("  ·  IA" if f.is_ai else ""), 22, x + 20, 550, (200, 190, 175))
            self.text(surf, f.fd["intro"], 18, x + 20, 585, (190, 180, 165))
        self.text(surf, "VS", 120, W // 2, H // 2 - 80, (255, 230, 180), center=True, serif=True)
        mode_txt = {"arcade": f"Arcade · combate {self.arcade_idx + 1}/{len(self.arcade_queue)}", "torneo": ["Cuartos de final", "Semifinal", "FINAL"][min(2, self.arcade_idx)]}.get(self.mode, "")
        if mode_txt:
            self.text(surf, mode_txt, 28, W // 2, H // 2 + 50, (220, 200, 170), center=True)
        self.text(surf, "Intro para empezar", 18, W // 2, H - 40, (170, 160, 150), center=True)

    def draw_replay_frame(self, surf):
        pygame.draw.rect(surf, (0, 0, 0), (0, 0, W, 70))
        pygame.draw.rect(surf, (0, 0, 0), (0, H - 70, W, 70))
        self.text(surf, f"REPETICIÓN · ángulo {self.match.replay_angle % 3 + 1}/3 · 1/3 velocidad", 22, W // 2, H - 48, (230, 230, 230), center=True)

    def draw_match_end(self, surf):
        m = self.match
        w = m.winner
        if w is None:
            return
        self.text(surf, f"{w.fd['name'].upper()} GANA", 72, W // 2, H // 2 - 140, (255, 236, 200), center=True, serif=True)
        self.text(surf, f"«{w.fd['title']}»", 28, W // 2, H // 2 - 60, (230, 210, 180), center=True)
        if self.mode != "spectate":
            self.text(surf, "Intro para continuar", 20, W // 2, H - 100, (200, 190, 175), center=True)

    def draw_pause(self, surf):
        d = pygame.Surface((W, H), pygame.SRCALPHA); d.fill((0, 0, 0, 160)); surf.blit(d, (0, 0))
        self.text(surf, "PAUSA", 64, W // 2, 200, (248, 236, 215), center=True, serif=True)
        for i, s in enumerate(("Continuar", "Reiniciar combate", "Volver al menú")):
            sel = i == self.pause_i
            self.text(surf, s, 36 if sel else 30, W // 2, 300 + i * 54, (255, 240, 215) if sel else (190, 180, 165), center=True, serif=sel)
        m = self.match
        self.text(surf, f"{m.f[0].fd['name']}  {m.f[0].wins} – {m.f[1].wins}  {m.f[1].fd['name']}   ·   {m.dojo.rig['name']}", 20, W // 2, 500, (200, 190, 175), center=True)

    def draw_photo_ui(self, surf):
        ph = self.photo
        box = pygame.Surface((W, 64), pygame.SRCALPHA); box.fill((0, 0, 0, 170)); surf.blit(box, (0, H - 64))
        self.text(surf, f"MODO FOTO   LUT [1-5]: {ph['lut']}   ·   zoom {ph['zoom']:.2f} (Q/E)   ·   exposición {ph['exposure']:+d} ([ ])   ·   G grano {'on' if ph['grain'] else 'off'}   ·   B bokeh {'on' if ph['dof'] else 'off'}   ·   H HUD", 20, 20, H - 52, (240, 232, 218), shadow=False)
        self.text(surf, "Flechas: encuadre   ·   Espacio: avanzar un fotograma   ·   P: guardar PNG 2x en ./fotos   ·   F12/Esc: salir", 18, 20, H - 26, (190, 180, 165), shadow=False)
        if ph["saved_msg"] > 0:
            ph["saved_msg"] -= 1
            self.text(surf, "FOTOGRAMA GUARDADO", 40, W // 2, 40, (255, 240, 200), center=True, serif=True)

    def draw_results(self, surf):
        m = self.match
        self.draw_backdrop(surf, 170)
        w = m.winner
        self.text(surf, "RESULTADO", 36, W // 2, 60, (220, 200, 170), center=True)
        if w is not None:
            self.text(surf, w.fd["name"], 72, W // 2, 110, (248, 236, 215), center=True, serif=True)
            self.text(surf, f"gana {w.wins}–{m.f[1 - m.f.index(w)].wins}", 32, W // 2, 190, (230, 210, 180), center=True)
        for i, f in enumerate(m.f):
            x = 160 if i == 0 else W - 560
            r = pygame.Rect(x, 260, 400, 220)
            draw_portrait(surf, f.fd, r, m.dojo.rig, f is w)
            self.text(surf, f.fd["name"], 28, x, 490, (240, 232, 218), serif=True)
            self.text(surf, f"Golpes conectados: {f.landed_hits}   Waza-ari: {f.wazaari}   Jogai: {f.jogai}", 18, x, 525, (200, 190, 175))
            self.text(surf, f"Vitalidad final: {f.vit}/{VIT_MAX}", 18, x, 548, (200, 190, 175))
        if self.mode == "arcade":
            msg = f"Arcade: {self.arcade_idx}/{len(self.arcade_queue)} rivales vencidos" if w is m.f[0] else "El camino termina aquí. Vuelve mañana al dojo."
            self.text(surf, msg, 24, W // 2, 600, (220, 200, 170), center=True)
        if self.mode == "torneo":
            msg = ("Pasas a la " + ["semifinal", "final", "..."][min(2, self.arcade_idx)]) if w is m.f[0] else "Eliminado del torneo."
            self.text(surf, msg, 24, W // 2, 600, (220, 200, 170), center=True)
        self.text(surf, "Intro para continuar", 20, W // 2, H - 50, (170, 160, 150), center=True)

    def draw_epilogue(self, surf):
        m = self.match
        self.draw_backdrop(surf, 200)
        fd = m.f[0].fd
        self.text(surf, "EPÍLOGO", 32, W // 2, 90, (220, 200, 170), center=True)
        self.text(surf, fd["name"], 64, W // 2, 130, (248, 236, 215), center=True, serif=True)
        # texto en varias líneas
        words = fd["epilogue"].split(" ")
        lines, cur = [], ""
        for wd in words:
            if len(cur) + len(wd) > 60:
                lines.append(cur); cur = wd
            else:
                cur = (cur + " " + wd).strip()
        lines.append(cur)
        y = 260
        for ln in lines:
            self.text(surf, ln, 30, W // 2, y, (235, 225, 210), center=True, serif=True); y += 42
        if self.mode == "torneo" and getattr(self, "unlock_msg", False):
            self.text(surf, "El dueño del dojo se levanta despacio. SENSEI OKABE DESBLOQUEADO.", 26, W // 2, y + 30, (255, 225, 170), center=True)
            self.unlock_msg = False
        self.text(surf, self.footer(), 22, W // 2, H - 90, (200, 190, 175), center=True)
        self.text(surf, "Intro para volver al menú", 18, W // 2, H - 44, (170, 160, 150), center=True)


# ─────────────────────────────────────────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(argv) -> dict:
    args = dict(preset=None, photo_mode=False, debug=False, ai_vs_ai=False, windowed=True, headless=False, max_frames=0)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--preset" and i + 1 < len(argv):
            args["preset"] = argv[i + 1]; i += 1
        elif a == "--photo-mode":
            args["photo_mode"] = True
        elif a == "--debug-hitboxes":
            args["debug"] = True
        elif a == "--ai-vs-ai":
            args["ai_vs_ai"] = True
        elif a == "--fullscreen":
            args["windowed"] = False
        elif a == "--windowed":
            args["windowed"] = True
        elif a == "--headless":
            args["headless"] = True
        elif a == "--frames" and i + 1 < len(argv):
            args["max_frames"] = int(argv[i + 1]); i += 1
        elif a == "--seed" and i + 1 < len(argv):
            random.seed(int(argv[i + 1])); i += 1
        elif a in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        i += 1
    return args


def main():
    args = parse_args(sys.argv[1:])
    if args["headless"]:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    Game(args).run()


if __name__ == "__main__":
    main()
