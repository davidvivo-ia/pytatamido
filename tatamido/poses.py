import math

from .config import *
from .fighter import Fighter

# ─────────────────────────────────────────────────────────────────────────────
#  POSES PROCEDIMENTALES
# ─────────────────────────────────────────────────────────────────────────────
# Cada pose es un diccionario de articulaciones en coordenadas locales (cm, mirando a la derecha,
# origen en la cadera, y negativo hacia arriba).  Interpolamos entre poses según el estado.
def pose_idle(t, fd, breath):
    bob = math.sin(t * 2.2) * (1.5 + 2.5 * breath)
    guard = {"gi": -20, "shorts": -30, "tank": -28, "changshan": -22, "mawashi": 6, "hakama": 2}.get(fd["outfit"], -18)
    return dict(hip=(0, -95 + bob), chest=(2, -140 + bob), head=(4, -176 + bob),
                sh_l=(-6, -138 + bob), sh_r=(8, -138 + bob),
                el_l=(14, -118 + bob), el_r=(28, -118 + bob),
                hd_l=(26, -128 + guard * 0.6 + bob), hd_r=(40, -128 + guard + bob),
                kn_l=(-12, -50), kn_r=(16, -50), ft_l=(-20, 0), ft_r=(22, 0))


def pose_walk(t, fd, back=False):
    s = math.sin(t * 9) * (-1 if back else 1)
    c = math.cos(t * 9)
    p = pose_idle(t, fd, 0)
    p["kn_l"] = (-12 + 14 * s, -52 - 6 * abs(c))
    p["kn_r"] = (14 - 14 * s, -52 - 6 * abs(c))
    p["ft_l"] = (-20 + 24 * s, -6 * max(0, c))
    p["ft_r"] = (22 - 24 * s, -6 * max(0, -c))
    return p


def pose_crouch(t, fd):
    return dict(hip=(0, -58), chest=(6, -98), head=(10, -132), sh_l=(0, -96), sh_r=(12, -96),
                el_l=(14, -82), el_r=(28, -80), hd_l=(28, -92), hd_r=(40, -96),
                kn_l=(-22, -36), kn_r=(26, -36), ft_l=(-26, 0), ft_r=(28, 0))


def pose_block(t, fd, crouch=False):
    p = pose_crouch(t, fd) if crouch else pose_idle(t, fd, 0)
    p["el_l"] = (12, p["sh_l"][1] + 20)
    p["el_r"] = (18, p["sh_r"][1] + 22)
    p["hd_l"] = (18, p["sh_l"][1] - 14)
    p["hd_r"] = (28, p["sh_r"][1] - 22)
    return p


def pose_jump(t, fd, vy):
    tuck = clamp(-vy / 16, -1, 1)
    return dict(hip=(0, -100), chest=(2, -145), head=(4, -181), sh_l=(-6, -142), sh_r=(8, -142),
                el_l=(4, -128), el_r=(26, -128), hd_l=(18, -150), hd_r=(40, -140),
                kn_l=(-6, -60 - 20 * tuck), kn_r=(20, -60 - 24 * tuck), ft_l=(-14, -24 * tuck), ft_r=(24, -30 * tuck))


def pose_attack(anim, phase, fd, kick=False):
    """phase 0..1 dentro del movimiento: startup (0..0.35), activo (..0.55), recovery."""
    base = pose_idle(0, fd, 0)
    ext = smoothstep(phase / 0.4) if phase < 0.4 else (1 - smoothstep((phase - 0.5) / 0.5)) if phase > 0.5 else 1.0
    lean = 10 * ext
    p = dict(base)
    if anim == "jab":
        p["hd_r"] = (lerp(40, 92, ext), -138)
        p["el_r"] = (lerp(28, 60, ext), -132)
        p["chest"] = (2 + lean * 0.6, -140)
        p["head"] = (4 + lean * 0.6, -176)
    elif anim == "cross":
        p["hd_l"] = (lerp(26, 100, ext), -140)
        p["el_l"] = (lerp(14, 66, ext), -136)
        p["hd_r"] = (lerp(40, 20, ext), -150)
        p["chest"] = (2 + lean, -140)
        p["head"] = (4 + lean, -176)
        p["hip"] = (lean * 0.5, -95)
    elif anim == "hook":
        p["hd_r"] = (lerp(40, 88, ext), lerp(-128, -150, ext))
        p["el_r"] = (lerp(28, 70, ext), lerp(-118, -128, ext))
        p["chest"] = (2 + lean * 1.4, -138)
        p["head"] = (4 + lean * 1.4, -174)
        p["hip"] = (lean * 0.8, -93)
        p["kn_r"] = (16 + lean, -50)
        p["ft_r"] = (22 + lean * 1.2, 0)
    elif anim == "kick_mid":
        p["kn_r"] = (lerp(16, 60, ext), lerp(-50, -95, ext))
        p["ft_r"] = (lerp(22, 118, ext), lerp(0, -100, ext))
        p["hip"] = (-6 * ext, -95)
        p["chest"] = (-10 * ext, -140)
        p["head"] = (-12 * ext, -176)
        p["hd_l"] = (18, -150)
    elif anim == "kick_high":
        p["kn_r"] = (lerp(16, 50, ext), lerp(-50, -120, ext))
        p["ft_r"] = (lerp(22, 108, ext), lerp(0, -160, ext))
        p["hip"] = (-10 * ext, -95)
        p["chest"] = (-18 * ext, -138)
        p["head"] = (-22 * ext, -172)
        p["hd_l"] = (-10, -150)
        p["hd_r"] = (30, -120)
    elif anim == "sweep":
        p = pose_crouch(0, fd)
        p["kn_r"] = (lerp(26, 60, ext), -34)
        p["ft_r"] = (lerp(28, 120, ext), -4)
        p["hd_l"] = (-20, -40)
    elif anim == "grab":
        p["hd_l"] = (lerp(26, 80, ext), -138)
        p["hd_r"] = (lerp(40, 84, ext), -118)
        p["el_l"] = (lerp(14, 50, ext), -130)
        p["el_r"] = (lerp(28, 56, ext), -116)
        p["chest"] = (2 + lean, -140)
        p["head"] = (4 + lean, -176)
        p["hip"] = (lean * 0.6, -95)
    elif anim in ("air_kick", "air_punch"):
        p = pose_jump(0, fd, 0)
        if anim == "air_kick":
            p["ft_r"] = (lerp(24, 110, ext), lerp(-30, -60, ext))
            p["kn_r"] = (lerp(20, 60, ext), -80)
        else:
            p["hd_r"] = (lerp(40, 90, ext), -120)
            p["el_r"] = (lerp(26, 60, ext), -124)
    elif anim == "special":
        if kick:
            p["kn_r"] = (lerp(16, 40, ext), lerp(-50, -130, ext))
            p["ft_r"] = (lerp(22, 120, ext), lerp(0, -150, ext))
            p["hip"] = (-14 * ext, -95)
            p["chest"] = (-26 * ext, -136)
            p["head"] = (-30 * ext, -170)
            p["hd_l"] = (-20 * ext, -150)
        else:
            p["hd_r"] = (lerp(40, 110, ext), -136)
            p["hd_l"] = (lerp(26, 96, ext), -124)
            p["el_r"] = (lerp(28, 70, ext), -130)
            p["el_l"] = (lerp(14, 60, ext), -122)
            p["chest"] = (2 + lean * 2, -138)
            p["head"] = (4 + lean * 2, -174)
            p["hip"] = (lean, -94)
            p["kn_r"] = (16 + lean * 1.5, -50)
            p["ft_r"] = (22 + lean * 2, 0)
    return p


def pose_hit(phase, fd):
    p = pose_idle(0, fd, 0)
    k = 1 - smoothstep(phase)
    p["head"] = (4 - 26 * k, -170 + 8 * k)
    p["chest"] = (2 - 14 * k, -138)
    p["hd_l"] = (12 - 10 * k, -120)
    p["hd_r"] = (30 - 20 * k, -110)
    p["el_l"] = (6, -118)
    p["el_r"] = (24, -112)
    return p


def pose_knock(phase, fd, airborne):
    """Volando hacia atrás y luego tumbado."""
    if airborne:
        return dict(hip=(0, -80), chest=(-30, -100), head=(-56, -112), sh_l=(-30, -100), sh_r=(-26, -96),
                    el_l=(-50, -120), el_r=(-44, -70), hd_l=(-70, -140), hd_r=(-60, -50),
                    kn_l=(30, -50), kn_r=(36, -70), ft_l=(60, -40), ft_r=(66, -70))
    return dict(hip=(0, -14), chest=(-40, -18), head=(-78, -16), sh_l=(-42, -22), sh_r=(-40, -12),
                el_l=(-66, -32), el_r=(-62, -8), hd_l=(-90, -24), hd_r=(-84, -6),
                kn_l=(34, -14), kn_r=(40, -22), ft_l=(70, -4), ft_r=(78, -14))


def pose_getup(phase, fd):
    a = pose_knock(1, fd, False)
    b = pose_crouch(0, fd)
    c = pose_idle(0, fd, 0)
    if phase < 0.6:
        return {k: (lerp(a[k][0], b[k][0], smoothstep(phase / 0.6)), lerp(a[k][1], b[k][1], smoothstep(phase / 0.6))) for k in a}
    t = smoothstep((phase - 0.6) / 0.4)
    return {k: (lerp(b[k][0], c[k][0], t), lerp(b[k][1], c[k][1], t)) for k in a}


def pose_throwing(phase, fd):
    p = pose_idle(0, fd, 0)
    k = math.sin(phase * math.pi)
    p["hip"] = (10 * k, -88 + 4 * k)
    p["chest"] = (18 * k, -134)
    p["head"] = (26 * k, -170)
    p["hd_l"] = (40 + 30 * k, -150 + 60 * phase)
    p["hd_r"] = (50 + 20 * k, -130 + 70 * phase)
    p["el_l"] = (24, -140 + 20 * phase)
    p["el_r"] = (36, -124 + 30 * phase)
    p["kn_l"] = (-20, -48)
    p["kn_r"] = (30, -48)
    p["ft_l"] = (-32, 0)
    p["ft_r"] = (40, 0)
    return p


def pose_thrown(phase, fd):
    """Rota en el aire: la pose se gira alrededor de la cadera."""
    ang = -math.pi * 1.1 * phase
    base = pose_jump(0, fd, 0)
    out = {}
    for k, (x, y) in base.items():
        rx, ry = rot(x, y + 100, ang)
        out[k] = (rx, ry - 60)
    return out


def pose_win(t, fd):
    p = pose_idle(t, fd, 0)
    k = (math.sin(t * 2) + 1) / 2
    p["hd_r"] = (30, -200 - 10 * k)
    p["el_r"] = (26, -170)
    p["hd_l"] = (-20, -120)
    p["el_l"] = (-6, -118)
    return p


def pose_bow(phase, fd):
    p = pose_idle(0, fd, 0)
    k = math.sin(clamp(phase, 0, 1) * math.pi) * 0.6
    for j in ("chest", "head", "sh_l", "sh_r", "el_l", "el_r", "hd_l", "hd_r"):
        x, y = p[j]
        y += 95
        rx, ry = rot(x, y, k)
        p[j] = (rx, ry - 95)
    p["hd_l"] = (p["hd_l"][0], p["hd_l"][1] + 20)
    p["hd_r"] = (p["hd_r"][0], p["hd_r"][1] + 20)
    return p


def pose_seiza(fd):
    return dict(hip=(0, -36), chest=(0, -82), head=(2, -118), sh_l=(-8, -80), sh_r=(8, -80),
                el_l=(-6, -58), el_r=(10, -58), hd_l=(6, -44), hd_r=(14, -44),
                kn_l=(28, -10), kn_r=(32, -14), ft_l=(-22, -2), ft_r=(-18, -6))


def pose_kiai(fd, phase, kind, idx):
    """Pose del Kiai Final: alterna golpes para las ráfagas, gira para las patadas."""
    if kind in ("barrage6", "barrage10", "knees", "liverhook"):
        anim = "cross" if idx % 2 == 0 else "jab"
        if kind == "knees":
            anim = "kick_mid"
        if kind == "liverhook":
            anim = "hook"
        return pose_attack(anim, 0.45, fd, kick=False)
    if kind in ("spinkick", "tornado"):
        base = pose_attack("kick_high", 0.45, fd, True)
        ang = phase * math.pi * 2 * (1 if kind == "spinkick" else 3)
        out = {}
        for k, (x, y) in base.items():
            if k in ("ft_l", "ft_r", "kn_l", "kn_r"):
                rx, ry = rot(x, y + 95, ang * 0.25)
                out[k] = (rx, ry - 95)
            else:
                out[k] = (x, y)
        return out
    if kind in ("throw", "kokyu", "tackle", "charge", "groundlock"):
        return pose_throwing(phase, fd)
    return pose_attack("special", 0.45, fd)


def blend(a, b, t):
    return {k: (lerp(a[k][0], b[k][0], t), lerp(a[k][1], b[k][1], t)) for k in a}


def fighter_pose(f: Fighter) -> dict:
    fd = f.fd
    st = f.state
    t = f.anim_t
    if st == Fighter.INTRO:
        if fd["id"] == "okabe":
            if f.timer < 110:
                return pose_seiza(fd)
            return blend(pose_seiza(fd), pose_idle(t, fd, 0), smoothstep((f.timer - 110) / 50))
        if f.timer < 40:
            return pose_idle(t, fd, 0)
        if f.timer < 140:
            return pose_bow((f.timer - 40) / 100, fd)
        return pose_idle(t, fd, 0)
    if st == Fighter.IDLE:
        return pose_idle(t, fd, f.breath)
    if st == Fighter.WALK:
        back = (f.vx * f.facing) < 0
        return pose_walk(t, fd, back)
    if st == Fighter.CROUCH:
        return pose_crouch(t, fd)
    if st == Fighter.BLOCK:
        return pose_block(t, fd, f.crouching)
    if st == Fighter.JUMP:
        return pose_jump(t, fd, f.vy)
    if st == Fighter.ATTACK and f.move:
        m = f.move
        ph = f.timer / max(1, m.total)
        return pose_attack(m.anim, ph, fd, m.kick)
    if st == Fighter.HIT:
        return pose_hit(f.timer / max(1, f.stun_len), fd)
    if st == Fighter.STUN:
        return pose_hit(0.3, fd)
    if st == Fighter.PARRY:
        return pose_block(t, fd)
    if st == Fighter.KNOCK or st == Fighter.KO:
        air = f.y < -2
        if st == Fighter.KO:
            air = False
        return pose_knock(1, fd, air)
    if st == Fighter.GETUP:
        return pose_getup(f.timer / 22, fd)
    if st == Fighter.THROWING:
        return pose_throwing(f.throw_phase, fd)
    if st == Fighter.THROWN:
        return pose_thrown(f.throw_partner.throw_phase if f.throw_partner else 0.5, fd)
    if st == Fighter.GROUND:
        return pose_crouch(t, fd) if f.ground_attacker else pose_knock(1, fd, False)
    if st == Fighter.WIN:
        return pose_win(t, fd)
    if st == Fighter.KIAI:
        dur = f.kiai_seq[-1][0] + 34 if f.kiai_seq else 60
        return pose_kiai(fd, f.timer / dur, fd["kiai_kind"], f.kiai_idx)
    return pose_idle(t, fd, 0)
