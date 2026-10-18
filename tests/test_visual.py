"""Tests visuales de los luchadores (punto 18).

1. Huesos: tras la cinemática inversa, brazo, antebrazo, muslo y pierna miden lo que miden (con el estirón de
   animación como mucho) y el cuello tiene siempre la misma longitud, en todas las poses de la hoja.
2. Referencias: cada luchador en seis poses, rasterizado por pygame (determinista), comparado con
   tests/ref. Si un cambio es intencionado: `python tools/hoja.py --refs` y revisar las imágenes nuevas.
"""
import math
import os
import sys

import pygame
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import hoja  # noqa: E402

from tatamido.anatomy import BODIES, LOOKS  # noqa: E402
from tatamido.config import LIGHT_RIGS  # noqa: E402
from tatamido.data import FIGHTERS  # noqa: E402
from tatamido.render_fighter import _leg_lengths, _neck_len, build_figure  # noqa: E402

pygame.init()


def _d(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


@pytest.mark.parametrize("fd", FIGHTERS, ids=[f["id"] for f in FIGHTERS])
def test_bone_lengths(fd):
    B = BODIES[LOOKS[fd["id"]]["body"]]
    U, F = B.get("arm", (30.0, 25.5))
    T, S = _leg_lengths(fd)
    nl = _neck_len(fd)
    for pn in hoja.POSES:
        d, pose = hoja.pose_for(fd, pn)
        j = build_figure(d, pose, LIGHT_RIGS[1], raw=True).joints
        for s in ("l", "r"):
            ua, fa = _d(j["sh_" + s], j["el_" + s]), _d(j["el_" + s], j["hd_" + s])
            assert U * 0.995 <= ua <= U * 1.26 and abs(ua / U - fa / F) < 0.01, (pn, s, ua, fa)
            th, sh = _d(j["hip"], j["kn_" + s]), _d(j["kn_" + s], j["ft_" + s])
            assert T * 0.995 <= th <= T * 1.21 and abs(th / T - sh / S) < 0.01, (pn, s, th, sh)
        assert abs(_d(j["chest"], j["head"]) - nl) < 0.05, (pn, _d(j["chest"], j["head"]), nl)


def _arr(img):
    a = pygame.surfarray.pixels_alpha(img).astype(int)
    c = pygame.surfarray.array3d(img).astype(int)
    return c, a


@pytest.mark.parametrize("fid,pn", [(f["id"], p) for f in FIGHTERS for p in hoja.REF_POSES])
def test_reference_images(fid, pn):
    import numpy as np
    path = os.path.join(hoja.REF_DIR, f"{fid}_{pn}.png")
    if not os.path.exists(path):
        pytest.skip("sin referencia: python tools/hoja.py --refs")
    ref = pygame.image.load(path)
    img = hoja.ref_image(fid, pn)
    assert img.get_size() == ref.get_size(), (img.get_size(), ref.get_size())
    c0, a0 = _arr(ref.convert_alpha() if pygame.display.get_surface() else ref)
    c1, a1 = _arr(img)
    m0, m1 = a0 > 128, a1 > 128
    # el rasterizado es determinista (0 píxeles distintos sin cambios); una cabeza un 3 % mayor ya mueve 0,46 %
    xor = (m0 ^ m1).sum() / max(1, m0.sum())
    assert xor < 0.003, f"silueta distinta ({xor:.2%} de la figura)"
    both = m0 & m1
    diff = np.abs(c0 - c1).sum(axis=2)[both].mean() if both.any() else 0.0
    assert diff < 1.0, f"color distinto (diferencia media {diff:.2f})"
