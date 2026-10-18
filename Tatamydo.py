#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TATAMI: DŌ — Edición 2026  ·  TatamyDo
========================================
Juego de lucha 2.5D, uno contra uno. Doce luchadores, un dojo al atardecer y una luz
que se apaga asalto a asalto.  Este fichero es el lanzador; el juego vive en el paquete
`tatamido/` (config, data, audio, inputs, moves, fighter, poses, camera, render_fighter,
fx, dojo, match, postfx, replay, game).

Requisitos:  python >= 3.10, pygame-ce (o pygame >= 2.1), numpy (opcional, para el audio).
    pip install pygame-ce numpy
    python Tatamydo.py [--preset portatil|bajo|medio|alto|ultra|cine] [--photo-mode] [--debug-hitboxes]
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
    from tatamido.game import Game
    Game(args).run()


if __name__ == "__main__":
    main()
