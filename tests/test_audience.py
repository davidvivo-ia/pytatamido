"""Público horneado en la GPU con la luz de los luchadores: una vez por luz de asalto, en formato nativo y
sin tirón (una figura por fotograma mientras se ve la versión anterior)."""
import pygame
import pytest

from tatamido.dojo import Dojo
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


@pytest.mark.skipif(not _gpu_ok(), reason="sin OpenGL")
def test_audience_is_baked_per_round_light():
    g = Game(dict(preset="ultra", headless=True, windowed=True, renderer="gpu"))
    assert Dojo.baker is not None
    g.setup_match("spectate", "lin", "kanoa")
    g.scene = "fight"
    keys = pygame.key.get_pressed()
    g.update(keys)
    g.draw()
    d = g.match.dojo
    first = d._aud_lit
    sprites = first["sprites"]
    assert len(sprites) >= 6                                     # al empezar se hornea todo de una vez
    img = next(iter(sprites.values()))[0]
    assert img.get_masks() == pygame.Surface((1, 1), pygame.SRCALPHA).get_masks()
    w, h = img.get_size()
    lit = {img.get_at((x, y))[:3] for x in range(0, w, 7) for y in range(0, h, 7) if img.get_at((x, y))[3] > 200}
    assert len(lit) > 40                                         # luz por píxel y tejidos, no un tinte plano
    # nueva luz de asalto: se hornea poco a poco y la anterior sigue a la vista mientras se funde
    d.set_round(3)
    g.update(keys)
    g.draw()
    assert d._aud_lit is not first and d._aud_lit_from is first
    assert len(d._aud_lit["sprites"]) == 1
    for _ in range(len(sprites)):
        g.update(keys)
        g.draw()
    assert len(d._aud_lit["sprites"]) == len(sprites)
