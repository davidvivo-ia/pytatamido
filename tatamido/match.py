import math
import random
from typing import List, Optional, Tuple

import pygame

from .audio import Audio
from .camera import Camera
from .config import *
from .dojo import Dojo
from .fighter import Fighter
from .fx import Effects, Fonts
from .ui import draw as udraw, panel
from .moves import Move
from .inputs import PlayerInput
from .render_fighter import draw_fighter, draw_trail

def _union(rects):
    """Rectángulo que abarca todos (None si no hay ninguno), recortado a la pantalla de render."""
    rects = [r for r in rects if r is not None and r.w > 0 and r.h > 0]
    if not rects:
        return None
    u = rects[0].unionall(rects[1:]).clip(pygame.Rect(0, 0, int(round(1280 * RS)), int(round(720 * RS))))
    return u if u.w > 0 and u.h > 0 else None


# Marcadores y textos se colocan en la resolución lógica de la interfaz; la GPU la reescala.
# (El camino CPU sólo existe a 720p, donde ambas coinciden.)
W, H = LW, LH

# ─────────────────────────────────────────────────────────────────────────────
#  COMBATE
# ─────────────────────────────────────────────────────────────────────────────
class Match:
    INTRO, FIGHT, ROUND_END, REPLAY, MATCH_END = range(5)

    def __init__(self, f1: Fighter, f2: Fighter, audio: Audio, cfg: dict, controls="moderno", training=False, access=None,
                 seed: Optional[int] = None, replay: Optional[dict] = None):
        self.f = [f1, f2]
        self.seed = seed if seed is not None else random.randrange(1 << 30)
        self.rng = random.Random(self.seed)          # toda la lógica de combate usa este generador
        self.input_log: List[Tuple[int, int]] = []   # (máscara J1, máscara J2) por frame de simulación
        self.replay = replay                         # dict con 'inputs' si estamos reproduciendo
        self.replay_pos = 0
        self.combo_popup = [0, 0]
        # entrenamiento
        self.fa = None                    # medición de ventaja de frames en curso
        self.last_adv = None              # (ventaja, tipo)
        self.dummy_mode = "IA"
        self.rec: List[int] = []
        self.rec_state = "off"            # off | rec | play
        self.rec_i = 0
        self.reflect_layer = pygame.Surface((W, H), pygame.SRCALPHA)
        self.audio = audio
        self.cfg = cfg
        self.controls = controls
        self.training = training
        self.access = access or {}
        self.fx = Effects(cfg)
        self.dojo = Dojo(cfg)
        self.cam = Camera()
        self.round = 0
        self.frame = 0
        self.timer = ROUND_TIME * 60
        self.state = Match.INTRO
        self.state_t = 0
        self.hitstop = 0
        self.slowmo = 1.0
        self.slowmo_t = 0
        self.shake_v = 0.0
        self.round_msg = ""
        self.round_result = None
        self.winner: Optional[Fighter] = None
        self.history: List[tuple] = []     # para la repetición del ippon
        self.replay_frames: List[tuple] = []
        self.replay_i = 0
        self.replay_angle = 0
        self.ground = None                  # fase de suelo
        self.kiai_active: Optional[Fighter] = None
        self.last_second_played = False
        self.intensity = 0.0
        self.debug = False
        self.frame_data_overlay = training
        self.start_round()

    # ----- flujo de asaltos -----
    def start_round(self):
        self.round += 1
        for f in self.f:
            f.reset_round(full=(self.round == 1))
        self.timer = ROUND_TIME * 60
        self.state = Match.INTRO
        self.state_t = 0
        self.dojo.set_round(self.round)
        self.audio.rain(self.dojo.target_rig["rain"])
        self.fx.popups.clear()
        self.history.clear()
        self.ground = None
        self.kiai_active = None
        self.cam.override = None
        self.last_second_played = False
        if self.round == 1:
            self.fx.popup(f"ASALTO {self.round}", (250, 230, 200), 54, H * 0.22, 150, sub=self.dojo.target_rig["name"])
        else:
            self.fx.popup(f"ASALTO {self.round}" if self.round < 4 else "DESEMPATE", (250, 230, 200), 54, H * 0.22, 150, sub=self.dojo.target_rig["name"])

    def kiai_allowed(self):
        return self.state == Match.FIGHT and self.kiai_active is None and self.ground is None

    def shake(self, v):
        if self.access.get("shake", True):
            self.shake_v = max(self.shake_v, v)

    def add_decal(self, x):
        self.dojo.add_decal(x)

    def tension(self):
        low = min(self.f[0].vit, self.f[1].vit) / VIT_MAX
        t = 0.0
        if low < 0.2:
            t += 0.5
        if self.timer < 600:
            t += 0.5
        return t if self.cfg.get("tilt") else 0.0

    # ----- actualización -----
    def update(self, keys, joys):
        self.frame += 1
        self.state_t += 1
        # entradas: repetición grabada, IA o teclado/mando
        if self.replay is not None:
            inputs = self.replay["inputs"]
            if self.replay_pos < len(inputs):
                m1, m2 = inputs[self.replay_pos]
            else:
                m1 = m2 = 0
            self.replay_pos += 1
            self.f[0].inp.ai_cmd = PlayerInput.cmd_from_mask(m1)
            self.f[1].inp.ai_cmd = PlayerInput.cmd_from_mask(m2)
        else:
            for i, f in enumerate(self.f):
                if f.is_ai:
                    f.think(self.f[1 - i], self)
            if self.training:
                self.training_inputs(keys)
        for f in self.f:
            f.inp.poll(keys, joys)
        if self.training and self.rec_state == "rec":
            self.rec.append(self.f[1].inp.mask())
        if self.replay is None:
            self.input_log.append((self.f[0].inp.mask(), self.f[1].inp.mask()))
        tscale = self.slowmo
        if self.slowmo_t > 0:
            self.slowmo_t -= 1
            if self.slowmo_t == 0:
                self.slowmo = 1.0
        # cámara
        if self.state == Match.REPLAY:
            self.update_replay()
        else:
            self.cam.follow(self.f[0], self.f[1], self.tension())
        self.cam.shake_x = random.uniform(-1, 1) * self.shake_v * RS
        self.cam.shake_y = random.uniform(-1, 1) * self.shake_v * 0.6 * RS
        self.shake_v *= 0.85
        self.dojo.update(1 / 60, self.intensity)
        self.fx.update(tscale)
        self.intensity = lerp(self.intensity, 0.0, 0.01)

        if self.state == Match.INTRO:
            if self.state_t == 20:
                self.audio.play("taiko", 0.8)
            if self.state_t == 60:
                for f in self.f:
                    if f.fd["id"] == "kanoa":
                        self.fx.salt(f.x, -150)
                        self.audio.play("stomp", 1.0)
                        self.shake(6)
                        self.dojo.react(1.0)
                    if f.fd["id"] == "ines":
                        self.audio.play("kiai", 0.8, pan=f.x / 600)
            if self.state_t == 150:
                self.fx.popup("HAJIME!", (255, 236, 200), 72, H * 0.3, 70)
                self.audio.play("taiko", 1.0)
                self.audio.play("gong", 0.4)
            if self.state_t >= 160:
                self.state = Match.FIGHT
                self.state_t = 0
                for f in self.f:
                    f.state = Fighter.IDLE
            return

        if self.state == Match.FIGHT:
            if self.hitstop > 0:
                self.hitstop -= 1
                return
            # slow-mo: avanzamos la simulación sólo algunos frames
            if tscale < 1.0 and (self.frame % max(1, int(round(1 / tscale)))) != 0:
                return
            if self.ground is not None:
                self.update_ground()
                return
            if not self.training:
                self.timer -= 1
            if self.timer <= 60 and not self.last_second_played:
                self.last_second_played = True
            if self.timer in (180, 120, 60):
                self.audio.play("tick", 0.8)
            a, b = self.f
            a.update(b, self)
            b.update(a, self)
            self.resolve_collisions()
            self.check_throw_tech()
            self.resolve_hits()
            self.track_advantage()
            self.check_jogai()
            self.record_history()
            for i, f in enumerate(self.f):
                if f.state not in (Fighter.HIT, Fighter.KNOCK, Fighter.THROWN, Fighter.STUN) and f.y >= 0:
                    f.combo_taken = 0
                f.trail = [t[:4] + (t[4] - 1,) for t in f.trail if t[4] > 1]
                self.combo_popup[i] = max(0, self.combo_popup[i] - 1)
            if self.training:
                for f in self.f:
                    if f.state in (Fighter.IDLE, Fighter.WALK) and f.vit < VIT_MAX:
                        f.vit = min(VIT_MAX, f.vit + 4)
                        f.stam = STAM_MAX
                        f.kiai = max(f.kiai, 60)
            self.check_round_end()
            return

        if self.state == Match.ROUND_END:
            # la pared del asalto siguiente se hornea y se sube a la GPU ahora, un trozo por fotograma
            if self.state_t > 20:
                self.dojo.prebake(LIGHT_RIGS[min(self.round + 1, 4)])
            for f in self.f:
                f.anim_t += 1 / 60
                if f.state == Fighter.KNOCK and f.y < 0:
                    f.vy += GRAVITY
                    f.y = min(0.0, f.y + f.vy)
            if self.state_t == 1:
                self.dojo.react(2.0)
            if self.state_t >= 170:
                if self.round_result == "ippon" and self.replay_frames:
                    self.state = Match.REPLAY
                    self.state_t = 0
                    self.replay_i = 0
                    self.replay_angle = 0
                    self.fx.popup("REPETICIÓN", (230, 230, 230), 30, H * 0.1, 400)
                else:
                    self.after_round()
            return

        if self.state == Match.REPLAY:
            if self.state_t > 60 * 6 or self.replay_i >= len(self.replay_frames) * 3 - 1:
                self.after_round()
            return

        if self.state == Match.MATCH_END:
            for f in self.f:
                f.anim_t += 1 / 60
            return

    def after_round(self):
        winner = self.winner
        if winner is not None:
            winner.wins += 1
        if winner is not None and winner.wins >= 2:
            self.state = Match.MATCH_END
            self.state_t = 0
            winner.state = Fighter.WIN
            loser = self.f[1 - self.f.index(winner)]
            loser.state = Fighter.KO
            self.cam.override = None
            self.audio.play("applause", 0.9)
            self.dojo.applaud = 3.0
        else:
            self.start_round()

    # ----- colisiones de cuerpos -----
    def resolve_collisions(self):
        a, b = self.f
        if a.state in (Fighter.THROWN, Fighter.THROWING, Fighter.KIAI) or b.state in (Fighter.THROWN, Fighter.THROWING, Fighter.KIAI):
            return
        if a.state in (Fighter.KNOCK, Fighter.KO) or b.state in (Fighter.KNOCK, Fighter.KO):
            return
        min_d = 30 * (a.width + b.width)
        d = b.x - a.x
        if abs(d) < min_d and abs(a.y - b.y) < 120:
            push = (min_d - abs(d)) / 2
            s = 1 if d >= 0 else -1
            a.x -= push * s
            b.x += push * s

    # ----- resolución del triángulo -----
    def resolve_hits(self):
        for i in (0, 1):
            att = self.f[i]
            df = self.f[1 - i]
            hb = att.hitbox()
            if hb is None or att.hit_done or att.move is None:
                continue
            if df.state in (Fighter.KNOCK, Fighter.KO, Fighter.THROWN, Fighter.GETUP, Fighter.GROUND, Fighter.KIAI):
                continue
            if not hb.colliderect(df.hurtbox()):
                continue
            m = att.move
            att.hit_done = True
            att.hit_count += 1
            att.rehit_at = att.timer + max(2, m.active // max(1, m.multihit))
            pan = df.x / 600
            dm = df.move if df.state == Fighter.ATTACK else None
            # guardia de contraataque (Irimi de Okabe): el golpe se devuelve como proyección
            if dm is not None and dm.counter and dm.startup <= df.timer < dm.startup + dm.active and m.kind == "strike":
                self.fx.popup("IRIMI", (255, 240, 210), 46, H * 0.36, 60)
                self.fx.do_flash(0.6, (255, 250, 235))
                self.audio.play("parry", 1.0, pan=pan)
                self.dojo.single_beam = 1.0
                df.throw_move = dm
                self.start_throw(df, att, kokyu=True, move=dm)
                continue
            # invulnerabilidad del arranque (anti-aéreos, guardias bajas)
            if dm is not None and df.timer < dm.invuln and m.kind == "strike":
                continue
            if m.kind == "grab":
                # GOLPE gana a AGARRE: si el rival está en frames activos de golpe, el agarre falla
                if df.state == Fighter.ATTACK and df.move and df.move.kind == "strike":
                    att.state = Fighter.STUN
                    att.stun_len = 16
                    att.timer = 0
                    continue
                if df.y < -5:
                    continue
                # AGARRE rompe BLOQUEO (y agarra también en guardia baja)
                self.start_throw(att, df, move=m)
                continue
            # armadura: absorbe el golpe a cambio de daño reducido
            if dm is not None and dm.armor > df.armor_used and df.timer < dm.startup + dm.active:
                df.armor_used += 1
                df.take_damage(int(m.damage * 0.5), att.fd["id"])
                self.hitstop = max(4, m.hitstop // 2)
                self.fx.spark(df.x, df.y + m.reach_y, 10, (255, 200, 120))
                self.fx.popup("ARMADURA", (255, 200, 140), 30, H * 0.42, 40)
                self.audio.play("block", 1.0, pan=pan)
                self.audio.play("hit_m", 0.5, pan=pan)
                if df.vit <= 0:
                    self.end_round(att, "ko")
                continue
            # golpe
            blocked = df.state == Fighter.BLOCK and df.blocking
            if blocked and m.height == "low" and not df.crouching:
                blocked = False
            if blocked and m.height == "high" and df.crouching:
                blocked = False
            if blocked and m.overhead and df.crouching:
                blocked = False
            if blocked:
                # parada perfecta: bloqueo iniciado en los últimos 4 frames
                if df.block_timer <= 4 or (df.fd["id"] == "okabe" and df.block_timer <= 7):
                    self.perfect_parry(df, att, m)
                    continue
                broken = df.on_block(m, att, self)
                self.audio.play("block", 0.8, pan=pan)
                self.fx.spark(df.x + df.facing * 20, m.reach_y if m.height != "low" else -25, 4, (200, 200, 220))
                self.hitstop = 2
                self.note_exchange(att, "bloqueo")
                if broken:
                    self.fx.popup("GUARDIA ROTA", (255, 170, 140), 46, H * 0.36, 70)
                    self.audio.play("hit_h", 1.0, pan=pan)
                    self.fx.ring(df.x, -120, 150, (255, 190, 150))
                    self.shake(8)
                    self.hitstop = 10
                continue
            counter = dm is not None and df.timer < dm.startup and not dm.counter
            dmg = df.on_hit(m, att, self, counter=counter)
            self.note_exchange(att, "golpe")
            if counter:
                self.fx.popup("CONTRA", (255, 210, 120), 34, H * 0.42, 45)
                self.hitstop += 3
                self.cam.punch = max(self.cam.punch, 0.06)
            self.intensity = min(1.0, self.intensity + 0.15)
            self.hitstop = m.hitstop
            self.shake(m.hitstop * 0.8)
            self.cam.punch = max(self.cam.punch, m.hitstop * 0.012)
            if df.combo_taken >= 2:
                self.combo_popup[self.f.index(df)] = 90
            self.audio.play("hit_l" if m.damage < 70 else "hit_m" if m.damage < 120 else "hit_h", 1.0, pan=pan)
            y = df.y + (m.reach_y if m.height != "low" else -25)
            self.fx.spark(df.x, y, 8 + m.hitstop, (255, 230, 190))
            self.fx.sweat(df.x, y, 5 + m.hitstop, df.facing)
            if m.hitstop >= 7:
                self.fx.ring(df.x, y, 90 + m.hitstop * 6)
                self.dojo.react(0.6)
                self.audio.play("gasp", 0.4)
            if att.move_key == "special":
                self.fx.ink(df.x, y, att.facing, 0.8)
            att.rim = 1.0
            if df.vit <= 0:
                self.end_round(att, "ko")

    # ----- escape de agarre -----
    def check_throw_tech(self):
        for df in self.f:
            att = df.throw_partner
            if df.state != Fighter.THROWN or att is None or df.timer > 9:
                continue
            tm = att.throw_move
            if tm is not None and not tm.techable:
                continue
            if df.inp.buffered("grab"):
                att.state = Fighter.STUN
                att.stun_len = 14
                att.timer = 0
                att.throw_partner = None
                df.state = Fighter.IDLE
                df.throw_partner = None
                df.y = 0
                df.x = att.x - att.facing * 70
                df.vx = -att.facing * 6
                att.vx = att.facing * 4
                self.slowmo = 1.0
                self.slowmo_t = 0
                self.fx.popup("¡ESCAPA!", (200, 230, 255), 40, H * 0.38, 55)
                self.fx.spark((att.x + df.x) / 2, -130, 10, (220, 230, 255))
                self.audio.play("block", 1.0)
                self.audio.play("whoosh", 0.7)
                self.hitstop = 6

    # ----- ventaja de frames (entrenamiento) -----
    @staticmethod
    def actionable(f: Fighter) -> bool:
        return f.state in (Fighter.IDLE, Fighter.WALK, Fighter.CROUCH, Fighter.BLOCK) and f.stun_frames == 0 and f.y >= 0

    def note_exchange(self, att: Fighter, kind: str):
        self.fa = dict(att=self.f.index(att), kind=kind, free=[None, None])

    def track_advantage(self):
        fa = self.fa
        if fa is None:
            return
        for i, f in enumerate(self.f):
            if fa["free"][i] is None and self.actionable(f):
                fa["free"][i] = self.frame
        if None not in fa["free"]:
            a = fa["att"]
            self.last_adv = (fa["free"][1 - a] - fa["free"][a], fa["kind"])
            self.fa = None
        elif any(f.state in (Fighter.KNOCK, Fighter.THROWN, Fighter.GROUND) for f in self.f):
            self.fa = None

    # ----- muñeco de entrenamiento -----
    DUMMY_MODES = ["IA", "Quieto", "Bloquea todo", "Bloqueo aleatorio", "Salta"]

    def cycle_dummy(self):
        i = self.DUMMY_MODES.index(self.dummy_mode)
        self.dummy_mode = self.DUMMY_MODES[(i + 1) % len(self.DUMMY_MODES)]
        self.fx.popup(f"Muñeco: {self.dummy_mode}", (230, 230, 230), 26, H * 0.2, 60)

    def toggle_record(self):
        if self.rec_state == "rec":
            self.rec_state = "off"
            self.fx.popup(f"Grabación: {len(self.rec) / 60:.1f} s", (255, 200, 200), 26, H * 0.2, 60)
        else:
            self.rec = []
            self.rec_state = "rec"
            self.fx.popup("GRABANDO: controlas al muñeco con los controles del J1", (255, 120, 110), 24, H * 0.2, 90)

    def toggle_playback(self):
        if self.rec_state == "play" or not self.rec:
            self.rec_state = "off"
        else:
            self.rec_state = "play"
            self.rec_i = 0
            self.fx.popup("Reproduciendo grabación en bucle", (200, 220, 255), 24, H * 0.2, 60)

    def training_inputs(self, keys):
        from .inputs import ACTIONS, KEYMAP_P1
        a, d = self.f
        if self.rec_state == "rec":
            d.inp.ai_cmd = {k: bool(keys[KEYMAP_P1[k]]) for k in ACTIONS}
            a.inp.ai_cmd = {k: False for k in ACTIONS}
            return
        a.inp.ai_cmd = None
        if self.rec_state == "play":
            d.inp.ai_cmd = PlayerInput.cmd_from_mask(self.rec[self.rec_i % len(self.rec)])
            self.rec_i += 1
            return
        mode = self.dummy_mode
        if mode == "IA":
            return
        cmd = {k: False for k in ACTIONS}
        if mode == "Bloquea todo" or (mode == "Bloqueo aleatorio" and (self.frame // 30) % 2 == self.rng.randrange(2)):
            cmd["block"] = True
            if a.state == Fighter.ATTACK and a.move is not None and a.move.height == "low":
                cmd["down"] = True
        elif mode == "Salta":
            cmd["up"] = self.frame % 50 == 0
        d.inp.ai_cmd = cmd

    def perfect_parry(self, df: Fighter, att: Fighter, m: Move):
        self.hitstop = 8
        self.fx.do_flash(1.0, (255, 250, 235))
        self.audio.play("parry", 1.0, pan=df.x / 600)
        self.fx.ring(df.x + df.facing * 30, -120, 160, (255, 255, 240))
        self.fx.popup("PARADA PERFECTA", (255, 250, 220), 36, H * 0.42, 60)
        df.kiai = min(KIAI_MAX, df.kiai + 18)
        df.state = Fighter.PARRY
        df.timer = 0
        df.stam = min(STAM_MAX, df.stam + 10)
        att.state = Fighter.STUN
        att.stun_len = 26
        att.timer = 0
        att.move = None
        att.move_key = ""
        self.intensity = min(1.0, self.intensity + 0.3)
        if df.fd["id"] == "okabe":
            # Kokyū Nage pasivo: el rival vuela con su propia inercia
            self.dojo.single_beam = 1.0
            self.slowmo = 0.35
            self.slowmo_t = 50
            self.start_throw(df, att, kokyu=True, move=df.moves["special"])

    def start_throw(self, att: Fighter, df: Fighter, kokyu=False, move=None):
        att.throw_move = move if move is not None else att.moves["grab"]
        att.state = Fighter.THROWING
        att.timer = 0
        att.throw_partner = df
        att.throw_phase = 0.0
        att.move = None
        att.move_key = ""
        df.state = Fighter.THROWN
        df.throw_partner = att
        df.blocking = False
        df.move = None
        df.move_key = ""
        df.timer = 0
        self.audio.play("grab", 0.9, pan=df.x / 600)
        att.rim = 1.0
        att.kiai = min(KIAI_MAX, att.kiai + 10)
        self.intensity = min(1.0, self.intensity + 0.25)
        if not kokyu:
            self.slowmo = 0.5
            self.slowmo_t = 30

    def on_throw_land(self, att: Fighter, df: Fighter):
        tm = att.throw_move or att.moves["grab"]
        base = tm.damage if tm.damage > 0 else att.moves["grab"].damage
        dmg = base
        T = att.fd["T"]
        bonus = 0.15 if tm is att.moves.get("special") and tm.kind == "grab" else 0.0
        clean = self.rng.random() < 0.08 + 0.07 * T + bonus or att.fd["id"] == "okabe"
        pan = df.x / 600
        self.fx.dust(df.x, 0, 18, 1.3)
        self.add_decal(df.x)
        self.audio.play("fall", 1.0, pan=pan)
        self.shake(8)
        self.dojo.react(1.0)
        self.fx.ring(df.x, -20, 160, (230, 210, 180))
        self.cam.punch = max(self.cam.punch, 0.1)
        if clean and att.fd["grappler"]:
            df.wazaari_received = True
            att.wazaari += 1
            dmg = int(base * 1.35)
            if att.wazaari >= 2:
                df.take_damage(dmg, att.fd["id"])
                self.end_round(att, "ippon")
                return
            self.fx.popup("WAZA-ARI", (255, 215, 150), 58, H * 0.28, 80, sub=f"{att.fd['name']} · proyección limpia")
            self.audio.play("taiko", 1.0)
            # ippon directo si el rival ya estaba agotado
            if df.exhausted and self.rng.random() < 0.3:
                df.take_damage(dmg, att.fd["id"])
                self.end_round(att, "ippon")
                return
        df.take_damage(dmg, att.fd["id"])
        if df.vit <= 0:
            self.end_round(att, "ko")
            return
        # fase de suelo para los grapplers de suelo
        if (tm.ground or att.fd["ground"] and self.rng.random() < 0.7) and df.vit > 0:
            self.begin_ground(att, df)

    # ----- fase de suelo -----
    def begin_ground(self, att: Fighter, df: Fighter):
        self.ground = dict(att=att, df=df, t=0, tension=0.0, dur=150)
        att.state = Fighter.GROUND
        df.state = Fighter.GROUND
        att.ground_attacker = True
        df.ground_attacker = False
        att.mash = 0
        df.mash = 0
        df.x = att.x + att.facing * 60
        df.y = 0
        self.cam.override = ((att.x + df.x) / 2, 2.5)
        self.fx.popup("SUMISIÓN", (255, 200, 170), 44, H * 0.18, 60, sub="¡Pulsa AGARRE / BLOQUEO con ritmo!")

    def update_ground(self):
        g = self.ground
        g["t"] += 1
        att, df = g["att"], g["df"]
        if att.inp.pressed["grab"]:
            att.mash += 1
            g["tension"] += 0.06 + 0.012 * att.fd["T"]
        if df.inp.pressed["block"]:
            df.mash += 1
            g["tension"] -= 0.05 + 0.012 * df.fd["D"]
        g["tension"] = clamp(g["tension"], -1, 1)
        if g["t"] % 20 == 0:
            self.audio.play("heart", 0.6)
        if g["t"] >= g["dur"] or abs(g["tension"]) >= 1:
            self.cam.override = None
            if g["tension"] > 0.15:
                dmg = int(120 + 25 * att.fd["T"] + 120 * g["tension"])
                df.take_damage(dmg, att.fd["id"])
                self.fx.popup("¡SUMISIÓN!", (255, 190, 150), 56, H * 0.28, 80, sub=att.fd["kiai"] if att.fd["kiai_kind"] == "groundlock" else "Control de suelo")
                self.audio.play("hit_h", 1.0)
                self.audio.play("gasp", 0.8)
                df.state = Fighter.KNOCK
                df.timer = 0
                att.state = Fighter.IDLE
                att.kiai = min(KIAI_MAX, att.kiai + 20)
                if df.vit <= 0:
                    self.end_round(att, "ko")
            else:
                self.fx.popup("ESCAPA", (200, 220, 255), 44, H * 0.28, 60)
                df.state = Fighter.GETUP
                df.timer = 0
                att.state = Fighter.IDLE
                att.stam = max(0.0, att.stam - 25)
                df.kiai = min(KIAI_MAX, df.kiai + 12)
            self.ground = None

    # ----- jogai -----
    def check_jogai(self):
        for i, f in enumerate(self.f):
            if abs(f.x) > TATAMI_HALF + 25 and f.y >= 0 and f.state in (Fighter.IDLE, Fighter.WALK, Fighter.HIT, Fighter.GETUP, Fighter.KNOCK, Fighter.BLOCK, Fighter.CROUCH):
                if f.state == Fighter.KNOCK and f.timer < 38:
                    continue
                f.jogai += 1
                self.audio.play("taiko_hi", 0.9)
                if f.jogai >= 3:
                    self.fx.popup("JOGAI × 3", (255, 170, 150), 56, H * 0.28, 90, sub=f"{f.fd['name']} pierde el asalto")
                    self.end_round(self.f[1 - i], "jogai")
                    return
                self.fx.popup("JOGAI", (255, 190, 170), 48, H * 0.28, 70, sub=f"{f.fd['name']} · {f.jogai}/3")
                # reposicionar a ambos
                s = -1 if f.x > 0 else 1
                f.x = s * -(TATAMI_HALF - 110)
                o = self.f[1 - i]
                o.x = f.x + s * 180
                for g in self.f:
                    g.state = Fighter.IDLE
                    g.vx = 0
                    g.move = None

    # ----- fin de asalto -----
    def check_round_end(self):
        if self.state != Match.FIGHT or self.training:
            return
        if self.timer <= 0:
            a, b = self.f
            if a.vit != b.vit:
                w = a if a.vit > b.vit else b
            elif a.wazaari != b.wazaari:
                w = a if a.wazaari > b.wazaari else b
            else:
                w = None
            self.end_round(w, "time")

    def end_round(self, winner: Optional[Fighter], how: str):
        if self.state != Match.FIGHT:
            return
        self.state = Match.ROUND_END
        self.state_t = 0
        self.winner = winner
        self.round_result = how
        self.ground = None
        self.cam.override = None
        self.slowmo = 1.0
        self.slowmo_t = 0
        loser = None if winner is None else self.f[1 - self.f.index(winner)]
        if how == "ko":
            self.fx.popup("K.O.", (255, 220, 190), 96, H * 0.26, 170, sub=f"{winner.fd['name']} gana el asalto")
            self.audio.play("gong", 0.9)
            self.audio.play("gasp", 0.9)
            self.slowmo = 0.25
            self.slowmo_t = 40
            if loser:
                loser.state = Fighter.KNOCK
                loser.timer = 0
                loser.vy = -8
                loser.y = -1
        elif how == "ippon":
            self.fx.popup("IPPON", (255, 230, 170), 110, H * 0.24, 170, sub=f"{winner.fd['name']} · proyección perfecta")
            self.fx.impact = 4
            self.audio.play("gong", 1.0)
            self.audio.play("applause", 0.8)
            self.dojo.applaud = 3.0
            self.replay_frames = list(self.history[-100:])
            if loser:
                loser.state = Fighter.KNOCK
                loser.timer = 0
        elif how == "jogai":
            self.audio.play("gong", 0.7)
        elif how == "time":
            self.fx.popup("TIEMPO", (230, 230, 230), 84, H * 0.26, 170, sub=("Asalto para " + winner.fd["name"]) if winner else "Asalto nulo")
            self.audio.play("gong", 0.8)
        if winner is not None:
            winner.rim = 1.0

    # ----- Kiai Final: cinemática -----
    def begin_kiai(self, att: Fighter, df: Fighter):
        self.kiai_active = att
        self.audio.play("kiai", 1.0, pan=att.x / 600)
        self.fx.popup(att.fd["kiai"].upper(), (255, 235, 200), 44, H * 0.14, 110, sub="Kiai Final")
        self.dojo.single_beam = 1.0

    def kiai_whiff(self, att: Fighter):
        self.kiai_active = None
        self.fx.popup("¡AL AIRE!", (220, 220, 220), 36, H * 0.4, 50)

    def kiai_connect(self, att: Fighter, df: Fighter):
        self.slowmo = 0.5
        self.slowmo_t = 120
        self.cam.override = ((att.x + df.x) / 2, 2.55)
        self.fx.do_flash(0.6, (255, 240, 220))
        self.intensity = 1.0

    def kiai_impact(self, att: Fighter, df: Fighter, dmg: int, last=False):
        y = -120
        pan = df.x / 600
        self.audio.play("hit_h" if last else "hit_m", 1.0, pan=pan)
        self.fx.ink(df.x, y, att.facing, 1.2 if last else 0.7)
        self.fx.spark(df.x, y, 16, (255, 240, 200))
        self.fx.sweat(df.x, y, 12, df.facing)
        self.hitstop = 10 if last else 3
        self.shake(10 if last else 4)
        self.dojo.react(0.8)
        if last:
            self.fx.impact = 3
            self.fx.ring(df.x, y, 220)
            self.fx.dust(df.x, 0, 26, 1.6)
            if att.fd["kiai_kind"] == "barrage6":
                self.fx.enso(att.x - att.facing * 40, -120)
            self.audio.play("gasp", 0.9)
            self.audio.play("kiai", 0.9, pan=att.x / 600)

    def end_kiai(self, att: Fighter, df: Fighter, kind: str):
        self.kiai_active = None
        self.cam.override = None
        self.slowmo = 1.0
        self.slowmo_t = 0
        self.add_decal(df.x)
        if kind in ("throw", "kokyu", "tackle") and att.fd["grappler"]:
            att.wazaari += 1
            if att.wazaari >= 2:
                self.end_round(att, "ippon")
                return
            self.fx.popup("WAZA-ARI", (255, 215, 150), 52, H * 0.3, 70)
        if df.vit <= 0:
            self.end_round(att, "ko")

    # ----- repetición -----
    def record_history(self):
        snap = tuple((f.x, f.y, f.facing, f.state, f.timer, f.anim_t, f.vy, f.throw_phase, f.kiai_idx, f.stun_len) for f in self.f)
        self.history.append(snap)
        if len(self.history) > 160:
            self.history.pop(0)

    def apply_snapshot(self, snap):
        for f, s in zip(self.f, snap):
            f.x, f.y, f.facing, f.state, f.timer, f.anim_t, f.vy, f.throw_phase, f.kiai_idx, f.stun_len = s

    def update_replay(self):
        n = len(self.replay_frames)
        if n == 0:
            return
        step = self.state_t // 3   # 1/3 de velocidad
        self.replay_i = step
        idx = step % n
        angle = step // n
        if angle != self.replay_angle:
            self.replay_angle = angle
        self.apply_snapshot(self.replay_frames[idx])
        a, b = self.f
        zooms = (1.7, 2.6, 2.15)
        self.cam.override = ((a.x + b.x) / 2 + (0, -60, 60)[angle % 3], zooms[angle % 3])
        self.cam.follow(a, b, 0)

    # ----- dibujo -----
    def draw(self, surf, fonts: Fonts, layer, photo=None):
        rig = self.dojo.rig
        lut = photo["lut"] if photo else "Natural"
        bw = lut in ("Kurosawa B/N", "Daido Moriyama")
        self.dojo.draw(surf, self.cam, fonts, bw)
        layer.fill((0, 0, 0, 0))
        self.dojo.draw_beams(surf, self.cam, layer)
        # los luchadores se dibujan de atrás hacia delante: el que ataca, delante
        order = sorted(self.f, key=lambda f: (f.state in (Fighter.ATTACK, Fighter.KIAI, Fighter.THROWING), -abs(f.y)))
        # reflejo en el tatami barnizado por la luz de los farolillos (hora azul y noche)
        refl = rig["lanterns"] * 0.55 + (0.12 if rig["lanterns"] < 0.3 else 0)
        if refl > 0.08 and self.cfg.get("shadows", True):
            self.reflect_layer.fill((0, 0, 0, 0))
            for f in order:
                draw_fighter(self.reflect_layer, f, self.cam, rig, dict(self.cfg, shadows=False), False, bw)
            fy = int(self.cam.P(0, 0)[1])
            band = self.reflect_layer.subsurface((0, max(0, fy - 260), W, min(260, fy))).copy()
            band = pygame.transform.flip(band, False, True)
            band.set_alpha(int(80 * refl))
            fade = pygame.Surface(band.get_size(), pygame.SRCALPHA)
            for yy in range(0, band.get_height(), 4):
                fade.fill((0, 0, 0, int(255 * min(1.0, yy / max(1, band.get_height() * 0.6)))), (0, yy, W, 4))
            band.blit(fade, (0, 0), special_flags=pygame.BLEND_RGBA_SUB)
            surf.blit(band, (0, fy + 2))
        for f in order:
            draw_trail(surf, f, self.cam)
        for f in order:
            draw_fighter(surf, f, self.cam, rig, self.cfg, self.debug, bw)
        self.fx.draw_world(surf, self.cam, layer)
        surf.blit(layer, (0, 0))
        self.dojo.draw_rain(surf)
        # tinte global de la iluminación
        tint_s = pygame.Surface((W, H))
        tint_s.fill(rig["tint"])
        surf.blit(tint_s, (0, 0), special_flags=pygame.BLEND_MULT)
        # haz único: oscurecer todo salvo el centro
        if self.dojo.single_beam > 0.05:
            dark = pygame.Surface((W, H), pygame.SRCALPHA)
            dark.fill((0, 0, 0, int(150 * self.dojo.single_beam)))
            cx = W // 2
            pygame.draw.polygon(dark, (0, 0, 0, 0), [(cx - 80, -10), (cx + 80, -10), (cx + 300, FLOOR_Y + 60), (cx - 300, FLOOR_Y + 60)])
            surf.blit(dark, (0, 0))
        surf.blit(self.dojo.vignette(rig["vignette"] + (60 if self.ground else 0)), (0, 0))

    def draw_layers(self, bg, fig, fxl, fonts: Fonts, photo=None, figd=None, dirty=None) -> dict:
        """Camino GPU: fondo sin suelo, luchadores y efectos en capas separadas; devuelve los parámetros de luz.

        Con `dirty` (zonas del fotograma anterior) sólo se borra lo que se pintó entonces y se devuelve en
        p["dirty"] la zona que hay que subir a la GPU: a 1440p o 4K las capas son grandes y casi vacías."""
        from .gpu import scene_params
        rig = self.dojo.rig
        lut = photo["lut"] if photo else "Natural"
        bw = lut in ("Kurosawa B/N", "Daido Moriyama")
        prev = dirty.get("prev") if dirty else None
        for name, s in (("bg", bg), ("fig", fig), ("fig", figd)):
            if s is None:
                continue
            if prev is None:
                s.fill((0, 0, 0, 0))
            elif prev.get(name) is not None:
                s.fill((0, 0, 0, 0), prev[name])
        fxl.fill((0, 0, 0, 0))
        self.dojo.draw(bg, self.cam, fonts, bw, gpu=True)
        self.dojo.draw_beams(None, self.cam, fxl, intensity_override=0)
        order = sorted(self.f, key=lambda f: (f.state in (Fighter.ATTACK, Fighter.KIAI, Fighter.THROWING), -abs(f.y)))
        fr = []
        for f in order:
            fr += draw_trail(fig, f, self.cam) or []       # las estelas van detrás de los cuerpos
        cfg = dict(self.cfg, shadows=False)
        pieces: list = []
        for f in order:
            fr.append(draw_fighter(fig, f, self.cam, rig, cfg, self.debug, bw, data_surf=figd, pieces=pieces))
        self.fx.draw_world(fxl, self.cam, fxl)
        self.dojo.draw_rain(fxl)
        extra = {}
        if self.cfg.get("bloom"):
            extra["dof"] = 2.4          # el fondo se suaviza un poco: los cuerpos se despegan
        if self.kiai_active is not None:
            extra["dof"] = 7.0
        if self.ground is not None:
            extra["vig"] = self.dojo.gpu_rig["vig"] + 0.35
        if self.access.get("flashes"):
            extra["impact"] = 0.0
        if not self.access.get("shake", True):
            extra["tilt"] = 0.0
        extra["pieces"] = pieces
        if fr:
            extra["fig_rect"] = _union(fr)              # la GPU acota a esta zona el campo de distancias
        if dirty is not None:
            new = dict(bg=_union(getattr(self.dojo, "drawn", [])), fig=_union(fr))
            dirty["new"] = new
            if prev is None:
                extra["dirty"] = None                      # primer fotograma: subida completa
            else:
                extra["dirty"] = {k: _union([r for r in (prev.get(k), new[k]) if r is not None]) for k in ("bg", "fig")}
        return scene_params(self.dojo, self.cam, self.f, self.fx, self.cfg, extra)

    def draw_hud(self, surf, fonts: Fonts):
        if self.state == Match.REPLAY:
            return
        fonts = fonts.at(surf)
        a, b = self.f
        bar_w = 470
        y = 28
        for i, f in enumerate(self.f):
            x = 40 if i == 0 else W - 40 - bar_w
            # vitalidad
            udraw.rect(surf, (20, 14, 12), (x - 2, y - 2, bar_w + 4, 22))
            frac = f.vit / VIT_MAX
            col = (236, 220, 190) if frac > 0.4 else (240, 160, 110) if frac > 0.15 else (230, 80, 70)
            w = int(bar_w * frac)
            rect = (x if i == 0 else x + bar_w - w, y, w, 18)
            udraw.rect(surf, col, rect)
            udraw.rect(surf, f.player_color, (x - 2, y - 2, bar_w + 4, 22), 2)
            # aliento
            sw = int((bar_w - 60) * f.stam / STAM_MAX)
            sc = (120, 200, 230) if not f.exhausted else (220, 90, 70)
            udraw.rect(surf, (20, 14, 12), (x if i == 0 else x + 60, y + 26, bar_w - 60, 6))
            udraw.rect(surf, sc, (x if i == 0 else x + bar_w - sw, y + 26, sw, 6))
            # nombre y estilo
            name = fonts.get(26, serif=True).render(f.fd["name"].upper(), True, (245, 238, 225))
            style = fonts.get(18).render(f.fd["style"], True, (200, 190, 175))
            if i == 0:
                surf.blit(name, (x, y + 38))
                surf.blit(style, (x, y + 64))
            else:
                surf.blit(name, (x + bar_w - name.get_width(), y + 38))
                surf.blit(style, (x + bar_w - style.get_width(), y + 64))
            # asaltos ganados, waza-ari y jogai
            for k in range(2):
                cx = (x + 10 + k * 22) if i == 0 else (x + bar_w - 10 - k * 22)
                udraw.circle(surf, (255, 220, 150) if f.wins > k else (60, 50, 44), (cx, y + 96), 7)
                udraw.circle(surf, (120, 100, 80), (cx, y + 96), 7, 1)
            for k in range(f.wazaari):
                cx = (x + 70 + k * 18) if i == 0 else (x + bar_w - 70 - k * 18)
                udraw.polygon(surf, (255, 200, 120), [(cx, y + 88), (cx + 7, y + 96), (cx, y + 104), (cx - 7, y + 96)])
            for k in range(f.jogai):
                cx = (x + 130 + k * 14) if i == 0 else (x + bar_w - 130 - k * 14)
                udraw.rect(surf, (230, 90, 80), (cx - 4, y + 92, 8, 8))
            # kiai (abajo)
            kw = 300
            kx = 40 if i == 0 else W - 40 - kw
            ky = H - 46
            udraw.rect(surf, (20, 14, 12), (kx - 2, ky - 2, kw + 4, 14))
            kf = f.kiai / KIAI_MAX
            kc = (255, 200, 90) if kf >= 1 else (190, 140, 70)
            if kf >= 1 and (self.frame // 8) % 2 == 0:
                kc = (255, 240, 180)
            kwid = int(kw * kf)
            udraw.rect(surf, kc, (kx if i == 0 else kx + kw - kwid, ky, kwid, 10))
            lab = fonts.get(16).render("KIAI" + ("  ·  ¡LISTO! (L / Num3)" if kf >= 1 else ""), True, (210, 190, 160))
            surf.blit(lab, (kx if i == 0 else kx + kw - lab.get_width(), ky - 20))
        # contador de combo (sobre el lado del que recibe)
        for i, f in enumerate(self.f):
            if self.combo_popup[i] > 0 and f.combo_taken >= 2:
                att = self.f[1 - i]
                a = min(255, self.combo_popup[i] * 6)
                img = fonts.get(44, serif=True).render(f"{f.combo_taken} GOLPES", True, att.player_color)
                img.set_alpha(a)
                x = 60 if i == 1 else W - 60 - img.get_width()
                surf.blit(img, (x, 150))
        # tarjetas de presentación durante la intro
        if self.state == Match.INTRO and 20 < self.state_t < 150:
            k = smoothstep(min(1.0, (self.state_t - 20) / 25)) * (1 - smoothstep(max(0.0, (self.state_t - 120) / 30)))
            for i, f in enumerate(self.f):
                w = 520
                x = int(lerp(-w, 40, k)) if i == 0 else int(lerp(W, W - 40 - w, k))
                y = H // 2 - 40 + i * 100
                panel(surf, (x, y, w, 84), (8, 6, 6, 170))
                udraw.rect(surf, f.player_color, (x, y, 6, 84))
                nm = fonts.get(40, serif=True).render(f.fd["name"], True, (248, 236, 215))
                tt = fonts.get(20).render(f"«{f.fd['title']}»  ·  {f.fd['style']}", True, (210, 195, 170))
                surf.blit(nm, (x + 20, y + 8))
                surf.blit(tt, (x + 20, y + 52))
        # temporizador
        t = max(0, self.timer // 60) if not self.training else 0
        tc = (245, 235, 220) if t > 10 else (240, 110, 90)
        timg = fonts.get(56, serif=True).render(f"{t:02d}" if not self.training else "∞", True, tc)
        surf.blit(timg, (W // 2 - timg.get_width() // 2, 18))
        rimg = fonts.get(18).render(f"ASALTO {self.round}" if self.round < 4 else "DESEMPATE", True, (200, 190, 175))
        surf.blit(rimg, (W // 2 - rimg.get_width() // 2, 74))
        # fase de suelo: barra de tensión
        if self.ground:
            g = self.ground
            gx, gw = W // 2 - 200, 400
            udraw.rect(surf, (20, 14, 12), (gx, H // 2 + 150, gw, 16))
            mid = gx + gw // 2
            t = g["tension"]
            if t >= 0:
                udraw.rect(surf, g["att"].player_color, (mid, H // 2 + 150, int(t * gw / 2), 16))
            else:
                udraw.rect(surf, g["df"].player_color, (mid + int(t * gw / 2), H // 2 + 150, int(-t * gw / 2), 16))
            udraw.line(surf, (255, 255, 255), (mid, H // 2 + 146), (mid, H // 2 + 170), 2)
        # datos de frames (entrenamiento)
        if self.frame_data_overlay:
            yy = 200
            for f in self.f:
                st = ["IDLE", "WALK", "CROUCH", "JUMP", "ATTACK", "BLOCK", "HIT", "KNOCK", "GETUP", "THROWN", "THROWING", "KO", "WIN", "INTRO", "KIAI", "GROUND", "PARRY", "STUN"][f.state]
                line = f"{f.fd['id']}: {st}"
                if f.move:
                    m = f.move
                    ph = "startup" if f.timer < m.startup else "ACTIVO" if f.timer < m.startup + m.active else "recovery"
                    line += f"  {m.name}  s{m.startup}/a{m.active}/r{m.recovery}  [{ph} f{f.timer}]  dmg {m.damage}"
                img = fonts.get(18).render(line, True, (255, 240, 200))
                surf.blit(img, (40, yy))
                yy += 20
            if self.last_adv is not None:
                adv, kind = self.last_adv
                col = (140, 230, 150) if adv > 0 else (240, 140, 120) if adv < 0 else (230, 230, 230)
                img = fonts.get(28, serif=True).render(f"Ventaja en {kind}: {adv:+d}", True, col)
                surf.blit(img, (40, yy + 4))
                yy += 34
            if self.training:
                rec = {"off": "", "rec": "  ·  GRABANDO", "play": "  ·  REPRODUCIENDO"}[self.rec_state]
                img = fonts.get(18).render(f"Muñeco: {self.dummy_mode}{rec}", True, (255, 220, 190))
                surf.blit(img, (40, yy))
                yy += 22
            hint = fonts.get(16).render("F1 hitboxes · F2 datos · F3 muñeco · F4 grabar · F5 reproducir · Esc pausa", True, (180, 170, 160))
            surf.blit(hint, (40, yy))
