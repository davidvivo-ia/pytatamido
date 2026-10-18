"""Interfaz a resolución nativa: coordenadas lógicas, rasterizado ×k, texto con glifos al tamaño real."""
import pygame

from tatamido.fx import Fonts
from tatamido.ui import HiText, UISurface, draw, panel

pygame.init()


def test_draw_scales_to_native():
    ui = UISurface((1280, 720), 2)
    assert ui.get_size() == (1280, 720) and ui.surf.get_size() == (2560, 1440)
    ui.clear()
    draw.rect(ui, (255, 0, 0), (10, 10, 20, 5))
    assert ui.surf.get_at((20, 20))[:3] == (255, 0, 0)
    assert ui.surf.get_at((59, 29))[:3] == (255, 0, 0)
    assert ui.surf.get_at((60, 30))[3] == 0
    # en una Surface corriente es pygame.draw sin más
    s = pygame.Surface((50, 50))
    assert draw.rect(s, (0, 255, 0), (1, 1, 3, 3)) == pygame.Rect(1, 1, 3, 3)


def test_text_is_rendered_at_native_size():
    fonts = Fonts()
    ui = UISurface((1280, 720), 2)
    f = fonts.at(ui).get(30)
    img = f.render("TATAMI", True, (255, 255, 255))
    assert isinstance(img, HiText)
    plain = fonts.get(60).render("TATAMI", True, (255, 255, 255))
    assert img.img.get_size() == plain.get_size()                 # glifos de 60 px, no 30 px reescalados
    assert abs(img.get_width() - plain.get_width() / 2) <= 1      # medidas en unidades lógicas
    assert fonts.at(pygame.Surface((4, 4))) is fonts


def test_dirty_strips_cover_old_and_new():
    ui = UISurface((1280, 720), 2)
    ui.clear()
    ui.take_dirty()                       # primer fotograma: todo
    ui.clear()
    assert ui.take_dirty() == []          # nada pintado ni antes ni ahora: no se sube nada
    ui.clear()
    draw.rect(ui, (255, 255, 255), (0, 0, 100, 10))
    draw.rect(ui, (255, 255, 255), (0, 700, 100, 10))
    d = ui.take_dirty()
    assert len(d) == 2 and d[0].bottom <= 40 and d[1].top >= 1380   # arriba y abajo, sin arrastrar el centro
    ui.clear()
    panel(ui, (0, 0, 1280, 720), (0, 0, 0, 128))
    assert ui.surf.get_at((1000, 1000))[3] == 128
    assert ui.surf.get_at((10, 1410))[3] == 128                       # lo de antes ya se borró
    ui.clear()
    assert sum(r.h for r in ui.take_dirty()) == 1440                  # hay que subir el velo para borrarlo
