"""Resolución de render por encima de 720p (1080p): capas del mundo a escala, interfaz lógica pintada a escala nativa,
lámina de pared horneada y subidas parciales. Se ejecuta en otro proceso porque la escala se fija al importar."""
import os
import subprocess
import sys

import pytest

from tatamido.gpu import GPURenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import os, sys, random
sys.path.insert(0, %r)
import pygame
from tatamido.game import Game
g = Game(dict(preset="ultra", headless=True, windowed=True, renderer="gpu"))
assert g.gpu is not None
keys = pygame.key.get_pressed()
random.seed(3)
g.setup_match("spectate", "lin", "kanoa")
g.scene = "fight"
for _ in range(120):
    g.update(keys)
for _ in range(3):
    g.update(keys)
    g.draw()
img = g.capture()
assert img.get_size() == (1920, 1080), img.get_size()
assert g.hud.get_size() == (1280, 720)              # la interfaz habla en unidades lógicas...
assert g.hud.surf.get_size() == (1920, 1080)        # ...y pinta a la resolución de render
assert g.match.dojo.plate["col"].get_width() > 1920
# la barra de vida (interfaz) está arriba a la izquierda: no es negra
r, gg, b, _ = img.get_at((300, 54))
assert r + gg + b > 200, (r, gg, b)
print("ok")
""" % ROOT


def _gpu_ok():
    if not GPURenderer.available():
        return False
    try:
        GPURenderer.offscreen_context()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _gpu_ok(), reason="sin OpenGL")
def test_1080p_render():
    env = dict(os.environ, TATAMIDO_RS="1.5", SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ok" in out.stdout
