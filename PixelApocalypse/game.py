"""Game: state machine, fixed 60 Hz timestep, input routing, the Run object.

Frame order (doc 4), one step = 1/60 s:
    events -> (state routing) -> hack session (real dt) -> player (world dt)
    -> minions (world dt) -> projectiles -> jack-in / death / objective
    -> camera -> render world, entities, HUD, hack terminal -> scale to window

Slow motion lives here: world_dt = dt * 0.30 while a hack is open (and during
an OVERLOAD-chain getaway, where the player keeps real dt: "the world, not you").
The hack session itself always gets real dt.

Test hook (used by tools/smoke_test.py):
    g = Game(headless_ok=True)
    g.step(events, dt=1/60)      process a list of pygame events, advance one frame, draw
    g.state, g.run, g.surface    current state, current Run (or None), internal frame
    g.save_frame(path)           write the current frame as a PNG
Mouse events given to step() use WINDOW pixels, like real ones (see gdev.click()).
"""
import os
import random

import pygame

import gdev
import hack
import hud
import menu
import minions as minions_mod
import player as player_mod
import settings as settings_mod
import world as world_mod

STEP = 1.0 / 60.0

# Game states
MENU = "MENU"
PLAY = "PLAY"
HACK = "HACK"
PAUSE = "PAUSE"
VICTORY = "VICTORY"
SETTINGS = "SETTINGS"
HOWTO = "HOWTO"
INTEL = "INTEL"
SUB_SCREENS = (SETTINGS, HOWTO, INTEL)     # these go "back" to Game.return_state

WORLD_SLOWMO = 0.30
JACK_HOLD_S = 0.4
JACK_RANGE_PX = 64
INPUT_LOCK_S = 0.2
TURN_S = 15.0
OVERLOAD_RADIUS = 160
CHAIN_MAX = 4
CHAIN_GETAWAY_S = 1.0
DEATH_S = 2.2
OBJECTIVE_HACKS = 5
HACK_TINT = (150, 185, 160)  # multiply colour for the greyscale world while hacking
HACK_CAM_LERP = 0.3         # per-frame camera lerp while hacking (PLAY uses 0.15)
HACK_CLEAR_PX = 34          # free screen rows between the hack window and the sprites under it
HACK_ZOOM = 1.15            # jack-in zoom (doc 5.7), eased in over HACK_ZOOM_S
HACK_ZOOM_S = 0.15
ZONE_CAM_BIAS = 48          # in Rack A's zone the camera leans this far toward the nearest port
FIRST_KEY_INDEX = 0          # name-bar cluster the prototype's Key de-leets (|_| -> U)


class Run(object):
    """Everything about one play session. Other modules may call the methods marked (API).

    world, camera, player, minions      the live objects
    hack            HackSession or None (open while state is HACK)
    hack_target     the Minion being hacked, or None
    codex, intel    hack.Codex / hack.IntelLog (intel survives death)
    rng             random.Random for this run
    bits            current Bits
    keys            set of name-bar cluster indices recovered (0..13)
    key_anim        {cluster index: seconds since its Key arrived} (HUD flourish)
    hacked_uids     uids of minions hacked successfully (objective counts these)
    stats           {"time","kills","hacks","clean_hacks","deaths","redacted","backtraces","aborts","bits_earned"}
    time            real seconds in PLAY/HACK
    time_scale      world speed this frame (1.0, 0.30 slow-mo, 0 during the Key flourish)
    toasts          [[text, color, seconds_left]]   floaters [[x, y, text, color, seconds_left]]
    jack_target, jack_progress (0..1)   for the HUD prompt
    """

    def __init__(self, settings, seed=None):
        self.settings = settings
        self.rng = random.Random(seed)
        self.world = world_mod.World.load()
        self.camera = world_mod.Camera(self.world.art_bounds, settings_mod.zoom(settings))
        self.player = player_mod.Player(self.world.player_start)
        self.camera.snap(self.player.pos)
        self.minions = minions_mod.MinionManager(self.world)
        self.codex = hack.Codex()
        self.intel = hack.IntelLog()
        self.hack = None
        self.hack_target = None
        self.chain = 0
        self.bits = 0
        self.keys = set()
        self.key_anim = {}
        self.hacked_uids = set()
        self.stats = {"time": 0.0, "kills": 0, "hacks": 0, "clean_hacks": 0, "deaths": 0,
                      "redacted": 0, "backtraces": 0, "aborts": 0, "bits_earned": 0}
        self.time = 0.0
        self.time_scale = 1.0
        self.getaway = 0.0
        self.input_lock = 0.0
        self.toasts = []
        self.floaters = []
        self.shake_px = 0.0
        self.shake_t = 0.0
        self.jack_target = None
        self.jack_hold = 0.0
        self.jack_progress = 0.0
        self.death_t = 0.0
        self.victory_t = None         # seconds into the Key flourish, None until the objective is met
        self.complete = False

    # ------------------------------------------------------------ API for other modules

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
        if self.rng.random() < 0.2 and self.player.hp < self.player.max_hp:
            self.player.heal(1)
            self.float_text((minion.pos[0], minion.pos[1] - 12), "+1 HP", gdev.GREEN)

    def objective_text(self):
        n = len(self.hacked_uids)
        if self.complete:
            return "KEY RECOVERED :: first worm origin: INTERNAL."
        rack = self.world.rack("A")
        if rack is not None and not rack.in_zone(self.player.pos):
            return "OBJECTIVE  reach RACK A's zone   ::   HACKS %d/%d" % (n, OBJECTIVE_HACKS)
        if not any(m.alive and m.team == "enemy" for m in self.minions.minions):
            return "OBJECTIVE  head for RACK A's ports, B1Ts come out there   ::   HACKS %d/%d" % (
                n, OBJECTIVE_HACKS)
        return "OBJECTIVE  strip a firewall, hold [F] on the EXPOSED minion   ::   HACKS %d/%d" % (
            n, OBJECTIVE_HACKS)

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
        for k in self.key_anim:
            self.key_anim[k] += dt


class Game(object):
    def __init__(self, headless_ok=False):
        if headless_ok:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        self.settings = settings_mod.load()
        gdev.init(60, "Pixel Apocalypse", self.settings["fullscreen"])
        pygame.key.set_repeat()
        self.surface = pygame.Surface(gdev.INTERNAL_SIZE).convert()
        self.crt = menu.CRT()
        self.main_menu = menu.MainMenu(self.crt)
        self.pause_menu = menu.PauseMenu(self.crt)
        self.settings_menu = menu.SettingsMenu(self.crt, self.settings, self._setting_changed)
        self.howto = menu.HowToScreen(self.crt)
        self.intel_screen = menu.IntelLogScreen(self.crt)
        self.victory = menu.VictoryScreen(self.crt)
        self.screens = {MENU: self.main_menu, PAUSE: self.pause_menu, SETTINGS: self.settings_menu,
                        HOWTO: self.howto, INTEL: self.intel_screen, VICTORY: self.victory}
        self.state = MENU
        self.return_state = MENU
        self.frozen = None
        self.run = None
        self.last_run = None
        self.running = True
        self.frame = 0
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

    def main_loop(self):
        """Real-time loop: fixed 60 Hz steps, one draw + present per displayed frame."""
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

    def _build_input(self):
        inp = gdev.InputState()
        k = self.keys_down
        inp.aim_screen = self.mouse_pos
        inp.aim = self.run.camera.internal_to_world(self.mouse_pos)
        if self.run.input_lock > 0 or self.run.player.dead or self.run.victory_t is not None:
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
        if action == "play" or action == "play_again":
            self.new_run()
        elif action in ("howto", "settings", "intel"):
            target = {"howto": HOWTO, "settings": SETTINGS, "intel": INTEL}[action]
            self._open_sub(target, self.state)
        elif action == "quit":
            self.running = False
        elif action == "resume":
            self._set_state(PLAY)
            self.run.input_lock = INPUT_LOCK_S
        elif action == "quit_to_menu" or action == "main_menu":
            self.end_run()
            self._set_state(MENU)
        elif action == "back":
            self._set_state(self.return_state)
            if self.return_state == PLAY:
                self.run.input_lock = INPUT_LOCK_S

    def _set_state(self, state):
        self.state = state
        scr = self.screens.get(state)
        if scr is not None:
            scr.frozen = self.frozen if state in (PAUSE,) + SUB_SCREENS else None
            if state == INTEL:
                r = self.run or self.last_run
                scr.intel = r.intel if r else None
                scr.codex = r.codex if r else None
            scr.open()
        pygame.mouse.set_visible(state not in (PLAY, HACK))   # the game draws its own cursor

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
        if self.state == PLAY:
            self._draw()                         # the pause backdrop shows the world, not the terminal
            self._open_pause()

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

    def new_run(self, seed=None):
        self.run = Run(self.settings, seed)
        self.frozen = None
        menu.game_start = True
        self._set_state(PLAY)
        self.run.input_lock = INPUT_LOCK_S
        self.run.toast("JACKED INTO DEEPSTACK :: find RACK A", gdev.GREEN, 3.0)

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

        if run.victory_t is not None:
            run.time_scale = 0.0
        elif hacking or run.getaway > 0:
            run.time_scale = WORLD_SLOWMO
        else:
            run.time_scale = 1.0
        wdt = dt * run.time_scale
        pdt = wdt                                    # player dt
        if p.hitstop > 0:                            # katana hit-stop: freeze the world briefly
            p.hitstop = max(0.0, p.hitstop - dt)
            wdt = pdt = 0.0
        elif run.getaway > 0 and not hacking and run.victory_t is None:
            pdt = dt                                 # chain getaway: the world (not you) is slowed

        if hacking:
            inp = gdev.InputState()
            inp.rooted = True
            inp.aim = run.camera.internal_to_world(self.mouse_pos)
            run.hack.update(dt)                      # real time
        else:
            inp = self._build_input()

        run.world.update(wdt)
        p.update(pdt, inp, run.world)
        run.minions.update(wdt, p, run)
        run.minions.resolve_projectiles(p.projectiles, p)

        if p.just_hit:
            p.just_hit = False
            run.jack_hold = 0.0
            run.shake(3, 0.15)
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
        self._update_victory(dt)
        if run.hack is not None:
            # settles in ~0.15s, while the terminal is still unfolding
            run.camera.update(dt, self._hack_camera_target(run), deadzone=False, lerp=HACK_CAM_LERP)
        else:
            run.camera.update(dt, self._play_camera_target(run))
        run.tick(dt)

    def _hack_camera_target(self, run):
        """While hacking, the terminal sits at the top of the safe area. Aim the camera so
        the player and the target both land in the band under it, never behind it."""
        a, b = run.player.pos, run.hack_target.pos
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

    def _play_camera_target(self, run):
        """In Rack A's zone, lean the camera toward the nearest port, so the ports and fresh
        B1Ts show below the name bar instead of under it.
        (run is passed in: the VICTORY switch earlier this frame may have ended self.run.)"""
        p = run.player.pos
        rack = run.world.rack("A")
        if rack is None or not rack.bars or run.player.dead or not rack.in_zone(p):
            return p
        ex = min((pygame.math.Vector2(b.spawn_exit) for b in rack.bars),
                 key=lambda v: (v - p).length_squared())
        d = ex - p
        n = d.length()
        if n < 1:
            return p
        return p + d * (min(ZONE_CAM_BIAS / run.camera.zoom, n * 0.25) / n)

    def _update_jack(self, dt, inp):
        run = self.run
        p = run.player
        target = None
        if not p.dead and run.input_lock <= 0 and run.victory_t is None:
            target = run.minions.nearest_exposed(p.pos, JACK_RANGE_PX)
        if target is not run.jack_target:
            run.jack_target = target
            run.jack_hold = 0.0
        if target is None:
            run.jack_progress = 0.0
            return
        if p.weapon_name() == "KATANA" and inp.interact_pressed:
            self._start_hack(target)                 # katana: tap F jacks in instantly
            return
        if inp.interact_held:
            run.jack_hold += dt
            if run.jack_hold >= JACK_HOLD_S:
                self._start_hack(target)
                return
        else:
            run.jack_hold = 0.0
        run.jack_progress = run.jack_hold / JACK_HOLD_S

    def _start_hack(self, target):
        run = self.run
        target.jacked = True
        run.hack_target = target
        run.hack = hack.HackSession(target, self.settings, run.codex, run.intel, run.rng)
        self._gray = None
        run.jack_target = None
        run.jack_hold = 0.0
        run.jack_progress = 0.0
        self.state = HACK
        pygame.mouse.set_visible(False)

    def _finish_hack(self):
        run = self.run
        res = run.hack.result
        m = run.hack_target
        m.jacked = False
        run.hack = None
        run.hack_target = None
        outcome = res.get("outcome")
        chained_next = None
        if outcome == "success":
            run.stats["hacks"] += 1
            run.hacked_uids.add(m.uid)
            if res.get("clean"):
                run.stats["clean_hacks"] += 1
            # the terminal already stamped ACCESS GRANTED; the toast says what was gained
            run.toast("INTEL SAVED :: %d/%d hacks" % (len(run.hacked_uids), OBJECTIVE_HACKS), gdev.CYAN)
            run.shake(3, 0.1)
            payload = res.get("payload") or "EXTRACT"
            if payload == "TURN":
                m.turn(TURN_S)
                run.float_text(m.pos, "TURNED", gdev.CYAN)
            elif payload == "OVERLOAD":
                exposed = run.minions.overload(m.pos, OVERLOAD_RADIUS, exclude=m)
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
        else:
            run.stats["aborts"] += 1
            run.toast("DISCONNECTED", gdev.GREEN_MID, 1.4)
            if m.alive:
                m.reboot()

        if chained_next is not None:
            run.chain += 1
            self._start_hack(chained_next)
            return
        if run.chain >= 1:
            run.getaway = CHAIN_GETAWAY_S * (run.chain + 1)
        run.chain = 0
        self.state = PLAY
        pygame.mouse.set_visible(False)
        run.input_lock = INPUT_LOCK_S
        if len(run.hacked_uids) >= OBJECTIVE_HACKS and not run.complete:
            self._recover_key(FIRST_KEY_INDEX)

    def _recover_key(self, index):
        run = self.run
        run.complete = True
        run.keys.add(index)
        run.key_anim[index] = 0.0
        run.victory_t = 0.0
        run.shake(4, 0.3)

    def _update_victory(self, dt):
        run = self.run
        if run is None or run.victory_t is None:
            return
        run.victory_t += dt
        if run.victory_t >= hud.KEY_ANIM_S + 1.6:
            self.victory.stats = dict(run.stats, intel=run.intel.count(), bits=run.bits)
            self.victory.keys = set(run.keys)
            self.victory.selected = 0
            self.end_run()
            self._set_state(VICTORY)

    def _update_death(self, dt):
        run = self.run
        if run is None:
            return
        p = run.player
        if p.dead and run.death_t <= 0:
            run.stats["deaths"] += 1
            lost = run.bits // 2
            run.bits -= lost
            run.death_t = DEATH_S
            run.jack_target = None
            run.shake(6, 0.3)
        elif run.death_t > 0:
            run.death_t -= dt
            if run.death_t <= 0:
                pos = run.world.respawn_point()
                p.respawn(pos)
                run.minions.clear()
                run.camera.snap(p.pos)
                run.input_lock = INPUT_LOCK_S
                run.toast("REBOOTED AT CLEAN NODE 1", gdev.GREEN)

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
        run.minions.draw(ws, cam)
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
        self._zoom_map = None
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
        if run.victory_t is not None:
            self._draw_key_banner(s)
        self._draw_cursor(s)

    def _draw_hack_world(self, s):
        # Desaturate the world, then redraw the cable and the target in colour.
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
        a = cam.world_to_screen(run.player.pos)
        b = cam.world_to_screen(run.hack_target.pos)
        fx = self.fx_rng
        mid = ((a[0] + b[0]) // 2 + fx.randint(-2, 2), (a[1] + b[1]) // 2 + 6 + fx.randint(-2, 2))
        # threats stay in colour (doc 5.1: they must show through), then cable, target, player
        run.minions.draw_threats(s, cam, exclude=run.hack_target, near=run.player.pos)
        pygame.draw.lines(s, gdev.CYAN, False, [a, mid, b], 2)
        run.hack_target.draw(s, cam)
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

    def _zoomed(self, pt):
        """A screen point of the world frame, moved the way the jack-in zoom moved it."""
        if self._zoom_map is None:
            return pt
        x0, y0, kx, ky = self._zoom_map
        return (int((pt[0] - x0) * kx), int((pt[1] - y0) * ky))

    def _draw_key_banner(self, s):
        run = self.run
        k = min(1.0, run.victory_t / 0.4)
        w = s.get_width()
        h = int(64 * k)
        band = pygame.Rect(0, 200 - h // 2, w, h)
        gdev.panel(s, band, fill=(4, 30, 36), alpha=220, border=gdev.CYAN)
        if k >= 1.0:
            gdev.text(s, "KEY 01 RECOVERED", (w // 2, band.y + 10), 24, gdev.CYAN, "midtop", True)
            gdev.text(s, "first worm origin: INTERNAL.", (w // 2, band.y + 40), 13, gdev.WHITE, "midtop")

    def _draw_cursor(self, s):
        x, y = self.mouse_pos
        if self.state == HACK:
            pts = [(x, y), (x, y + 11), (x + 3, y + 8), (x + 6, y + 13), (x + 8, y + 12), (x + 5, y + 7), (x + 9, y + 7)]
            pygame.draw.polygon(s, gdev.WHITE, pts)
            pygame.draw.polygon(s, gdev.BG, pts, 1)
        elif self.state == PLAY and not self.run.player.dead:
            col =gdev.RED if self.run.player.weapon_hud().get("jammed") else gdev.WHITE
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                pygame.draw.line(s, col, (x + dx * 3, y + dy * 3), (x + dx * 7, y + dy * 7))
            s.set_at((x, y), col)
