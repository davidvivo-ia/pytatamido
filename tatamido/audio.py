import math
import random
from typing import Dict

import pygame

from .config import *

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

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
