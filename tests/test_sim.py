import pygame
from helpers import make_match, Keys, skip_intro, step, state_hash
from tatamido.data import FIGHTERS
from tatamido.fighter import Fighter
from tatamido.inputs import KEYMAP_P1, KEYMAP_P2, PlayerInput
from tatamido.match import Match
from tatamido.moves import build_moveset
from tatamido.replay import _rle, _unrle

K1, K2 = KEYMAP_P1, KEYMAP_P2


def test_moveset_sanity():
    for fd in FIGHTERS:
        ms = build_moveset(fd)
        for k, m in ms.items():
            assert m.startup > 0 and m.active > 0 and m.recovery > 0, (fd["id"], k)
            assert (m.damage > 0 and m.rng > 0) or m.counter
        assert ms["heavy"].damage > ms["medium"].damage > ms["light"].damage
        assert ms["light"].startup <= ms["medium"].startup <= ms["heavy"].startup


def test_determinism_ai_vs_ai():
    hashes = []
    for _ in range(2):
        m = make_match("tomasz", "ayo", ai=(True, True), seed=42)
        for _ in range(3000):
            m.update(Keys(), [])
        hashes.append(state_hash(m))
    assert hashes[0] == hashes[1]


def test_strike_beats_grab():
    m = make_match("ines", "haruki")
    skip_intro(m)
    a, b = m.f
    a.x, b.x = -30, 30
    # el rival inicia un agarre; nosotros golpeamos: el golpe llega antes (startup menor) y el agarre debe fallar
    step(m, (K2["grab"],))
    step(m, (K1["light"],))
    for _ in range(12):
        step(m)
    assert b.state in (Fighter.HIT, Fighter.STUN, Fighter.IDLE, Fighter.KNOCK)
    assert b.vit < 1000 or b.state == Fighter.STUN


def test_grab_beats_block():
    m = make_match("haruki", "ines")
    skip_intro(m)
    a, b = m.f
    a.x, b.x = -30, 30
    step(m, (K2["block"],), 3)           # el rival bloquea con antelación (sin ventana de parada)
    step(m, (K1["grab"], K2["block"]), 1)
    for _ in range(14):
        step(m, (K2["block"],))
    assert a.state == Fighter.THROWING or b.state in (Fighter.THROWN, Fighter.KNOCK)


def test_block_beats_strike_and_perfect_parry():
    m = make_match("ines", "haruki")
    skip_intro(m)
    a, b = m.f
    a.x, b.x = -30, 30
    step(m, (K2["block"],), 10)          # bloqueo "viejo": bloquea, sin parada
    step(m, (K1["light"], K2["block"]), 1)
    for _ in range(10):
        step(m, (K2["block"],))
    assert b.vit == 1000
    # parada perfecta: bloquear en los 4 frames previos al impacto
    m = make_match("ines", "haruki")
    skip_intro(m)
    a, b = m.f
    a.x, b.x = -30, 30
    step(m, (K1["light"],), 1)
    st = a.move.startup
    step(m, (), st - 2)
    step(m, (K2["block"],), 6)
    assert b.state == Fighter.PARRY or a.state == Fighter.STUN


def test_jogai_three_times_loses_round():
    m = make_match("kanoa", "lin")
    skip_intro(m)
    a, b = m.f
    for _ in range(3):
        if m.state != Match.FIGHT:
            break
        b.x = 460; b.state = Fighter.IDLE; b.y = 0
        step(m)
    assert b.jogai >= 3 or m.state == Match.ROUND_END


def test_ko_ends_round_and_match_flow():
    m = make_match("somchai", "tito")
    skip_intro(m)
    a, b = m.f
    b.vit = 1
    a.x, b.x = -30, 30
    step(m, (K1["light"],), 20)
    assert m.state in (Match.ROUND_END, Match.REPLAY) or b.vit <= 0


def test_input_buffer_and_cancel():
    m = make_match("matias", "elsa")
    skip_intro(m)
    a, b = m.f
    a.x, b.x = -30, 30
    step(m, (K1["light"],), 1)
    # golpe conecta; pulsamos fuerte justo después: debe cancelar en el fuerte
    step(m, (), a.move.startup + 1)
    assert a.hit_done
    step(m, (K1["heavy"],), 1)
    step(m, (), 4)   # el hitstop retrasa la cancelación unos frames
    assert a.move_key == "heavy"


def test_replay_roundtrip_matches_live():
    m = make_match("yeon", "okabe", ai=(True, True), seed=7)
    for _ in range(2500):
        m.update(Keys(), [])
    live = state_hash(m)
    log = _unrle(_rle(m.input_log))
    assert log == m.input_log
    r = make_match("yeon", "okabe", ai=(False, False), seed=7, replay=dict(inputs=log, seed=7, controls="moderno"))
    for _ in range(2500):
        r.update(Keys(), [])
    assert state_hash(r) == live
