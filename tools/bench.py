"""Banco de pruebas del render: tiempo de CPU y de cada etapa de la GPU en un combate real.

Pensado para medir en una máquina con GPU de verdad (el contenedor de desarrollo usa llvmpipe, una GPU por
software: sus tiempos no dicen nada del juego). La GPU se sincroniza entre etapas para poder atribuir el
tiempo; el total resultante es algo mayor que el de un fotograma normal.

    python tools/bench.py --res 1440p --frames 300
    python tools/bench.py --res 4k --gpu-raster 1
"""
import argparse
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="1440p", choices=["720p", "1080p", "1440p", "4k"])
    ap.add_argument("--frames", type=int, default=240)
    ap.add_argument("--preset", default="ultra")
    ap.add_argument("--gpu-raster", choices=["auto", "0", "1"], default="auto",
                    help="figuras rasterizadas por la GPU (1), por pygame (0) o según la resolución")
    ap.add_argument("--window", action="store_true", help="en ventana real (si no, contexto sin ventana)")
    a = ap.parse_args()
    os.environ["TATAMIDO_RS"] = str({"720p": 1, "1080p": 1.5, "1440p": 2, "4k": 3}[a.res])
    if a.gpu_raster != "auto":
        os.environ["TATAMIDO_GPU_RASTER"] = a.gpu_raster
    if not a.window:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    sys.path.insert(0, ROOT)
    import pygame
    from tatamido.game import Game
    g = Game(dict(preset=a.preset, headless=not a.window, windowed=True, renderer="gpu"))
    if g.gpu is None:
        print("sin GPU (OpenGL 3.3): nada que medir")
        return
    print("GPU:", g.gpu.renderer_name)
    keys = pygame.key.get_pressed()
    random.seed(1)
    g.setup_match("spectate", "haruki", "lin")
    g.scene = "fight"
    for _ in range(90):                       # intro y primeros horneados fuera de la medida
        g.update(keys)
        g.draw()
    g.gpu.profile = True
    g.gpu.timings = {}
    cpu = []
    t_all = time.perf_counter()
    for _ in range(a.frames):
        t = time.perf_counter()
        g.update(keys)
        g.draw()
        if a.window:
            pygame.display.flip()
        cpu.append(time.perf_counter() - t)
    total = (time.perf_counter() - t_all) / a.frames * 1e3
    n = a.frames
    print(f"\n{a.res} · {a.frames} fotogramas · media por fotograma {total:.2f} ms ({1000 / total:.0f} fps con sincronización)")
    gpu_ms = sum(g.gpu.timings.values()) / n
    print(f"  CPU (simulación + capas + envío): {total - gpu_ms:.2f} ms")
    for k, v in sorted(g.gpu.timings.items(), key=lambda kv: -kv[1]):
        print(f"  {k:36s} {v / n:7.2f} ms")


if __name__ == "__main__":
    main()
