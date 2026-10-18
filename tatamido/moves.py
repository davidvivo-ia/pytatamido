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
    # propiedades de las técnicas únicas
    armor: int = 0         # golpes que absorbe durante arranque y frames activos
    overhead: bool = False # no se puede bloquear agachado
    invuln: int = 0        # frames iniciales invulnerables a golpes (anti-aéreo)
    multihit: int = 1      # impactos durante los frames activos
    dash: float = 0.0      # avance durante el arranque
    ground: bool = False   # si conecta, pasa siempre a la fase de suelo
    counter: bool = False  # guardia de contraataque (no golpea: responde)
    techable: bool = True  # los agarres normales se pueden escapar; los de comando no
    whiff_stam: float = 0  # aliento extra que cuesta fallar
    air_bonus: float = 1.0 # multiplicador de daño contra rivales en el aire
    desc: str = ""

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
    sp = SPECIALS.get(fd["id"])
    if sp:
        ms["special"] = sp(F, V, D, A, T, reach)
    ms["grab"].desc = "Rompe el bloqueo. Pierde contra cualquier golpe activo. Se escapa pulsando agarre al ser agarrado."
    ms["kiai"].desc = "Con la barra de Kiai llena. Cinemática con cámara lenta; si conecta, no se puede escapar."
    return ms


def _s(name, kind, st, ac, rc, dmg, stam, rng, **kw):
    return Move(name, kind, st, ac, rc, dmg, stam, rng, anim=kw.pop("anim", "special"), **kw)


# Técnica de estilo de cada luchador: Atrás + Medio (moderno) o ↓↘→ + Medio (clásico)
SPECIALS = {
    "haruki": lambda F, V, D, A, T, r: _s("Uchi Mata", "grab", 9, 4, 22, 150 + T * 18, 18, 82, knockdown=True, hitstop=10,
                                         hitstun=30, kiai_gain=16, techable=False, anim="grab",
                                         desc="Agarre de comando de largo alcance. Proyección limpia muy probable: busca el waza-ari."),
    "ines": lambda F, V, D, A, T, r: _s("Ushiro Mawashi Geri", "strike", 14, 5, 22, 175 + F * 10, 18, r * 1.3, knockdown=True,
                                       hitstop=11, push=16, hitstun=26, blockstun=16, kiai_gain=12, kick=True, armor=1, anim="kick_high",
                                       desc="Patada giratoria con armadura: absorbe un golpe y sigue. No da un paso atrás."),
    "tomasz": lambda F, V, D, A, T, r: _s("Abrazo del oso", "grab", 8, 4, 24, 120 + T * 14, 20, 72, knockdown=True, hitstop=10,
                                         hitstun=30, kiai_gain=14, techable=False, ground=True, anim="grab",
                                         desc="Agarre de comando que siempre termina en el suelo: allí manda el Sambo."),
    "ayo": lambda F, V, D, A, T, r: _s("Aú Batido", "strike", 13, 6, 20, 120 + F * 12, 16, r * 1.15, knockdown=True, hitstop=8,
                                      push=12, hitstun=22, blockstun=12, kiai_gain=11, kick=True, overhead=True, dash=4.2,
                                      anim="kick_high", desc="Rueda con patada que cae desde arriba: hay que bloquearla de pie."),
    "kanoa": lambda F, V, D, A, T, r: _s("Tsuppari", "strike", 9, 14, 20, 48 + F * 6, 20, r * 1.05, hitstop=4, push=7,
                                        hitstun=14, blockstun=10, kiai_gain=4, multihit=4, armor=1, dash=1.6, anim="cross",
                                        desc="Cuatro empujones de palma con armadura. Arrastra al rival hacia el jogai."),
    "lin": lambda F, V, D, A, T, r: _s("Lin Wan Kuen", "strike", 5, 12, 14, 30 + F * 5, 14, r * 0.95, hitstop=2, push=3,
                                      hitstun=12, blockstun=8, kiai_gain=3, multihit=5, anim="jab",
                                      desc="Cinco puñetazos en cadena, muy rápidos. Ideal tras una parada perfecta."),
    "tito": lambda F, V, D, A, T, r: _s("Puxada de guarda", "grab", 8, 5, 20, 80 + T * 10, 16, 74, knockdown=True, hitstop=8,
                                       hitstun=30, kiai_gain=12, techable=False, ground=True, invuln=8, anim="grab",
                                       desc="Se tira al suelo agarrando: esquiva golpes al empezar y lleva el combate a su terreno."),
    "somchai": lambda F, V, D, A, T, r: _s("Teep", "strike", 9, 5, 18, 95 + F * 10, 12, r * 1.45, hitstop=7, push=30,
                                          hitstun=18, blockstun=16, kiai_gain=9, kick=True, anim="kick_mid",
                                          desc="Patada frontal de empuje con el mayor alcance del juego. Controla la distancia."),
    "yeon": lambda F, V, D, A, T, r: _s("Dwi Chagi", "strike", 8, 4, 18, 125 + F * 10, 13, r * 1.35, knockdown=True, hitstop=9,
                                       push=18, hitstun=22, blockstun=14, kiai_gain=11, kick=True, anim="kick_mid",
                                       desc="Patada atrás giratoria: rápida y larga. Castiga a quien camina hacia ella."),
    "elsa": lambda F, V, D, A, T, r: _s("Single Leg", "grab", 12, 5, 22, 140 + T * 14, 18, 70, knockdown=True, hitstop=10,
                                       hitstun=30, kiai_gain=14, techable=False, dash=6.5, whiff_stam=28, anim="grab",
                                       desc="Derribo a la carrera desde media distancia. Si falla, le cuesta mucho aliento."),
    "matias": lambda F, V, D, A, T, r: _s("Uppercut de Rosario", "strike", 5, 4, 22, 115 + F * 12, 14, r * 0.95, knockdown=True,
                                         hitstop=9, push=10, hitstun=24, blockstun=14, kiai_gain=10, invuln=7, air_bonus=1.5,
                                         reach_y=-150, anim="hook",
                                         desc="Gancho ascendente invulnerable al empezar: el mejor anti-aéreo. Más daño a rivales en el aire."),
    "okabe": lambda F, V, D, A, T, r: _s("Irimi", "counter", 3, 28, 14, 0, 10, 1, kiai_gain=8, counter=True, anim="special",
                                        desc="Guardia de contraataque: si le golpean durante ella, proyecta al rival con su propia inercia."),
}

