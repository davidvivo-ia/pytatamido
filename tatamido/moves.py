from dataclasses import dataclass
from typing import Dict

# ─────────────────────────────────────────────────────────────────────────────
#  MOVIMIENTOS
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Move:
    name: str
    kind: str              # strike | grab | kiai
    startup: int
    active: int
    recovery: int
    damage: int
    stamina: float
    rng: float             # alcance en cm
    height: str = "mid"    # high | mid | low
    knockdown: bool = False
    hitstop: int = 4
    push: float = 6.0
    hitstun: int = 14
    blockstun: int = 10
    kiai_gain: float = 6.0
    kick: bool = False
    anim: str = "jab"
    reach_y: float = -110  # altura del golpe

    @property
    def total(self):
        return self.startup + self.active + self.recovery


def build_moveset(fd: dict) -> Dict[str, Move]:
    F, V, D, A, T = fd["F"], fd["V"], fd["D"], fd["A"], fd["T"]
    spd = (6 - V)            # 1..5 → más alto, más lento
    reach = 62 + A * 14
    kick_m = fd["kicks"] >= 0.5
    kick_h = fd["kicks"] >= 0.3
    ms = {
        "light": Move("Golpe ligero", "strike", 3 + spd // 2, 3, 7 + spd, 42 + F * 7, 5, reach * 0.85, "high",
                      hitstop=3, push=4, hitstun=12, blockstun=8, kiai_gain=4, anim="jab"),
        "medium": Move("Golpe medio", "strike", 7 + spd // 2, 4, 13 + spd, 72 + F * 13, 9, reach * (1.15 if kick_m else 1.0), "mid",
                       hitstop=5, push=7, hitstun=16, blockstun=12, kiai_gain=7, kick=kick_m, anim="kick_mid" if kick_m else "cross"),
        "heavy": Move("Golpe fuerte", "strike", 13 + spd, 5, 22 + spd, 112 + F * 22, 16, reach * (1.3 if kick_h else 1.1), "mid",
                      knockdown=True, hitstop=9, push=14, hitstun=24, blockstun=16, kiai_gain=12, kick=kick_h,
                      anim="kick_high" if kick_h else "hook"),
        "low": Move("Barrido", "strike", 8 + spd // 2, 3, 15 + spd, 60 + F * 9, 8, reach * 0.95, "low",
                    hitstop=4, push=5, hitstun=15, blockstun=11, kiai_gain=6, kick=True, anim="sweep", reach_y=-25),
        "grab": Move("Agarre", "grab", 6 + spd // 2, 3, 17 + spd, 95 + T * 17, 14, 58 + (6 if fd["grappler"] else 0), "mid",
                     knockdown=True, hitstop=10, push=0, hitstun=30, kiai_gain=14, anim="grab"),
        "air": Move("Golpe aéreo", "strike", 5, 5, 10, 68 + F * 10, 7, reach, "high", hitstop=4, push=8, hitstun=15,
                    blockstun=10, kiai_gain=6, kick=fd["kicks"] > 0.3, anim="air_kick" if fd["kicks"] > 0.3 else "air_punch"),
        "special": Move("Técnica de estilo", "strike", 10 + spd // 2, 6, 18 + spd, 95 + F * 16 + T * 6, 18, reach * 1.25, "mid",
                        knockdown=T >= 4, hitstop=7, push=12, hitstun=20, blockstun=14, kiai_gain=10, kick=fd["kicks"] > 0.5,
                        anim="special"),
        "kiai": Move(fd["kiai"], "kiai", 10, 6, 30, 230 + T * 14 + F * 10, 0, reach * 1.1, "mid", knockdown=True, hitstop=12,
                     push=22, hitstun=60, kiai_gain=0, anim="kiai"),
    }
    return ms
