import hashlib
from tatamido.audio import Audio
from tatamido.config import PRESETS
from tatamido.data import FIGHTER_BY_ID
from tatamido.fighter import Fighter
from tatamido.inputs import KEYMAP_P1, KEYMAP_P2, PlayerInput
from tatamido.match import Match


class SilentAudio(Audio):
    def __init__(self):
        self.ok = False; self.volume = 0; self.sounds = {}; self.rain_ch = None
        self.music_intensity = 0; self.beat_t = 0


def make_match(id1="haruki", id2="ines", ai=(False, False), seed=1, training=False, replay=None):
    p1, p2 = PlayerInput(KEYMAP_P1), PlayerInput(KEYMAP_P2)
    f1 = Fighter(FIGHTER_BY_ID[id1], 0, p1, ai[0], 0.9)
    f2 = Fighter(FIGHTER_BY_ID[id2], 1, p2, ai[1], 0.9)
    return Match(f1, f2, SilentAudio(), dict(PRESETS["bajo"]), training=training, seed=seed, replay=replay)


class Keys:
    """Simula pygame.key.get_pressed()."""
    def __init__(self, down=()):
        self.down = set(down)
    def __getitem__(self, k):
        return k in self.down


def skip_intro(m):
    while m.state == Match.INTRO:
        m.update(Keys(), [])


def step(m, keys=(), n=1):
    for _ in range(n):
        m.update(Keys(keys), [])


def state_hash(m):
    parts = []
    for f in m.f:
        parts.append((round(f.x, 4), round(f.y, 4), f.facing, f.state, f.timer, f.vit, round(f.stam, 4), round(f.kiai, 4), f.wazaari, f.jogai, f.wins))
    parts.append((m.round, m.timer, m.state, m.hitstop, m.slowmo))
    return hashlib.sha256(repr(parts).encode()).hexdigest()
