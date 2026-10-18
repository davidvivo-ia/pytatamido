#!/usr/bin/env python3
"""Carteles de TATAMI: DŌ — imágenes de prensa renderizadas por el propio motor.

Cada cartel es un instante escenificado (el pie que llega, el cuerpo en el aire, el kiai), encuadrado y
gradado como una fotografía: cámara baja, profundidad de campo, exposición y LUT del Modo Foto.
Se renderiza a escala real (TATAMI_RS: 3 = 3840×2160) y se reduce con Lanczos a la salida, de modo que
cada píxel final promedia nueve píxeles de render.

    python tools/carteles.py                     # todos, 4K → 2560×1440, en docs/
    python tools/carteles.py --rs 1 --only 01    # vista rápida de uno
"""
import argparse
import os
import random
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rs", type=float, default=3.0, help="escala de render (1 = 720p, 3 = 4K)")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--width", type=int, default=2560, help="ancho final de cada imagen")
    ap.add_argument("--only", default="", help="prefijos separados por comas, p. ej. 01,05")
    return ap.parse_args()


ARGS = parse()
os.environ["TATAMIDO_RS"] = str(ARGS.rs)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
warnings.simplefilter("ignore")
sys.path.insert(0, ROOT)

import pygame  # noqa: E402
from PIL import Image  # noqa: E402

from tatamido.config import RS, W, H  # noqa: E402
from tatamido.fighter import Fighter  # noqa: E402
from tatamido.game import Game  # noqa: E402
from tatamido.match import Match  # noqa: E402

G = Game(dict(preset="cine", headless=True, windowed=True, renderer="gpu"))
G.cfg["ss_px"] = 3.2 * RS          # figuras con supermuestreo propio además del de la escala
G.cfg["rim_ink"] = False           # sin contorno de color del jugador: en un cartel manda la tinta
KEYS = pygame.key.get_pressed()


def save(img: pygame.Surface, name: str):
    raw = pygame.image.tobytes(img, "RGB")
    im = Image.frombytes("RGB", img.get_size(), raw)
    w = ARGS.width
    if im.width != w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    path = os.path.join(ARGS.out, name)
    im.save(path, quality=93, optimize=True, progressive=True, subsampling=0)
    print("  →", os.path.relpath(path, ROOT), im.size)


def stage(a, b, rnd=1, xa=-70, xb=70, seed=7):
    """Combate sin IA, listo para pelear, con la luz del asalto pedido y los luchadores colocados."""
    random.seed(seed)
    G.setup_match("versus", a, b)
    G.scene = "fight"
    m = G.match
    for _ in range(600):
        if m.state == Match.FIGHT:
            break
        G.update(KEYS)
    if rnd > 1:
        m.dojo.set_round(rnd)
        m.dojo.rig = dict(m.dojo.target_rig)
        m.dojo.gpu_rig = dict(m.dojo.target_gpu)
        m.audio.rain(False)
    m.round = rnd
    fa, fb = m.f
    fa.x, fb.x = xa, xb
    for _ in range(40):            # que el dojo, el polvo y la cámara se asienten
        G.update(KEYS)
        fa.x, fb.x = xa, xb
    return m


def step(n):
    for _ in range(n):
        G.update(KEYS)


def until(cond, limit=240):
    for _ in range(limit):
        if cond():
            return True
        G.update(KEYS)
    return False


# Gradación de cartel: más contraste y negros con color que en juego (allí manda la legibilidad)
POSTER = dict(power=1.32, sat=1.18, vig=0.55, bloom=0.06, ca=0.35, impact=0.0)


def shoot(name, x, zoom, yoff=0.0, lut="Natural", dof=True, exposure=0, vignette=True, grain=True, scope=None, **grade):
    """Encuadre del Modo Foto (sin interfaz) y lectura de la imagen limpia."""
    G.enter_photo()
    G.photo.update(x=x, zoom=zoom, yoff=yoff * RS, lut=lut, dof=dof, exposure=exposure, vignette=vignette,
                   grain=grain, hud=False, grade=dict(POSTER, **grade))
    G.draw_photo_ui = lambda *a, **k: None
    G.draw()
    img = G.gpu.read(False)
    G.photo = None
    if scope is not None:                  # recorte panorámico 2,39:1 empezando en la fracción de altura dada
        h = int(W / 2.39)
        img = img.subsurface((0, int(H * scope), W, h)).copy()
    save(img, name)


def wants(prefix):
    return not ARGS.only or any(prefix.startswith(p) for p in ARGS.only.split(","))


# ─────────────────────────────────────────────────────────────────────────────
#  LOS CARTELES
# ─────────────────────────────────────────────────────────────────────────────
def c01():
    """Tarde dorada: la patada de Yeon llega a la guardia de Somchai, a contraluz de los shōji."""
    m = stage("yeon", "somchai", 1, xa=-62, xb=58, seed=11)
    a, b = m.f
    a.start_move("heavy", m.audio)
    until(lambda: a.hit_done, 80)
    until(lambda: b.y < -32, 40)          # Somchai despega con la pierna de Yeon aún extendida
    shoot("01_tarde_dorada.jpg", x=(a.x + b.x) / 2 + 4, zoom=2.95, yoff=-10, exposure=-1,
          lift=(0.0, 0.0, 0.012), gain=(1.06, 1.0, 0.9))


def c02():
    """Noche de farolillos: Kanoa empuja y Lin cede, con los farolillos desenfocados detrás."""
    m = stage("lin", "kanoa", 3, xa=-58, xb=66, seed=5)
    a, b = m.f
    b.start_move("special", m.audio)
    until(lambda: b.hit_done or (b.move and b.timer >= b.move.startup + 1), 80)
    step(2)
    shoot("02_noche.jpg", x=(a.x + b.x) / 2 + 6, zoom=2.85, yoff=-10, exposure=0, sat=0.98, gain=(0.97, 1.0, 1.05))


def c03():
    """Desempate bajo la lluvia: Elsa levanta a Matías; el tatami mojado lo refleja todo."""
    m = stage("elsa", "matias", 4, xa=-30, xb=26, seed=3)
    a, b = m.f
    a.start_move("grab", m.audio)
    until(lambda: a.state == Fighter.THROWING, 60)
    until(lambda: b.y < -60 or a.state != Fighter.THROWING, 60)
    shoot("03_lluvia.jpg", x=(a.x + b.x) / 2, zoom=2.8, yoff=30, exposure=1, gain=(0.95, 1.0, 1.08))


def c04():
    """Kiai final: un solo haz cenital sobre Inés; Tomasz ya no está en el suelo."""
    m = stage("ines", "tomasz", 2, xa=-40, xb=48, seed=9)
    a, b = m.f
    a.kiai = 100
    a.state = b.state = Fighter.IDLE
    a.start_kiai(b, m)
    step(44)
    shoot("04_kiai_final.jpg", x=(a.x + b.x) / 2, zoom=2.6, yoff=10, exposure=-1, sat=1.05, gain=(1.05, 1.0, 0.92))


def c05():
    """Modo Foto «Caravaggio»: Okabe proyecta a Haruki sin esfuerzo; tenebrismo cálido."""
    m = stage("okabe", "haruki", 1, xa=-26, xb=24, seed=4)
    a, b = m.f
    a.start_move("grab", m.audio)
    until(lambda: a.state == Fighter.THROWING, 60)
    until(lambda: b.y < -45 or a.state != Fighter.THROWING, 60)
    shoot("05_modo_foto_caravaggio.jpg", x=(a.x + b.x) / 2, zoom=2.9, yoff=10, lut="Caravaggio", exposure=-3, vig=0.5)


def c10():
    """Retrato: Haruki en guardia, a la luz rasante del atardecer, con el dojo deshecho en bokeh."""
    m = stage("haruki", "tomasz", 1, xa=-20, xb=420, seed=2)
    a, b = m.f
    step(20)
    shoot("10_retrato.jpg", x=a.x + 36, zoom=6.4, yoff=470, exposure=-1, scope=0.13)


def c06():
    """Selección de luchador: interfaz, retratos y luchadores a resolución nativa, con su luz y tejidos."""
    G.scene, G.mode, G.sel = "select", "versus", [3, 8]
    for _ in range(3):
        G.frame += 1
        G.draw()
    save(G.gpu.read(True), "06_seleccion.jpg")


def lineup(rnd, name):
    """Los doce en el dojo, en dos filas, con luz por píxel y tejidos de cada pieza."""
    from tatamido.camera import Camera
    from tatamido.config import FLOOR_Y, PRESETS
    from tatamido.dojo import Dojo
    from tatamido.gpu import scene_params
    from tatamido.data import FIGHTERS
    from tatamido.poses import fighter_pose
    from tatamido.render_fighter import PoseDummy, render_figure
    cfg = dict(PRESETS["cine"])
    dojo = Dojo(cfg)
    dojo.set_round(rnd)
    dojo.rig = dict(dojo.target_rig)
    dojo.gpu_rig = dict(dojo.target_gpu)
    for _ in range(30):
        dojo.update(1 / 60)
    cam = Camera()
    cam.zoom = 1.0
    mk = lambda: pygame.Surface((W, H), pygame.SRCALPHA)
    bg, fig, figd, fx = mk(), mk(), mk(), mk()
    hud = pygame.Surface((W, H), pygame.SRCALPHA)            # sin interfaz: vacía, a la resolución de render
    dojo.draw(bg, cam, G.fonts, gpu=True)
    pieces = []
    for row, idxs in ((0, list(range(0, 12, 2))), (1, list(range(1, 12, 2)))):
        for j, i in enumerate(idxs):
            fd = FIGHTERS[i]
            d = PoseDummy(fd, 1 if j % 2 == 0 else -1, 0.4 + i)
            U = (1.05 if row == 0 else 1.25) * RS
            mine = []
            img, data, rx, ry = render_figure(d, fighter_pose(d), U, dojo.rig, ss=1.6, ow_px=2.0 * RS, maps=True,
                                              id_base=len(pieces) + 1, pieces=mine)
            x = (95 + j * 200 + (0 if row == 0 else 100)) * RS
            y = FLOOR_Y + (-70 if row == 0 else 40) * RS
            ox, oy = int(x - rx), int(y - ry)
            fig.blit(img, (ox, oy))
            figd.blit(data, (ox, oy))
            pieces.extend((ax + ox, ay + oy) + tuple(r) for ax, ay, *r in mine)
    p = scene_params(dojo, cam, [], None, cfg, dict(tilt=0.0, pieces=pieces))
    p.update(POSTER, vig=0.35)
    G.gpu.render(p, bg, fig, fx, hud, figd=figd)
    img = G.gpu.read(False)
    top = int(H * 0.14)                       # fuera el techo vacío: el encuadre empieza en las vigas
    save(img.subsurface((0, top, W, H - top)).copy(), name)


def c07():
    """Los doce, tarde dorada."""
    lineup(1, "07_luchadores.jpg")


def c08():
    """Los doce, noche de farolillos."""
    lineup(3, "08_luchadores_noche.jpg")


def c09():
    """Tejidos de cerca: judogi, damasco de seda, satén thai, lycra, cuero y neopreno."""
    tiles = []
    for a, b in (("haruki", "lin"), ("lin", "okabe"), ("somchai", "lin"), ("elsa", "lin")):
        m = stage(a, b, 1, xa=0, xb=420, seed=2)
        step(12)
        f = m.f[0]
        G.enter_photo()
        G.photo.update(x=f.x, zoom=3.4, yoff=30 * RS, dof=True, exposure=-1, hud=False, grade=dict(POSTER))
        G.draw_photo_ui = lambda *a, **k: None
        G.draw()
        img = G.gpu.read(False)
        G.photo = None
        tw = int(W * 0.36)
        tiles.append(img.subsurface((W // 2 - tw // 2, 0, tw, H)).copy())
    out = pygame.Surface((tw * len(tiles), H))
    for i, t in enumerate(tiles):
        out.blit(t, (i * tw, 0))
    save(out, "09_tejidos.jpg")


SHOTS = [("01", c01), ("02", c02), ("03", c03), ("04", c04), ("05", c05), ("06", c06), ("07", c07), ("08", c08),
         ("09", c09), ("10", c10)]

if __name__ == "__main__":
    print(f"render {W}×{H} (RS {RS:g}) → {ARGS.width} px")
    for pre, fn in SHOTS:
        if wants(pre):
            print(pre, fn.__doc__.strip().split(":")[0])
            fn()
