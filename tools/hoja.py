"""Hoja de poses: cada luchador en guardia, golpes, agarre, impacto y caída, con la luz por píxel de la GPU.

Sirve para revisar la anatomía de cerca y como base de los tests visuales (tests/test_visual.py).

    python tools/hoja.py --rs 2 --fighters haruki,kanoa,ayo --poses guardia,jab,patada,agarre --out hoja.png
"""
import argparse
import os
import sys
import types
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POSES = {
    # nombre: (estado, animación, fase, patada)
    "guardia": ("IDLE", None, 0.0, False),
    "andar": ("WALK", None, 0.3, False),
    "jab": ("ATTACK", "jab", 0.45, False),
    "cross": ("ATTACK", "cross", 0.45, False),
    "gancho": ("ATTACK", "hook", 0.45, False),
    "patada": ("ATTACK", "kick_mid", 0.45, False),
    "alta": ("ATTACK", "kick_high", 0.45, False),
    "agarre": ("ATTACK", "grab", 0.45, False),
    "bloqueo": ("BLOCK", None, 0.0, False),
    "impacto": ("HIT", None, 0.3, False),
    "caida": ("KNOCK", None, 0.5, False),
    "agachado": ("CROUCH", None, 0.0, False),
}


def pose_for(fd, name, t=0.6):
    """(dummy, pose) listos para render_figure."""
    from tatamido.fighter import Fighter
    from tatamido.poses import fighter_pose, pose_attack, pose_hit, pose_knock
    from tatamido.render_fighter import PoseDummy
    st, anim, ph, kick = POSES[name]
    d = PoseDummy(fd, 1, t, getattr(Fighter, st))
    if st == "ATTACK":
        d.move = types.SimpleNamespace(anim=anim, kick=kick, name="", total=30, startup=10, active=4, recovery=16, rng=80)
        d.move_key = "heavy" if anim in ("kick_high", "hook") else "light"
        d.timer = d.move.startup + 1                  # fotograma activo
        return d, pose_attack(anim, ph, fd, kick)
    if st == "HIT":
        return d, pose_hit(ph, fd)
    if st == "KNOCK":
        return d, pose_knock(ph, fd, False)
    return d, fighter_pose(d)


def sheet(G, fighters, poses, rnd=1, cols=None, studio=False):
    """Renderiza la hoja con el renderer GPU de `G` (una Game con gpu) y devuelve la imagen."""
    import pygame
    from tatamido.camera import Camera
    from tatamido.config import FLOOR_Y, PRESETS, RS, W, H
    from tatamido.data import FIGHTER_BY_ID
    from tatamido.dojo import Dojo
    from tatamido.gpu import scene_params
    from tatamido.render_fighter import render_figure
    cfg = dict(PRESETS["ultra"])
    dojo = Dojo(cfg)
    dojo.set_round(rnd)
    dojo.rig = dict(dojo.target_rig)
    dojo.gpu_rig = dict(dojo.target_gpu)
    cam = Camera()
    cam.zoom = 1.0
    mk = lambda: pygame.Surface((W, H), pygame.SRCALPHA)
    bg, fig, figd, fx = mk(), mk(), mk(), mk()
    if studio:
        bg.fill((58, 52, 48, 255))
    else:
        dojo.draw(bg, cam, G.fonts, gpu=True)
    cells = [(fid, pn) for fid in fighters for pn in poses]
    cols = cols or len(poses)
    rows = (len(cells) + cols - 1) // cols
    cw, ch = W / cols, H / rows
    rendered = []
    for i, (fid, pn) in enumerate(cells):
        fd = FIGHTER_BY_ID[fid]
        d, pose = pose_for(fd, pn)
        U = ch * 0.86 / (200.0 * fd["h"])
        mine = []
        img, data, rx, ry = render_figure(d, pose, U, dojo.rig, ss=1.6, ow_px=2.0 * RS, maps=True, id_base=1, pieces=mine)
        x = (i % cols + 0.42) * cw
        y = (i // cols + 0.95) * ch
        rendered.append((i, img, data, int(x - rx), int(y - ry), mine))
    # la tabla de piezas tiene 254 huecos: se pinta por tandas que quepan y se compone celda a celda
    batches, cur, n = [], [], 0
    for r in rendered:
        if cur and n + len(r[5]) > 250:
            batches.append(cur)
            cur, n = [], 0
        cur.append(r)
        n += len(r[5])
    if cur:
        batches.append(cur)
    out = None
    hud = pygame.Surface((W, H), pygame.SRCALPHA)
    for batch in batches:
        fig.fill((0, 0, 0, 0))
        figd.fill((0, 0, 0, 0))
        pieces = []
        for i, img, data, ox, oy, mine in batch:
            base = len(pieces)
            # renumerar las piezas de la celda dentro de la tanda
            if base:
                arr = pygame.surfarray.pixels3d(data)
                g = arr[:, :, 1]
                g[g > 0] += base
                del arr, g
            fig.blit(img, (ox, oy))
            figd.blit(data, (ox, oy))
            pieces.extend((ax + ox, ay + oy) + tuple(r) for ax, ay, *r in mine)
        p = scene_params(dojo, cam, [], None, cfg, dict(tilt=0.0, pieces=pieces, dof=0.0, grain=0.0))
        p["plate"] = None if studio else p["plate"]
        G.gpu.render(p, bg, fig, fx, hud, figd=figd)
        img_ = G.gpu.read(False)
        if out is None:
            out = img_.copy()
        for i, *_ in batch:
            rc = pygame.Rect(int((i % cols) * cw), int((i // cols) * ch), int(cw) + 1, int(ch) + 1)
            out.blit(img_, rc.topleft, rc)
    return out


REF_POSES = ("guardia", "jab", "patada", "agarre", "impacto", "agachado")
REF_DIR = os.path.join(ROOT, "tests", "ref")


def ref_image(fid, pose_name):
    """Figura de referencia para los tests visuales: rasterizado de pygame (determinista), sin GPU."""
    from tatamido.config import LIGHT_RIGS
    from tatamido.data import FIGHTER_BY_ID
    from tatamido.render_fighter import render_figure
    d, pose = pose_for(FIGHTER_BY_ID[fid], pose_name)
    img, _rx, _ry = render_figure(d, pose, 1.1, LIGHT_RIGS[1], ss=2)
    return img


def write_refs():
    """Regenera tests/ref: hay que hacerlo (y revisar las imágenes) cuando un cambio en las figuras es intencionado."""
    import pygame
    from tatamido.data import FIGHTERS
    os.makedirs(REF_DIR, exist_ok=True)
    for fd in FIGHTERS:
        for pn in REF_POSES:
            pygame.image.save(ref_image(fd["id"], pn), os.path.join(REF_DIR, f"{fd['id']}_{pn}.png"))
    print("referencias en", REF_DIR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rs", type=float, default=2.0)
    ap.add_argument("--fighters", default="haruki,kanoa,ayo")
    ap.add_argument("--poses", default="guardia,jab,patada,agarre")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--studio", action="store_true", help="fondo neutro en vez del dojo")
    ap.add_argument("--out", default="hoja.png")
    ap.add_argument("--refs", action="store_true", help="regenera las imágenes de referencia de tests/ref")
    a = ap.parse_args()
    if a.refs:
        a.rs = 1.0
    os.environ["TATAMIDO_RS"] = str(a.rs)
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    warnings.simplefilter("ignore")
    sys.path.insert(0, ROOT)
    import pygame
    if a.refs:
        pygame.init()
        write_refs()
        return
    from tatamido.game import Game
    G = Game(dict(preset="ultra", headless=True, windowed=True, renderer="gpu"))
    img = sheet(G, a.fighters.split(","), a.poses.split(","), a.round, studio=a.studio)
    pygame.image.save(img, a.out)
    print(a.out, img.get_size())


if __name__ == "__main__":
    main()
