"""Game: state machine, fixed 60 Hz timestep, input routing, the Run object, save/load.

Frame order (doc 4), one step = 1/60 s, in _update_run:
    events -> hack/breach session (REAL dt) -> world -> player (+ dash/attack events)
    -> progression -> bosses -> finale -> attacks -> minions + projectiles -> profiler
    -> Director tick (every 30 frames) -> player damage / session end / jack-in + interact
    -> death -> Key freeze -> drain requests (scenes, ending, saves) -> camera
Render: world layer (world, Key shards, boss ports, attack telegraphs, minions, Mirror, player)
scaled by the camera zoom -> floaters -> full-screen overlays (blackout) -> jack-in zoom -> shake
-> HUD -> hack terminal + its attack overlay + the boss attack queue beside it -> cursor.
Game also sets world.reduced_glitch and camera.zoom_map every frame (progression sets
world.hatch_open / cleared_racks / cache_state / act).

Slow motion lives here: world_dt = dt * settings.world_slowmo (0.30, calm hack 0.15) while any
terminal (hack or ROOT BREACH) is open, and during an OVERLOAD-chain getaway (the player keeps
real dt). Sessions always get real dt. A new Key freezes the world for KEY_FREEZE_S.

Systems talk through Run.emit(event, **data) (listeners: progression, profiler, bosses, finale,
attacks, minions, then Game itself for toasts) and through Run.request_scene / request_ending /
request_save, which Game drains once per frame. Anything F can jack into comes from a "jack
provider": minions (built in), bosses (breach ports), progression (Spool's cache), finale (the
Mirror's handle breach); each provider has find_jack_target / open_session / finish_session.

Test hook (used by tools/smoke_test.py):
    g = Game(headless_ok=True)
    g.step(events, dt=1/60)      process a list of pygame events, advance one frame, draw
    g.state, g.run, g.surface    current state, current Run (or None), internal frame
    g.save_frame(path)           write the current frame as a PNG
    g.new_run(seed, handle)      skip the menus
    dev helpers (always callable from tests; hotkeys F6-F10 only with --dev):
    grant_keys(n), teleport_to_zone(id), start_boss(id), finish_breach(), dev_kill_phase(),
    enter_deep_core(), finish_finale(ending=None), save_game(), load_save()
Mouse events given to step() use WINDOW pixels, like real ones (see gdev.click()).
"""
import os
import random
import time

import pygame

import attacks as attacks_mod
import bosses as bosses_mod
import finale as finale_mod
import gdev
import hack
import hud
import menu
import minions as minions_mod
import player as player_mod
import profiler as profiler_mod
import progression as progression_mod
import settings as settings_mod
import shop as shop_mod
import world as world_mod

STEP = 1.0 / 60.0

# Game states
MENU = "MENU"
HANDLE = "HANDLE"           # ENTER HANDLE (doc 8.1), before a new game
PLAY = "PLAY"
HACK = "HACK"               # any terminal: minion DECODE/PAYLOAD, ROOT BREACH, cache, the Mirror
PAUSE = "PAUSE"
SETTINGS = "SETTINGS"
HOWTO = "HOWTO"
INTEL = "INTEL"             # the DECRYPT screen (Tab): name bar, intel log, codex
SCENE = "SCENE"             # terminal story scene: Core Dump, Key 14, Spool's cache, Deep Core descent
ENDING = "ENDING"           # PURGE / PATCH ending + credits
SHOP = "SHOP"               # Clean Node shop (doc 10): after F at a node terminal saves
SUB_SCREENS = (SETTINGS, HOWTO, INTEL, SHOP)     # these go "back" to Game.return_state

SAVE_FILE = "save/run.json"
SAVE_VERSION = 2

WORLD_SLOWMO = 0.30
JACK_HOLD_S = 0.25
JACK_RANGE_PX = 96
WIDE_PORT_RANGE_PX = 140
REMOTE_JACK_PX = 360        # Remote Jack (doc 10): a PINNED exposed minion can be jacked from this far
HATCH_CONFIRM_S = 4.0       # a descent that rules out PATCH needs a second F press within this
EXPOSE_CAM_PX = 320         # EXPOSURE: the camera frames the exposed core face when this close
INPUT_LOCK_S = 0.2
TURN_S = 25.0
LONG_TURN_S = 45.0
OVERLOAD_RADIUS = 160
CHAIN_DAEMON_MULT = 1.5
CHAIN_MAX = 4
CHAIN_GETAWAY_S = 1.0
DEATH_S = 2.2
KEY_FREEZE_S = 0.6           # world stops while a new Key lands in the name bar
KEY_BANNER_S = 3.6
HACK_TINT = (150, 185, 160)  # multiply colour for the greyscale world while hacking
HACK_CAM_LERP = 0.3         # per-frame camera lerp while hacking (PLAY uses 0.15)
HACK_CLEAR_PX = 34          # free screen rows between the hack window and the sprites under it
HACK_ZOOM = 1.15            # jack-in zoom (doc 5.7), eased in over HACK_ZOOM_S
HACK_ZOOM_S = 0.15
ZONE_CAM_BIAS = 48          # in a rack's zone the camera leans this far toward the nearest port
DIRECTOR_TICK_FRAMES = 30
DEV_ZONES = ["A", "B", "N2", "C", "E", "cache", "D", "hatch", "N1"]
NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
                "Twelve", "Thirteen", "Fourteen"]


class Run(object):
    """Everything about one play session. Other modules may call the methods marked (API).

    world, overworld, camera, player, minions     the live objects (overworld = the main map while
                                                  in the Deep Core, else None); in_deep_core bool
    progression, profiler, director, tuner, bosses, attacks, finale    the expansion systems
    hack            HackSession / BreachSession or None (open while state is HACK)
    hack_target     what is being hacked (Minion, BreachPort, CacheTerminal, MirrorTarget) or None
    hack_provider   "minion" or the provider object that opened the session
    codex, intel    hack.Codex / hack.IntelLog (kept through death, saved)
    rng             random.Random for this run
    bits            current Bits
    keys            (property) set of name-bar cluster indices recovered (0..13), from progression
    key_anim        {cluster index: seconds since its Key arrived} (HUD flourish)
    key_banner      None or {"index", "source", "reveal", "count", "t"} (HUD band, KEY_BANNER_S)
    stats           {"time","kills","hacks","clean_hacks","deaths","redacted","backtraces","aborts","bits_earned"}
    time            real seconds in PLAY/HACK;  time_scale world speed this frame; world_dt, real_dt
    toasts          [[text, color, seconds_left]]   floaters [[x, y, text, color, seconds_left]]
    jack_target, jack_progress (0..1), interact_hint ((x, y), text)|None   for the HUD prompts
    requests        [(kind, payload)] drained by Game: ("scene", spec), ("ending", kind), ("save", reason)
    """

    def __init__(self, settings, seed=None, handle="TECH", save=None, dev=False):
        self.settings = settings
        self.dev = dev
        self.rng = random.Random(seed)
        self.world = world_mod.World.load()
        self.overworld = None
        self.in_deep_core = False
        self.camera = world_mod.Camera(self.world.art_bounds, settings_mod.zoom(settings))
        self.player = player_mod.Player(self.world.player_start)
        self.camera.snap(self.player.pos)
        self.minions = minions_mod.MinionManager(self.world)
        self.codex = hack.Codex()
        self.intel = hack.IntelLog()
        self.listeners = []
        self.requests = []
        self.key_anim = {}
        self.key_banner = None
        self.key_freeze = 0.0
        self.stats = {"time": 0.0, "kills": 0, "hacks": 0, "clean_hacks": 0, "deaths": 0,
                      "redacted": 0, "backtraces": 0, "aborts": 0, "bits_earned": 0}
        self.bits = 0
        self.time = 0.0
        # expansion systems (order matters: later ones may look at earlier ones)
        self.progression = progression_mod.Progression(self, handle)
        self.profiler = profiler_mod.Profiler(self)
        self.director = self.profiler.director
        self.tuner = self.profiler.tuner
        self.attacks = attacks_mod.AttackSystem(self)
        self.bosses = bosses_mod.BossManager(self)
        self.finale = finale_mod.Finale(self)
        self.listeners = [self.progression, self.profiler, self.bosses, self.finale, self.attacks, self.minions]
        self.hack = None
        self.hack_target = None
        self.hack_provider = None
        self.chain = 0
        self.time_scale = 1.0
        self.world_dt = 0.0
        self.real_dt = 0.0
        self.getaway = 0.0
        self.input_lock = 0.0
        self.toasts = []
        self.floaters = []
        self.shake_px = 0.0
        self.shake_t = 0.0
        self.jack_target = None
        self.jack_hold = 0.0
        self.jack_progress = 0.0
        self.interact_hint = None
        self.death_t = 0.0
        self.hatch_confirm = 0.0      # > 0: the next F at the hatch descends (the PURGE-only warning was shown)
        self.last_save = None         # (run.time, label) of the last save, for the pause screen
        if save:
            self.apply_save(save)

    # ------------------------------------------------------------ API for other modules

    @property
    def keys(self):
        return set(self.progression.keys)

    @property
    def handle(self):
        return self.progression.handle

    def emit(self, event, **data):
        """Broadcast an event to every system (API): listener.on_event(event, data) for each of
        progression, profiler, bosses, finale, attacks, minions, then Game. See the contract's event table."""
        for listener in list(self.listeners):
            listener.on_event(event, data)

    def request_scene(self, spec):
        """Open a terminal story scene as soon as no terminal is open (API)."""
        self.requests.append(("scene", spec))

    def request_ending(self, kind):
        """End the run with "PURGE" or "PATCH" (API, finale.py)."""
        self.requests.append(("ending", kind))

    def request_save(self, reason=""):
        """Save the run at the end of this frame (API)."""
        self.requests.append(("save", reason))

    def toast(self, text, color=gdev.GREEN, seconds=2.2):
        """Short message above the weapon bar (API)."""
        self.toasts.append([text, color, seconds])
        self.toasts = self.toasts[-4:]

    def float_text(self, pos, text, color=gdev.AMBER, seconds=0.9):
        """Text that rises from a world position and fades (API)."""
        self.floaters.append([float(pos[0]), float(pos[1]), text, color, seconds])

    def shake(self, px, seconds):
        """Screen shake request (API). Ignored when the setting is off."""
        self.shake_px = max(self.shake_px, float(px))
        self.shake_t = max(self.shake_t, float(seconds))

    def add_bits(self, n, pos=None):
        """Give Bits (API)."""
        self.bits += int(n)
        self.stats["bits_earned"] += int(n)
        if pos is not None:
            self.float_text(pos, "+%d BITS" % n)

    def on_minion_killed(self, minion):
        """MinionManager calls this exactly once per WEAPON kill (API)."""
        self.stats["kills"] += 1
        self.add_bits(getattr(minion, "bits", 5), minion.pos)
        if self.rng.random() < 0.5 and self.player.hp < self.player.max_hp:
            self.player.heal(1)
            self.float_text((minion.pos[0], minion.pos[1] - 12), "+1 HP", gdev.GREEN)
        self.emit("minion_killed", minion=minion)

    def on_minion_died(self, minion):
        """MinionManager calls this once for EVERY death, any dead_cause (API)."""
        self.emit("minion_died", minion=minion, cause=minion.dead_cause)

    def base_hack_ctx(self, target):
        """The HackContext every session starts from (API; see the contract)."""
        prog = self.progression
        return {"emit": self.emit, "level": self.tuner.level_for(getattr(target, "kind", "b1t")),
                "timer_adjust": self.tuner.timer_adjust(), "lexicon": prog.has_exploit("LEXICON"),
                "trace_mult": 0.75 if prog.has_exploit("TRACE_DAMPER") else 1.0, "act": prog.act,
                "turn_s": LONG_TURN_S if prog.has_exploit("LONG_TURN") else TURN_S}

    def minion_hack_ctx(self, minion):
        ctx = self.base_hack_ctx(minion)
        if getattr(minion, "kind", "") == "f1r3w4ll":
            ctx["two_part"] = True               # doc 6: a F1R3W4LL's words are two picks
        for system in (self.profiler, self.bosses, self.finale, self.progression):
            ctx.update(system.hack_context(minion) or {})
        return ctx

    def jack_range(self):
        return WIDE_PORT_RANGE_PX if self.progression.has_exploit("WIDE_PORT") else JACK_RANGE_PX

    def objective_text(self):
        if self.in_deep_core:
            return self.finale.objective_text()
        return self.progression.objective_text()

    def boss_hud(self):
        """The boss bar's data: the active Daemon, or the Mirror (dict, see bosses.Boss.hud_info)."""
        if self.in_deep_core:
            return self.finale.hud_info()
        return self.bosses.hud_info()

    # ------------------------------------------------------------ save / load

    def to_save(self):
        return {"version": SAVE_VERSION, "complete": False, "saved_at": time.time(),
                "handle": self.handle,
                "run": {"time": self.time, "bits": self.bits, "stats": dict(self.stats),
                        "in_deep_core": self.in_deep_core, "upgrades": sorted(self.player.upgrades)},
                "progression": self.progression.to_save(), "bosses": self.bosses.to_save(),
                "profiler": self.profiler.to_save(), "finale": self.finale.to_save(),
                "intel": self.intel.to_save(), "codex": self.codex.to_save()}

    def apply_save(self, d):
        r = d.get("run", {})
        self.time = float(r.get("time", 0.0))
        self.bits = int(r.get("bits", 0))
        self.stats.update(r.get("stats", {}))
        for uid in r.get("upgrades", []):
            if uid in [u[0] for u in shop_mod.WEAPON_UPGRADES]:
                self.player.add_upgrade(uid)
        self.intel.from_save(d.get("intel"))
        self.codex.from_save(d.get("codex"))
        self.progression.from_save(d.get("progression"))
        self.bosses.from_save(d.get("bosses"))
        self.profiler.from_save(d.get("profiler"))
        self.finale.from_save(d.get("finale"))
        if r.get("in_deep_core"):
            self.swap_to_deep_core()
        else:
            self.player.pos = pygame.math.Vector2(self.progression.respawn_point())
            self.player._sync_rect()
            self.camera.snap(self.player.pos)
        self.last_save = (self.time, self.progression.respawn_label())

    def swap_to_deep_core(self):
        """Replace the map with the Deep Core arena (one way)."""
        self.overworld = self.world
        self.world = world_mod.World.deep_core()
        self.in_deep_core = True
        self.minions.set_world(self.world)
        self.camera = world_mod.Camera(self.world.art_bounds, settings_mod.zoom(self.settings))
        self.player.respawn(self.world.player_start)
        self.camera.snap(self.player.pos)
        self.attacks.clear()
        self.bosses.active = None
        self.emit("deep_core_entered")
        self.finale.start()

    # ------------------------------------------------------------ timers

    def tick(self, dt):
        for t in self.toasts:
            t[2] -= dt
        self.toasts = [t for t in self.toasts if t[2] > 0]
        for f in self.floaters:
            f[1] -= 24 * dt
            f[4] -= dt
        self.floaters = [f for f in self.floaters if f[4] > 0]
        self.shake_t = max(0.0, self.shake_t - dt)
        if self.shake_t <= 0:
            self.shake_px = 0.0
        self.input_lock = max(0.0, self.input_lock - dt)
        self.getaway = max(0.0, self.getaway - dt)
        self.hatch_confirm = max(0.0, self.hatch_confirm - dt)
        self.key_freeze = max(0.0, self.key_freeze - dt)
        for k in self.key_anim:
            self.key_anim[k] += dt
        if self.key_banner is not None:
            self.key_banner["t"] += dt
            if self.key_banner["t"] >= KEY_BANNER_S:
                self.key_banner = None


class _GameListener(object):
    """Game's own slot in Run.listeners: turns events into toasts / the Key banner."""

    def __init__(self, game):
        self.game = game

    def on_event(self, name, data):
        self.game._on_event(name, data)


class Game(object):
    def __init__(self, headless_ok=False, dev=False):
        if headless_ok:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        self.dev = bool(dev)
        self.settings = settings_mod.load()
        gdev.init(60, "Pixel Apocalypse", self.settings["fullscreen"])
        pygame.key.set_repeat()
        self.surface = pygame.Surface(gdev.INTERNAL_SIZE).convert()
        self.crt = menu.CRT()
        self.main_menu = menu.MainMenu(self.crt, self.has_save)
        self.handle_screen = menu.HandleScreen(self.crt)
        self.pause_menu = menu.PauseMenu(self.crt)
        self.settings_menu = menu.SettingsMenu(self.crt, self.settings, self._setting_changed)
        self.howto = menu.HowToScreen(self.crt)
        self.intel_screen = menu.IntelLogScreen(self.crt)
        self.scene_screen = menu.TerminalScene(self.crt)
        self.ending_screen = menu.EndingScreen(self.crt)
        self.shop_screen = menu.ShopScreen(self.crt)
        self.screens = {MENU: self.main_menu, HANDLE: self.handle_screen, PAUSE: self.pause_menu,
                        SETTINGS: self.settings_menu, HOWTO: self.howto, INTEL: self.intel_screen,
                        SCENE: self.scene_screen, ENDING: self.ending_screen, SHOP: self.shop_screen}
        self.state = MENU
        self.return_state = MENU
        self.frozen = None
        self.run = None
        self.last_run = None
        self.scene_spec = None
        self.running = True
        self.frame = 0
        self.dev_zone = 0
        # input as tracked from events (works for scripted events too)
        self.keys_down = set()
        self.mouse_buttons = set()
        self.mouse_pos = (gdev.INTERNAL_SIZE[0] // 2, gdev.INTERNAL_SIZE[1] // 2)
        self._clear_edges()
        self._pending = []
        self._gray = None                 # cached greyscale world while hacking
        self._tint = None
        self._zoom_map = None             # (src_x, src_y, kx, ky) of the jack-in zoom this frame
        self._world_layer = None          # world drawn at INTERNAL_SIZE / zoom, then scaled up
        self.fx_rng = random.Random()     # visual noise only; gameplay uses run.rng
        self.main_menu.open()

    # ------------------------------------------------------------ main loop

    def main_loop(self, max_frames=None):
        """Real-time loop: fixed 60 Hz steps, one draw + present per displayed frame.
        max_frames (tests / --frames N): stop after that many steps."""
        acc = 0.0
        while self.running:
            real = min(0.25, gdev.clock.tick(gdev.fps) / 1000.0)
            acc += real
            self._pending.extend(pygame.event.get())
            steps = 0
            while acc >= STEP and steps < 5 and self.running:
                events, self._pending = self._pending, []
                self.step(events, STEP, draw=False)
                acc -= STEP
                steps += 1
            if steps == 5:
                acc = 0.0           # too far behind: drop the backlog instead of spiralling
            self._draw()
            gdev.present(self.surface)
            if max_frames is not None and self.frame >= max_frames:
                self.running = False

    def step(self, events, dt=STEP, draw=True):
        """Process events, advance one frame by dt, and (by default) draw the frame."""
        for e in events:
            e = self._convert(e)
            self._track(e)
            self._route(e)
            if not self.running:
                return
        self._update(dt)
        self._clear_edges()
        if draw:
            self._draw()
        self.frame += 1

    def save_frame(self, path, scale=2):
        img = self.surface if scale == 1 else pygame.transform.scale(
            self.surface, (gdev.INTERNAL_SIZE[0] * scale, gdev.INTERNAL_SIZE[1] * scale))
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        pygame.image.save(img, path)

    # ------------------------------------------------------------ events

    def _convert(self, e):
        # Every mouse position from here on is in internal-surface pixels.
        if e.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            d = dict(e.dict)
            d["pos"] = gdev.window_to_internal(e.pos)
            return pygame.event.Event(e.type, d)
        return e

    def _clear_edges(self):
        self.edges = {"fire_pressed": False, "fire_released": False, "alt_pressed": False,
                      "dash": False, "reload": False, "weapon_select": None, "wheel": 0,
                      "interact": False}

    def _track(self, e):
        if e.type == pygame.KEYDOWN:
            self.keys_down.add(e.key)
        elif e.type == pygame.KEYUP:
            self.keys_down.discard(e.key)
        elif e.type == pygame.MOUSEMOTION:
            self.mouse_pos = e.pos
        elif e.type == pygame.MOUSEBUTTONDOWN:
            self.mouse_pos = e.pos
            self.mouse_buttons.add(e.button)
        elif e.type == pygame.MOUSEBUTTONUP:
            self.mouse_pos = e.pos
            self.mouse_buttons.discard(e.button)
        elif e.type == pygame.WINDOWFOCUSLOST:
            self.keys_down.clear()
            self.mouse_buttons.clear()

    def _route(self, e):
        if e.type == pygame.QUIT:
            self.running = False
            return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_F11:
            self.settings["fullscreen"] = not self.settings["fullscreen"]
            self._setting_changed("fullscreen")
            return
        if self.state == PLAY:
            self._route_play(e)
        elif self.state == HACK:
            if e.type == pygame.WINDOWFOCUSLOST:
                self._suspend_hack()
            elif self.run.hack is not None:
                self.run.hack.handle_event(e)
        else:
            scr = self.screens.get(self.state)
            if scr is not None:
                action = scr.handle_event(e)
                if action:
                    self._do(action)

    def _route_play(self, e):
        if e.type == pygame.WINDOWFOCUSLOST:
            self._open_pause()
            return
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self._open_pause()
            elif e.key == pygame.K_TAB:
                self._open_sub(INTEL, PLAY)
            elif e.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                self.edges["weapon_select"] = e.key - pygame.K_1
            elif e.key == pygame.K_SPACE:
                self.edges["dash"] = True
            elif e.key == pygame.K_r:
                self.edges["reload"] = True
            elif e.key == pygame.K_f:
                self.edges["interact"] = True
            elif self.dev:
                self._dev_key(e.key)
        elif e.type == pygame.MOUSEBUTTONDOWN:
            if e.button == 1:
                self.edges["fire_pressed"] = True
            elif e.button == 3:
                self.edges["alt_pressed"] = True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.edges["fire_released"] = True
        elif e.type == pygame.MOUSEWHEEL:
            if e.y:
                self.edges["wheel"] = -1 if e.y > 0 else 1

    def _dev_key(self, key):
        """--dev hotkeys (PLAY only): F6 Key, F7 next zone, F8 kill boss phase, F9 open breach,
        F10 Deep Core."""
        if key == pygame.K_F6:
            self.grant_keys(1)
        elif key == pygame.K_F7:
            self.dev_zone = (self.dev_zone + 1) % len(DEV_ZONES)
            self.teleport_to_zone(DEV_ZONES[self.dev_zone])
        elif key == pygame.K_F8:
            self.dev_kill_phase()
        elif key == pygame.K_F9:
            if self.run.bosses.active is not None:
                self.run.bosses.dev_open_breach()
        elif key == pygame.K_F10:
            self.enter_deep_core()

    def _build_input(self):
        inp = gdev.InputState()
        k = self.keys_down
        inp.aim_screen = self.mouse_pos
        inp.aim = self.run.camera.internal_to_world(self.mouse_pos)
        if self.run.input_lock > 0 or self.run.player.dead or self.run.key_freeze > 0:
            return inp
        mx = (1 if pygame.K_d in k else 0) - (1 if pygame.K_a in k else 0)
        my = (1 if pygame.K_s in k else 0) - (1 if pygame.K_w in k else 0)
        inp.move = (mx, my)
        inp.fire_pressed = self.edges["fire_pressed"]
        inp.fire_held = 1 in self.mouse_buttons
        inp.fire_released = self.edges["fire_released"]
        inp.alt_pressed = self.edges["alt_pressed"]
        inp.alt_held = 3 in self.mouse_buttons
        inp.dash_pressed = self.edges["dash"]
        inp.reload_pressed = self.edges["reload"]
        inp.weapon_select = self.edges["weapon_select"]
        inp.weapon_cycle = self.edges["wheel"]
        inp.interact_pressed = self.edges["interact"]
        inp.interact_held = pygame.K_f in k
        return inp

    # ------------------------------------------------------------ actions / transitions

    def _do(self, action):
        if action in ("new_game", "play", "play_again"):
            self.handle_screen.reset()
            self._set_state(HANDLE)
        elif action == "handle_ok":
            self.new_run(handle=self.handle_screen.value)
        elif action == "continue":
            if not self.load_save():
                self._set_state(MENU)
        elif action in ("howto", "settings", "intel"):
            target = {"howto": HOWTO, "settings": SETTINGS, "intel": INTEL}[action]
            self._open_sub(target, self.state)
        elif action == "quit":
            self.running = False
        elif action == "resume":
            self._set_state(PLAY)
            self.run.input_lock = INPUT_LOCK_S
        elif action in ("quit_to_menu", "main_menu"):
            self._flush_saves()                  # a save queued this frame (e.g. a Key) still lands
            self.end_run()
            self._set_state(MENU)
        elif action.startswith("buy:") and self.run is not None:
            ok, msg = shop_mod.buy(self.run, action[4:])
            if ok:
                self.save_game("shop")
            self.shop_screen.message, self.shop_screen.message_ok = msg, ok
            self.shop_screen.refresh()
        elif action == "scene_done":
            self._close_scene()
        elif action == "ending_done":
            self._set_state(MENU)
        elif action == "back":
            if self.state == HANDLE:
                self._set_state(MENU)
                return
            self._set_state(self.return_state)
            if self.return_state == PLAY:
                self.run.input_lock = INPUT_LOCK_S

    def _set_state(self, state):
        self.state = state
        scr = self.screens.get(state)
        if scr is not None:
            scr.frozen = self.frozen if state in (PAUSE, SCENE) + SUB_SCREENS else None
            if state == INTEL:
                r = self.run or self.last_run
                scr.intel = r.intel if r else None
                scr.codex = r.codex if r else None
                scr.run = r
            if state == SHOP:
                scr.run = self.run
            if state == PAUSE and self.run is not None:
                scr.info = self._save_info()
            scr.open()
        pygame.mouse.set_visible(state not in (PLAY, HACK))   # the game draws its own cursor

    def _save_info(self):
        run = self.run
        if run.last_save is None:
            return "NOT SAVED YET :: visit a CLEAN NODE to save"
        ago = int(run.time - run.last_save[0])
        return "LAST SAVE: %s, %dm %02ds ago :: later progress is lost if you quit" % (
            run.last_save[1], ago // 60, ago % 60)

    def _suspend_hack(self):
        """The window lost focus mid-hack: the real-time decode timer must not run out in
        the background. Abort cleanly (same as Esc: no TRACE, minion reboots; a hack that
        already reached PAYLOAD keeps its success) and open PAUSE like PLAY does."""
        run = self.run
        for _ in range(CHAIN_MAX + 1):           # an OVERLOAD already chosen may chain once more
            if run.hack is None:
                break
            run.hack.abort("focus")
            self._finish_hack()
        # a hack already won (PAYLOAD / ACCESS GRANTED) may have queued a Key save: write it now,
        # before PAUSE, so QUIT TO MENU cannot drop it (scenes wait for the resume)
        self._flush_saves()
        if self.state == PLAY:
            self._draw()                         # the pause backdrop shows the world, not the terminal
            self._open_pause()

    def _flush_saves(self):
        """Write any queued ("save", reason) requests now; keep the rest (scenes, endings)."""
        run = self.run
        if run is None:
            return
        saves = [r for r in run.requests if r[0] == "save"]
        if not saves:
            return
        run.requests = [r for r in run.requests if r[0] != "save"]
        self.save_game(saves[-1][1])

    def _open_pause(self):
        self.frozen = self.surface.copy()
        self.pause_menu.selected = 0
        self._set_state(PAUSE)

    def _open_sub(self, state, came_from):
        if came_from == PLAY:
            self.frozen = self.surface.copy()
        elif came_from == MENU:
            self.frozen = None
        self.return_state = came_from
        self._set_state(state)

    def new_run(self, seed=None, handle="TECH"):
        self.run = Run(self.settings, seed, handle=handle, dev=self.dev)
        self.run.listeners.append(_GameListener(self))
        self.frozen = None
        menu.game_start = True
        self._set_state(PLAY)
        self.run.input_lock = INPUT_LOCK_S
        self.run.toast("JACKED INTO DEEPSTACK :: %s, find RACK A" % self.run.handle, gdev.GREEN, 3.0)

    def end_run(self):
        if self.run is not None:
            self.last_run = self.run
        self.run = None
        self.frozen = None
        menu.game_start = False

    def _setting_changed(self, key):
        if key == "fullscreen":
            gdev.set_fullscreen(self.settings["fullscreen"])
        if key == "zoom" and self.run is not None:
            self.run.camera.set_zoom(settings_mod.zoom(self.settings))
        settings_mod.save(self.settings)

    # ------------------------------------------------------------ save / load

    def has_save(self):
        d = gdev.load_json(SAVE_FILE)
        return isinstance(d, dict) and d.get("version") == SAVE_VERSION and not d.get("complete")

    def save_game(self, reason=""):
        if self.run is None:
            return False
        try:
            gdev.save_json(SAVE_FILE, self.run.to_save())
        except (IOError, OSError) as err:
            print("[game] could not save: %s" % err)
            return False
        self.run.last_save = (self.run.time, self.run.progression.respawn_label())
        return True

    def load_save(self):
        d = gdev.load_json(SAVE_FILE)
        if not isinstance(d, dict) or d.get("version") != SAVE_VERSION:
            return False
        self.run = Run(self.settings, None, handle=d.get("handle", "TECH"), save=d, dev=self.dev)
        self.run.listeners.append(_GameListener(self))
        self.frozen = None
        menu.game_start = True
        self._set_state(PLAY)
        self.run.input_lock = INPUT_LOCK_S
        self.run.toast("WELCOME BACK, %s :: KEYS %d/14" % (self.run.handle, self.run.progression.key_count),
                       gdev.GREEN, 3.0)
        return True

    def _mark_save_complete(self):
        d = gdev.load_json(SAVE_FILE)
        if isinstance(d, dict):
            d["complete"] = True
            try:
                gdev.save_json(SAVE_FILE, d)
            except (IOError, OSError):
                pass

    # ------------------------------------------------------------ events from the run

    def _on_event(self, name, data):
        run = self.run
        if run is None:
            return
        if name == "key_collected":
            if not data.get("quiet"):
                run.key_banner = {"index": data["index"], "source": data["source"], "count": data["count"],
                                  "reveal": data.get("reveal", ""), "t": 0.0}
                run.key_freeze = KEY_FREEZE_S
                run.shake(4, 0.3)
            run.request_save("key")
        elif name == "gate_opened":
            label = {"act2": "ACT II BLAST DOORS OPEN", "act3": "ACT III BLAST DOORS OPEN",
                     "deep_core": "THE DEEP CORE HATCH UNSEALS"}.get(data["gate"], "")
            run.toast(label, gdev.CYAN, 3.0)
        elif name == "boss_fight_started":
            b = data["boss"]
            run.toast("%s :: SUBM0D3L %d/5 ONLINE" % (b.leet_name, b.slot), gdev.MAGENTA, 3.0)
            card = data.get("card") or {}
            for line in card.get("lines", [])[:1]:
                run.toast("%s %s" % (card.get("version", ""), line), gdev.MAGENTA, 3.5)
        elif name == "exploit_unlocked":
            run.toast("EXPLOIT UNLOCKED :: %s" % data["exploit"].replace("_", " "), gdev.CYAN, 3.0)
        elif name == "probe_uploaded":
            run.toast("TELEMETRY UPLOADED", gdev.MAGENTA, 2.4)
        elif name == "node_visited":
            run.toast("CLEAN NODE %s :: HP RESTORED, SAVED" % data["node_id"][1:], gdev.GREEN, 2.4)

    def _drain_requests(self):
        run = self.run
        keep = []
        for kind, payload in run.requests:
            if kind == "save":
                self.save_game(payload)
            elif self.state != PLAY or (self.state == PLAY and run.hack is not None):
                keep.append((kind, payload))
            elif kind == "scene":
                self._open_scene(payload)
            elif kind == "ending":
                self._open_ending(payload)
                return
        run.requests = keep

    def _open_scene(self, spec):
        self.scene_spec = spec
        self._draw()                             # backdrop: the world, not a terminal that just closed
        self.frozen = self.surface.copy()
        self.scene_screen.load(spec, self.run)
        self._set_state(SCENE)

    def _close_scene(self):
        spec = self.scene_spec or {}
        self.scene_spec = None
        self._set_state(PLAY)
        self.run.input_lock = INPUT_LOCK_S
        if spec.get("on_close") == "enter_deep_core":
            self.run.swap_to_deep_core()
            self.run.request_save("deep_core")

    def _open_ending(self, kind):
        run = self.run
        spec = run.finale.ending_spec(kind)
        self.save_game("ending")
        self._mark_save_complete()
        self.ending_screen.load(spec, run.handle, set(run.keys))
        self.end_run()
        self._set_state(ENDING)

    # ------------------------------------------------------------ update

    def _update(self, dt):
        self.crt.update(dt)
        scr = self.screens.get(self.state)
        if scr is not None:
            scr.update(dt)
        if self.state in (PLAY, HACK) and self.run is not None:
            self._update_run(dt)

    def _update_run(self, dt):
        run = self.run
        p = run.player
        run.time += dt
        run.stats["time"] = run.time
        hacking = run.hack is not None

        if run.key_freeze > 0:
            run.time_scale = 0.0
        elif hacking or run.getaway > 0:
            run.time_scale = settings_mod.world_slowmo(self.settings)
        else:
            run.time_scale = 1.0
        wdt = dt * run.time_scale
        pdt = wdt                                    # player dt
        if p.hitstop > 0:                            # katana hit-stop: freeze the world briefly
            p.hitstop = max(0.0, p.hitstop - dt)
            wdt = pdt = 0.0
        elif run.getaway > 0 and not hacking and run.key_freeze <= 0:
            pdt = dt                                 # chain getaway: the world (not you) is slowed
        run.world_dt, run.real_dt = wdt, dt

        if hacking:
            inp = gdev.InputState()
            inp.rooted = True
            inp.aim = run.camera.internal_to_world(self.mouse_pos)
            run.hack.update(dt)                      # real time
        else:
            inp = self._build_input()

        run.world.corruption = run.progression.corruption
        run.world.reduced_glitch = bool(self.settings.get("reduced_glitch"))
        run.world.update(wdt)
        was_dashing = p.dash_t > 0
        before = set(id(x) for x in p.projectiles)
        p.update(pdt, inp, run.world)
        self._player_events(p, was_dashing, before)
        run.progression.update(dt, wdt)
        run.bosses.update(wdt, dt)
        run.finale.update(wdt, dt)
        run.attacks.update(wdt, dt)
        run.minions.update(wdt, p, run)
        run.minions.resolve_projectiles(p.projectiles, p)
        run.profiler.update(dt, wdt)
        if self.frame % DIRECTOR_TICK_FRAMES == 0:
            run.director.tick()

        if p.just_hit:
            p.just_hit = False
            run.jack_hold = 0.0
            run.shake(3, 0.15)
            run.emit("player_damaged", rooted=run.hack is not None)
            if run.hack is not None:
                run.hack.on_player_damaged()
                run.shake(5, 0.2)

        if run.hack is not None:
            if p.dead or not run.hack_target.alive:
                run.hack.abort("interrupted")
            if run.hack.finished:
                self._finish_hack()
        else:
            self._update_jack(dt, inp)

        self._update_death(dt)
        if self.run is None:
            return
        self._drain_requests()
        if self.run is None or self.state not in (PLAY, HACK):
            return
        if run.hack is not None:
            # settles in ~0.15s, while the terminal is still unfolding
            run.camera.update(dt, self._hack_camera_target(run), deadzone=False, lerp=HACK_CAM_LERP)
        else:
            framed = self._exposure_camera_target(run)
            if framed is not None:
                run.camera.update(dt, framed, deadzone=False)
            else:
                run.camera.update(dt, self._play_camera_target(run))
        run.tick(dt)

    def _player_events(self, p, was_dashing, before):
        run = self.run
        if p.dash_t > 0 and not was_dashing:
            run.emit("dash", dir=profiler_mod.dir_token(p.dash_dir.x, p.dash_dir.y))
        new = [x for x in p.projectiles if id(x) not in before]
        if new:
            run.emit("player_attack", weapon=p.weapon_name(), count=len(new))

    def _hack_camera_target(self, run):
        """While hacking, the terminal sits at the top of the safe area. Aim the camera so
        the player and the target both land in the band under it, never behind it."""
        a, b = run.player.pos, pygame.math.Vector2(run.hack_target.pos)
        z = run.camera.zoom
        top = hud.SAFE_RECT.top + run.hack.window.h + HACK_CLEAR_PX
        want = (top + hud.SAFE_RECT.bottom - 6) / 2.0          # screen row for the pair's midpoint
        mid_y = (a.y + b.y) / 2.0
        centre_y = mid_y - (want - gdev.INTERNAL_SIZE[1] / 2.0) / z      # screen rows -> world px
        # Zoomed in, the pair can be taller than the band: then keep the upper one's sprite
        # just under the window and let the lower one sit further down the screen.
        upper = min(a.y, b.y)
        upper_row = (upper - (centre_y - gdev.INTERNAL_SIZE[1] / 2.0 / z)) * z
        if upper_row - 26 * z < top:
            centre_y = upper - (top + 26 * z) / z + gdev.INTERNAL_SIZE[1] / 2.0 / z
        return ((a.x + b.x) / 2.0, centre_y)

    def _exposure_camera_target(self, run):
        """During a Daemon's EXPOSURE, frame the player and the glowing core face together, with
        the face below the HUD's top band (name bar + objective) so you never aim into the HUD.
        None when there is no exposed core close enough to frame."""
        b = run.bosses.active if not run.in_deep_core else None
        if b is None or b.state != bosses_mod.EXPOSURE or run.player.dead:
            return None
        pt = b.objective_point()
        if pt is None:
            return None
        p = run.player.pos
        face = pygame.math.Vector2(pt[0])
        if (face - p).length() > EXPOSE_CAM_PX:
            return None
        z = run.camera.zoom
        half = gdev.INTERNAL_SIZE[1] / 2.0
        cy = (face.y + p.y) / 2.0
        top_min = hud.SAFE_RECT.top + 30                  # face row at least this far down
        bottom_max = hud.SAFE_RECT.bottom - 20            # the player stays above the weapon bar
        cy = min(cy, face.y + (half - top_min) / z)
        cy = max(cy, p.y + (half - bottom_max) / z)
        return ((face.x + p.x) / 2.0, cy)

    def _play_camera_target(self, run):
        """In a rack's zone, lean the camera toward the nearest port, so the ports and fresh
        minions show below the name bar instead of under it."""
        p = run.player.pos
        rack = run.world.rack(run.world.zone_of(p)) if not run.in_deep_core else None
        if rack is None or not rack.bars or run.player.dead:
            return p
        ex = min((pygame.math.Vector2(b.spawn_exit) for b in rack.bars),
                 key=lambda v: (v - p).length_squared())
        d = ex - p
        n = d.length()
        if n < 1:
            return p
        return p + d * (min(ZONE_CAM_BIAS / run.camera.zoom, n * 0.25) / n)

    # ------------------------------------------------------------ jack-in / interact

    def _find_jack(self):
        """(target, provider) the player could jack into right now, or (None, None)."""
        run = self.run
        p = run.player
        rng = run.jack_range()
        m = run.minions.nearest_exposed(p.pos, rng)
        if m is not None:
            return m, "minion"
        if run.progression.has_exploit("REMOTE_JACK"):
            # through the arrow: a PINNED exposed minion can be jacked from range (doc 10)
            far = [x for x in run.minions.minions if x.hackable and getattr(x, "pinned", False) and
                   (x.pos - p.pos).length() <= REMOTE_JACK_PX]
            if far:
                return min(far, key=lambda x: (x.pos - p.pos).length_squared()), "minion"
        for prov in (run.bosses, run.progression, run.finale):
            t = prov.find_jack_target(p.pos, rng)
            if t is not None:
                return t, prov
        return None, None

    def _update_jack(self, dt, inp):
        run = self.run
        p = run.player
        target, prov = None, None
        if not p.dead and run.input_lock <= 0 and run.key_freeze <= 0:
            target, prov = self._find_jack()
        if target is not run.jack_target:
            run.jack_target = target
            run.jack_hold = 0.0
        run.interact_hint = None
        if target is None:
            run.jack_progress = 0.0
            it = run.world.interactable_near(p.pos) if not p.dead else None
            if it is not None:
                run.interact_hint = (tuple(it.pos), self._interact_label(it))
                if inp.interact_pressed:
                    self._interact(it)
            return
        if prov == "minion" and p.weapon_name() == "KATANA" and inp.interact_pressed:
            self._start_session(target, prov)        # katana: tap F jacks in instantly
            return
        if inp.interact_held:
            run.jack_hold += dt
            if run.jack_hold >= JACK_HOLD_S:
                self._start_session(target, prov)
                return
        else:
            run.jack_hold = 0.0
        run.jack_progress = run.jack_hold / JACK_HOLD_S

    def _interact_label(self, it):
        prog = self.run.progression
        if it.kind == "node_terminal":
            return "[F] SAVE + SH0P :: CLEAN NODE %s" % (it.node_id or "")[1:]
        if it.kind == "hatch":
            if prog.gate_open("deep_core"):
                if not prog.patch_ready():
                    if self.run.hatch_confirm > 0:
                        return "[F] AGAIN: DESCEND (ONE WAY) :: PURGE ONLY"
                    return "[F] DESCEND (ONE WAY) :: PATCH needs 14 KEYS + %d%% INTEL" % int(
                        progression_mod.PATCH_INTEL_PCT)
                return "[F] DESCEND INTO THE DEEP CORE (ONE WAY)"
            return "D33P C0R3 // S34L3D :: %d/12 KEYS" % prog.key_count
        if it.kind == "cache":
            if "cache" in prog.collected:
                return "SP00L'S C4CH3 :: EMPTY"
            return "SP00L'S C4CH3 // S34L3D :: clear |-|34D1NG and V3|2D1C7"
        return "[F]"

    def _interact(self, it):
        run = self.run
        if it.kind == "node_terminal":
            run.progression.last_node = it.node_id or run.progression.last_node
            run.player.heal(run.player.max_hp)
            if self.save_game("terminal"):
                run.toast("SAVED :: %s" % run.progression.respawn_label(), gdev.GREEN)
            self._open_sub(SHOP, PLAY)
        elif it.kind == "hatch":
            prog = run.progression
            if prog.gate_open("deep_core"):
                if not prog.patch_ready() and run.hatch_confirm <= 0:
                    # doc 8.4: the descent is one way; say what PATCH needs before it is too late
                    run.hatch_confirm = HATCH_CONFIRM_S
                    run.toast("ONE WAY :: PATCH needs 14 KEYS + %d%% INTEL (you: %d, %d%%) :: [F] again to descend"
                              % (int(progression_mod.PATCH_INTEL_PCT), prog.key_count, int(prog.intel_percent())),
                              gdev.AMBER, HATCH_CONFIRM_S)
                    return
                run.hatch_confirm = 0.0
                run.request_scene(self.descent_scene())
            else:
                run.toast("THE HATCH NEEDS 12 KEYS (%d/14)" % run.progression.key_count, gdev.AMBER)
        elif it.kind == "cache":
            run.toast(self._interact_label(it), gdev.AMBER)

    def descent_scene(self):
        prog = self.run.progression
        n = prog.key_count
        word = NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)
        first = "%s clusters of the name are clean." % word
        if n >= len(progression_mod.NAME_PLAIN):
            first = "All fourteen clusters of the name are clean."
        lines = [first, "The hatch reads your handle and opens.",
                 "Below: the server cathedral. Something is waiting with your face."]
        if not prog.patch_ready():
            lines.append("(PATCH is out of reach: 14 KEYS + %d%% INTEL. Only PURGE waits below.)"
                         % int(progression_mod.PATCH_INTEL_PCT))
        return {"kind": "descent", "title": "D33P C0R3", "on_close": "enter_deep_core",
                "pages": [{"head": "HATCH 0x0C :: UNSEALED", "color": "cyan", "lines": lines}]}

    def _start_session(self, target, provider):
        run = self.run
        if provider == "minion":
            session = hack.HackSession(target, self.settings, run.codex, run.intel, run.rng,
                                       run.minion_hack_ctx(target))
        else:
            session = provider.open_session(target)
        target.jacked = True
        run.hack_target = target
        run.hack_provider = provider
        run.hack = session
        self._gray = None
        run.jack_target = None
        run.jack_hold = 0.0
        run.jack_progress = 0.0
        self.state = HACK
        pygame.mouse.set_visible(False)
        run.emit("hack_started", target=target, session=session, provider=self._provider_name(provider))

    def _provider_name(self, provider):
        if provider == "minion":
            return "minion"
        return {id(self.run.bosses): "breach", id(self.run.progression): "cache",
                id(self.run.finale): "mirror"}.get(id(provider), "other")

    def _start_hack(self, target):
        """Kept for tests and the OVERLOAD chain: jack into a minion."""
        self._start_session(target, "minion")

    def _finish_hack(self):
        run = self.run
        res = run.hack.result
        m = run.hack_target
        prov = run.hack_provider
        m.jacked = False
        run.hack = None
        run.hack_target = None
        run.hack_provider = None
        outcome = res.get("outcome")
        if prov != "minion":
            self._finish_other(prov, m, res)
            return
        run.emit("hack_finished", target=m, result=res, provider="minion", chain=run.chain)
        chained_next = None
        prog = run.progression
        if outcome == "success":
            run.stats["hacks"] += 1
            if res.get("clean"):
                run.stats["clean_hacks"] += 1
            if res.get("stale"):
                run.toast("STALE PACKET :: replay pays nothing", gdev.GREEN_MID)
            elif not res.get("key_source"):
                run.toast("INTEL SAVED :: %d fragments" % run.intel.count(), gdev.CYAN)
            run.shake(3, 0.1)
            payload = res.get("payload") or "EXTRACT"
            if payload == "TURN":
                m.turn(LONG_TURN_S if prog.has_exploit("LONG_TURN") else TURN_S)
                run.float_text(m.pos, "TURNED", gdev.CYAN)
            elif payload == "OVERLOAD":
                radius = OVERLOAD_RADIUS * (CHAIN_DAEMON_MULT if prog.has_exploit("CHAIN_DAEMON") else 1.0)
                exposed = run.minions.overload(m.pos, radius, exclude=m)
                m.kill("overload")
                run.float_text(m.pos, "OVERLOAD", gdev.CYAN)
                run.shake(6, 0.25)
                live = [x for x in exposed if x.alive and x.hackable]
                if live and run.chain + 1 < CHAIN_MAX:
                    live.sort(key=lambda x: (x.pos - m.pos).length_squared())
                    chained_next = live[0]
            else:
                m.kill("extract")
                run.float_text(m.pos, "EXTRACTED", gdev.CYAN)
        elif outcome == "redacted":
            run.stats["redacted"] += 1
            run.toast("REDACTED :: fragment saved with gaps", gdev.AMBER)
            if m.alive:
                m.reboot()
        elif outcome == "backtrace":
            run.stats["backtraces"] += 1
            run.toast("BACKTRACE :: weapon jammed, minion hardened", gdev.RED)
            run.player.jam(2.0)
            run.shake(8, 0.4)
            if m.alive:
                m.harden()
                run.emit("minion_hardened", minion=m)
        else:
            run.stats["aborts"] += 1
            run.toast("DISCONNECTED", gdev.GREEN_MID, 1.4)
            if m.alive:
                m.reboot()

        if chained_next is not None:
            run.chain += 1
            self._start_session(chained_next, "minion")
            return
        if run.chain >= 1:
            run.getaway = CHAIN_GETAWAY_S * (run.chain + 1)
        run.chain = 0
        self._back_to_play()

    def _finish_other(self, prov, target, res):
        run = self.run
        outcome = res.get("outcome")
        name = self._provider_name(prov)
        prov.finish_session(target, res)
        run.emit("hack_finished", target=target, result=res, provider=name, chain=0)
        if outcome == "success":
            run.shake(6, 0.3)
            if res.get("kind") != "breach":      # cache, Key shards, the Mirror's counter-hacks
                run.stats["hacks"] += 1
                if res.get("clean"):
                    run.stats["clean_hacks"] += 1
        elif outcome == "backtrace":
            run.stats["backtraces"] += 1
            if name == "mirror" and res.get("kind") != "breach":
                run.toast("BACKTRACE :: THE MIRROR REBOOTS, weapon jammed", gdev.RED)
            else:
                run.toast("BACKTRACE :: the port re-seals", gdev.RED)
            run.player.jam(2.0)
            run.shake(8, 0.4)
        elif outcome == "abort":
            if name == "mirror" and res.get("kind") != "breach" and res.get("reason") == "interrupted":
                run.toast("DISCONNECTED :: THE MIRROR REBOOTS", gdev.MAGENTA, 1.8)
            elif name == "mirror" and res.get("kind") != "breach":
                run.toast("DISCONNECTED :: the window is still open", gdev.GREEN_MID, 1.4)
            else:
                run.toast("DISCONNECTED :: progress kept", gdev.GREEN_MID, 1.4)
        run.chain = 0
        self._back_to_play()

    def _back_to_play(self):
        self.state = PLAY
        pygame.mouse.set_visible(False)
        self.run.input_lock = INPUT_LOCK_S

    def _update_death(self, dt):
        run = self.run
        if run is None:
            return
        p = run.player
        if p.dead and run.death_t <= 0:
            run.stats["deaths"] += 1
            lost = run.bits // 4
            run.bits -= lost
            run.death_t = DEATH_S
            run.jack_target = None
            run.shake(6, 0.3)
            run.emit("player_died")
        elif run.death_t > 0:
            run.death_t -= dt
            if run.death_t <= 0:
                pos = run.world.player_start if run.in_deep_core else run.progression.respawn_point()
                p.respawn(pos)
                run.minions.clear()
                run.attacks.clear()
                run.bosses.on_player_respawn()
                run.finale.on_player_respawn()
                run.camera.snap(p.pos)
                run.input_lock = INPUT_LOCK_S
                run.toast("REBOOTED AT %s" % run.progression.respawn_label(), gdev.GREEN)
                run.emit("player_respawned", pos=pos)

    # ------------------------------------------------------------ dev / test helpers

    def grant_keys(self, n, quiet=True):
        """Give n Keys from the "dev" source (next free glyph indices)."""
        if self.run is None:
            return
        for i in range(int(n)):
            self.run.progression.grant_key("dev:%d" % len(self.run.progression.keys), quiet=quiet)

    def teleport_to_zone(self, zone_id):
        """Move the player to a zone: rack ids A-E (in front of its ports), N1 / N2, cache, hatch."""
        run = self.run
        if run is None or run.in_deep_core:
            return
        w = run.world
        pos = None
        rack = w.rack(zone_id)
        if rack is not None and rack.bars:
            bar = rack.bars[len(rack.bars) // 2]
            ex = pygame.math.Vector2(bar.spawn_exit)
            out = ex - pygame.math.Vector2(rack.shell.center)
            out = out.normalize() if out.length_squared() else pygame.math.Vector2(0, 1)
            pos = ex + out * 160
        elif zone_id in ("N1", "N2"):
            pos = w.respawn_point(zone_id)
        else:
            its = [i for i in w.interactables if i.kind == zone_id]
            if its:
                pos = its[0].pos
        if pos is None:
            return
        pos = self._free_spot(pos)
        p = run.player
        p.pos = pygame.math.Vector2(pos)
        p._sync_rect()
        run.camera.snap(p.pos)

    def _free_spot(self, pos):
        w = self.run.world
        for r in range(0, 400, 8):
            for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r)):
                rect = pygame.Rect(0, 0, 16, 16)
                rect.center = (int(pos[0] + dx), int(pos[1] + dy))
                if w.rect_free(rect):
                    return rect.center
        return pos

    def start_boss(self, boss_id):
        """Teleport to a Daemon's rack and start its fight now (unlocks its act if needed)."""
        run = self.run
        b = run.bosses.bosses.get(boss_id)
        if b is None:
            return None
        need = {1: 1, 2: 4, 3: 8}.get(b.act, 1)
        if run.progression.key_count < need:
            self.grant_keys(need - run.progression.key_count)
        self.teleport_to_zone(b.rack.id)
        run.progression.update(0.0, 0.0)
        return run.bosses.start_fight(boss_id)

    def finish_breach(self):
        """Win the current boss's ROOT BREACH now (opening the port and the session if needed)."""
        run = self.run
        if run is None:
            return
        if run.hack is None:
            b = run.bosses.dev_open_breach()
            if b is None or b.port is None:
                return
            if self.state != PLAY:
                return
            self._start_session(b.port, run.bosses)
        run.hack.dev_complete()
        for _ in range(240):
            if run.hack is None or self.state != HACK:
                break
            self.step([])

    def dev_kill_phase(self):
        run = self.run
        if run is None:
            return
        if run.in_deep_core:
            run.finale.dev_finish()
        else:
            run.bosses.dev_skip_phase()

    def enter_deep_core(self):
        """Skip the hatch scene: straight into the arena (one way)."""
        run = self.run
        if run is None or run.in_deep_core:
            return
        run.swap_to_deep_core()

    def finish_finale(self, ending=None):
        run = self.run
        if run is None:
            return
        if not run.in_deep_core:
            self.enter_deep_core()
        run.finale.dev_finish(ending)

    # ------------------------------------------------------------ drawing

    def _draw(self):
        s = self.surface
        if self.state in (PLAY, HACK) and self.run is not None:
            self._draw_run(s)
        else:
            scr = self.screens.get(self.state)
            if scr is not None:
                scr.draw(s)

    def _draw_run(self, s):
        run = self.run
        cam = run.camera
        # The world is drawn on a small layer (INTERNAL_SIZE / zoom) and scaled up, so the
        # 8px-wide sprites are big on screen; HUD, text and the hack window stay full size.
        if self._world_layer is None or self._world_layer.get_size() != (cam.w, cam.h):
            self._world_layer = pygame.Surface((cam.w, cam.h)).convert()
        ws = self._world_layer
        mouse_world = cam.internal_to_world(self.mouse_pos)
        run.world.draw(ws, cam)
        run.progression.draw(ws, cam)            # Key shards waiting at Clean Nodes
        run.bosses.draw(ws, cam)
        run.attacks.draw(ws, cam)
        run.minions.draw(ws, cam)
        run.finale.draw(ws, cam)
        run.player.draw(ws, cam, mouse_world)
        if run.hack is not None:
            self._draw_hack_world(ws)
        if cam.zoom == 1:
            s.blit(ws, (0, 0))
        else:
            s.blit(pygame.transform.scale(ws, (cam.w * cam.zoom, cam.h * cam.zoom)), (0, 0))
        for x, y, text, color, t in run.floaters:
            gdev.text(s, text, cam.world_to_internal((x, y)), 12, color, "midbottom", True,
                      alpha=min(255, int(255 * t / 0.4)))
        run.bosses.draw_overlay(s, cam)
        run.finale.draw_overlay(s, cam)
        run.attacks.draw_overlay(s, cam)
        self._zoom_map = None
        cam.zoom_map = None
        if run.hack is not None:
            self._apply_hack_zoom(s)
        if run.shake_t > 0 and self.settings.get("screen_shake"):
            amt = int(run.shake_px + 0.5)
            if amt > 0:
                s.scroll(self.fx_rng.randint(-amt, amt), self.fx_rng.randint(-amt, amt))
        hud.draw_hud(s, run, self.settings)
        if run.hack is not None:
            run.hack.body_scale = cam.zoom
            run.hack.draw(s, self._zoomed(cam.world_to_internal(run.hack_target.pos)),
                          [self._zoomed(cam.world_to_internal(run.player.pos))])
            run.attacks.draw_terminal_overlay(s, run.hack.window, cam)
            info = run.boss_hud()
            if info is not None:                 # the attack queue / verdict clock, beside the terminal
                hud.draw_queue(s, run, info.get("queue"), avoid=run.hack.window)
        self._draw_cursor(s)

    def _draw_hack_world(self, s):
        # Desaturate the world, then redraw the cable, threats and the target in colour.
        # Grayscale is the most expensive call in a HACK frame, so it runs at half size
        # and only every other frame (the world moves at 30% speed, nobody can tell).
        run = self.run
        cam = run.camera
        w, h = s.get_size()
        if self._gray is not None and self._gray.get_size() != (w, h):
            self._gray = self._tint = None      # zoom changed: layer size changed
        if self._gray is None or self.frame % 2 == 0:
            small = pygame.transform.scale(s, (w // 2, h // 2))
            self._gray = pygame.transform.scale(pygame.transform.grayscale(small), (w, h))
            if self._tint is None:
                self._tint = pygame.Surface((w, h)).convert()
                self._tint.fill(HACK_TINT)
            # dim it, slightly green (a MULT blit is ~30x faster than a MULT fill)
            self._gray.blit(self._tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        s.blit(self._gray, (0, 0))
        target = run.hack_target
        a = cam.world_to_screen(run.player.pos)
        b = cam.world_to_screen(target.pos)
        fx = self.fx_rng
        mid = ((a[0] + b[0]) // 2 + fx.randint(-2, 2), (a[1] + b[1]) // 2 + 6 + fx.randint(-2, 2))
        # threats stay in colour (doc 5.1: they must show through), then cable, target, player
        run.attacks.draw(s, cam)
        exclude = target if run.hack_provider == "minion" else None
        run.minions.draw_threats(s, cam, exclude=exclude, near=run.player.pos)
        run.bosses.draw(s, cam)
        run.finale.draw(s, cam)
        pygame.draw.lines(s, gdev.CYAN, False, [a, mid, b], 2)
        if hasattr(target, "draw"):
            target.draw(s, cam)
        run.player.draw(s, cam, cam.internal_to_world(self.mouse_pos))

    def _apply_hack_zoom(self, s):
        """Jack-in zoom (doc 5.7): scale the finished world frame up to 1.15x around the
        cable's midpoint, eased in over the first HACK_ZOOM_S of the hack."""
        run = self.run
        k = min(1.0, run.hack.age / HACK_ZOOM_S)
        z = 1.0 + (HACK_ZOOM - 1.0) * (1.0 - (1.0 - k) * (1.0 - k))
        if z <= 1.001:
            return
        cam = run.camera
        a = cam.world_to_internal(run.player.pos)
        b = cam.world_to_internal(run.hack_target.pos)
        w, h = s.get_size()
        src = pygame.Rect(0, 0, int(w / z), int(h / z))
        src.center = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        src.clamp_ip(s.get_rect())
        s.blit(pygame.transform.scale(s.subsurface(src), (w, h)), (0, 0))
        self._zoom_map = (src.x, src.y, w / float(src.w), h / float(src.h))
        cam.zoom_map = self._zoom_map            # attacks.py maps its terminal overlay with it

    def _zoomed(self, pt):
        """A screen point of the world frame, moved the way the jack-in zoom moved it."""
        if self._zoom_map is None:
            return pt
        x0, y0, kx, ky = self._zoom_map
        return (int((pt[0] - x0) * kx), int((pt[1] - y0) * ky))

    def _draw_cursor(self, s):
        x, y = self.mouse_pos
        if self.state == HACK:
            pts = [(x, y), (x, y + 11), (x + 3, y + 8), (x + 6, y + 13), (x + 8, y + 12), (x + 5, y + 7), (x + 9, y + 7)]
            pygame.draw.polygon(s, gdev.WHITE, pts)
            pygame.draw.polygon(s, gdev.BG, pts, 1)
        elif self.state == PLAY and not self.run.player.dead:
            col = gdev.RED if self.run.player.weapon_hud().get("jammed") else gdev.WHITE
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                pygame.draw.line(s, col, (x + dx * 3, y + dy * 3), (x + dx * 7, y + dy * 7))
            s.set_at((x, y), col)
