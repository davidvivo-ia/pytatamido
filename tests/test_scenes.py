"""Cada escena del juego se dibuja al menos una vez sin lanzar excepciones."""
import pygame
from tatamido.game import Game


def test_every_scene_renders():
    g = Game(dict(preset="bajo", headless=True, windowed=True, debug=False, ai_vs_ai=False, photo_mode=False, max_frames=0))
    g.setup_match("versus_ai", "haruki", "ines")
    keys = pygame.key.get_pressed()
    for sc in ("title", "menu", "select", "options", "replays", "vs", "results", "epilogue"):
        g.scene = sc
        g.replay_files = []
        g.update(keys)
        g.draw()
    g.scene = "fight"
    for _ in range(200):
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
