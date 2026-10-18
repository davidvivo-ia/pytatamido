import math

import pygame

from .camera import Camera
from .config import *
from .fighter import Fighter
from .poses import fighter_pose

# ─────────────────────────────────────────────────────────────────────────────
#  DIBUJO DEL LUCHADOR
# ─────────────────────────────────────────────────────────────────────────────
def draw_capsule(surf, c, a, b, r):
    if r < 1:
        r = 1
    pygame.draw.line(surf, c, a, b, int(r * 2))
    pygame.draw.circle(surf, c, (int(a[0]), int(a[1])), int(r))
    pygame.draw.circle(surf, c, (int(b[0]), int(b[1])), int(r))


LIGHT = (-0.72, -0.69)   # dirección de la luz en pantalla (de los shōji, arriba a la izquierda)


def draw_limb(surf, c, a, b, r, light=LIGHT):
    """Extremidad con volumen: sombra, tono medio desplazado hacia la luz y brillo especular estrecho.

    El desplazamiento usa sólo la componente de la luz perpendicular al eje del miembro,
    como en un cilindro iluminado de lado.
    """
    if r < 2:
        draw_capsule(surf, c, a, b, r)
        return
    ax, ay = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(ax, ay) or 1.0
    ax, ay = ax / ln, ay / ln
    d = light[0] * ax + light[1] * ay
    px, py = light[0] - d * ax, light[1] - d * ay
    draw_capsule(surf, shade(c, 0.72), a, b, r)
    o1 = (px * r * 0.22, py * r * 0.22)
    draw_capsule(surf, c, (a[0] + o1[0], a[1] + o1[1]), (b[0] + o1[0], b[1] + o1[1]), r * 0.76)
    o2 = (px * r * 0.48, py * r * 0.48)
    draw_capsule(surf, shade(c, 1.16), (a[0] + o2[0], a[1] + o2[1]), (b[0] + o2[0], b[1] + o2[1]), r * 0.3)


def shade_poly(surf, c, pts, light=LIGHT, k=0.18):
    """Polígono con sombra de forma: base oscura, interior iluminado desplazado hacia la luz."""
    pygame.draw.polygon(surf, shade(c, 0.74), pts)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    span = max(max(p[0] for p in pts) - min(p[0] for p in pts), 1)
    ox, oy = light[0] * span * k, light[1] * span * k * 0.5
    inner = [(cx + (p[0] - cx) * 0.8 + ox, cy + (p[1] - cy) * 0.9 + oy) for p in pts]
    pygame.draw.polygon(surf, c, inner)
    hi = [(cx + (p[0] - cx) * 0.35 + ox * 2.2, cy + (p[1] - cy) * 0.6 + oy * 1.5) for p in pts]
    pygame.draw.polygon(surf, shade(c, 1.1), hi)


def draw_fighter(surf, f: Fighter, cam: Camera, rig: dict, cfg: dict, debug=False, lut_bw=False):
    """Figura articulada con ropa, luz lateral, sudor, rojeces y rim light de jugador."""
    fd = f.fd
    pose = fighter_pose(f)
    f.lastpose = pose
    sc = f.scale
    fac = f.facing
    skin, hair_c = f.pal[2], f.pal[3]
    main, trim = f.pal[0], f.pal[1]
    key = rig["key"]
    amb = rig["ambient"]
    # dirección de la luz: entra por la izquierda (shōji al norte-oeste); la cara iluminada es la izquierda
    def lit(x_local):
        # x_local en mundo (ya con facing): más a la izquierda → más luz
        k = clamp(0.55 - x_local / 220.0, 0.0, 1.0)
        return k

    def col(c, xw, extra=1.0):
        k = lit(xw)
        base = tint(c, key, 0.35 * k)
        base = mixc(base, amb, 0.35 * (1 - k))
        base = shade(base, (0.78 + 0.42 * k) * extra)
        return base

    def WP(j):
        lx, ly = pose[j]
        return cam.P(f.x + lx * fac * sc, f.y + ly * sc)

    def WX(j):
        return pose[j][0] * fac * sc

    if f.squash > 0.02 and f.y >= 0:
        # aplastamiento al aterrizar: comprime verticalmente la pose
        q = 1 - 0.12 * f.squash
        pose = {k: (v[0] * (2 - q), v[1] * q) for k, v in pose.items()}
    J = {k: WP(k) for k in pose}
    r_limb = cam.S(7.5 * sc * f.width)
    r_arm = cam.S(6.2 * sc * f.width)
    outfit = fd["outfit"]
    LIMBS = (("sh_l", "el_l"), ("el_l", "hd_l"), ("sh_r", "el_r"), ("el_r", "hd_r"), ("hip", "kn_l"), ("kn_l", "ft_l"), ("hip", "kn_r"), ("kn_r", "ft_r"), ("hip", "chest"), ("chest", "head"))

    # sombra en el suelo
    if cfg.get("shadows", True):
        sx, sy = cam.P(f.x, 0)
        hgt = max(0.0, -f.y)
        sw = cam.S(70 * sc * f.width) * (1 - min(0.5, hgt / 400))
        sh = cam.S(14) * (1 - min(0.5, hgt / 400))
        shadow = pygame.Surface((int(sw * 2) + 2, int(sh * 2) + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (10, 6, 4, int(90 * (1 - min(0.6, hgt / 300)))), shadow.get_rect())
        surf.blit(shadow, (sx - sw + cam.S(12), sy - sh))

    # rim light del jugador cuando está activo
    active = f.state in (Fighter.ATTACK, Fighter.KIAI) and f.move is not None and f.move.startup <= f.timer < f.move.startup + f.move.active
    rim_a = 1.0 if active else f.rim
    if rim_a > 0.05 and not lut_bw:
        rc = f.player_color
        for a, b in (("sh_l", "el_l"), ("el_l", "hd_l"), ("sh_r", "el_r"), ("el_r", "hd_r"), ("hip", "kn_l"), ("kn_l", "ft_l"), ("hip", "kn_r"), ("kn_r", "ft_r"), ("hip", "chest")):
            draw_capsule(surf, mixc(rc, (255, 255, 255), 0.2), J[a], J[b], r_limb + cam.S(2.5))
        pygame.draw.circle(surf, rc, (int(J["head"][0]), int(J["head"][1])), int(cam.S(14 * sc) + cam.S(2.5)))

    # ----- contorno oscuro: la silueta se lee en negro puro -----
    if not lut_bw or True:
        oc = (18, 12, 10)
        ow = cam.S(2.2)
        for a, b in LIMBS:
            draw_capsule(surf, oc, J[a], J[b], (r_limb if a in ("hip", "kn_l", "kn_r") else r_arm) * (1.15 if outfit in ("gi", "hakama", "kurtka", "changshan") else 1.0) + ow)
        pygame.draw.circle(surf, oc, (int(J["head"][0]), int(J["head"][1])), int(cam.S(13.5 * sc) + ow))
        hx0, hy0 = J["hip"]
        slx0, sly0 = J["sh_l"]
        srx0, sry0 = J["sh_r"]
        wt0 = cam.S(26 * sc * f.width)
        pygame.draw.polygon(surf, oc, [(slx0 - cam.S(6) * fac - ow, sly0 - cam.S(6) - ow), (srx0 + cam.S(10) * fac + ow, sry0 - cam.S(6) - ow),
                                       (hx0 + wt0 * 0.75 + ow, hy0 + cam.S(8) + ow), (hx0 - wt0 * 0.75 - ow, hy0 + cam.S(8) + ow)])
        if outfit == "mawashi":
            bl = pygame.Rect(0, 0, int(wt0 * 1.9 + ow * 2), int(cam.S(80 * sc) + ow * 2))
            bl.center = (int(lerp(hx0, J["chest"][0], 0.4) + cam.S(4) * fac), int(lerp(hy0, J["chest"][1], 0.45)))
            pygame.draw.ellipse(surf, oc, bl)
    # ----- piernas -----
    pants_c = {"gi": main, "abada": main, "changshan": trim, "tank": main, "hakama": main, "kurtka": trim}.get(outfit)
    for hipk, kn, ft, order in (("hip", "kn_l", "ft_l", 0), ("hip", "kn_r", "ft_r", 1)):
        depth = 0.86 if order == 0 else 1.0
        if outfit == "hakama":
            # falda pesada que barre el tatami
            hx, hy = J["hip"]
            kx, ky = J[kn]
            fx_, fy_ = J[ft]
            sway = math.sin(f.anim_t * 3) * cam.S(4)
            poly = [(hx - cam.S(22 * sc), hy - cam.S(6)), (hx + cam.S(22 * sc), hy - cam.S(6)),
                    (fx_ + cam.S(34 * sc) * (1 if order else -1) + sway, fy_ + cam.S(2)), (fx_ - cam.S(10 * sc) * (1 if order else -1) + sway, fy_ + cam.S(2))]
            pygame.draw.polygon(surf, col(main, WX(kn), depth), poly)
            pygame.draw.lines(surf, shade(col(main, WX(kn), depth), 1.35), False, [poly[0], poly[3]], max(1, int(cam.S(1.2))))
            draw_limb(surf, col(skin, WX(ft), depth), (lerp(J[kn][0], J[ft][0], 0.7), lerp(J[kn][1], J[ft][1], 0.7)), J[ft], r_limb * 0.8)
            continue
        leg_c = col(skin, WX(kn), depth)
        if pants_c is not None:
            leg_c = col(pants_c, WX(kn), depth)
        thick = r_limb * (1.15 if outfit in ("gi", "hakama", "abada") else 1.0)
        draw_limb(surf, leg_c, J[hipk], J[kn], thick)
        if outfit in ("shorts", "mawashi", "singlet"):
            # pantalón corto / mawashi: muslo de ropa, pantorrilla de piel
            mid = (lerp(J[hipk][0], J[kn][0], 0.5), lerp(J[hipk][1], J[kn][1], 0.5))
            c2 = main if outfit != "singlet" else main
            draw_limb(surf, col(c2, WX(kn), depth), J[hipk], mid, thick * 1.1)
            draw_limb(surf, col(skin, WX(ft), depth), J[kn], J[ft], r_limb * 0.9)
        else:
            draw_limb(surf, leg_c, J[kn], J[ft], thick * 0.95)
        # pie
        fx_, fy_ = J[ft]
        foot_c = col(skin, WX(ft), depth)
        if outfit in ("kurtka", "singlet", "tank"):
            foot_c = col((40, 30, 24) if outfit != "tank" else (30, 30, 30), WX(ft), depth)
        pygame.draw.ellipse(surf, foot_c, (fx_ - cam.S(6), fy_ - cam.S(5), cam.S(20), cam.S(9)))
    # ----- torso -----
    hx, hy = J["hip"]
    cx, cy = J["chest"]
    slx, sly = J["sh_l"]
    srx, sry = J["sh_r"]
    wtor = cam.S(26 * sc * f.width)
    torso_poly = [(slx - cam.S(6) * fac, sly - cam.S(6)), (srx + cam.S(10) * fac, sry - cam.S(6)),
                  (hx + wtor * 0.75, hy + cam.S(8)), (hx - wtor * 0.75, hy + cam.S(8))]
    torso_c = {"gi": main, "kurtka": main, "changshan": main, "singlet": main, "tank": trim, "hakama": trim,
               "shorts": skin, "abada": skin, "mawashi": skin}[outfit]
    tc = col(torso_c, 0)
    shade_poly(surf, tc, torso_poly)
    if outfit == "mawashi":
        belly = pygame.Rect(0, 0, int(wtor * 1.9), int(cam.S(80 * sc)))
        belly.center = (int(lerp(hx, cx, 0.4) + cam.S(4) * fac), int(lerp(hy, cy, 0.45)))
        pygame.draw.ellipse(surf, col(skin, 0), belly)
        # mawashi
        pygame.draw.rect(surf, col(main, 0), (hx - wtor * 0.9, hy - cam.S(12), wtor * 1.8, cam.S(26)))
        pygame.draw.rect(surf, col(main, 0, 1.2), (hx - cam.S(5), hy - cam.S(12), cam.S(10), cam.S(34)))
    if outfit in ("gi", "changshan", "kurtka", "hakama"):
        # solapa en V con desorden según daño
        d = f.dishevel * 10
        lap = [(cx + cam.S(4 + d) * fac, cy + cam.S(10)), (slx, sly - cam.S(4)), (srx, sry - cam.S(4))]
        pygame.draw.lines(surf, shade(tc, 0.72), False, [lap[1], lap[0], lap[2]], max(1, int(cam.S(2))))
        pygame.draw.line(surf, shade(tc, 1.2), (lerp(slx, cx, 0.3), sly), (hx - wtor * 0.1, hy), max(1, int(cam.S(1.5))))
        # cinturón (se afloja con el daño)
        belt_c = trim if outfit != "kurtka" else (40, 40, 40)
        if outfit != "hakama":
            pygame.draw.rect(surf, col(belt_c, 0), (hx - wtor * 0.8, hy - cam.S(4), wtor * 1.6, cam.S(7)))
            tail = cam.S(26 + 14 * f.dishevel) * sc
            sw = math.sin(f.anim_t * 4 + f.x * 0.01) * cam.S(5) + f.vx * cam.S(1.5)
            pygame.draw.line(surf, col(belt_c, 0), (hx, hy), (hx - cam.S(4) + sw, hy + tail), max(1, int(cam.S(3))))
            pygame.draw.line(surf, col(belt_c, 0), (hx, hy), (hx + cam.S(6) - sw * 0.6, hy + tail * 0.9), max(1, int(cam.S(3))))
        else:
            pygame.draw.rect(surf, col(main, 0), (hx - wtor * 0.85, hy - cam.S(6), wtor * 1.7, cam.S(10)))
    if outfit == "singlet":
        pygame.draw.polygon(surf, col(trim, 0), [(cx - cam.S(6) * fac, cy + cam.S(12)), (cx + cam.S(6) * fac, cy + cam.S(12)), (hx + cam.S(8) * fac, hy - cam.S(4)), (hx - cam.S(8) * fac, hy - cam.S(4))])
    if outfit == "abada":
        pygame.draw.rect(surf, col(trim, 0), (hx - wtor * 0.8, hy - cam.S(4), wtor * 1.6, cam.S(6)))
        pygame.draw.rect(surf, col(hex2rgb("#2E8B57"), 0), (hx - wtor * 0.8, hy + cam.S(2), wtor * 1.6, cam.S(3)))
    if outfit == "shorts":
        # mongkol/prajied: brazaletes
        pass
    if outfit == "tank":
        pygame.draw.line(surf, col(trim, 0), (slx, sly), (srx, sry), max(1, int(cam.S(3))))
    # tatuaje sak yant (Somchai) / pecas (Elsa) — detalle sutil
    if fd["id"] == "somchai" and fac == -1:
        pygame.draw.lines(surf, shade(tc, 0.6), False, [(cx, cy), (cx + cam.S(8), cy + cam.S(14)), (cx - cam.S(8), cy + cam.S(14)), (cx, cy)], 1)

    # ----- brazos -----
    sleeve = {"gi": 1.0, "kurtka": 1.0, "changshan": 1.0, "hakama": 1.0, "tank": 0.0, "singlet": 0.0, "shorts": 0.0, "abada": 0.0, "mawashi": 0.0}[outfit]
    if fd["id"] == "ines":
        sleeve = 0.75
    arm_c_cloth = main if outfit not in ("hakama",) else trim
    for sh, el, hd, depth in (("sh_l", "el_l", "hd_l", 0.86), ("sh_r", "el_r", "hd_r", 1.0)):
        upper_c = col(arm_c_cloth if sleeve > 0 else skin, WX(el), depth)
        draw_limb(surf, upper_c, J[sh], J[el], r_arm * (1.2 if sleeve > 0 else 1.0))
        if sleeve >= 1.0:
            draw_limb(surf, upper_c, J[el], J[hd], r_arm * 1.15)
        elif sleeve > 0:
            mid = (lerp(J[el][0], J[hd][0], sleeve - 0.2), lerp(J[el][1], J[hd][1], sleeve - 0.2))
            draw_limb(surf, upper_c, J[el], mid, r_arm * 1.15)
            draw_limb(surf, col(skin, WX(hd), depth), mid, J[hd], r_arm * 0.9)
        else:
            fore_c = col(skin, WX(hd), depth)
            if f.redness > 0.05 and not lut_bw:
                fore_c = mixc(fore_c, (200, 60, 50), 0.35 * f.redness)
            draw_limb(surf, fore_c, J[el], J[hd], r_arm * 0.9)
        # mano / guantilla / prajied
        hxh, hyh = J[hd]
        hand_c = col(skin, WX(hd), depth)
        if fd["id"] == "matias":
            hand_c = (30, 30, 30)
        pygame.draw.circle(surf, hand_c, (int(hxh), int(hyh)), int(r_arm * 1.05))
        if fd["id"] == "somchai":
            ex, ey = J[el]
            pygame.draw.circle(surf, (250, 245, 230), (int(lerp(ex, J[sh][0], 0.3)), int(lerp(ey, J[sh][1], 0.3))), int(r_arm * 1.2), max(1, int(cam.S(2))))
    # ----- cabeza -----
    hdx, hdy = J["head"]
    rh = cam.S(13.5 * sc)
    head_c = col(skin, WX("head"))
    if f.redness > 0.1 and not lut_bw:
        head_c = mixc(head_c, (210, 90, 80), 0.18 * f.redness)
    pygame.draw.circle(surf, head_c, (int(hdx), int(hdy)), int(rh))
    # cuello
    pygame.draw.line(surf, col(skin, WX("head"), 0.9), (hdx, hdy + rh * 0.6), (cx, cy - cam.S(2)), max(1, int(cam.S(7 * sc))))
    # pelo
    hc = col(hair_c, WX("head"))
    style = fd["hair"]
    if style == "short":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.55)))
    elif style == "shaved":
        pygame.draw.arc(surf, mixc(head_c, hc, 0.35), (hdx - rh, hdy - rh, rh * 2, rh * 2), 0, math.pi, max(1, int(rh * 0.3)))
        # barba corta
        pygame.draw.arc(surf, mixc(head_c, hc, 0.4), (hdx - rh * 0.9, hdy - rh * 0.7, rh * 1.8, rh * 1.7), math.pi, 2 * math.pi, max(1, int(rh * 0.25)))
    elif style == "braid":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.5)))
        sw = math.sin(f.anim_t * 5) * cam.S(4) - f.vx * cam.S(1.2)
        pygame.draw.line(surf, hc, (hdx - rh * 0.6 * fac, hdy), (hdx - rh * 1.3 * fac + sw, hdy + rh * 2.6), max(2, int(rh * 0.35)))
    elif style == "ponytail":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.5)))
        sw = math.sin(f.anim_t * 6) * cam.S(6) - f.vx * cam.S(2.5)
        pts = [(hdx - rh * 0.5 * fac, hdy - rh * 0.7), (hdx - rh * 1.6 * fac + sw * 0.5, hdy - rh * 0.2), (hdx - rh * 2.4 * fac + sw, hdy + rh * 1.4)]
        pygame.draw.lines(surf, hc, False, pts, max(2, int(rh * 0.4)))
    elif style == "locs":
        for i in range(6):
            a = -math.pi * 0.1 + i * math.pi * 0.24
            sw = math.sin(f.anim_t * 7 + i) * cam.S(3)
            pygame.draw.line(surf, hc, (hdx + math.cos(a) * rh * 0.6, hdy - math.sin(a) * rh * 0.8),
                             (hdx + math.cos(a) * rh * 1.5 + sw, hdy - math.sin(a) * rh * 1.5 + rh * 0.6), max(2, int(rh * 0.25)))
    elif style == "chonmage":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.5)))
        pygame.draw.ellipse(surf, shade(hc, 1.6), (hdx - rh * 0.35, hdy - rh * 1.4, rh * 0.7, rh * 0.55))
    elif style == "bob":
        pygame.draw.arc(surf, hc, (hdx - rh * 1.08, hdy - rh * 1.1, rh * 2.16, rh * 2.3), 0, math.pi, max(2, int(rh * 0.7)))
        pygame.draw.rect(surf, hc, (hdx - rh * 1.08, hdy - rh * 0.2, rh * 0.45, rh * 1.1))
        pygame.draw.rect(surf, hc, (hdx + rh * 0.63, hdy - rh * 0.2, rh * 0.45, rh * 1.1))
    elif style == "curly":
        for i in range(7):
            a = i * math.pi / 6
            pygame.draw.circle(surf, hc, (int(hdx + math.cos(a) * rh * 0.8), int(hdy - math.sin(a) * rh * 0.9)), int(rh * 0.4))
    elif style == "white":
        pygame.draw.arc(surf, hc, (hdx - rh, hdy - rh * 1.05, rh * 2, rh * 2), 0, math.pi, max(2, int(rh * 0.4)))
        pygame.draw.circle(surf, hc, (int(hdx - rh * 0.3 * fac), int(hdy - rh * 1.1)), int(rh * 0.3))
        pygame.draw.arc(surf, mixc(head_c, hc, 0.5), (hdx - rh * 0.8, hdy - rh * 0.5, rh * 1.6, rh * 1.6), math.pi * 1.1, math.pi * 1.9, max(1, int(rh * 0.2)))
    # rostro: ojo y ceja (expresión por esfuerzo)
    ex = hdx + rh * 0.45 * fac
    ey = hdy - rh * 0.1
    eye_open = 1.0 if f.state not in (Fighter.HIT, Fighter.KNOCK, Fighter.KO, Fighter.STUN) else 0.3
    pygame.draw.ellipse(surf, (20, 14, 12), (ex - rh * 0.16, ey - rh * 0.12 * eye_open, rh * 0.32, rh * 0.24 * eye_open + 1))
    brow = -rh * 0.1 * f.breath - (rh * 0.12 if f.state in (Fighter.ATTACK, Fighter.KIAI) else 0)
    pygame.draw.line(surf, shade(head_c, 0.55), (ex - rh * 0.3, ey - rh * 0.32 + brow), (ex + rh * 0.3, ey - rh * 0.34 + brow * 0.5), max(1, int(cam.S(1.5))))
    # boca: jadeo si está agotado
    if f.exhausted or f.state in (Fighter.HIT, Fighter.KIAI):
        pygame.draw.ellipse(surf, (60, 30, 30), (hdx + rh * 0.4 * fac - rh * 0.12, hdy + rh * 0.4, rh * 0.24, rh * 0.22))
    if fd["id"] == "matias":
        pygame.draw.line(surf, shade(hc, 0.9), (hdx + rh * 0.15 * fac, hdy + rh * 0.38), (hdx + rh * 0.75 * fac, hdy + rh * 0.35), max(1, int(cam.S(1.6))))
    # sudor: brillo especular sobre piel
    if f.sweat > 0.05 and not lut_bw:
        glint = (255, 255, 250)
        a = int(140 * f.sweat)
        gl = pygame.Surface((int(rh * 2) + 4, int(rh * 2) + 4), pygame.SRCALPHA)
        pygame.draw.circle(gl, (*glint, a), (int(rh * 0.65), int(rh * 0.6)), max(1, int(rh * 0.28)))
        surf.blit(gl, (hdx - rh, hdy - rh))
        for sh, el, hd in (("sh_r", "el_r", "hd_r"),):
            if sleeve < 1.0:
                ax, ay = J[el]
                bx, by = J[hd]
                pygame.draw.line(gl, (*glint, a), (0, 0), (1, 1), 1)
                pygame.draw.line(surf, mixc(skin, glint, 0.6), (ax - cam.S(3), ay), (bx - cam.S(3), by), 1)
    # debug
    if debug:
        hb = f.hurtbox()
        p1 = cam.P(hb.left, hb.top)
        p2 = cam.P(hb.right, hb.bottom)
        pygame.draw.rect(surf, (80, 200, 255), (p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]), 1)
        at = f.hitbox()
        if at:
            p1 = cam.P(at.left, at.top)
            p2 = cam.P(at.right, at.bottom)
            pygame.draw.rect(surf, (255, 60, 60), (p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]), 2)


def draw_trail(layer, f: Fighter, cam: Camera):
    """Estelas translúcidas (afterimages) en el color del jugador durante los golpes fuertes."""
    if not f.trail:
        return
    sc = f.scale
    for pose, x, y, fac, life in f.trail:
        a = int(70 * life / 14)
        c = (*f.player_color, a)
        J = {k: cam.P(x + v[0] * fac * sc, y + v[1] * sc) for k, v in pose.items()}
        r = max(1, int(cam.S(7 * sc * f.width)))
        for a_, b_ in (("sh_l", "el_l"), ("el_l", "hd_l"), ("sh_r", "el_r"), ("el_r", "hd_r"), ("hip", "kn_l"), ("kn_l", "ft_l"), ("hip", "kn_r"), ("kn_r", "ft_r"), ("hip", "chest")):
            pygame.draw.line(layer, c, J[a_], J[b_], r * 2)
        pygame.draw.circle(layer, c, (int(J["head"][0]), int(J["head"][1])), int(cam.S(13 * sc)))


def draw_portrait(surf, fd: dict, rect: pygame.Rect, rig: dict, selected=False, locked=False):
    """Retrato procedimental para la pantalla de selección: busto con luz lateral."""
    pal = [hex2rgb(c) for c in fd["pal"]]
    bg = pygame.Surface(rect.size)
    for i in range(rect.h):
        t = i / rect.h
        bg.fill(mixc(shade(pal[0], 0.25), (12, 10, 10), t), (0, i, rect.w, 1))
    cx, cy = rect.w // 2, int(rect.h * 0.62)
    r = int(rect.w * 0.19)
    skin = pal[2]
    if locked:
        skin = (40, 40, 42)
        pal = [(30, 30, 32)] * 4
    # hombros
    pygame.draw.ellipse(bg, shade(pal[0] if fd["outfit"] not in ("shorts", "abada", "mawashi") else skin, 0.9), (cx - r * 2.4, cy + r * 0.9, r * 4.8, r * 2.6))
    pygame.draw.polygon(bg, shade(pal[0] if fd["outfit"] not in ("shorts", "abada", "mawashi") else skin, 0.75), [(cx - r * 0.7, cy + r * 1.1), (cx + r * 0.7, cy + r * 1.1), (cx, cy + r * 2.2)])
    # luz lateral sobre la cabeza
    pygame.draw.circle(bg, shade(skin, 0.7), (cx, cy), r)
    hl = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(hl, (255, 230, 200, 110), (int(r * 0.7), int(r * 0.85)), int(r * 0.75))
    bg.blit(hl, (cx - r, cy - r))
    hc = pal[3]
    if fd["hair"] in ("short", "braid", "ponytail", "chonmage", "white", "shaved"):
        pygame.draw.arc(bg, hc if fd["hair"] != "shaved" else mixc(skin, hc, 0.4), (cx - r, cy - r * 1.05, r * 2, r * 2), 0, math.pi, max(2, int(r * 0.5)))
    elif fd["hair"] == "bob":
        pygame.draw.arc(bg, hc, (cx - r * 1.1, cy - r * 1.1, r * 2.2, r * 2.3), 0, math.pi, int(r * 0.7))
    elif fd["hair"] in ("locs", "curly"):
        for i in range(7):
            a = i * math.pi / 6
            pygame.draw.circle(bg, hc, (int(cx + math.cos(a) * r * 0.85), int(cy - math.sin(a) * r * 0.9)), int(r * 0.38))
    if not locked:
        pygame.draw.ellipse(bg, (20, 14, 12), (cx - r * 0.5, cy - r * 0.1, r * 0.3, r * 0.2))
        pygame.draw.ellipse(bg, (20, 14, 12), (cx + r * 0.2, cy - r * 0.1, r * 0.3, r * 0.2))
    surf.blit(bg, rect.topleft)
    pygame.draw.rect(surf, (255, 220, 160) if selected else (60, 50, 44), rect, 3 if selected else 1)
