#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TATAMI: DŌ — Edición 2026  ·  TatamyDo
========================================
Juego de lucha 2.5D, uno contra uno. Doce luchadores, un dojo al atardecer y una luz
que se apaga asalto a asalto.  Este fichero es el lanzador; el juego vive en el paquete
`tatamido/` (config, data, audio, inputs, moves, fighter, poses, camera, render_fighter,
fx, dojo, match, postfx, replay, game).

Requisitos:  python >= 3.10, pygame-ce, numpy (audio) y moderngl (renderer de shaders, opcional).
    pip install pygame-ce numpy moderngl
    python Tatamydo.py [--preset portatil|bajo|medio|alto|ultra|cine] [--renderer auto|gpu|cpu]
                       [--res auto|720p|1080p|1440p|4k] [--photo-mode] [--debug-hitboxes]
                       [--ai-vs-ai] [--windowed] [--seed N]

Todo lo que ves es procedimental: no hay ficheros de arte ni de audio.
La simulación es determinista: cada combate se graba en ./replays y se puede reproducir.
Tests:  python -m pytest -q
    「礼に始まり礼に終わる」 — Empieza con un saludo, termina con un saludo.
"""
import os
import random
import sys

# ─────────────────────────────────────────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(argv) -> dict:
    args = dict(preset=None, photo_mode=False, debug=False, ai_vs_ai=False, windowed=True, headless=False, max_frames=0, renderer=None,
                res=None)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--preset" and i + 1 < len(argv):
            args["preset"] = argv[i + 1]; i += 1
        elif a == "--renderer" and i + 1 < len(argv):
            args["renderer"] = argv[i + 1]; i += 1
        elif a == "--res" and i + 1 < len(argv):
            args["res"] = argv[i + 1].lower(); i += 1
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


def render_scale(args) -> float:
    """Escala de render antes de importar el juego (las medidas en píxeles se fijan al importar).
    Sólo el renderer GPU sube de 720p: la interfaz se reescala allí. «auto» elige por el escritorio:
    1080p en pantallas Full HD, 1440p en QHD y también en 4K (se reescala; rinde mejor)."""
    import json
    try:
        with open(os.path.join(os.path.expanduser("~"), ".tatamydo_save.json"), encoding="utf-8") as f:
            save = json.load(f)
    except Exception:
        save = {}
    res = args["res"] or save.get("res", "auto")
    renderer = args["renderer"] or save.get("renderer", "auto")
    if renderer == "cpu" or args["headless"]:
        return 1.0
    try:
        import moderngl  # noqa: F401
    except Exception:
        return 1.0
    table = {"720p": 1.0, "1080p": 1.5, "1440p": 2.0, "4k": 3.0}
    if res in table:
        return table[res]
    try:
        import pygame
        pygame.display.init()
        dh = pygame.display.get_desktop_sizes()[0][1]
    except Exception:
        return 1.0
    return 2.0 if dh >= 1440 else 1.5 if dh >= 1080 else 1.0


def main():
    args = parse_args(sys.argv[1:])
    if args["headless"]:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("TATAMIDO_RS", str(render_scale(args)))
    from tatamido.game import Game
    Game(args).run()


if __name__ == "__main__":
    main()
