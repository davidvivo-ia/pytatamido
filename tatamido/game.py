import math
import os
import random
import sys
import time
from typing import List, Optional

import pygame

from .audio import Audio
from .camera import Camera
from .config import *
from .data import FIGHTERS, FIGHTER_BY_ID, EPILOGUE_FOOTER
from .dojo import Dojo
from .fighter import Fighter
from .fx import Fonts, draw_kanji_do
from .gpu import GPURenderer, scene_params
from .inputs import KEYMAP_P1, KEYMAP_P2, PlayerInput
from .match import Match
from .postfx import apply_lut, bloom
from .replay import list_replays, load_replay, save_replay
from .render_fighter import draw_figure_preview, draw_portrait

# ─────────────────────────────────────────────────────────────────────────────
#  JUEGO: escenas, menús, modos
# ─────────────────────────────────────────────────────────────────────────────
MENU_ITEMS = [("Versus local", "versus"), ("Versus contra la IA", "versus_ai"), ("Arcade", "arcade"), ("Torneo", "torneo"),
              ("Entrenamiento", "training"), ("Espectador (IA vs IA)", "spectate"), ("Repeticiones", "replays"),
              ("Opciones", "options"), ("Salir", "quit")]
DIFFICULTIES = [("Fácil", 0.3), ("Normal", 0.55), ("Difícil", 0.8), ("Maestro", 0.98)]


class Game:
    def __init__(self, args):
        pygame.init()
        self.save = load_save()
        preset = args.get("preset") or self.save.get("preset", "alto")
        if preset not in PRESETS:
            preset = "alto"
        self.preset = preset
        self.cfg = dict(PRESETS[preset])
        self.headless = args.get("headless", False)
        self.gpu: Optional[GPURenderer] = None
        self.renderer_pref = args.get("renderer") or self.save.get("renderer", "auto")
        self.screen = self._open_display(args)
        pygame.display.set_caption(TITLE + ("  ·  GPU" if self.gpu else "  ·  CPU"))
        self.bg = pygame.Surface((W, H), pygame.SRCALPHA)
        self.fig = pygame.Surface((W, H), pygame.SRCALPHA)
        self.figd = pygame.Surface((W, H), pygame.SRCALPHA)   # altura y material de los luchadores (GPU)
        self.hud = pygame.Surface((W, H), pygame.SRCALPHA)
        self._bd_params = None
        self.audio = Audio(self.save.get("volume", 0.8))
        self.fonts = Fonts()
        self.clock = pygame.time.Clock()
        self.layer = pygame.Surface((W, H), pygame.SRCALPHA)
        self.world = pygame.Surface((W, H))
        self.joys = []
        try:
            pygame.joystick.init()
            self.joys = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
            for j in self.joys:
                j.init()
        except Exception:
            pass
        self.p1 = PlayerInput(KEYMAP_P1, 0 if self.joys else None)
        self.p2 = PlayerInput(KEYMAP_P2, 1 if len(self.joys) > 1 else None)
        self.scene = "title"
        self.scene_t = 0
        self.menu_i = 0
        self.mode = "versus"
        self.sel = [0, 1]
        self.sel_locked = [False, False]
        self.match: Optional[Match] = None
        self.photo = None
        self.paused = False
        self.pause_i = 0
        self.options_i = 0
        self.debug = args.get("debug", False)
        self.ai_vs_ai = args.get("ai_vs_ai", False)
        self.arcade_queue: List[str] = []
        self.arcade_idx = 0
        self.arcade_lost_round = False
        self.tourney: List[str] = []
        self.results_t = 0
        self.replay_files: List[str] = []
        self.movelist = False
        self.movelist_p = 0
        self.replay_i = 0
        self.title_dojo = Dojo(self.cfg)
        self.title_cam = Camera()
        self.title_cam.zoom = 0.9
        self.bg_fighters = []
        self.last_access = dict(self.save.get("access", {}))
        self.quit = False
        self.frame = 0
        self.headless = args.get("headless", False)
        self.max_frames = args.get("max_frames", 0)
        if args.get("photo_mode"):
            self.setup_match("versus_ai", "haruki", "ines")
            self.scene = "fight"
            self.enter_photo()
        if self.ai_vs_ai:
            self.setup_match("spectate", random.choice(FIGHTERS)["id"], random.choice(FIGHTERS)["id"])
            self.scene = "fight"

    def _open_display(self, args):
        """Intenta abrir una ventana OpenGL 3.3 con el renderer de shaders; si falla, vuelve a pygame puro."""
        headless = args.get("headless", False)
        windowed = args.get("windowed", True)
        if self.renderer_pref != "cpu" and GPURenderer.available():
            try:
                if headless:
                    screen = pygame.display.set_mode((W, H))
                    self.gpu = GPURenderer.offscreen_context()
                else:
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
                    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
                    if sys.platform == "darwin":
                        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_FORWARD_COMPATIBLE_FLAG, True)
                    flags = pygame.OPENGL | pygame.DOUBLEBUF | (0 if windowed else pygame.FULLSCREEN)
                    try:
                        screen = pygame.display.set_mode((W, H), flags, vsync=1)
                    except Exception:
                        screen = pygame.display.set_mode((W, H), flags)
                    self.gpu = GPURenderer.for_window()
                return screen
            except Exception as e:
                print(f"[TATAMI: DŌ] Renderer GPU no disponible ({e}). Se usa el renderer CPU.")
                self.gpu = None
        flags = 0 if headless else (pygame.SCALED | (0 if windowed else pygame.FULLSCREEN))
        try:
            return pygame.display.set_mode((W, H), flags, vsync=1)
        except Exception:
            return pygame.display.set_mode((W, H))

    def capture(self) -> pygame.Surface:
        """Fotograma actual tal y como lo ve el jugador."""
        if self.gpu is not None:
            return self.gpu.read(True)
        return self.world.copy()

    # ----- utilidades -----
    def access(self):
        return self.save.get("access", {})

    def available_fighters(self):
        return [f for f in FIGHTERS if not f.get("locked") or self.save.get("okabe")]

    def difficulty_value(self):
        return DIFFICULTIES[self.save.get("difficulty", 1)][1]

    def setup_match(self, mode, id1, id2, difficulty=None, replay=None):
        self.mode = mode
        fd1, fd2 = FIGHTER_BY_ID[id1], FIGHTER_BY_ID[id2]
        ai1 = mode == "spectate"
        ai2 = mode in ("versus_ai", "arcade", "torneo", "spectate", "training")
        base = self.difficulty_value()
        diff = difficulty if difficulty is not None else (0.3 if mode == "training" else base)
        if mode == "arcade":
            diff = clamp(base - 0.2 + 0.05 * self.arcade_idx, 0.2, 0.98)
        if mode == "torneo":
            diff = clamp(base + 0.1 * self.arcade_idx, 0.2, 0.98)
        if replay is not None:
            ai1 = ai2 = False
        f1 = Fighter(fd1, 0, self.p1, ai1, diff)
        f2 = Fighter(fd2, 1, self.p2, ai2, diff)
        self.p1.ai_cmd = None
        self.p2.ai_cmd = None
        controls = replay["controls"] if replay else self.save.get("controls", "moderno")
        self.match = Match(f1, f2, self.audio, self.cfg, controls, training=(mode == "training"), access=self.access(),
                           seed=replay["seed"] if replay else None, replay=replay)
        self.match.debug = self.debug
        self.paused = False

    def enter_photo(self):
        if self.match is None:
            return
        self.photo = dict(lut="Natural", grain=True, vignette=True, dof=False, zoom=self.match.cam.zoom, x=self.match.cam.x, yoff=0.0, exposure=0, hud=False, saved_msg=0)

    # ----- bucle -----
    def run(self):
        while not self.quit:
            dt = self.clock.tick(FPS) / 1000.0
            self.frame += 1
            events = pygame.event.get()
            for e in events:
                if e.type == pygame.QUIT:
                    self.quit = True
                elif e.type == pygame.KEYDOWN:
                    self.on_key(e)
                elif e.type == pygame.JOYBUTTONDOWN:
                    self.on_joy(e)
            keys = pygame.key.get_pressed()
            self.update(keys)
            self.draw()
            pygame.display.flip()
            if self.max_frames and self.frame >= self.max_frames:
                self.quit = True
        self.save["preset"] = self.preset
        write_save(self.save)
        pygame.quit()

    # ----- entrada por eventos -----
    def on_joy(self, e):
        # mandos: A/✕ (0) acepta, B/○ (1) atrás, Select (6) modo foto
        fake = {0: pygame.K_RETURN, 1: pygame.K_ESCAPE, 6: pygame.K_F12}.get(e.button)
        if fake is not None:
            self.on_key(pygame.event.Event(pygame.KEYDOWN, key=fake, unicode="", mod=0))

    def on_key(self, e):
        k = e.key
        sc = self.scene
        if k == pygame.K_F11:
            try:
                pygame.display.toggle_fullscreen()
            except Exception:
                pass
            return
        if k == pygame.K_F12 and self.match is not None and sc == "fight":
            if self.photo is None:
                self.enter_photo()
            else:
                self.photo = None
            return
        if self.photo is not None:
            self.photo_key(k)
            return
        if sc == "title":
            if k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j):
                self.scene = "menu"
                self.scene_t = 0
                self.audio.play("ui_ok")
            elif k == pygame.K_ESCAPE:
                self.quit = True
        elif sc == "menu":
            if k in (pygame.K_DOWN, pygame.K_s):
                self.menu_i = (self.menu_i + 1) % len(MENU_ITEMS)
                self.audio.play("ui_move")
            elif k in (pygame.K_UP, pygame.K_w):
                self.menu_i = (self.menu_i - 1) % len(MENU_ITEMS)
                self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j):
                self.audio.play("ui_ok")
                self.menu_select(MENU_ITEMS[self.menu_i][1])
            elif k == pygame.K_ESCAPE:
                self.scene = "title"
                self.audio.play("ui_back")
        elif sc == "select":
            self.select_key(k)
        elif sc == "options":
            self.options_key(k)
        elif sc == "replays":
            n = len(self.replay_files)
            if k in (pygame.K_DOWN, pygame.K_s) and n:
                self.replay_i = (self.replay_i + 1) % n; self.audio.play("ui_move")
            elif k in (pygame.K_UP, pygame.K_w) and n:
                self.replay_i = (self.replay_i - 1) % n; self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_u, pygame.K_j) and n:
                try:
                    rp = load_replay(self.replay_files[self.replay_i])
                    self.setup_match("replay", rp["p1"], rp["p2"], replay=rp)
                    self.scene = "fight"; self.scene_t = 0
                    self.audio.play("ui_ok")
                except Exception:
                    self.audio.play("ui_back")
            elif k == pygame.K_ESCAPE:
                self.scene = "menu"; self.audio.play("ui_back")
        elif sc == "fight":
            self.fight_key(k)
        elif sc in ("results", "epilogue", "vs"):
            if k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j, pygame.K_ESCAPE):
                self.advance_after_results()

    def menu_select(self, key):
        if key == "quit":
            self.quit = True
        elif key == "options":
            self.scene = "options"
            self.options_i = 0
        elif key == "replays":
            self.replay_files = list_replays()
            self.replay_i = 0
            self.scene = "replays"
        elif key == "spectate":
            ids = [f["id"] for f in self.available_fighters()]
            a, b = random.sample(ids, 2)
            self.setup_match("spectate", a, b)
            self.scene = "vs"
            self.scene_t = 0
        else:
            self.mode = key
            self.scene = "select"
            self.scene_t = 0
            self.sel_locked = [False, False]
            if key == "arcade":
                self.arcade_idx = 0
                self.arcade_lost_round = False
            if key == "torneo":
                self.arcade_idx = 0
                self.arcade_lost_round = False

    # ----- selección -----
    def select_key(self, k):
        avail = self.available_fighters()
        n = len(FIGHTERS)
        cols = 6
        two_players = self.mode == "versus"
        for p, (keys_h) in enumerate(((pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s, (pygame.K_u, pygame.K_j, pygame.K_RETURN)),
                                       (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, (pygame.K_KP4, pygame.K_KP1, pygame.K_KP_ENTER)))):
            if p == 1 and not two_players:
                # en modos de un jugador, P1 puede usar también las flechas
                keys_h = (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, (pygame.K_RETURN, pygame.K_u, pygame.K_j))
                p = 0
            if self.sel_locked[p]:
                continue
            L, R, U, D, OK = keys_h
            i = self.sel[p]
            if k == L:
                self.sel[p] = (i - 1) % n; self.audio.play("ui_move")
            elif k == R:
                self.sel[p] = (i + 1) % n; self.audio.play("ui_move")
            elif k == U:
                self.sel[p] = (i - cols) % n; self.audio.play("ui_move")
            elif k == D:
                self.sel[p] = (i + cols) % n; self.audio.play("ui_move")
            elif k in OK:
                fd = FIGHTERS[self.sel[p]]
                if fd.get("locked") and not self.save.get("okabe"):
                    self.audio.play("ui_back")
                    continue
                self.sel_locked[p] = True
                self.audio.play("ui_ok")
                self.audio.play("kiai", 0.5)
        if k == pygame.K_ESCAPE:
            if any(self.sel_locked):
                self.sel_locked = [False, False]
            else:
                self.scene = "menu"
            self.audio.play("ui_back")
            return
        # ¿listos?
        if self.sel_locked[0] and (self.sel_locked[1] or not two_players):
            id1 = FIGHTERS[self.sel[0]]["id"]
            if two_players:
                id2 = FIGHTERS[self.sel[1]]["id"]
            elif self.mode == "arcade":
                others = [f["id"] for f in FIGHTERS if f["id"] != id1 and (not f.get("locked") or self.save.get("okabe"))]
                random.shuffle(others)
                self.arcade_queue = others
                id2 = self.arcade_queue[0]
            elif self.mode == "torneo":
                others = [f["id"] for f in FIGHTERS if f["id"] != id1 and not f.get("locked")]
                random.shuffle(others)
                self.tourney = others[:3]   # cuartos, semifinal, final
                id2 = self.tourney[0]
            else:
                others = [f["id"] for f in self.available_fighters() if f["id"] != id1]
                id2 = random.choice(others)
            self.setup_match(self.mode, id1, id2)
            self.scene = "vs"
            self.scene_t = 0

    # ----- opciones -----
    OPTS = ["Preset gráfico", "Renderer", "Dificultad IA", "Volumen", "Controles", "Reducir destellos", "Temblor de cámara", "Alto contraste", "Filtro daltonismo", "Volver"]

    def options_key(self, k):
        n = len(self.OPTS)
        if k in (pygame.K_DOWN, pygame.K_s):
            self.options_i = (self.options_i + 1) % n; self.audio.play("ui_move")
        elif k in (pygame.K_UP, pygame.K_w):
            self.options_i = (self.options_i - 1) % n; self.audio.play("ui_move")
        elif k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_a, pygame.K_d, pygame.K_RETURN, pygame.K_u, pygame.K_j):
            d = -1 if k in (pygame.K_LEFT, pygame.K_a) else 1
            o = self.OPTS[self.options_i]
            acc = self.save.setdefault("access", {})
            if o == "Preset gráfico":
                i = (PRESET_ORDER.index(self.preset) + d) % len(PRESET_ORDER)
                self.preset = PRESET_ORDER[i]
                self.cfg.clear(); self.cfg.update(PRESETS[self.preset])
                self.title_dojo = Dojo(self.cfg)
            elif o == "Renderer":
                opts = ["auto", "gpu", "cpu"]
                self.save["renderer"] = opts[(opts.index(self.save.get("renderer", "auto")) + d) % 3]
            elif o == "Dificultad IA":
                self.save["difficulty"] = (self.save.get("difficulty", 1) + d) % len(DIFFICULTIES)
            elif o == "Volumen":
                self.audio.volume = clamp(round(self.audio.volume + 0.1 * d, 1), 0, 1)
                self.save["volume"] = self.audio.volume
            elif o == "Controles":
                self.save["controls"] = "clasico" if self.save.get("controls", "moderno") == "moderno" else "moderno"
            elif o == "Reducir destellos":
                acc["flashes"] = not acc.get("flashes", False)
            elif o == "Temblor de cámara":
                acc["shake"] = not acc.get("shake", True)
            elif o == "Alto contraste":
                acc["contrast"] = not acc.get("contrast", False)
            elif o == "Filtro daltonismo":
                opts = ["off", "protanopia", "deuteranopia", "tritanopia"]
                acc["daltonism"] = opts[(opts.index(acc.get("daltonism", "off")) + d) % 4]
            elif o == "Volver":
                self.scene = "menu"
            self.audio.play("ui_ok")
            write_save(self.save)
        elif k == pygame.K_ESCAPE:
            self.scene = "menu"
            self.audio.play("ui_back")

    # ----- combate -----
    def fight_key(self, k):
        m = self.match
        if self.paused and self.movelist:
            if k in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_u, pygame.K_j, pygame.K_SPACE):
                self.movelist = False
                self.audio.play("ui_back")
            elif k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_a, pygame.K_d):
                self.movelist_p = 1 - self.movelist_p
                self.audio.play("ui_move")
            return
        if k == pygame.K_ESCAPE:
            self.paused = not self.paused
            self.pause_i = 0
            self.audio.play("ui_back")
            return
        if self.paused:
            items = 4
            if k in (pygame.K_DOWN, pygame.K_s):
                self.pause_i = (self.pause_i + 1) % items; self.audio.play("ui_move")
            elif k in (pygame.K_UP, pygame.K_w):
                self.pause_i = (self.pause_i - 1) % items; self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_u, pygame.K_j, pygame.K_SPACE):
                self.audio.play("ui_ok")
                if self.pause_i == 0:
                    self.paused = False
                elif self.pause_i == 1:
                    self.movelist = True
                    self.movelist_p = 0
                elif self.pause_i == 2:
                    self.setup_match(self.mode, m.f[0].fd["id"], m.f[1].fd["id"])
                else:
                    self.paused = False
                    self.audio.rain(False)
                    self.scene = "menu"
            return
        if k == pygame.K_F1:
            m.debug = not m.debug
        elif k == pygame.K_F2:
            m.frame_data_overlay = not m.frame_data_overlay
        elif m.training and k == pygame.K_F3:
            m.cycle_dummy()
        elif m.training and k == pygame.K_F4:
            m.toggle_record()
        elif m.training and k == pygame.K_F5:
            m.toggle_playback()
        elif m.state == Match.MATCH_END and k in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_u, pygame.K_j):
            self.go_results()

    def photo_key(self, k):
        ph = self.photo
        if k == pygame.K_ESCAPE:
            self.photo = None
            return
        if k in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            ph["lut"] = PHOTO_LUTS[k - pygame.K_1]
        elif k == pygame.K_g:
            ph["grain"] = not ph["grain"]
        elif k == pygame.K_v:
            ph["vignette"] = not ph["vignette"]
        elif k == pygame.K_b:
            ph["dof"] = not ph["dof"]
        elif k == pygame.K_h:
            ph["hud"] = not ph["hud"]
        elif k in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_e):
            ph["zoom"] = clamp(ph["zoom"] * 1.1, 0.5, 3.0)
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_q):
            ph["zoom"] = clamp(ph["zoom"] / 1.1, 0.5, 3.0)
        elif k == pygame.K_LEFTBRACKET:
            ph["exposure"] -= 1
        elif k == pygame.K_RIGHTBRACKET:
            ph["exposure"] += 1
        elif k == pygame.K_p:
            self.save_photo()
        elif k == pygame.K_SPACE:
            # avanzar un frame (fotograma a fotograma)
            keys = pygame.key.get_pressed()
            self.match.update(keys, self.joys)

    def save_photo(self):
        os.makedirs("fotos", exist_ok=True)
        name = os.path.join("fotos", f"tatami_{int(time.time())}.png")
        src = self.gpu.read(False) if self.gpu is not None else self.world   # en GPU: imagen limpia, sin interfaz
        big = pygame.transform.smoothscale(src, (W * 2, H * 2))
        pygame.image.save(big, name)
        self.photo["saved_msg"] = 120
        self.audio.play("ui_ok")

    # ----- resultados y modos -----
    def go_results(self):
        m = self.match
        winner = m.winner
        self.audio.rain(False)
        if self.mode == "replay":
            self.scene = "menu"
            return
        if self.mode != "training":
            try:
                save_replay(m, self.mode)
            except Exception:
                pass
        if self.mode in ("versus", "versus_ai", "spectate", "training"):
            if winner is not None and not winner.is_ai:
                self.save["wins"] = self.save.get("wins", 0) + 1
                write_save(self.save)
            self.scene = "results"
            self.scene_t = 0
            return
        player_won = winner is not None and winner is m.f[0]
        if m.f[1].wins > 0:
            self.arcade_lost_round = True
        if self.mode == "arcade":
            if not player_won:
                self.scene = "results"; self.scene_t = 0; return
            self.arcade_idx += 1
            if self.arcade_idx >= len(self.arcade_queue):
                cl = self.save.setdefault("arcade_clears", [])
                if m.f[0].fd["id"] not in cl:
                    cl.append(m.f[0].fd["id"])
                write_save(self.save)
                self.scene = "epilogue"; self.scene_t = 0
            else:
                self.scene = "results"; self.scene_t = 0
        elif self.mode == "torneo":
            if not player_won:
                self.scene = "results"; self.scene_t = 0; return
            self.arcade_idx += 1
            if self.arcade_idx >= len(self.tourney):
                if not self.arcade_lost_round and not self.save.get("okabe"):
                    self.save["okabe"] = True
                    write_save(self.save)
                    self.unlock_msg = True
                self.scene = "epilogue"; self.scene_t = 0
            else:
                self.scene = "results"; self.scene_t = 0

    def advance_after_results(self):
        m = self.match
        self.audio.play("ui_ok")
        if self.scene == "vs":
            self.scene = "fight"
            self.scene_t = 0
            return
        if self.scene == "epilogue":
            self.scene = "menu"
            return
        player_won = m is not None and m.winner is m.f[0]
        if self.mode == "arcade" and player_won and self.arcade_idx < len(self.arcade_queue):
            self.setup_match("arcade", m.f[0].fd["id"], self.arcade_queue[self.arcade_idx])
            self.scene = "vs"; self.scene_t = 0
        elif self.mode == "torneo" and player_won and self.arcade_idx < len(self.tourney):
            self.setup_match("torneo", m.f[0].fd["id"], self.tourney[self.arcade_idx])
            self.scene = "vs"; self.scene_t = 0
        else:
            self.scene = "menu"

    # ----- actualización -----
    def update(self, keys):
        self.scene_t += 1
        sc = self.scene
        if sc in ("title", "menu", "select", "options", "results", "epilogue", "replays"):
            self.title_dojo.update(1 / 60)
            if sc == "title" and self.frame % 240 == 0:
                self.audio.play(f"shaku{random.randrange(6)}", 0.4)
            return
        if sc == "vs":
            if self.scene_t > 170 or (self.headless and self.scene_t > 5):
                self.scene = "fight"
                self.scene_t = 0
            return
        if sc == "fight":
            m = self.match
            if self.photo is not None or self.paused:
                return
            m.update(keys, self.joys)
            last_sec = m.state == Match.FIGHT and m.timer <= 60
            silent = m.state in (Match.REPLAY,) or (m.slowmo < 1.0)
            self.audio.music_tick(1 / 60, m.intensity * 0.7 + 0.3 * (1 - min(m.f[0].vit, m.f[1].vit) / VIT_MAX) if m.state == Match.FIGHT else 0.0, last_sec, silent)
            if m.state == Match.MATCH_END and m.state_t > 240 and (self.mode in ("spectate",) or self.headless):
                self.go_results()
            if m.state == Match.MATCH_END and m.state_t > 600:
                self.go_results()

    # ----- dibujo -----
    def draw_gpu(self):
        sc = self.scene
        acc = self.access()
        hud = self.hud
        hud.fill((0, 0, 0, 0))
        self._bd_params = None
        p = None
        if sc == "fight" and self.match is not None:
            m = self.match
            ph = self.photo
            if ph is not None:
                m.cam.override = (ph["x"], ph["zoom"])
                keys = pygame.key.get_pressed()
                if keys[pygame.K_LEFT]: ph["x"] -= 6 / ph["zoom"]
                if keys[pygame.K_RIGHT]: ph["x"] += 6 / ph["zoom"]
                if keys[pygame.K_UP]: ph["yoff"] += 4
                if keys[pygame.K_DOWN]: ph["yoff"] -= 4
                m.cam.x, m.cam.zoom = ph["x"], ph["zoom"]
                m.cam.yoff = ph["yoff"]
                p = m.draw_layers(self.bg, self.fig, self.layer, self.fonts, photo=ph, figd=self.figd)
                p.update(lut=ph["lut"], exposure=p["exposure"] + ph["exposure"] * 0.35, dof=9.0 if ph["dof"] else 0.0,
                         grain=0.045 if ph["grain"] else 0.0, tilt=0.0)
                if not ph["vignette"]:
                    p["vig"] = 0.0
                if ph["hud"]:
                    m.draw_hud(hud, self.fonts)
                self.draw_photo_ui(hud)
                m.cam.override = None
                m.cam.yoff = 0
            else:
                p = m.draw_layers(self.bg, self.fig, self.layer, self.fonts, figd=self.figd)
                m.draw_hud(hud, self.fonts)
                m.fx.draw_screen(hud, self.fonts, reduce_flash=acc.get("flashes", False))
                if m.state == Match.REPLAY:
                    self.draw_replay_frame(hud)
                    p["dof"] = 3.0
                if m.replay is not None and m.state != Match.REPLAY:
                    self.text(hud, "REPETICIÓN", 20, W - 190, H - 30, (255, 120, 100))
                if m.state == Match.MATCH_END and m.state_t > 60:
                    self.draw_match_end(hud)
                    p["dof"] = max(p["dof"], 4.0)
                if self.paused:
                    self.draw_pause(hud)
                    p["dof"] = 6.0
        else:
            {"title": self.draw_title, "menu": self.draw_menu, "select": self.draw_select, "options": self.draw_options,
             "replays": self.draw_replays, "vs": self.draw_vs, "results": self.draw_results,
             "epilogue": self.draw_epilogue}.get(sc, self.draw_title)(hud)
            p = self._bd_params
        if p is None:
            self.draw_backdrop(hud, 120)
            p = self._bd_params
        p["contrast"] = acc.get("contrast", False)
        p["daltonism"] = acc.get("daltonism", "off")
        ws = None if self.headless else pygame.display.get_window_size()
        lit = sc == "fight" and self.match is not None
        self.gpu.render(p, self.bg, self.fig, self.layer, hud, window_size=ws, figd=self.figd if lit else None)

    def draw(self):
        if self.gpu is not None:
            self.draw_gpu()
            return
        sc = self.scene
        surf = self.world
        acc = self.access()
        if sc == "fight" and self.match is not None:
            m = self.match
            if self.photo is not None:
                ph = self.photo
                m.cam.override = (ph["x"], ph["zoom"])
                keys = pygame.key.get_pressed()
                if keys[pygame.K_LEFT]: ph["x"] -= 6 / ph["zoom"]
                if keys[pygame.K_RIGHT]: ph["x"] += 6 / ph["zoom"]
                if keys[pygame.K_UP]: ph["yoff"] += 4
                if keys[pygame.K_DOWN]: ph["yoff"] -= 4
                m.cam.x, m.cam.zoom = ph["x"], ph["zoom"]
                m.cam.yoff = ph["yoff"]
                m.draw(surf, self.fonts, self.layer, photo=ph)
                if ph["dof"] and hasattr(pygame.transform, "box_blur"):
                    # profundidad de campo falsa: desenfoque del fondo por encima del plano de combate
                    region = surf.subsurface((0, 0, W, int(FLOOR_Y * 0.62))).copy()
                    region = pygame.transform.box_blur(region, 4)
                    surf.blit(region, (0, 0))
                if ph["exposure"]:
                    e = pygame.Surface((W, H)); e.fill((abs(ph["exposure"]) * 18,) * 3)
                    surf.blit(e, (0, 0), special_flags=pygame.BLEND_ADD if ph["exposure"] > 0 else pygame.BLEND_SUB)
                apply_lut(surf, ph["lut"], m.dojo, ph["grain"] and self.cfg.get("grain", False), acc.get("contrast"), acc.get("daltonism", "off"))
                if not ph["vignette"]:
                    pass
                if ph["hud"]:
                    m.draw_hud(surf, self.fonts)
                self.draw_photo_ui(surf)
                m.cam.override = None
                m.cam.yoff = 0
            else:
                m.draw(surf, self.fonts, self.layer)
                if self.cfg.get("bloom") and m.state != Match.REPLAY:
                    bloom(surf, 0.35)
                if self.cfg.get("tilt") and abs(m.cam.tilt) > 0.05:
                    rotated = pygame.transform.rotozoom(surf, m.cam.tilt, 1.06)
                    surf.blit(rotated, (W // 2 - rotated.get_width() // 2, H // 2 - rotated.get_height() // 2))
                apply_lut(surf, "Natural", m.dojo, self.cfg.get("grain", False), acc.get("contrast"), acc.get("daltonism", "off"))
                m.draw_hud(surf, self.fonts)
                m.fx.draw_screen(surf, self.fonts, reduce_flash=acc.get("flashes", False))
                if m.state == Match.REPLAY:
                    self.draw_replay_frame(surf)
                if m.replay is not None and m.state != Match.REPLAY:
                    self.text(surf, "REPETICIÓN", 20, W - 190, H - 30, (255, 120, 100))
                if m.state == Match.MATCH_END and m.state_t > 60:
                    self.draw_match_end(surf)
                if self.paused:
                    self.draw_pause(surf)
        elif sc == "title":
            self.draw_title(surf)
        elif sc == "menu":
            self.draw_menu(surf)
        elif sc == "select":
            self.draw_select(surf)
        elif sc == "options":
            self.draw_options(surf)
        elif sc == "replays":
            self.draw_replays(surf)
        elif sc == "vs":
            self.draw_vs(surf)
        elif sc == "results":
            self.draw_results(surf)
        elif sc == "epilogue":
            self.draw_epilogue(surf)
        self.screen.blit(surf, (0, 0))

    def draw_backdrop(self, surf, darken=140):
        cam = self.title_cam
        cam.x = math.sin(self.frame * 0.002) * 120
        if self.gpu is not None:
            # el dojo va a las capas de la GPU; el texto de la escena se dibuja después sobre el HUD
            for s in (self.bg, self.fig, self.layer):
                s.fill((0, 0, 0, 0))
            self.title_dojo.draw(self.bg, cam, self.fonts, gpu=True)
            self.title_dojo.draw_beams(None, cam, self.layer, intensity_override=0)
            self._bd_params = scene_params(self.title_dojo, cam, [], None, self.cfg,
                                           dict(dim=darken / 255 * 0.75, dof=6.0 if darken >= 140 else 3.0, tilt=0.0))
            return
        self.title_dojo.draw(surf, cam, self.fonts)
        self.layer.fill((0, 0, 0, 0))
        self.title_dojo.draw_beams(surf, cam, self.layer)
        surf.blit(self.layer, (0, 0))
        t = pygame.Surface((W, H)); t.fill(self.title_dojo.rig["tint"]); surf.blit(t, (0, 0), special_flags=pygame.BLEND_MULT)
        d = pygame.Surface((W, H), pygame.SRCALPHA); d.fill((0, 0, 0, darken)); surf.blit(d, (0, 0))
        surf.blit(self.title_dojo.vignette(180), (0, 0))

    def text(self, surf, s, size, x, y, color=(240, 232, 218), center=False, serif=False, alpha=255, shadow=True):
        f = self.fonts.get(size, serif)
        img = f.render(s, True, color)
        if alpha < 255:
            img.set_alpha(alpha)
        if center:
            x -= img.get_width() // 2
        if shadow:
            sh = f.render(s, True, (0, 0, 0)); sh.set_alpha(min(alpha, 150))
            surf.blit(sh, (x + 2, y + 2))
        surf.blit(img, (x, y))
        return img.get_height()

    def footer(self):
        return EPILOGUE_FOOTER if self.fonts.cjk_name else EPILOGUE_FOOTER.split("— ")[1]

    def draw_title(self, surf):
        self.draw_backdrop(surf, 90)
        band = pygame.Surface((W, 230), pygame.SRCALPHA); band.fill((8, 6, 6, 120)); surf.blit(band, (0, H // 2 - 10))
        pulse = (math.sin(self.frame * 0.05) + 1) / 2
        # caligrafía 道 grande, en tinta
        draw_kanji_do(surf, W // 2, H // 2 - 70, 230, (22, 16, 18), self.fonts)
        self.text(surf, "TATAMI: DŌ", 110, W // 2, H // 2 + 10, (248, 236, 215), center=True, serif=True)
        self.text(surf, "Edición 2026  ·  TatamyDo", 30, W // 2, H // 2 + 115, (220, 200, 170), center=True)
        self.text(surf, "Doce luchadores. Un dojo al atardecer. La luz se va apagando asalto a asalto.", 22, W // 2, H // 2 + 155, (200, 190, 175), center=True)
        self.text(surf, "PULSA INTRO", 28, W // 2, H - 110, (255, 240, 210), center=True, alpha=int(120 + 135 * pulse))
        self.text(surf, self.footer(), 18, W // 2, H - 50, (170, 160, 150), center=True)
        self.text(surf, f"Preset: {self.preset}   ·   F11 pantalla completa", 16, 20, H - 28, (150, 140, 130))

    def draw_menu(self, surf):
        self.draw_backdrop(surf, 150)
        self.text(surf, "TATAMI: DŌ", 64, 90, 70, (248, 236, 215), serif=True)
        self.text(surf, "Kōyō-kan · Kioto", 22, 92, 140, (200, 190, 175))
        y = 210
        for i, (label, key) in enumerate(MENU_ITEMS):
            sel = i == self.menu_i
            if sel:
                pygame.draw.rect(surf, (255, 220, 160), (76, y + 6, 6, 28))
            self.text(surf, label, 36 if sel else 32, 100, y, (255, 240, 215) if sel else (190, 180, 165), serif=sel)
            y += 52
        desc = {"versus": "Dos jugadores en el mismo teclado o con mandos. Al mejor de tres asaltos.",
                "versus_ai": "Elige luchador y enfréntate a un rival controlado por la IA.",
                "arcade": "Vence a los once rivales. Al final, el epílogo de tu luchador.",
                "torneo": "Cuartos, semifinal y final. Gana sin perder un asalto para desbloquear al Sensei Okabe.",
                "training": "Vitalidad infinita, datos de frames y hitboxes (F1 / F2).",
                "spectate": "Siéntate en seiza y mira a la IA combatir. Pulsa F12 para el Modo Foto.",
                "options": "Preset gráfico, volumen, controles y accesibilidad.",
                "quit": "Termina con un saludo."}[MENU_ITEMS[self.menu_i][1]]
        self.text(surf, desc, 20, 100, y + 20, (210, 200, 185))
        # recordatorio de controles
        box = pygame.Surface((420, 300), pygame.SRCALPHA); box.fill((10, 8, 8, 150)); surf.blit(box, (W - 470, 200))
        lines = ["CONTROLES", "", "J1: WASD mover · U/I/O golpes · J agarre", "    K bloqueo (parada: 4 frames) · L Kiai Final",
                 "J2: Flechas · Num 4/5/6 · Num 1/2/3", "", "Atrás + Medio: técnica de estilo", "Agacharse + Fuerte: barrido",
                 "Saltar + golpe: golpe aéreo", "", "F12 Modo Foto · F1 hitboxes · Esc pausa"]
        yy = 212
        for ln in lines:
            self.text(surf, ln, 20 if ln != "CONTROLES" else 26, W - 455, yy, (230, 220, 205) if ln == "CONTROLES" else (200, 190, 175), shadow=False)
            yy += 24
        okabe = "Sensei Okabe: DESBLOQUEADO" if self.save.get("okabe") else "Sensei Okabe: bloqueado (gana el Torneo sin perder un asalto)"
        self.text(surf, okabe, 16, 100, H - 40, (170, 160, 150))

    def draw_select(self, surf):
        self.draw_backdrop(surf, 160)
        self.text(surf, "ELIGE LUCHADOR", 44, W // 2, 24, (248, 236, 215), center=True, serif=True)
        cols = 6
        cw, ch = 150, 120
        x0 = W // 2 - cols * (cw + 10) // 2
        y0 = 90
        for i, fd in enumerate(FIGHTERS):
            r = pygame.Rect(x0 + (i % cols) * (cw + 10), y0 + (i // cols) * (ch + 10), cw, ch)
            locked = fd.get("locked") and not self.save.get("okabe")
            sel = (i == self.sel[0]) or (self.mode == "versus" and i == self.sel[1])
            draw_portrait(surf, fd, r, self.title_dojo.rig, sel, locked)
            if i == self.sel[0]:
                pygame.draw.rect(surf, (255, 170, 80), r.inflate(4, 4), 3)
            if self.mode == "versus" and i == self.sel[1]:
                pygame.draw.rect(surf, (90, 170, 255), r.inflate(10, 10), 3)
            nm = "???" if locked else fd["name"].split(" ")[0] if " " in fd["name"] else fd["name"]
            self.text(surf, nm, 18, r.centerx, r.bottom - 22, (240, 232, 218), center=True)
        # fichas
        for p in range(2 if self.mode == "versus" else 1):
            fd = FIGHTERS[self.sel[p]]
            locked = fd.get("locked") and not self.save.get("okabe")
            x = 60 if p == 0 else W - 480
            y = 360
            pygame.draw.rect(surf, (255, 170, 80) if p == 0 else (90, 170, 255), (x - 10, y, 4, 300))
            if locked:
                self.text(surf, "???", 40, x, y, (120, 110, 100), serif=True)
                self.text(surf, "Gana el Torneo sin perder un asalto.", 20, x, y + 50, (170, 160, 150))
                continue
            self.text(surf, fd["name"], 40, x, y, (248, 236, 215), serif=True)
            self.text(surf, f"«{fd['title']}»", 22, x, y + 48, (220, 200, 170))
            self.text(surf, f"{fd['style']}  ·  {fd['nation']}  ·  {fd['age']} años", 20, x, y + 78, (200, 190, 175))
            self.text(surf, f"Arquetipo: {fd['arche']}", 20, x, y + 104, (200, 190, 175))
            yy = y + 140
            for lab, key in (("Fuerza", "F"), ("Velocidad", "V"), ("Defensa", "D"), ("Alcance", "A"), ("Técnica", "T")):
                self.text(surf, lab, 18, x, yy, (190, 180, 165), shadow=False)
                for k in range(5):
                    pygame.draw.rect(surf, (255, 215, 150) if k < fd[key] else (60, 50, 44), (x + 110 + k * 22, yy + 4, 16, 10))
                yy += 22
            self.text(surf, f"Kiai Final: {fd['kiai']}", 20, x, yy + 6, (255, 225, 180))
            self.text(surf, f"Debilidad: {fd['weak']}", 18, x, yy + 32, (200, 170, 160))
            if self.sel_locked[p]:
                self.text(surf, "LISTO", 30, x, yy + 62, (255, 240, 200), serif=True)
        # figuras a cuerpo entero, frente a frente, respirando en su guardia
        t = self.frame / 60.0
        two = self.mode == "versus"
        for p in range(2 if two else 1):
            fd = FIGHTERS[self.sel[p]]
            if fd.get("locked") and not self.save.get("okabe"):
                continue
            x = (535 if p == 0 else 750) if two else 640
            draw_figure_preview(surf, fd, x, 672, 290 * fd["h"], facing=1 if p == 0 else -1, t=t + p * 1.3,
                                rig=self.title_dojo.rig, ss=self.cfg.get("ss", 2))
        if self.mode != "versus":
            self.text(surf, "El rival lo elige el destino" if self.mode in ("versus_ai",) else "Arcade: once rivales" if self.mode == "arcade" else "Torneo: tres combates" if self.mode == "torneo" else "Entrenamiento", 22, W - 480, 360, (200, 190, 175))
        self.text(surf, "WASD / Flechas mover · U o Intro confirmar · Esc atrás", 18, W // 2, H - 32, (170, 160, 150), center=True)

    def draw_options(self, surf):
        self.draw_backdrop(surf, 170)
        self.text(surf, "OPCIONES", 54, 100, 60, (248, 236, 215), serif=True)
        acc = self.access()
        vals = {"Preset gráfico": self.preset, "Renderer": self.save.get("renderer", "auto") + " (al reiniciar)", "Dificultad IA": DIFFICULTIES[self.save.get("difficulty", 1)][0], "Volumen": f"{int(self.audio.volume * 100)} %",
                "Controles": self.save.get("controls", "moderno"), "Reducir destellos": "sí" if acc.get("flashes") else "no",
                "Temblor de cámara": "sí" if acc.get("shake", True) else "no", "Alto contraste": "sí" if acc.get("contrast") else "no",
                "Filtro daltonismo": acc.get("daltonism", "off"), "Volver": ""}
        y = 150
        for i, o in enumerate(self.OPTS):
            sel = i == self.options_i
            self.text(surf, o, 32, 120, y, (255, 240, 215) if sel else (190, 180, 165), serif=sel)
            self.text(surf, f"<  {vals[o]}  >" if vals[o] else "", 28, 520, y + 2, (255, 225, 180) if sel else (170, 160, 150))
            y += 50
        p = PRESETS[self.preset]
        info = [f"Partículas ×{p['particles']}", f"Haces volumétricos: {'sí' if p['beams'] else 'no'}", f"Grano 35 mm: {'sí' if p['grain'] else 'no'}",
                f"Bloom: {'sí' if p['bloom'] else 'no'}", f"Inclinación de cámara: {'sí' if p['tilt'] else 'no'}", f"Polvo en suspensión: {p['dust']} motas"]
        yy = 160
        for ln in info:
            self.text(surf, ln, 20, 820, yy, (200, 190, 175)); yy += 26
        self.text(surf, "Moderno: Atrás+Medio = técnica de estilo.  Clásico: abajo, diagonal, adelante + Medio; doble toque = carrera.", 18, 100, H - 70, (190, 180, 165))
        active = f"GPU · {self.gpu.renderer_name}" if self.gpu is not None else "CPU (pygame)"
        self.text(surf, f"Renderer activo: {active}. Los sonidos son sintetizados; sin numpy el juego es mudo.", 18, 100, H - 44, (170, 160, 150))

    def draw_replays(self, surf):
        self.draw_backdrop(surf, 170)
        self.text(surf, "REPETICIONES", 54, 100, 60, (248, 236, 215), serif=True)
        self.text(surf, "Cada combate que terminas se graba en ./replays (seed + entradas). La simulación es determinista.", 20, 100, 125, (200, 190, 175))
        if not self.replay_files:
            self.text(surf, "Todavía no hay repeticiones. Juega un combate.", 28, 100, 200, (190, 180, 165))
        start = max(0, self.replay_i - 6)
        y = 180
        for i, path in enumerate(self.replay_files[start:start + 12], start=start):
            sel = i == self.replay_i
            try:
                with open(path, encoding="utf-8") as fh:
                    head = fh.read(400)
                import re as _re
                p1 = _re.search(r'"p1": "(\w+)"', head); p2 = _re.search(r'"p2": "(\w+)"', head)
                sc = _re.search(r'"score": \[(\d+), (\d+)\]', head); dt = _re.search(r'"date": "([^"]+)"', head)
                label = f"{FIGHTER_BY_ID[p1.group(1)]['name']}  {sc.group(1)}–{sc.group(2)}  {FIGHTER_BY_ID[p2.group(1)]['name']}" if p1 and p2 and sc else os.path.basename(path)
                if dt:
                    label = f"{dt.group(1)}   ·   " + label
            except Exception:
                label = os.path.basename(path)
            self.text(surf, label, 28 if sel else 24, 120, y, (255, 240, 215) if sel else (190, 180, 165), serif=sel)
            y += 36
        self.text(surf, "Intro reproducir · Esc volver", 18, W // 2, H - 32, (170, 160, 150), center=True)

    def draw_vs(self, surf):
        m = self.match
        self.draw_backdrop(surf, 120)
        t = self.scene_t
        a, b = m.f
        slide = smoothstep(min(1, t / 40))
        for i, f in enumerate((a, b)):
            x = int(lerp(-500, 80, slide)) if i == 0 else int(lerp(W + 500, W - 560, slide))
            r = pygame.Rect(x, 150, 480, 300)
            draw_portrait(surf, f.fd, r, m.dojo.rig, True)
            self.text(surf, f.fd["name"], 44, x + 20, 470, (248, 236, 215), serif=True)
            self.text(surf, f"«{f.fd['title']}»", 22, x + 20, 520, (220, 200, 170))
            self.text(surf, f.fd["style"] + ("  ·  IA" if f.is_ai else ""), 22, x + 20, 550, (200, 190, 175))
            self.text(surf, f.fd["intro"], 18, x + 20, 585, (190, 180, 165))
        self.text(surf, "VS", 120, W // 2, H // 2 - 80, (255, 230, 180), center=True, serif=True)
        mode_txt = {"arcade": f"Arcade · combate {self.arcade_idx + 1}/{len(self.arcade_queue)}", "torneo": ["Cuartos de final", "Semifinal", "FINAL"][min(2, self.arcade_idx)]}.get(self.mode, "")
        if mode_txt:
            self.text(surf, mode_txt, 28, W // 2, H // 2 + 50, (220, 200, 170), center=True)
        self.text(surf, "Intro para empezar", 18, W // 2, H - 40, (170, 160, 150), center=True)

    def draw_replay_frame(self, surf):
        pygame.draw.rect(surf, (0, 0, 0), (0, 0, W, 70))
        pygame.draw.rect(surf, (0, 0, 0), (0, H - 70, W, 70))
        self.text(surf, f"REPETICIÓN · ángulo {self.match.replay_angle % 3 + 1}/3 · 1/3 velocidad", 22, W // 2, H - 48, (230, 230, 230), center=True)

    def draw_match_end(self, surf):
        m = self.match
        w = m.winner
        if w is None:
            return
        self.text(surf, f"{w.fd['name'].upper()} GANA", 72, W // 2, H // 2 - 140, (255, 236, 200), center=True, serif=True)
        self.text(surf, f"«{w.fd['title']}»", 28, W // 2, H // 2 - 60, (230, 210, 180), center=True)
        if self.mode != "spectate":
            self.text(surf, "Intro para continuar", 20, W // 2, H - 100, (200, 190, 175), center=True)

    def draw_pause(self, surf):
        d = pygame.Surface((W, H), pygame.SRCALPHA); d.fill((0, 0, 0, 205 if self.movelist else 160)); surf.blit(d, (0, 0))
        if self.movelist:
            self.draw_movelist(surf)
            return
        self.text(surf, "PAUSA", 64, W // 2, 200, (248, 236, 215), center=True, serif=True)
        for i, s in enumerate(("Continuar", "Lista de técnicas", "Reiniciar combate", "Volver al menú")):
            sel = i == self.pause_i
            self.text(surf, s, 36 if sel else 30, W // 2, 290 + i * 54, (255, 240, 215) if sel else (190, 180, 165), center=True, serif=sel)
        m = self.match
        self.text(surf, f"{m.f[0].fd['name']}  {m.f[0].wins} – {m.f[1].wins}  {m.f[1].fd['name']}   ·   {m.dojo.rig['name']}", 20, W // 2, 540, (200, 190, 175), center=True)

    MOVE_INPUTS = [("light", "U", "Golpe ligero"), ("medium", "I", "Golpe medio"), ("heavy", "O", "Golpe fuerte"),
                   ("low", "S + I / O", "Barrido"), ("air", "W + golpe", "Golpe aéreo"), ("grab", "J", "Agarre"),
                   ("special", "A(atrás) + I", "Técnica de estilo"), ("kiai", "L (Kiai lleno)", "Kiai Final")]

    def draw_movelist(self, surf):
        """Lista de técnicas con datos de frames y propiedades de cada movimiento."""
        f = self.match.f[self.movelist_p]
        fd = f.fd
        self.text(surf, f"{fd['name']}  ·  {fd['style']}", 40, 70, 40, (248, 236, 215), serif=True)
        self.text(surf, "Arranque / activos / recuperación en frames a 60 fps.  A / D cambia de luchador · Esc vuelve", 18, 72, 92, (190, 180, 165))
        cols = [(70, "Entrada"), (250, "Técnica"), (520, "Arr."), (590, "Act."), (660, "Rec."), (740, "Daño"), (830, "Propiedades")]
        for x, lab in cols:
            self.text(surf, lab, 18, x, 130, (255, 215, 160), shadow=False)
        y = 160
        for key, inp, label in self.MOVE_INPUTS:
            m = f.moves[key]
            props = []
            if m.kind == "grab":
                props.append("agarre" + ("" if m.techable else " de comando"))
            if m.knockdown:
                props.append("derriba")
            if m.armor:
                props.append(f"armadura ×{m.armor}")
            if m.overhead:
                props.append("alto: bloquear de pie")
            if m.height == "low":
                props.append("bajo: bloquear agachado")
            if m.invuln:
                props.append(f"invulnerable {m.invuln}f")
            if m.multihit > 1:
                props.append(f"{m.multihit} golpes")
            if m.dash:
                props.append("avanza")
            if m.ground:
                props.append("lleva al suelo")
            if m.counter:
                props.append("contraataque")
            name = m.name if key in ("special", "kiai") else label
            vals = [inp, name, str(m.startup), str(m.active), str(m.recovery), str(m.damage * m.multihit) if m.damage else "—", ", ".join(props)]
            for (x, _), v in zip(cols, vals):
                self.text(surf, v, 20, x, y, (240, 232, 218) if key not in ("special", "kiai") else (255, 225, 180), shadow=False)
            y += 34
            if m.desc and key in ("special", "kiai", "grab"):
                self.text(surf, m.desc, 17, 250, y - 8, (185, 175, 160), shadow=False)
                y += 22
        tips = ["Golpe gana a agarre · Agarre gana a bloqueo · Bloqueo gana a golpe.",
                "Parada perfecta: bloquea en los 4 frames previos al impacto. Golpear en el arranque del rival es CONTRA (+25 %).",
                "Bloquear gasta aliento: sin aliento, la guardia se rompe. Ligero cancela en medio, fuerte o agarre si conecta."]
        for t in tips:
            self.text(surf, t, 18, 70, y + 10, (210, 200, 185), shadow=False)
            y += 24

    def draw_photo_ui(self, surf):
        ph = self.photo
        box = pygame.Surface((W, 64), pygame.SRCALPHA); box.fill((0, 0, 0, 170)); surf.blit(box, (0, H - 64))
        self.text(surf, f"MODO FOTO   LUT [1-5]: {ph['lut']}   ·   zoom {ph['zoom']:.2f} (Q/E)   ·   exposición {ph['exposure']:+d} ([ ])   ·   G grano {'on' if ph['grain'] else 'off'}   ·   B bokeh {'on' if ph['dof'] else 'off'}   ·   H HUD", 20, 20, H - 52, (240, 232, 218), shadow=False)
        self.text(surf, "Flechas: encuadre   ·   Espacio: avanzar un fotograma   ·   P: guardar PNG 2x en ./fotos   ·   F12/Esc: salir", 18, 20, H - 26, (190, 180, 165), shadow=False)
        if ph["saved_msg"] > 0:
            ph["saved_msg"] -= 1
            self.text(surf, "FOTOGRAMA GUARDADO", 40, W // 2, 40, (255, 240, 200), center=True, serif=True)

    def draw_results(self, surf):
        m = self.match
        self.draw_backdrop(surf, 170)
        w = m.winner
        self.text(surf, "RESULTADO", 36, W // 2, 60, (220, 200, 170), center=True)
        if w is not None:
            self.text(surf, w.fd["name"], 72, W // 2, 110, (248, 236, 215), center=True, serif=True)
            self.text(surf, f"gana {w.wins}–{m.f[1 - m.f.index(w)].wins}", 32, W // 2, 190, (230, 210, 180), center=True)
        for i, f in enumerate(m.f):
            x = 160 if i == 0 else W - 560
            r = pygame.Rect(x, 260, 400, 220)
            draw_portrait(surf, f.fd, r, m.dojo.rig, f is w)
            self.text(surf, f.fd["name"], 28, x, 490, (240, 232, 218), serif=True)
            self.text(surf, f"Golpes conectados: {f.landed_hits}   Waza-ari: {f.wazaari}   Jogai: {f.jogai}", 18, x, 525, (200, 190, 175))
            self.text(surf, f"Vitalidad final: {f.vit}/{VIT_MAX}", 18, x, 548, (200, 190, 175))
        if self.mode == "arcade":
            msg = f"Arcade: {self.arcade_idx}/{len(self.arcade_queue)} rivales vencidos" if w is m.f[0] else "El camino termina aquí. Vuelve mañana al dojo."
            self.text(surf, msg, 24, W // 2, 600, (220, 200, 170), center=True)
        if self.mode == "torneo":
            msg = ("Pasas a la " + ["semifinal", "final", "..."][min(2, self.arcade_idx)]) if w is m.f[0] else "Eliminado del torneo."
            self.text(surf, msg, 24, W // 2, 600, (220, 200, 170), center=True)
        self.text(surf, "Intro para continuar", 20, W // 2, H - 50, (170, 160, 150), center=True)

    def draw_epilogue(self, surf):
        m = self.match
        self.draw_backdrop(surf, 200)
        fd = m.f[0].fd
        self.text(surf, "EPÍLOGO", 32, W // 2, 90, (220, 200, 170), center=True)
        self.text(surf, fd["name"], 64, W // 2, 130, (248, 236, 215), center=True, serif=True)
        # texto en varias líneas
        words = fd["epilogue"].split(" ")
        lines, cur = [], ""
        for wd in words:
            if len(cur) + len(wd) > 60:
                lines.append(cur); cur = wd
            else:
                cur = (cur + " " + wd).strip()
        lines.append(cur)
        y = 260
        for ln in lines:
            self.text(surf, ln, 30, W // 2, y, (235, 225, 210), center=True, serif=True); y += 42
        if self.mode == "torneo" and getattr(self, "unlock_msg", False):
            self.text(surf, "El dueño del dojo se levanta despacio. SENSEI OKABE DESBLOQUEADO.", 26, W // 2, y + 30, (255, 225, 170), center=True)
            self.unlock_msg = False
        self.text(surf, self.footer(), 22, W // 2, H - 90, (200, 190, 175), center=True)
        self.text(surf, "Intro para volver al menú", 18, W // 2, H - 44, (170, 160, 150), center=True)
