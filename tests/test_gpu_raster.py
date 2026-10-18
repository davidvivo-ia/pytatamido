"""Rasterizado de los luchadores en la GPU (punto 16): mismos trazos que pygame, triangulados y pintados con
MSAA. La imagen tiene que coincidir con la del camino de pygame (en otro proceso: el modo se fija al importar)."""
import os
import subprocess
import sys

import pytest

from tatamido.gpu import GPURenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import sys, random
sys.path.insert(0, %r)
import pygame
from tatamido.game import Game
g = Game(dict(preset="ultra", headless=True, windowed=True, renderer="gpu"))
keys = pygame.key.get_pressed()
random.seed(7)
g.setup_match("spectate", "ayo", "lin")
g.scene = "fight"
for _ in range(140):
    g.update(keys)
g.enter_photo()
g.photo.update(dof=False, grain=False)
for _ in range(2):
    g.draw()
pygame.image.save(g.gpu.read(False), sys.argv[1])
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
def test_gpu_raster_matches_pygame(tmp_path):
    import numpy as np
    import pygame
    imgs = []
    for mode in ("0", "1"):
        out = tmp_path / f"r{mode}.png"
        env = dict(os.environ, TATAMIDO_RS="1", TATAMIDO_GPU_RASTER=mode, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        r = subprocess.run([sys.executable, "-c", SCRIPT, str(out)], env=env, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        imgs.append(pygame.surfarray.array3d(pygame.image.load(str(out))).astype(int))
    d = np.abs(imgs[0] - imgs[1]).sum(axis=2)
    assert d.mean() < 3.0, d.mean()                 # sólo bordes de suavizado distintos (MSAA frente a supermuestreo)
    assert (d > 120).mean() < 0.01
