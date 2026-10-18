"""Mecánicas de la capa competitiva: técnicas únicas, contra, escape de agarre, guardia rota, entrenamiento."""
from helpers import make_match, skip_intro, step
from tatamido.data import FIGHTERS
from tatamido.fighter import Fighter
from tatamido.inputs import KEYMAP_P1, KEYMAP_P2
from tatamido.match import Match

K1, K2 = KEYMAP_P1, KEYMAP_P2


def ready(id1, id2, gap=60):
    m = make_match(id1, id2)
    skip_intro(m)
    a, b = m.f
    a.x, b.x = -gap / 2, gap / 2
    return m, a, b


def special(m, f):
    f.start_move("special", m.audio)


def test_every_fighter_has_unique_special():
    names = set()
    for fd in FIGHTERS:
        m = make_match(fd["id"], "haruki")
        sp = m.f[0].moves["special"]
        assert sp.desc, fd["id"]
        names.add(sp.name)
    assert len(names) == 12


def test_armor_absorbs_one_hit():
    m, a, b = ready("ines", "lin")
    special(m, a)                        # Ushiro Mawashi con armadura
    step(m, (K2["light"],), 1)
    for _ in range(8):
        step(m)
    assert a.armor_used == 1
    assert a.state in (Fighter.ATTACK, Fighter.IDLE)    # no fue interrumpida


def test_overhead_beats_crouch_block():
    m, a, b = ready("ayo", "haruki", gap=120)
    step(m, (K2["down"], K2["block"]), 3)
    special(m, a)
    for _ in range(30):
        step(m, (K2["down"], K2["block"]))
        if b.vit < 1000:
            break
    assert b.vit < 1000


def test_counter_hit_bonus():
    m1, a1, b1 = ready("matias", "haruki")
    b1.start_move("heavy", m1.audio)      # rival en arranque de un golpe lento
    step(m1, (K1["light"],), 1)
    for _ in range(10):
        step(m1)
    m2, a2, b2 = ready("matias", "haruki")
    step(m2, (K1["light"],), 1)
    for _ in range(10):
        step(m2)
    assert (1000 - b1.vit) > (1000 - b2.vit)


def test_throw_tech_and_command_grab_untechable():
    m, a, b = ready("haruki", "ines", gap=40)
    step(m, (K1["grab"],), 1)
    for _ in range(12):
        step(m)
        if b.state == Fighter.THROWN:
            break
    assert b.state == Fighter.THROWN
    step(m, (K2["grab"],), 1)
    step(m, (), 2)
    assert b.state != Fighter.THROWN and b.vit == 1000
    # Uchi Mata (comando) no se escapa
    m, a, b = ready("haruki", "ines", gap=40)
    special(m, a)
    for _ in range(14):
        step(m)
        if b.state == Fighter.THROWN:
            break
    assert b.state == Fighter.THROWN
    step(m, (K2["grab"],), 1)
    step(m, (), 2)
    assert b.state == Fighter.THROWN


def test_guard_break():
    m, a, b = ready("kanoa", "lin")
    b.stam = 2.0
    step(m, (K2["block"],), 8)
    a.start_move("heavy", m.audio)
    for _ in range(30):
        step(m, (K2["block"],))
        if b.state == Fighter.STUN:
            break
    assert b.state == Fighter.STUN


def test_okabe_irimi_counters_strike():
    m, a, b = ready("okabe", "ines")
    special(m, a)
    step(m, (), 4)
    step(m, (K2["light"],), 1)
    for _ in range(12):
        step(m)
        if b.state == Fighter.THROWN:
            break
    assert b.state == Fighter.THROWN


def test_multihit_lands_several_times():
    m, a, b = ready("lin", "kanoa")
    b.stam = 100
    special(m, a)
    hits = 0
    last = b.vit
    for _ in range(40):
        step(m)
        if b.vit < last:
            hits += 1
            last = b.vit
    assert hits >= 3


def test_frame_advantage_measured_on_block():
    m, a, b = ready("ines", "haruki")
    step(m, (K2["block"],), 10)
    step(m, (K1["light"], K2["block"]), 1)
    for _ in range(40):
        step(m, (K2["block"],))
    assert m.last_adv is not None and m.last_adv[1] == "bloqueo"


def test_training_record_and_playback():
    m = make_match("ines", "haruki", ai=(False, True), training=True)
    skip_intro(m)
    m.toggle_record()
    step(m, (K1["right"],), 30)            # con J1 movemos al muñeco
    m.toggle_record()
    assert len(m.rec) == 30
    x0 = m.f[1].x
    m.toggle_playback()
    step(m, (), 30)
    assert m.f[1].x != x0
