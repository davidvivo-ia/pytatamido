from typing import List, Optional, Tuple

import pygame

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
        self.ai_cmd: Optional[dict] = None   # si no es None, la IA (o una repetición) sustituye al teclado
        self.buffer = {a: 99 for a in ACTIONS}   # frames desde la última pulsación (buffer de entrada)
        self.BUFFER_FRAMES = 7

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
            self.buffer[a] = 0 if self.pressed[a] else self.buffer[a] + 1
        self.frame += 1
        d = self.direction()
        if not self.history or self.history[-1][1] != d:
            self.history.append((self.frame, d))
            if len(self.history) > 24:
                self.history.pop(0)

    def buffered(self, action: str) -> bool:
        """¿Se pulsó la acción en los últimos frames? Consume el buffer para que no se dispare dos veces."""
        if self.buffer[action] <= self.BUFFER_FRAMES:
            self.buffer[action] = 99
            return True
        return False

    def mask(self) -> int:
        """Estado de los botones como máscara de bits (para grabar repeticiones)."""
        m = 0
        for i, a in enumerate(ACTIONS):
            if self.held[a]:
                m |= 1 << i
        return m

    @staticmethod
    def cmd_from_mask(m: int) -> dict:
        return {a: bool(m >> i & 1) for i, a in enumerate(ACTIONS)}

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
