import math
import random
from typing import List, Optional

import pygame

from .config import *
from .inputs import ACTIONS, PlayerInput, mirror_dir
from .moves import Move, build_moveset

# ─────────────────────────────────────────────────────────────────────────────
#  LUCHADOR
# ─────────────────────────────────────────────────────────────────────────────
class Fighter:
    # Estados
    IDLE, WALK, CROUCH, JUMP, ATTACK, BLOCK, HIT, KNOCK, GETUP, THROWN, THROWING, KO, WIN, INTRO, KIAI, GROUND, PARRY, STUN = range(18)

    def __init__(self, fd: dict, side: int, pinput: PlayerInput, is_ai=False, difficulty=0.6):
        self.fd = fd
        self.side = side              # 0 izquierda (P1), 1 derecha (P2)
        self.inp = pinput
        self.is_ai = is_ai
        self.difficulty = difficulty
        self.moves = build_moveset(fd)
        self.pal = [hex2rgb(c) for c in fd["pal"]]
        self.scale = fd["h"]
        self.width = fd["w"]
        self.mass = 0.8 + 0.25 * fd["w"] + 0.1 * fd["F"]
        self.player_color = (255, 170, 80) if side == 0 else (90, 170, 255)
        self.reset_match()

    # ----- ciclo de vida -----
    def reset_match(self):
        self.wins = 0
        self.reset_round(full=True)

    def reset_round(self, full=True):
        self.x = -170.0 if self.side == 0 else 170.0
        self.y = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.facing = 1 if self.side == 0 else -1
        self.state = Fighter.INTRO
        self.timer = 0
        self.move: Optional[Move] = None
        self.move_key = ""
        self.hit_done = False
        self.vit = VIT_MAX
        self.stam = STAM_MAX
        if full:
            self.kiai = 0.0
        self.wazaari = 0
        self.jogai = 0
        self.crouching = False
        self.blocking = False
        self.block_timer = 0           # frames desde que empezó a bloquear (ventana de parada)
        self.exhausted = False
        self.combo = 0
        self.sweat = 0.0
        self.redness = 0.0
        self.dishevel = 0.0
        self.breath = 0.0
        self.anim_t = 0.0
        self.throw_partner: Optional["Fighter"] = None
        self.throw_phase = 0.0
        self.stun_frames = 0
        self.kiai_seq = []
        self.kiai_idx = 0
        self.ai_timer = 0
        self.ai_plan = {}
        self.ai_hold_block = 0
        self.last_hit_by = ""
        self.lastpose = None
        self.mash = 0
        self.rim = 0.0
        self.landed_hits = 0
        self.ground_attacker = False
        self.stun_len = 10
        self.trail: List[tuple] = []     # estelas (afterimages) de golpes fuertes
        self.hit_count = 0               # impactos del movimiento actual (multigolpe)
        self.rehit_at = 0
        self.armor_used = 0
        self.throw_move = None           # movimiento con el que se inició la proyección
        self.combo_taken = 0             # golpes recibidos en el combo actual
        self.squash = 0.0                # aplastamiento al aterrizar

    # ----- geometría -----
    @property
    def height_cm(self):
        return 175 * self.scale * (0.62 if self.crouching and self.state in (Fighter.CROUCH, Fighter.BLOCK) else 1.0)

    def hurtbox(self) -> pygame.Rect:
        if self.state in (Fighter.KNOCK, Fighter.KO, Fighter.GROUND, Fighter.THROWN):
            return pygame.Rect(int(self.x - 55), -40, 110, 40)
        w = int(40 * self.width)
        h = int(self.height_cm)
        return pygame.Rect(int(self.x - w / 2), int(self.y - h), w, h)

    def hitbox(self) -> Optional[pygame.Rect]:
        if self.state not in (Fighter.ATTACK, Fighter.KIAI) or self.move is None:
            return None
        m = self.move
        if m.counter:
            return None
        if not (m.startup <= self.timer < m.startup + m.active):
            return None
        rng = m.rng
        yy = m.reach_y if m.height != "low" else -25
        if self.state == Fighter.JUMP or self.y < -5:
            yy = -90
        cx = self.x + self.facing * (20 + rng * 0.5)
        return pygame.Rect(int(cx - rng * 0.5), int(self.y + yy - 30), int(rng), 60)

    # ----- helpers -----
    def can_act(self):
        return self.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH) and self.stun_frames == 0

    def start_move(self, key: str, audio: "Audio"):
        m = self.moves[key]
        if self.stam < 4 and key != "kiai":
            return False
        self.state = Fighter.ATTACK if key != "kiai" else Fighter.KIAI
        self.move = m
        self.move_key = key
        self.timer = 0
        self.hit_done = False
        self.hit_count = 0
        self.armor_used = 0
        self.stam = max(0.0, self.stam - m.stamina * (1.4 if self.exhausted else 1.0))
        self.blocking = False
        if key in ("heavy", "special", "grab"):
            audio.play("whoosh", 0.6, pan=self.x / 600)
        return True

    def take_damage(self, dmg: int, source: str):
        if self.exhausted:
            pass
        self.vit = max(0, self.vit - int(dmg))
        self.redness = min(1.0, self.redness + dmg / 900)
        self.sweat = min(1.0, self.sweat + dmg / 1500)
        self.dishevel = min(1.0, self.dishevel + dmg / 1200)
        self.last_hit_by = source

    def damage_level(self) -> int:
        return 0 if self.vit > 700 else 1 if self.vit > 400 else 2 if self.vit > 150 else 3

    # ----- actualización -----
    def update(self, opp: "Fighter", match: "Match"):
        inp = self.inp
        audio = match.audio
        self.timer += 1
        self.anim_t += 1 / 60
        self.rim = max(0.0, self.rim - 0.08)
        if self.stun_frames > 0:
            self.stun_frames -= 1
        # orientación
        if self.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH, Fighter.BLOCK):
            self.facing = 1 if opp.x >= self.x else -1
        # aliento: regeneración
        regen = 0.22 if self.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH) else 0.12
        if self.state == Fighter.BLOCK:
            regen = 0.1
        if self.state not in (Fighter.KO, Fighter.INTRO):
            self.stam = min(STAM_MAX, self.stam + regen * (1.5 if self.exhausted else 1.0))
        if self.stam <= 0.5:
            self.exhausted = True
        if self.exhausted and self.stam > 45:
            self.exhausted = False
        self.sweat = min(1.0, self.sweat + 0.00025 + 0.0004 * (1 - self.stam / STAM_MAX))
        self.breath = (1 - self.stam / STAM_MAX)

        if self.state in (Fighter.INTRO, Fighter.KO, Fighter.WIN):
            self.vx = 0
            return
        if self.state == Fighter.GROUND:
            return

        # dirección relativa
        fwd = inp.held["right"] if self.facing == 1 else inp.held["left"]
        back = inp.held["left"] if self.facing == 1 else inp.held["right"]
        down = inp.held["down"]
        up = inp.held["up"]

        # ----- máquina de estados -----
        st = self.state
        if st in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH):
            self.crouching = bool(down) and self.y >= 0
            speed = (3.2 + 0.55 * self.fd["V"]) * (0.65 if self.exhausted else 1.0)
            if self.crouching:
                self.state = Fighter.CROUCH
                self.vx = 0
            else:
                if fwd:
                    self.vx = self.facing * speed
                    self.state = Fighter.WALK
                elif back:
                    self.vx = -self.facing * speed * 0.8
                    self.state = Fighter.WALK
                else:
                    self.vx = 0
                    self.state = Fighter.IDLE
            # dash (doble toque)
            if match.controls == "clasico" and (inp.double_tap(6) or inp.double_tap(4)) and self.stam > 10:
                self.vx *= 2.2
                self.stam -= 0.4
            # salto
            if inp.buffered("up") and self.y >= 0 and not self.crouching and self.stam > 6:
                self.state = Fighter.JUMP
                self.vy = -(14 + self.fd["V"] * 0.9)
                self.vx = (self.facing * speed if fwd else -self.facing * speed * 0.8 if back else 0)
                self.stam -= 4
                self.timer = 0
            # bloqueo
            elif inp.held["block"]:
                self.state = Fighter.BLOCK
                self.block_timer = 0
                self.blocking = True
                self.vx = 0
                self.timer = 0
            # kiai final
            elif inp.buffered("kiai") and self.kiai >= KIAI_MAX and match.kiai_allowed():
                self.start_kiai(opp, match)
            # ataques (con buffer de entrada: la pulsación durante la recuperación no se pierde)
            elif inp.buffered("grab"):
                self.start_move("grab", audio)
            elif inp.buffered("heavy"):
                if self.crouching:
                    self.start_move("low", audio)
                else:
                    self.start_move("heavy", audio)
            elif inp.buffered("medium"):
                # técnica de estilo: moderno = atrás+medio; clásico = ↓↘→ + medio
                cmd = (inp.command([mirror_dir(d, self.facing) for d in (2, 3, 6)]) if match.controls == "clasico" else back)
                if cmd and self.stam > 12:
                    self.start_move("special", audio)
                elif self.crouching:
                    self.start_move("low", audio)
                else:
                    self.start_move("medium", audio)
            elif inp.buffered("light"):
                self.start_move("light", audio)

        elif st == Fighter.BLOCK:
            self.block_timer += 1
            self.vx = 0
            self.crouching = bool(down)
            if not inp.held["block"]:
                self.state = Fighter.IDLE
                self.blocking = False
            elif inp.pressed["grab"]:
                self.blocking = False
                self.start_move("grab", audio)

        elif st == Fighter.JUMP:
            if inp.pressed["light"] or inp.pressed["medium"] or inp.pressed["heavy"]:
                if self.move is None or self.move_key != "air":
                    self.move = self.moves["air"]
                    self.move_key = "air"
                    self.timer = 0
                    self.hit_done = False
                    self.state = Fighter.ATTACK

        elif st == Fighter.ATTACK:
            m = self.move
            # cancelación en golpe: ligero → medio/fuerte/agarre, medio → fuerte/especial. Sólo si conectó.
            if self.hit_done and self.y >= 0 and self.timer <= m.startup + m.active + 6:
                nxt = None
                if self.move_key == "light":
                    nxt = "heavy" if inp.buffered("heavy") else "medium" if inp.buffered("medium") else "grab" if inp.buffered("grab") else None
                elif self.move_key == "medium":
                    if inp.buffered("heavy"):
                        nxt = "heavy"
                    elif inp.buffered("medium") and self.stam > 12:
                        nxt = "special"
                if nxt and self.stam > 6:
                    self.start_move(nxt, audio)
                    self.timer = max(0, self.moves[nxt].startup // 3)   # cancel rápido
                    match.fx.spark(self.x + self.facing * 30, -120, 3, self.player_color)
                    return
            # multigolpe: el mismo movimiento vuelve a poder golpear tras un intervalo
            if self.hit_done and m.multihit > 1 and self.hit_count < m.multihit and self.timer >= self.rehit_at:
                self.hit_done = False
            # carrera durante el arranque
            if m.dash and self.timer < m.startup and self.y >= 0:
                self.vx = self.facing * m.dash
            # estela en frames activos de golpes fuertes
            if m.startup <= self.timer < m.startup + m.active and self.move_key in ("heavy", "special", "air") and self.timer % 2 == 0:
                self.push_trail()
            if self.y < 0 or self.move_key == "air":
                pass  # en el aire se mantiene la inercia
            else:
                self.vx *= 0.8
                # avance en el golpe
                if m.kind == "strike" and m.startup <= self.timer < m.startup + m.active:
                    self.vx = self.facing * (2.5 if m.kick else 1.8)
            if self.timer >= m.total:
                if m.whiff_stam and not self.hit_done:
                    self.stam = max(0.0, self.stam - m.whiff_stam)
                    match.fx.popup("¡FALLA!", (220, 200, 190), 30, H * 0.45, 40)
                self.state = Fighter.IDLE if self.y >= 0 else Fighter.JUMP
                self.move = None
                self.move_key = ""

        elif st == Fighter.HIT:
            self.vx *= 0.82
            if self.timer >= self.stun_len:
                self.state = Fighter.IDLE

        elif st == Fighter.KNOCK:
            self.vx *= 0.9
            if self.y >= 0 and self.timer >= 40:
                self.state = Fighter.GETUP
                self.timer = 0

        elif st == Fighter.GETUP:
            self.vx = 0
            if self.timer >= 22:
                self.state = Fighter.IDLE

        elif st == Fighter.THROWN:
            # la posición la controla el que lanza
            if self.throw_partner is None or self.throw_partner.state != Fighter.THROWING:
                self.state = Fighter.KNOCK
                self.timer = 0
                self.vy = 0
                self.y = 0

        elif st == Fighter.THROWING:
            dur = 46
            p = self.timer / dur
            self.throw_phase = p
            self.vx = 0
            tgt = self.throw_partner
            if tgt is not None:
                ang = math.pi * smoothstep(p)
                r = 95 * self.scale
                tgt.x = self.x + self.facing * math.cos(ang) * r * 0.9
                tgt.y = -abs(math.sin(ang)) * 130
                tgt.facing = -self.facing
                if p >= 1.0:
                    tgt.y = 0
                    tgt.x = self.x - self.facing * 85
                    tgt.state = Fighter.KNOCK
                    tgt.timer = 0
                    tgt.vx = -self.facing * 2
                    match.on_throw_land(self, tgt)
                    self.state = Fighter.IDLE
                    self.throw_partner = None

        elif st == Fighter.STUN:
            self.vx = 0
            if self.timer >= self.stun_len:
                self.state = Fighter.IDLE

        elif st == Fighter.PARRY:
            self.vx = 0
            if self.timer >= 10:
                self.state = Fighter.IDLE

        elif st == Fighter.KIAI:
            if self.timer % 2 == 0:
                self.push_trail()
            self.update_kiai(opp, match)

        self.squash = max(0.0, self.squash - 0.08)
        # ----- física -----
        if self.state not in (Fighter.THROWN, Fighter.GROUND):
            self.x += self.vx
            if self.y < 0 or self.vy != 0:
                self.vy += GRAVITY
                self.y += self.vy
                if self.y >= 0:
                    self.y = 0
                    self.vy = 0
                    if self.state == Fighter.JUMP or (self.state == Fighter.ATTACK and self.move_key == "air"):
                        self.state = Fighter.IDLE
                        self.move = None
                        self.move_key = ""
                        self.squash = 1.0
                        match.fx.dust(self.x, 0, 6, 0.6)
                    elif self.state == Fighter.KNOCK:
                        match.fx.dust(self.x, 0, 14, 1.0)
                        match.add_decal(self.x)
                        match.audio.play("fall", 0.9, pan=self.x / 600)
                        match.shake(5)
        # límites de la zona
        self.x = clamp(self.x, -SAFE_HALF - 40, SAFE_HALF + 40)

    def push_trail(self):
        from .poses import fighter_pose
        self.trail.append((fighter_pose(self), self.x, self.y, self.facing, 14))
        if len(self.trail) > 6:
            self.trail.pop(0)

    # ----- recibir un golpe -----
    def on_hit(self, m: Move, attacker: "Fighter", match: "Match", counter=False):
        mult = 0.6 if attacker.exhausted else 1.0
        if counter:
            mult *= 1.25
        if self.y < -5:
            mult *= m.air_bonus
        dmg = int(m.damage * mult * (0.85 + 0.03 * (6 - self.fd["D"])))
        if self.state in (Fighter.HIT, Fighter.KNOCK) or self.y < 0:
            self.combo_taken += 1
            dmg = int(dmg * max(0.45, 0.85 ** self.combo_taken))  # escalado de combo
        else:
            self.combo_taken = 1
        self.take_damage(dmg, attacker.fd["id"])
        self.kiai = min(KIAI_MAX, self.kiai + m.kiai_gain * 0.6)
        attacker.kiai = min(KIAI_MAX, attacker.kiai + m.kiai_gain)
        attacker.landed_hits += 1
        self.blocking = False
        self.move = None
        self.move_key = ""
        knock = m.knockdown or self.vit <= 0 or self.y < 0
        if knock:
            self.state = Fighter.KNOCK
            self.timer = 0
            self.vy = -7 - m.push * 0.25
            self.vx = -self.facing * (m.push * 0.9 + 2) / self.mass
            self.y = min(self.y, -1)
        else:
            self.state = Fighter.HIT
            self.timer = 0
            self.stun_len = m.hitstun + (8 if counter else 0)
            self.vx = -self.facing * m.push / self.mass
        return dmg

    def on_block(self, m: Move, attacker: "Fighter", match: "Match") -> bool:
        """Devuelve True si la guardia se rompe (aliento agotado bloqueando)."""
        self.stam = max(0.0, self.stam - m.stamina * 0.6)
        if self.stam <= 0.0:
            self.state = Fighter.STUN
            self.stun_len = 42
            self.timer = 0
            self.blocking = False
            self.vx = -self.facing * 3
            self.stam = 18.0
            return True
        self.redness = min(1.0, self.redness + 0.04)
        self.kiai = min(KIAI_MAX, self.kiai + m.kiai_gain * 0.4)
        self.vx = -self.facing * m.push * 0.5 / self.mass
        self.stun_frames = m.blockstun
        self.state = Fighter.BLOCK
        self.block_timer = 99
        return False

    # ----- Kiai Final -----
    def start_kiai(self, opp: "Fighter", match: "Match"):
        self.kiai = 0.0
        self.state = Fighter.KIAI
        self.timer = 0
        self.move = self.moves["kiai"]
        self.move_key = "kiai"
        self.hit_done = False
        self.kiai_idx = 0
        kind = self.fd["kiai_kind"]
        # secuencia de impactos (frame, daño relativo)
        seqs = {
            "throw": [(26, 1.0)],
            "barrage6": [(14 + i * 7, 0.17) for i in range(6)],
            "barrage10": [(12 + i * 5, 0.1) for i in range(10)],
            "groundlock": [(22, 0.5), (46, 0.5)],
            "spinkick": [(24, 1.0)],
            "charge": [(20, 0.45), (34, 0.55)],
            "knees": [(16, 0.33), (28, 0.33), (40, 0.34)],
            "tornado": [(16, 0.3), (30, 0.3), (50, 0.4)],
            "tackle": [(22, 1.0)],
            "liverhook": [(24, 1.0)],
            "kokyu": [(22, 1.0)],
        }
        self.kiai_seq = seqs.get(kind, [(24, 1.0)])
        match.begin_kiai(self, opp)

    def update_kiai(self, opp: "Fighter", match: "Match"):
        self.vx = 0
        kind = self.fd["kiai_kind"]
        dur = self.kiai_seq[-1][0] + 34
        # ¿conecta? Se decide en el frame 8: debe estar en alcance y el rival no bloqueando.
        if self.timer == 8:
            dist = abs(opp.x - self.x)
            if dist > self.moves["kiai"].rng + 40 or (opp.state == Fighter.BLOCK and opp.blocking and kind not in ("throw", "kokyu", "tackle", "groundlock")):
                match.kiai_whiff(self)
                self.state = Fighter.ATTACK
                self.move = self.moves["heavy"]
                self.move_key = "heavy"
                self.timer = self.move.startup + self.move.active  # directo a recuperación
                return
            match.kiai_connect(self, opp)
            opp.state = Fighter.STUN
            opp.stun_len = dur + 10
            opp.timer = 0
            opp.facing = -self.facing
            opp.vx = 0
        if self.timer >= 8:
            # acercar al rival a distancia de impacto
            target = self.x + self.facing * 70
            if kind in ("charge", "tackle"):
                self.x += self.facing * 3.0
                target = self.x + self.facing * 70
            opp.x = lerp(opp.x, target, 0.3)
            if self.kiai_idx < len(self.kiai_seq) and self.timer >= self.kiai_seq[self.kiai_idx][0]:
                f, frac = self.kiai_seq[self.kiai_idx]
                self.kiai_idx += 1
                dmg = int(self.moves["kiai"].damage * frac)
                opp.take_damage(dmg, self.fd["id"])
                match.kiai_impact(self, opp, dmg, last=(self.kiai_idx == len(self.kiai_seq)))
            if kind in ("throw", "kokyu", "tackle") and self.timer >= self.kiai_seq[0][0] - 12 and self.timer <= self.kiai_seq[0][0] + 14:
                p = (self.timer - (self.kiai_seq[0][0] - 12)) / 26
                ang = math.pi * smoothstep(p)
                opp.x = self.x + self.facing * math.cos(ang) * 90
                opp.y = -abs(math.sin(ang)) * 150
            if self.timer >= dur:
                opp.y = 0
                opp.x = self.x + self.facing * (-80 if kind in ("throw", "kokyu") else 110)
                opp.state = Fighter.KNOCK
                opp.timer = 0
                opp.vy = 0
                self.state = Fighter.IDLE
                self.move = None
                self.move_key = ""
                match.end_kiai(self, opp, kind)

    # ----- IA -----
    def think(self, opp: "Fighter", match: "Match"):
        """IA por árbol de decisión sencillo con personalidad por estadísticas."""
        cmd = {a: False for a in ACTIONS}
        fd = self.fd
        if self.state in (Fighter.INTRO, Fighter.KO, Fighter.WIN):
            self.inp.ai_cmd = cmd
            return
        if self.state == Fighter.THROWN:
            if self.timer < 7 and match.rng.random() < 0.012 + 0.03 * self.difficulty:
                cmd["grab"] = True
            self.inp.ai_cmd = cmd
            return
        if self.state == Fighter.GROUND:
            # minijuego de sumisión: pulsa con ritmo
            if match.frame % max(2, 8 - fd["T"] - int(self.difficulty * 3)) == 0:
                cmd["grab"] = True
                cmd["block"] = True
            self.inp.ai_cmd = cmd
            return
        dist = abs(opp.x - self.x)
        toward = "right" if opp.x > self.x else "left"
        away = "left" if toward == "right" else "right"
        self.ai_timer -= 1
        diff = self.difficulty
        aggression = clamp(0.25 + 0.1 * (fd["F"] + fd["V"]) * 0.5 + (0.2 if fd["id"] in ("matias", "ines", "somchai") else 0) - (0.35 if fd["id"] == "okabe" else 0), 0.1, 0.95)
        reach = self.moves["medium"].rng
        opp_attacking = opp.state == Fighter.ATTACK and opp.move is not None and opp.timer < opp.move.startup + opp.move.active
        # reacción defensiva
        if opp_attacking and dist < opp.move.rng + 60 and self.can_act():
            if match.rng.random() < 0.25 + 0.12 * fd["D"] * diff:
                self.ai_hold_block = 10 + match.rng.randint(0, 12)
                if opp.move.kind == "grab" and match.rng.random() < 0.5 * diff:
                    cmd["light"] = True   # golpe interrumpe agarre
                    self.ai_hold_block = 0
        if self.ai_hold_block > 0:
            self.ai_hold_block -= 1
            cmd["block"] = True
            if opp.state == Fighter.ATTACK and opp.move and opp.move.kind == "grab":
                cmd["block"] = False
                cmd[away] = True
            self.inp.ai_cmd = cmd
            return
        if self.ai_timer <= 0:
            self.ai_timer = match.rng.randint(6, 18 - int(8 * diff))
            r = match.rng.random()
            plan = {}
            if self.kiai >= KIAI_MAX and dist < reach + 30 and match.rng.random() < 0.6 + 0.3 * diff:
                plan["kiai"] = True
            elif dist > reach * 1.6:
                if fd["id"] == "okabe":
                    plan[toward if match.rng.random() < 0.3 else "wait"] = True
                elif fd["id"] == "ayo" and match.rng.random() < 0.3:
                    plan[toward] = True
                    plan["up"] = True
                else:
                    plan[toward] = True
                    if fd["id"] == "yeon" and dist < reach * 1.9 and match.rng.random() < 0.4:
                        plan["heavy"] = True
            elif dist > reach * 1.05:
                if match.rng.random() < aggression:
                    plan[toward] = True
                    if fd["kicks"] > 0.5 and match.rng.random() < 0.4:
                        plan["medium"] = True
                else:
                    plan[match.rng.choice([toward, away, "wait"])] = True
            else:
                # en alcance
                if opp.state in (Fighter.KNOCK, Fighter.GETUP):
                    plan[away if match.rng.random() < 0.4 else "wait"] = True
                elif opp.state == Fighter.BLOCK or (opp.blocking and match.rng.random() < 0.6 * diff):
                    plan["grab"] = True  # agarre rompe bloqueo
                elif match.rng.random() < aggression + 0.2 * diff:
                    if fd["grappler"] and match.rng.random() < 0.45:
                        plan["grab"] = True
                    elif match.rng.random() < 0.15 and fd["T"] >= 3:
                        plan["medium"] = True
                        plan["back_special"] = True
                    else:
                        plan[match.rng.choices(["light", "medium", "heavy"], weights=[3, 3, 1 + fd["F"]])[0]] = True
                        if match.rng.random() < 0.2:
                            plan["down"] = True
                else:
                    plan[match.rng.choice(["block", away, "wait"])] = True
            self.ai_plan = plan
        p = self.ai_plan
        # nunca retroceder hacia el jogai: a menos de 60 cm del borde, el retroceso se convierte en bloqueo
        if p.get(away) and abs(self.x) > TATAMI_HALF - 60 and (self.x > 0) == (away == "right"):
            p[away] = False
            p["block"] = True
        # rival aturdido o en recuperación larga: castigo
        if opp.state in (Fighter.STUN, Fighter.PARRY) and dist < reach * 1.1 and self.can_act():
            p[toward] = True
            p["heavy" if self.kiai < KIAI_MAX else "kiai"] = True
        for k in ("left", "right", "up", "down", "block"):
            if p.get(k):
                cmd[k] = True
        if p.get("back_special"):
            cmd[away] = True
        # los botones son pulsaciones de un frame
        for k in ("light", "medium", "heavy", "grab", "kiai"):
            if p.get(k):
                cmd[k] = True
                p[k] = False
        if fd["id"] == "okabe" and opp_attacking and dist < 120 and match.rng.random() < 0.55 + 0.4 * diff:
            cmd["block"] = True   # Okabe vive de la parada perfecta
            if opp.timer < opp.move.startup - 3 and self.can_act() and match.rng.random() < 0.35 * diff:
                cmd["block"] = False
                cmd[away] = True
                cmd["medium"] = True   # Irimi: guardia de contraataque
        self.inp.ai_cmd = cmd
