"""Cada escena del juego se dibuja sin excepciones, con el renderer GPU (si hay OpenGL) y con el CPU."""
import pygame
import pytest
from tatamido.game import Game
from tatamido.gpu import GPURenderer


def _gpu_ok():
    if not GPURenderer.available():
        return False
    try:
        GPURenderer.offscreen_context()
        return True
    except Exception:
        return False


RENDERERS = ["cpu"] + (["gpu"] if _gpu_ok() else [])


@pytest.mark.parametrize("renderer", RENDERERS)
def test_every_scene_renders(renderer):
    g = Game(dict(preset="ultra", headless=True, windowed=True, renderer=renderer))
    assert (g.gpu is not None) == (renderer == "gpu")
    g.setup_match("versus_ai", "haruki", "ines")
    keys = pygame.key.get_pressed()
    for sc in ("title", "menu", "select", "options", "replays", "vs", "results", "epilogue"):
        g.scene = sc
        g.update(keys)
        g.draw()
    g.scene = "fight"
    for _ in range(200):
        g.update(keys)
    g.draw()
    for rnd in (2, 3, 4):                 # luz de cada asalto, lluvia incluida
        g.match.dojo.set_round(rnd)
        g.update(keys)
        g.draw()
    g.enter_photo()
    for lut in ("Natural", "Caravaggio", "Hammershøi", "Kurosawa B/N", "Daido Moriyama"):
        g.photo["lut"] = lut
        g.photo["dof"] = True
        g.draw()
    g.photo = None
    g.paused = True
    g.draw()
    g.movelist = True
    g.draw()
    img = g.capture()
    assert img.get_size() == (1280, 720)
    # la imagen no puede ser plana: hay contenido real
    colors = {img.get_at((x, y))[:3] for x in range(0, 1280, 97) for y in range(0, 720, 61)}
    assert len(colors) > 20


def test_training_mode_renders_overlay():
    g = Game(dict(preset="bajo", headless=True, windowed=True, renderer="cpu"))
    g.setup_match("training", "lin", "kanoa")
    g.scene = "fight"
    keys = pygame.key.get_pressed()
    m = g.match
    m.cycle_dummy()
    m.toggle_record(); m.toggle_record()
    for _ in range(200):
        g.update(keys)
    g.draw()
