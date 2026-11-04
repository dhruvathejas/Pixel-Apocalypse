"""The Deep Core and the Mirror (doc 8.4): the arena fight, the PURGE / PATCH endings and the
credits data.

The Mirror is the five sub-models (MEMORY, AIM, MOVEMENT, DECISION, VOICE) stitched back together.
It looks like you (your sprites, palette-swapped from cyan to green), fights with your top weapon,
aims where your dodge predictor says you will be, and its counter-hacks speak your dialect.

Fight (state FIGHT), once per sub-model core:
    1. its FIREWALL is stripped by weapon hits (hacking one of its reflections strips it faster;
       killing them feeds it a little, doc 5.3)
    2. it is EXPOSED for EXPOSE_S world seconds: hold [F] on it to counter-hack it
       (a HackSession with the mirror_dialect ctx; the saboteur's are reversed DECODEs)
    3. a successful counter-hack breaks that core; a failed one, or a missed window, reboots it
       at half firewall
Temperament, from your kill:hack ratio (>= 1 kill per hack: duelist):
    duelist    closes in, strafes, dashes out of your shots (toward your own favourite dodge),
               fires its weapon variants often
    saboteur   keeps its distance, blinks around you, rains packets, blacks the
               room out, sends reflections, jams your weapon with a telegraphed pulse, and forces
               reversed DECODEs (its packets and its reflections')
Ending (decided when the last core breaks):
    PATCH  all 14 Keys and >= 40% intel: the Mirror goes down and opens a ROOT BREACH of your own
           handle (BreachSession mode "reversed", handle_chunks, never pre-solved). Winning it is
           the PATCH ending. A BACKTRACE re-seals it: a short fight (RESEAL_FW), then the port
           reopens. A disconnect keeps the port open. Adaptation is capped for the whole fight.
    PURGE  everything else: beaten by force.
Death in the arena: you reboot at the arena start and the Mirror goes back to the start of its
current phase (full firewall for that core; broken cores and locked handle chunks are kept).

Coordinates: draw() is the WORLD LAYER (camera.world_to_screen); draw_overlay() is the internal
surface (camera.world_to_internal): its speech, the intro title. Time: AI, telegraphs and the
EXPOSED window count in world time; the intro and speech in real time.
"""
import math
import random

import pygame

import gdev
import hack
import player as player_mod

V = pygame.math.Vector2

INTEL_PATCH_PCT = 40.0
MIRROR_HP = 5.0                 # kept for older callers: one point per sub-model core

SUBMODELS = ["M3M0RY", "41M", "M0V3M3NT", "D3C1510N", "V01C3"]
SUBMODEL_PLAIN = ["MEMORY", "AIM", "MOVEMENT", "DECISION", "VOICE"]
TEMPERAMENT_LEET = {"duelist": "DU3L157", "saboteur": "54B07EUR"}
ADAPTATION_POOL = ["FIREWALL_WEIGHT", "BIT_SPEED", "LEAD_SHOTS", "FAST_KITE", "MORE_PROBES", "TIER2_SHARE"]

# Firewall, in weapon damage (a pistol bullet = 1). The HUD shows it as FW_PIPS pips.
FW_HP = [6, 7, 8, 9, 10]
FW_PIPS = 8
RESEAL_FW = 4                   # the short fight after a failed handle breach
REFLECT_HACK_FW = 5             # a hacked reflection strips this much
KILL_FW = 0.0                   # a reflection killed by a weapon feeds it this much ...
KILL_FW_CAP = 3.0               # ... at most this much per phase
UPLOAD_FW = 1                   # a reflection PR0B3 that reaches its socket
EXPOSE_S = 15.0                  # world seconds to jack in once the firewall is down
INTRO_LINE_S = 1.5
OUTRO_S = 1.6                   # dissolve before the ending screen
BODY = (8, 10)
HIT_R = 11.0                     # bullet / arrow vs the Mirror's centre
SPEECH_CPS = 40.0
SPEECH_W = 330                  # speech bubble text width, internal px

SPEED = {"duelist": 90.0, "saboteur": 75.0}
HOLD_PX = {"duelist": 105.0, "saboteur": 200.0}
CADENCE = {"duelist": (4.0, 5.0), "saboteur": (5.0, 6.5)}      # world s between attacks
DODGE_CHANCE = {"duelist": 0.2, "saboteur": 0.1}
DODGE_CD = {"duelist": 3.0, "saboteur": 4.0}
DASH_DIST = 96.0
DASH_S = 0.15
MAX_ALIVE = {"duelist": 2, "saboteur": 4}
SPAWN_EVERY = {"duelist": (12.0, 16.0), "saboteur": (9.0, 12.0)}
SPAWN_KINDS = {"duelist": [("b1t", 0.6), ("p1ng", 0.4)],
               "saboteur": [("p1ng", 0.3), ("pr0b3", 0.25), ("f1r3w4ll", 0.25), ("b1t", 0.2)]}
JAM_EVERY = (12.0, 16.0)
JAM_TELEGRAPH_S = 1.8
JAM_R = 34.0
JAM_S = 0.8
BLINK_EVERY = (7.0, 10.0)
PREDICT_MIN = 0.8              # the dodge predictor's guess must be this sure to aim with it
PREDICT_PX = 64.0
PREDICT_SHOW_S = 2.0

# Attacks per phase (attacks.py library names); every one also gets {"weapon": your top weapon}.
ATTACKS = {
    "duelist": [["radial_burst"],
                ["radial_burst", "port_beam"],
                ["port_beam", "firewall_dome", "radial_burst"],
                ["firewall_dome", "port_beam", "packet_rain"],
                ["radial_burst", "port_beam", "firewall_dome", "packet_rain"]],
    "saboteur": [["packet_rain"],
                 ["packet_rain", "port_overload"],
                 ["blackout", "packet_rain", "port_beam"],
                 ["port_overload", "packet_rain", "firewall_dome"],
                 ["blackout", "port_overload", "packet_rain", "port_beam"]],
}
AIMED = ("radial_burst", "port_beam", "packet_rain", "firewall_dome")

# Cyan-to-green ramp for the palette swap (dark -> light), and the eyes.
RAMP = [(4, 40, 30), (14, 100, 70), (34, 180, 110), (80, 235, 170), (150, 255, 240)]
EYES = gdev.MAGENTA
MIRROR_GREEN = (60, 230, 150)
GHOST = (70, 240, 190)

FALLBACK_TEXT = {
    "intro": ["h3ll0, [HANDLE].", "1 4m wh47 k3rn3l l34rn3d fr0m y0u."],
    "taunts": {"duelist": ["y0u 5h007 f1r57. 50 d0 1."], "saboteur": ["j4mm3d."], "common": ["gg? n07 y37."]},
    "phase_lines": ["%s 0FFL1N3." % s for s in SUBMODELS],
    "breach": {"open": "r3wr173 m3, [HANDLE].", "reseal": "n07 y37.", "reopen": "4g41n.",
               "purge": "y0u w1n. f0r n0w.", "patch": "0BJ3C71V3 R3WR1773N."},
}
FALLBACK_PACKET = {"id": "MIR-X", "tier": "MIRROR", "title": "C0UNT3R-H4CK",
                   "words": [["1"], {"c": ["4", "|\\/|"], "target": True}, ["Y", "0", "U"], ["N", "0", "W"],
                             {"c": ["5", "7", "0", "P"], "target": True, "p": "."}]}


def handle_chunks(handle):
    """The handle split for the PATCH breach: clamp(ceil(len / 3), 2, 4) chunks of 1-3 letters."""
    h = str(handle or "TECH").upper()
    n = max(2, min(4, int(math.ceil(len(h) / 3.0))))
    out, i = [], 0
    for k in range(n):
        size = int(math.ceil((len(h) - i) / float(n - k)))
        out.append(h[i:i + size])
        i += size
    return [c for c in out if c]


def _lerp(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _ramp(k):
    k = max(0.0, min(1.0, k)) * (len(RAMP) - 1)
    i = min(len(RAMP) - 2, int(k))
    return _lerp(RAMP[i], RAMP[i + 1], k - i)


_swap_cache = {}


def mirror_sprite(rel):
    """A player sprite palette-swapped for the Mirror (doc 8.4): each colour is mapped by its
    brightness onto a green-to-cyan ramp, the black eyes turn magenta. Cached per sprite."""
    if rel in _swap_cache:
        return _swap_cache[rel]
    img = gdev.image(rel)
    out = None
    if img is not None:
        out = img.copy()
        w, h = img.get_size()
        lums = []
        for x in range(w):
            for y in range(h):
                c = img.get_at((x, y))
                if c.a > 0 and (c.r, c.g, c.b) != (0, 0, 0):
                    lums.append(0.3 * c.r + 0.59 * c.g + 0.11 * c.b)
        lo, hi = (min(lums), max(lums)) if lums else (0.0, 1.0)
        span = max(1.0, hi - lo)
        for x in range(w):
            for y in range(h):
                c = img.get_at((x, y))
                if c.a == 0:
                    continue
                if (c.r, c.g, c.b) == (0, 0, 0):
                    out.set_at((x, y), (EYES[0], EYES[1], EYES[2], c.a))
                else:
                    k = (0.3 * c.r + 0.59 * c.g + 0.11 * c.b - lo) / span
                    col = _ramp(0.15 + 0.85 * k)
                    out.set_at((x, y), (col[0], col[1], col[2], c.a))
    _swap_cache[rel] = out
    return out


def _silhouette(img, color, alpha):
    s = img.copy()
    s.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
    s.fill((color[0], color[1], color[2], 0), special_flags=pygame.BLEND_RGBA_ADD)
    if alpha < 255:
        s.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
    return s


def _seg_dist(p, a, b):
    ab = b - a
    n = ab.length_squared()
    if n <= 1e-9:
        return (p - a).length()
    k = max(0.0, min(1.0, (p - a).dot(ab) / n))
    return (p - (a + ab * k)).length()


class Mirror(object):
    """The Mirror's body. Also the owner of its attacks: attacks.py reads pos, ports, max_alive,
    roll_kinds(n) and rack (None); the Patch Notes read d["adaptation_pool"], leet_name, adaptation.
      pos, vel (Vector2), hp / hp_max (sub-model cores left / 5), temperament "duelist"|"saboteur",
      mode "hunt"|"exposed"|"jacked"|"down", side "left"|"right" """
    id = "mirror"
    leet_name = "TH3 M1RR0R"
    plain_name = "THE MIRROR"
    slot = 6
    rack = None

    def __init__(self, finale, pos, temperament, ports):
        self.finale = finale
        self.run = finale.run
        self.pos = V(pos)
        self.vel = V(0, 0)
        self.temperament = temperament
        self.hp_max = float(len(SUBMODELS))
        self.hp = self.hp_max
        self.ports = [list(p) for p in ports]
        self.max_alive = MAX_ALIVE[temperament]
        self.d = {"adaptation_pool": list(ADAPTATION_POOL)}
        self.adaptation = 0
        self.mode = "hunt"
        self.side = "right"
        self.back = False
        self.flash = 0.0
        self.dash_t = 0.0
        self.dash_cd = 0.0
        self.dash_dir = V(1, 0)
        self.strafe = 1
        self.strafe_t = 2.0
        self.rect = pygame.Rect(0, 0, BODY[0], BODY[1])
        self._sync()

    def _sync(self):
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))

    def roll_kinds(self, n):
        table = SPAWN_KINDS[self.temperament]
        rng = self.finale.rng
        out = []
        for _ in range(int(n)):
            r = rng.random() * sum(w for _k, w in table)
            for k, w in table:
                r -= w
                if r <= 0:
                    out.append(k)
                    break
            else:
                out.append(table[-1][0])
        return out


class MirrorTarget(object):
    """The Mirror as a jack-in target: EXPOSED (counter-hack) or down (the handle breach).
    Duck-types a Minion for Game; its pos is the Mirror's own Vector2."""
    kind = "mirror"
    rack_id = None
    boss_id = "mirror"

    def __init__(self, mirror, breach=False):
        self.mirror = mirror
        self.label = "TH3 M1RR0R"
        self.prompt = "HOLD [F] REWRITE ITS OBJECTIVE" if breach else "HOLD [F] COUNTER-HACK THE MIRROR"
        self.breach = breach
        self.alive = True
        self.hackable = True
        self.jacked = False

    @property
    def pos(self):
        return self.mirror.pos


class Finale(object):
    """Public surface (contract):
      active bool (True once start() ran), state "IDLE"|"INTRO"|"FIGHT"|"HANDLE_BREACH"|"DONE"
      mirror (Mirror: pos, hp, hp_max, temperament) | None;  ending None|"PURGE"|"PATCH"
      target (MirrorTarget while it can be jacked) | None
      start()                     Game calls it right after the Deep Core swap
      update(wdt, real_dt), on_event(name, data), hack_context(minion) -> dict
      find_jack_target(pos, range) / open_session(target) / finish_session(target, result)
      hud_info() -> dict|None (same shape as bosses.Boss.hud_info)
      objective_text(), draw(world_surface, camera), draw_overlay(surface, camera)
      patch_eligible() -> bool    14 Keys and >= 40% intel
      ending_spec(kind) -> dict   for menu.EndingScreen
      on_player_respawn()
      dev_finish(ending=None)     tests / --dev: end the fight now
      to_save() -> dict, from_save(d)
    Extra fields: phase (cores broken, 0..5), fw / fw_max (firewall in weapon damage), locked
    (handle chunks read in earlier tries), eligible (PATCH path), resealing (after a failed breach).
    """

    def __init__(self, run):
        self.run = run
        self.content = gdev.load_json("data/intel.json", {}) or {}
        self.text = dict(FALLBACK_TEXT)
        self.text.update(self.content.get("mirror") or {})
        seed = run.rng.random() if hasattr(run, "rng") else None
        self.rng = random.Random(seed)
        self.fx_rng = random.Random()             # visual noise only
        self.active = False
        self.state = "IDLE"
        self.mirror = None
        self.ending = None
        self.target = None
        self.phase = 0
        self.fw = 0.0
        self.fw_max = 1.0
        self.fed = 0.0
        self.expose_t = 0.0
        self.locked = set()
        self.eligible = False
        self.resealing = False
        self.session_kind = None
        self.dialect = None
        self.weapon = "PISTOL"
        self.t = 0.0
        self.intro_t = 0.0
        self.intro_i = 0
        self.end_t = None
        self.done_t = 0.0
        self.attack_t = 0.0
        self.attack_i = 0
        self.spawn_t = 0.0
        self.jam_t = 0.0
        self.blink_t = 0.0
        self.taunt_t = 0.0
        self.absorb_t = 0.0
        self.jams = []
        self.ghosts = []                          # dash / blink after-images [x, y, rel, age]
        self.speech = None                        # {"text", "t", "total"}
        self.prediction = None                    # [text, seconds left]
        self.seen_shots = set()
        self.hit_slashes = set()                  # ids of slashes / piercing arrows that already hit
        self.counter_attempts = 0
        self._loaded = None

    # ------------------------------------------------------------ flow

    def start(self):
        """Game calls this right after the arena swap (also on a CONTINUE inside the Deep Core)."""
        run = self.run
        arena = run.world.arena or {}
        self.arena = arena
        self.start_pos = V(arena.get("mirror_start", (800, 400)))
        ports = arena.get("ports") or [[self.start_pos.x - 200, self.start_pos.y], [self.start_pos.x + 200,
                                                                                     self.start_pos.y]]
        saved = self._loaded or {}
        temper = saved.get("temperament")
        if temper not in ("duelist", "saboteur"):
            temper = "duelist" if run.profiler.kill_hack_ratio() >= 1.0 else "saboteur"
        self.mirror = Mirror(self, self._entry_spot(), temper, ports)
        self.active = True
        self.ending = None
        self.target = None
        self.end_t = None
        self.phase = max(0, min(len(SUBMODELS), int(saved.get("phase", 0))))
        self.locked = set(int(i) for i in saved.get("locked", []))
        self.eligible = self.patch_eligible()
        self.resealing = False
        self._make_dialect()
        self._update_weapon()
        self.mirror.hp = self.mirror.hp_max - self.phase
        self._reset_timers()
        lines = list(self.text.get("intro") or [])
        if saved:
            lines = lines[:1]                       # a CONTINUE: a short hello, then straight in
        self.intro_lines = lines
        self.intro_i = 0
        self.intro_t = INTRO_LINE_S * len(lines) + 0.6
        self.state = "INTRO"
        self.fw_max = float(FW_HP[min(self.phase, len(FW_HP) - 1)])
        self.fw = self.fw_max
        resume = saved.get("state")
        self._loaded = None
        if resume == "HANDLE_BREACH" or (self.phase >= len(SUBMODELS) and self.eligible):
            self._resume_state = "HANDLE_BREACH"
        else:
            self._resume_state = "FIGHT"

    def _make_dialect(self):
        self.dialect = None
        try:
            import bosses
            self.dialect = bosses.make_modifier({"id": "mirror_dialect", "worst_glyphs": 5,
                                                 "decoys_from_history": True, "thin_on_correct": True,
                                                 "run": self.run}, self.mirror)
        except Exception as err:            # bosses.py not ready: the packets still work, plainer
            print("[finale] no mirror_dialect (%s)" % err)
        if self.dialect is not None and hasattr(self.dialect, "on_fight_start"):
            self.dialect.on_fight_start()

    def _update_weapon(self):
        try:
            w = self.run.profiler.top_weapon()[0]
        except Exception:
            w = None
        self.weapon = w if w in ("BOW", "PISTOL", "KATANA") else "PISTOL"

    def _reset_timers(self):
        r = self.rng
        self.attack_t = 1.2
        self.spawn_t = r.uniform(2.0, 4.0)
        self.jam_t = r.uniform(*JAM_EVERY)
        self.blink_t = r.uniform(*BLINK_EVERY)
        self.taunt_t = r.uniform(8.0, 12.0)
        self.jams = []
        self.fed = 0.0

    def _begin_fight(self):
        run = self.run
        if self._resume_state == "HANDLE_BREACH":
            self._open_handle_port(first=False)
            return
        if self.phase >= len(SUBMODELS):             # a save from the very end of a PURGE fight
            self._end("PURGE", OUTRO_S)
            return
        self.state = "FIGHT"
        self.mirror.mode = "hunt"
        pn = getattr(run.profiler, "patches", None)
        if pn is not None:
            try:
                pn.capped = bool(self.eligible)
                card = pn.on_fight_start(self.mirror)
            except Exception as err:
                print("[finale] patch card failed (%s)" % err)
                card = None
            if card:
                for line in (card.get("lines") or [])[:2]:
                    run.toast("%s %s" % (card.get("version", ""), line), gdev.MAGENTA, 3.5)
        if self.eligible:
            run.toast("Y0U UND3R574ND 17 :: ADAPTATION CAPPED", gdev.CYAN, 3.0)

    def patch_eligible(self):
        prog = self.run.progression
        return prog.key_count >= 14 and prog.intel_percent() >= INTEL_PATCH_PCT

    # ------------------------------------------------------------ update

    def update(self, wdt, real_dt):
        if not self.active or self.mirror is None:
            return
        run = self.run
        self.t += real_dt
        if self.speech is not None:
            self.speech["t"] += real_dt
            if self.speech["t"] >= self.speech["total"]:
                self.speech = None
        if self.prediction is not None:
            self.prediction[1] -= real_dt
            if self.prediction[1] <= 0:
                self.prediction = None
        self._update_fx(wdt)
        if self.state == "DONE":
            self.done_t += real_dt
            if self.end_t is not None:
                self.end_t -= real_dt
                if self.end_t <= 0:
                    self.end_t = None
                    run.request_ending(self.ending)
            return
        if self.state == "INTRO":
            self._absorb_shots(wdt)
            self.intro_t -= real_dt
            due = int((INTRO_LINE_S * len(self.intro_lines) + 0.6 - self.intro_t) / INTRO_LINE_S)
            while self.intro_i < len(self.intro_lines) and self.intro_i <= due:
                self.say(self.intro_lines[self.intro_i], INTRO_LINE_S + 0.4)
                self.intro_i += 1
            if self.intro_t <= 0:
                self._begin_fight()
            return
        m = self.mirror
        m.flash = max(0.0, m.flash - wdt)
        if run.player.dead:
            m.vel = V(0, 0)
            return
        if self.state == "FIGHT":
            self._hits(wdt)
            self._update_fight(wdt)
        elif self.state == "HANDLE_BREACH":
            self._absorb_shots(wdt)
            self._update_spawns(wdt, down=True)

    def _update_fight(self, wdt):
        m = self.mirror
        m.dash_cd = max(0.0, m.dash_cd - wdt)
        if m.mode == "exposed":
            m.vel = V(0, 0)
            if self.target is None or not self.target.jacked:
                self.expose_t -= wdt
                if self.expose_t <= 0:
                    self._reboot("window")
            return
        if m.mode != "hunt":
            m.vel = V(0, 0)
            return
        self._move(wdt)
        self._maybe_dodge()
        self._update_attacks(wdt)
        self._update_spawns(wdt)
        if m.temperament == "saboteur":
            self._update_jam(wdt)
            self._update_blink(wdt)
        self.taunt_t -= wdt
        if self.taunt_t <= 0:
            self.taunt_t = self.rng.uniform(10.0, 15.0)
            self.taunt()

    # ------------------------------------------------------------ movement

    def _speed(self):
        return SPEED[self.mirror.temperament] * (1.0 + 0.06 * self.phase)

    def _move(self, wdt):
        m = self.mirror
        p = self.run.player.pos
        if m.dash_t > 0:
            m.dash_t = max(0.0, m.dash_t - wdt)
            m.vel = m.dash_dir * (DASH_DIST / DASH_S)
            if self.fx_rng.random() < 0.7:
                self._ghost()
        else:
            # side-on duel: the view is wide but short (and the HUD covers its top), so it holds a
            # spot left or right of you, bobbing a little, and crosses over by going around you
            _hw, hh = self._half_view()
            hold = self._hold_px()
            m.strafe_t -= wdt
            if m.strafe_t <= 0:
                m.strafe = -m.strafe
                m.strafe_t = self.rng.uniform(3.0, 6.0)
            bob = math.sin(self.t * 1.3 + m.strafe) * 0.3 * hh
            goal = V(p.x + m.strafe * hold, p.y + bob)
            if (m.pos.x - p.x) * m.strafe < 0.3 * hold:       # on the wrong side: arc around you
                goal.y = p.y + (0.55 * hh if m.pos.y >= p.y else -0.55 * hh)
                goal.x = p.x + m.strafe * hold * 0.5
            to = goal - m.pos
            d = to.length()
            desire = V(0, 0)
            if d > 4:
                desire = to * (self._speed() / d) * min(1.0, d / 40.0)
            m.vel += (desire - m.vel) * min(1.0, 6.0 * wdt)
        if not self._slide(m.vel * wdt) and m.dash_t <= 0 and m.strafe_t < 2.0:
            m.strafe = -m.strafe                    # cornered: take the other side
            m.strafe_t = self.rng.uniform(3.0, 6.0)
        m.back = m.vel.y < -40 and abs(m.vel.y) > abs(m.vel.x) and m.dash_t <= 0
        m.side = "left" if p.x < m.pos.x else "right"

    def _slide(self, move):
        """Move with world collision, X then Y (actors slide along walls). False if blocked."""
        m = self.mirror
        w = self.run.world
        ok = True
        for axis in (0, 1):
            step = move.x if axis == 0 else move.y
            if abs(step) < 1e-6:
                continue
            trial = V(m.pos)
            if axis == 0:
                trial.x += step
            else:
                trial.y += step
            r = pygame.Rect(0, 0, BODY[0], BODY[1])
            r.center = (int(round(trial.x)), int(round(trial.y)))
            if w.rect_free(r):
                m.pos = trial
            else:
                ok = False
        m._sync()
        return ok

    def _half_view(self):
        """Half the camera's view in world px (the 3X zoom shows only about 320 x 167)."""
        cam = self.run.camera
        return cam.view_rect.w / 2.0, cam.view_rect.h / 2.0

    def _hold_px(self):
        """Side-on holding distance, kept inside the view's width."""
        hw, _hh = self._half_view()
        k = 0.65 if self.mirror.temperament == "duelist" else 0.85
        return min(HOLD_PX[self.mirror.temperament], k * hw)

    def _entry_spot(self):
        """Where the Mirror steps out: in front of you, inside the view (not at the far core)."""
        p = V(self.run.player.pos)
        _hw, hh = self._half_view()
        for dy in (0.6, 0.5, 0.4, 0.7, 0.3):
            for dx in (0, -24, 24, -48, 48):
                q = p + V(dx, -hh * dy)
                if self._free(q):
                    return q
        return V(self.start_pos)

    def _free(self, pos):
        r = pygame.Rect(0, 0, BODY[0] + 4, BODY[1] + 4)
        r.center = (int(pos[0]), int(pos[1]))
        return self.run.world.rect_free(r)

    def _maybe_dodge(self):
        """It dodges your shots the way you dodge: toward your own most common dash direction."""
        m = self.mirror
        if m.dash_t > 0 or m.dash_cd > 0:
            return
        for p in self.run.player.projectiles:
            if not p.alive or p.kind == "slash" or p.team != "player" or id(p) in self.seen_shots:
                continue
            rel = m.pos - p.pos
            if rel.length() > 90 or p.vel.length_squared() < 1:
                continue
            if p.vel.dot(rel) <= 0:
                continue
            ahead = p.pos + p.vel.normalize() * 120
            if _seg_dist(m.pos, p.pos, ahead) > 14:
                continue
            self.seen_shots.add(id(p))
            if self.rng.random() >= DODGE_CHANCE[m.temperament]:
                continue
            perp = V(-p.vel.y, p.vel.x).normalize()
            bias = self._your_dodge()
            if bias is not None:
                if perp.dot(bias) < 0:
                    perp = -perp
            elif self.rng.random() < 0.5:
                perp = -perp
            if not self._free(m.pos + perp * DASH_DIST * 0.6):
                perp = -perp
            m.dash_dir = perp
            m.dash_t = DASH_S
            m.dash_cd = DODGE_CD[m.temperament]
            break
        if len(self.seen_shots) > 200:
            self.seen_shots = set(list(self.seen_shots)[-60:])

    def _your_dodge(self):
        try:
            name, share = self.run.profiler.dodge_bias()
            import profiler
            if name is not None and share > 0.2:
                i = profiler.DIR_NAMES.index(name)
                return V(math.cos(i * math.pi / 4), math.sin(i * math.pi / 4))
        except Exception:
            pass
        return None

    def _ghost(self):
        m = self.mirror
        self.ghosts.append([m.pos.x, m.pos.y, self._body_rel(), 0.0])
        self.ghosts = self.ghosts[-10:]

    def _update_blink(self, wdt):
        """Saboteur: blinks to another spot in view, away from you (sooner when you get close)."""
        m = self.mirror
        self.blink_t -= wdt
        close = (self.run.player.pos - m.pos).length() < 0.5 * self._hold_px()
        if self.blink_t > 0 and not (close and self.blink_t < BLINK_EVERY[0] - 2.0):
            return
        self.blink_t = self.rng.uniform(*BLINK_EVERY)
        p = self.run.player.pos
        hw, hh = self._half_view()
        for _ in range(16):
            a = self.rng.uniform(0, 2 * math.pi)
            ca, sa = abs(math.cos(a)), abs(math.sin(a))
            reach = min(hw / max(ca, 1e-3), hh / max(sa, 1e-3)) * 0.85      # stays inside the view
            d = self.rng.uniform(0.7, 1.0) * min(reach, 220.0)
            q = p + V(math.cos(a), math.sin(a)) * d
            if d >= 50 and self._free(q):
                self._ghost()
                m.pos = q
                m.vel = V(0, 0)
                m._sync()
                self._ghost()
                return

    # ------------------------------------------------------------ hits

    def _shots(self, wdt):
        """Player shots that touch the Mirror this frame: [(projectile, damage)]."""
        m = self.mirror
        out = []
        body = m.rect.inflate(6, 6)
        for p in self.run.player.projectiles:
            if not p.alive or p.team != "player":
                continue
            if p.kind == "slash":
                if id(p) in self.hit_slashes or not p.rect.colliderect(body):
                    continue
                self.hit_slashes.add(id(p))
                out.append((p, p.damage))
                continue
            if id(p) in self.hit_slashes:          # a piercing arrow hits once
                continue
            prev = p.pos - p.vel * max(wdt, 1.0 / 60.0)
            if _seg_dist(m.pos, prev, p.pos) <= HIT_R:
                if p.pierce:
                    self.hit_slashes.add(id(p))
                out.append((p, p.damage))
        if len(self.hit_slashes) > 64:
            live = set(id(p) for p in self.run.player.projectiles)
            self.hit_slashes &= live
        return out

    def _absorb_shots(self, wdt):
        """Shots stop at the Mirror without effect (intro, exposed, down)."""
        hits = self._shots(wdt)
        for p, _dmg in hits:
            if p.kind != "slash" and not p.pierce:
                p.alive = False
        if hits and self.absorb_t <= self.t:
            self.absorb_t = self.t + 1.0
            m = self.mirror
            if self.state == "FIGHT" and m.mode == "exposed":
                self.run.float_text((m.pos.x, m.pos.y - 18), "H4CK 17 [F]", gdev.CYAN, 0.8)
            elif self.state == "HANDLE_BREACH":
                self.run.float_text((m.pos.x, m.pos.y - 18), "R3WR173 17 [F]", gdev.CYAN, 0.8)

    def _hits(self, wdt):
        m = self.mirror
        if m.mode != "hunt" or m.dash_t > 0:
            if m.mode != "hunt":
                self._absorb_shots(wdt)
            return
        for p, dmg in self._shots(wdt):
            if p.kind != "slash" and not p.pierce:
                p.alive = False
            self.fw = max(0.0, self.fw - float(dmg))
            m.flash = 0.08
        if self.fw <= 0:
            self._firewall_down()

    def _firewall_down(self):
        if self.resealing:
            self.say(self.text["breach"].get("reopen", ""))
            self._open_handle_port(first=False)
            return
        self._expose()

    def _expose(self):
        m = self.mirror
        run = self.run
        m.mode = "exposed"
        m.vel = V(0, 0)
        m.dash_t = 0.0
        self.expose_t = EXPOSE_S
        self.target = MirrorTarget(m)
        run.attacks.clear(m)
        run.float_text((m.pos.x, m.pos.y - 20), "3XP053D", gdev.CYAN, 1.2)
        run.toast("THE MIRROR IS EXPOSED :: hold [F] on it", gdev.CYAN, 2.4)
        run.shake(4, 0.2)

    def _reboot(self, why):
        m = self.mirror
        m.mode = "hunt"
        self.target = None
        self.session_kind = None
        self.fw = max(self.fw, math.ceil(self.fw_max / 2.0))
        self.attack_t = 1.0
        if why == "window":
            self.run.toast("THE MIRROR REBOOTS :: firewall at half", gdev.MAGENTA, 2.2)
        self.say(self.rng.choice([self.text["breach"].get("reseal", "n07 y37.")] +
                                 list((self.text.get("taunts") or {}).get("common") or [])))

    # ------------------------------------------------------------ attacks, jams, reflections

    def _live_attacks(self):
        return len([a for a in self.run.attacks.active if a.owner is self.mirror and a.state != "done"])

    def _update_attacks(self, wdt):
        m = self.mirror
        self.attack_t -= wdt
        if self.attack_t > 0 or self._live_attacks() >= 2:
            return
        pool = ATTACKS[m.temperament][min(self.phase, 4)]
        entry = pool[self.attack_i % len(pool)] if self.rng.random() < 0.6 else self.rng.choice(pool)
        self.attack_i += 1
        if entry == "port_overload" and len(self._reflections()) >= m.max_alive:
            entry = "packet_rain"
        params = {"weapon": self.weapon, "predict": True}
        if entry == "port_overload":
            params["tags"] = {"boss_id": "mirror"}
        if entry == "packet_rain" and m.temperament == "saboteur" and self.rng.random() < 0.5:
            params["aim"] = "lead"
        if entry in AIMED and self.attack_i % 2 == 0:
            guess = self._guess()
            if guess is not None:
                tok, prob = guess
                d = V(math.cos(tok * math.pi / 4), math.sin(tok * math.pi / 4))
                params["aim_point"] = V(self.run.player.pos) + d * PREDICT_PX * prob
                self._show_prediction(tok, prob)
        self.run.attacks.spawn(entry, V(m.pos), params, owner=m)
        lo, hi = CADENCE[m.temperament]
        self.attack_t = self.rng.uniform(lo, hi) * max(0.6, 1.0 - 0.07 * self.phase)

    def _guess(self):
        try:
            g = self.run.profiler.predictor.predict()
        except Exception:
            return None
        if g is None or g[0] is None or g[1] < PREDICT_MIN:
            return None
        return int(g[0]) % 8, float(g[1])

    def _show_prediction(self, tok, prob):
        try:
            import profiler
            name = profiler.leet_word(profiler.DIR_NAMES[tok])
        except Exception:
            name = str(tok)
        self.prediction = ["PR3D1C710N: %s (%d%%)" % (name, int(round(100 * prob))), PREDICT_SHOW_S]

    def _update_jam(self, wdt):
        p = self.run.player
        self.jam_t -= wdt
        if self.jam_t <= 0:
            self.jam_t = self.rng.uniform(*JAM_EVERY) * max(0.6, 1.0 - 0.05 * self.phase)
            self.jams.append({"pos": V(p.pos), "t": 0.0, "total": JAM_TELEGRAPH_S, "r": JAM_R})

    def _land_jams(self):
        p = self.run.player
        keep = []
        for j in self.jams:
            if j["t"] < j["total"]:
                keep.append(j)
                continue
            if not p.dead and not p.dashing and (p.pos - j["pos"]).length() <= j["r"]:
                p.jam(JAM_S)
                self.run.float_text((p.pos.x, p.pos.y - 18), "J4MM3D", gdev.MAGENTA, 1.0)
                self.taunt("jam")
        self.jams = keep

    def _reflections(self):
        return [x for x in self.run.minions.live() if getattr(x, "boss_id", None) == "mirror"]

    def _update_spawns(self, wdt, down=False):
        m = self.mirror
        self.spawn_t -= wdt
        if self.spawn_t > 0:
            return
        lo, hi = SPAWN_EVERY[m.temperament]
        self.spawn_t = self.rng.uniform(lo, hi) * (1.6 if down else 1.0)
        cap = 3 if down else m.max_alive + min(2, self.phase // 2)
        live = len(self._reflections()) + self.run.minions.pending(None)
        n = 1 if (down or m.temperament == "duelist") else 2
        n = min(n, cap - live)
        if n <= 0:
            return
        director = getattr(self.run, "director", None)
        if director is not None and hasattr(director, "request_spawn") and not director.request_spawn(n):
            return
        p = self.run.player.pos
        ports = [V(q) for q in m.ports if (V(q) - p).length() >= 150] or [V(q) for q in m.ports]
        for kind in m.roll_kinds(n):
            q = self.rng.choice(ports)
            self.run.minions.spawn_at(kind, q, tags={"boss_id": "mirror"}, telegraph=1.0)

    def _update_fx(self, wdt):
        for g in self.ghosts:
            g[3] += max(wdt, 0.004)
        self.ghosts = [g for g in self.ghosts if g[3] < 0.3]
        for j in self.jams:
            j["t"] += wdt
        if self.jams:
            self._land_jams()

    # ------------------------------------------------------------ speech

    def _values(self):
        """Placeholder values for taunts, from the profile (missing ones are left out)."""
        v = {}
        prof = self.run.profiler
        try:
            import profiler
            w, pct = prof.top_weapon()
            if w:
                v["weapon"], v["pct"] = profiler.leet_word(w).lower(), pct
            d, _share = prof.dodge_bias()
            if d:
                v["dir"] = profiler.leet_word(d).lower()
            worst = prof.worst_glyphs(1)
            if worst:
                v["glyph"] = worst[0]
                v["n"] = self.run.codex.misses.get(worst[0], 0)
            s = prof.median_pick_s()
            if s is not None:
                v["s"] = "%.1f" % s
        except Exception:
            pass
        return v

    def _fill(self, text, v):
        out = str(text).replace("[HANDLE]", self.run.handle)
        for k in ("weapon", "pct", "dir", "glyph", "n", "s"):
            key = "{%s}" % k
            if key in out:
                if k not in v:
                    return None
                out = out.replace(key, str(v[k]))
        return out

    def say(self, text, seconds=None):
        text = self._fill(text, self._values()) if text else None
        if not text:
            return
        total = seconds if seconds is not None else max(2.4, 1.6 + len(text) / SPEECH_CPS * 1.6)
        self.speech = {"text": text, "t": 0.0, "total": total}

    def taunt(self, kind=None):
        taunts = self.text.get("taunts") or {}
        if kind is None:
            pool = list(taunts.get(self.mirror.temperament) or []) + list(taunts.get("common") or [])
        else:
            pool = list(taunts.get(kind) or [])
        v = self._values()
        filled = [t for t in (self._fill(x, v) for x in pool) if t]
        if filled:
            self.say(self.rng.choice(filled))

    # ------------------------------------------------------------ events

    def on_event(self, name, data):
        if not self.active or not self.run.in_deep_core or self.state in ("IDLE", "DONE"):
            return
        m = self.mirror
        if name == "pick":
            if self.dialect is not None and hasattr(self.dialect, "on_pick"):
                self.dialect.on_pick(data)
        elif name == "hack_finished" and data.get("provider") == "minion":
            t = data.get("target")
            res = data.get("result") or {}
            if getattr(t, "boss_id", None) == "mirror" and res.get("outcome") == "success" and not res.get("stale"):
                if self.state == "FIGHT" and m.mode == "hunt":
                    self.fw = max(0.0, self.fw - REFLECT_HACK_FW)
                    self.run.float_text((m.pos.x, m.pos.y - 18), "-FW", gdev.CYAN, 0.9)
                    if self.fw <= 0:
                        self._firewall_down()
            self._check_eligible()
        elif name == "minion_killed":
            t = data.get("minion")
            if getattr(t, "boss_id", None) == "mirror" and self.state == "FIGHT" and m.mode == "hunt" and \
               self.fed < KILL_FW_CAP:
                gain = min(KILL_FW, KILL_FW_CAP - self.fed)
                self.fed += gain
                self.fw = min(self.fw_max, self.fw + gain)
        elif name == "probe_uploaded":
            t = data.get("minion")
            if getattr(t, "boss_id", None) == "mirror":
                m.adaptation += 1
                if self.state == "FIGHT" and m.mode == "hunt":
                    self.fw = min(self.fw_max, self.fw + UPLOAD_FW)
                self.taunt("upload")
        elif name == "player_respawned":
            if self.state == "FIGHT":
                self.taunt("respawn")

    def _check_eligible(self):
        if self.eligible or not self.patch_eligible():
            return
        self.eligible = True
        pn = getattr(self.run.profiler, "patches", None)
        if pn is not None:
            pn.capped = True
        self.run.toast("40%% 1NT3L :: Y0U UND3R574ND 17 N0W", gdev.CYAN, 3.0)

    def hack_context(self, minion):
        """Reflections in the arena speak your dialect; the saboteur's are reversed DECODEs."""
        if not self.active or not self.run.in_deep_core or getattr(minion, "boss_id", None) != "mirror":
            return {}
        ctx = {}
        if self.dialect is not None:
            ctx.update(self.dialect.hack_context(minion) or {})
        if self.mirror is not None and self.mirror.temperament == "saboteur":
            ctx["reversed"] = True
        return ctx

    # ------------------------------------------------------------ jack provider

    def find_jack_target(self, pos, max_range):
        if self.target is None or self.state not in ("FIGHT", "HANDLE_BREACH"):
            return None
        if self.state == "FIGHT" and self.mirror.mode != "exposed":
            return None
        if (self.target.pos - V(pos)).length() <= max_range:
            return self.target
        return None

    def open_session(self, target):
        run = self.run
        m = self.mirror
        ctx = run.base_hack_ctx(target)
        if self.dialect is not None:
            ctx.update(self.dialect.hack_context(target) or {})
        if self.state == "HANDLE_BREACH":
            self.session_kind = "breach"
            handle = run.progression.handle
            level = 4
            try:
                level = max(4, int(run.tuner.breach_level()))
            except Exception:
                pass
            chunks = handle_chunks(handle)
            spec = {"title": "R00T BR34CH :: %s" % handle, "password_word": handle, "chunks": chunks,
                    "presolved": sorted(i for i in self.locked if i < len(chunks)), "prestrike": False,
                    "level": level, "mode": "reversed", "boss_id": "mirror"}
            return hack.BreachSession(spec, run.settings, run.codex, run.rng, ctx)
        self.session_kind = "counter"
        m.mode = "jacked"
        packets = list(self.text.get("packets") or []) or [FALLBACK_PACKET]
        frag = dict(packets[min(self.phase, len(packets) - 1)])
        self.counter_attempts += 1
        frag["id"] = "%s#%d" % (frag.get("id", "MIR"), self.counter_attempts)
        ctx.update({"fragment": frag, "payloads": ["EXTRACT"],
                    "title": "C0UNT3R-H4CK :: %s" % SUBMODELS[min(self.phase, len(SUBMODELS) - 1)],
                    "level": max(3, int(ctx.get("level") or 3))})
        if m.temperament == "saboteur":
            ctx["reversed"] = True
        return hack.HackSession(target, run.settings, run.codex, None, run.rng, ctx)

    def finish_session(self, target, result):
        outcome = (result or {}).get("outcome")
        kind = self.session_kind
        self.session_kind = None
        if kind == "breach" or getattr(target, "breach", False):
            for i in (result or {}).get("locked") or []:
                self.locked.add(int(i))
            if outcome == "success":
                self._end("PATCH", OUTRO_S)
            elif outcome == "backtrace":
                self._reseal()
            # abort / interrupted: the port stays open, progress kept
            return
        if self.state != "FIGHT":
            return
        if outcome == "success":
            self._break_core()
        elif outcome == "abort" and (result or {}).get("reason") != "interrupted" and not self.run.player.dead:
            # Esc / RMB / focus loss: a clean disconnect keeps the exposure window (doc 7.2:
            # disconnect keeps progress); expose_t was paused while jacked in
            self.mirror.mode = "exposed"
        else:
            self._reboot("hack")

    def _break_core(self):
        run = self.run
        m = self.mirror
        idx = self.phase
        self.phase += 1
        m.hp = max(0.0, m.hp_max - self.phase)
        self.target = None
        lines = self.text.get("phase_lines") or FALLBACK_TEXT["phase_lines"]
        self.say(lines[min(idx, len(lines) - 1)])
        run.toast("SUBM0D3L %s :: 0FFL1N3 (%d/%d)" % (SUBMODELS[min(idx, 4)], self.phase, len(SUBMODELS)),
                  gdev.CYAN, 3.0)
        run.shake(6, 0.3)
        guess = self._guess()
        if guess is not None:
            self._show_prediction(*guess)
        if self.phase >= len(SUBMODELS):
            self._check_eligible()
            if self.patch_eligible():
                self._open_handle_port(first=True)
            else:
                self._end("PURGE", OUTRO_S)
            return
        self.fw_max = float(FW_HP[min(self.phase, len(FW_HP) - 1)])
        self.fw = self.fw_max
        self.fed = 0.0
        m.mode = "hunt"
        self._update_weapon()
        self.attack_t = 1.5
        self.blink_t = 0.0 if m.temperament == "saboteur" else self.blink_t
        run.request_save("mirror_core")

    def _open_handle_port(self, first=True):
        run = self.run
        m = self.mirror
        self.state = "HANDLE_BREACH"
        self.resealing = False
        m.mode = "down"
        m.vel = V(0, 0)
        m.dash_t = 0.0
        self.target = MirrorTarget(m, breach=True)
        run.attacks.clear(m)
        self.spawn_t = 4.0
        if first:
            self.say(self.text["breach"].get("open", ""), 4.0)
            run.toast("R00T BR34CH :: Y0UR 0WN H4NDL3", gdev.CYAN, 3.0)
            run.request_save("mirror_down")

    def _reseal(self):
        m = self.mirror
        self.state = "FIGHT"
        self.resealing = True
        self.target = None
        m.mode = "hunt"
        self.fw_max = float(RESEAL_FW)
        self.fw = self.fw_max
        self.attack_t = 1.2
        self.say(self.text["breach"].get("reseal", ""))
        self.run.toast(self.text.get("reseal_hint") or "THE MIRROR RE-SEALS", gdev.MAGENTA, 3.0)

    def _end(self, kind, delay=0.0):
        run = self.run
        self.state = "DONE"
        self.ending = kind
        self.done_t = 0.0
        self.target = None
        self.session_kind = None
        self.jams = []
        if self.mirror is not None:
            self.mirror.mode = "down"
            self.mirror.vel = V(0, 0)
            run.attacks.clear(self.mirror)
        for x in self._reflections():
            if not x.jacked:
                x.kill("flush")
        orders = getattr(run.minions, "orders", None)
        if isinstance(orders, list):
            run.minions.orders = [o for o in orders if not (len(o) > 4 and isinstance(o[4], dict) and
                                                            o[4].get("boss_id") == "mirror")]
        pn = getattr(run.profiler, "patches", None)
        if pn is not None:
            pn.capped = False
        self.say(self.text["breach"].get("patch" if kind == "PATCH" else "purge", ""), OUTRO_S + 1.0)
        run.emit("mirror_defeated", ending=kind)
        if delay and delay > 0:
            self.end_t = float(delay)
        else:
            self.end_t = None
            run.request_ending(kind)

    # ------------------------------------------------------------ HUD

    def hud_info(self):
        if not self.active or self.mirror is None or self.state in ("DONE", "IDLE"):
            return None
        m = self.mirror
        n = len(SUBMODELS)
        if self.state == "INTRO":
            state = "R3C0MP1L1NG"
        elif self.state == "HANDLE_BREACH":
            state = "BREACH_READY" if not (self.target and self.target.jacked) else "BREACH"
        elif m.mode in ("exposed", "jacked"):
            state = "EXPOSURE"
        else:
            state = "SIEGE"
        fw = 0 if self.state != "FIGHT" or m.mode != "hunt" else int(math.ceil(FW_PIPS * self.fw / max(1.0, self.fw_max)))
        cores = [{"hp": 0.0 if i < self.phase else 1.0, "max": 1.0, "broken": i < self.phase} for i in range(n)]
        exposed = self.state == "FIGHT" and m.mode in ("exposed", "jacked")
        return {"name": "TH3 M1RR0R", "plain": "THE MIRROR",
                "subtitle": "%d/5 :: %s" % (n - min(self.phase, n), TEMPERAMENT_LEET.get(m.temperament, "")),
                "state": state, "firewall": fw, "firewall_max": FW_PIPS if self.state == "FIGHT" else 0,
                "cores": cores, "phase": min(self.phase, n - 1), "phases": n,
                "window": self.expose_t if exposed else None, "window_total": EXPOSE_S if exposed else None,
                "queue": None, "adaptation": m.adaptation,
                "prediction": self.prediction[0] if self.prediction else None}

    def objective_text(self):
        if self.state == "INTRO":
            return "OBJECTIVE  THE MIRROR is compiling itself"
        if self.state == "HANDLE_BREACH":
            return "OBJECTIVE  hold [F] on THE MIRROR: spell your own handle to rewrite it"
        if self.state == "FIGHT":
            m = self.mirror
            if self.resealing:
                return "OBJECTIVE  strip the re-sealed firewall to reopen the port"
            if m is not None and m.mode in ("exposed", "jacked"):
                return "OBJECTIVE  hold [F] on THE MIRROR: counter-hack %s (%ds)" % (
                    SUBMODEL_PLAIN[min(self.phase, 4)], int(math.ceil(max(0.0, self.expose_t))))
            return "OBJECTIVE  strip THE MIRROR's firewall (or hack its reflections) :: %s %d/5" % (
                SUBMODEL_PLAIN[min(self.phase, 4)], self.phase + 1)
        return ""

    # ------------------------------------------------------------ drawing

    def _body_rel(self):
        m = self.mirror
        if m.back:
            return "player_assets/player_back.png"
        if m.mode == "hunt" and self.state == "FIGHT":
            return "player_assets/player_%s_asset_weapon.png" % m.side
        return "player_assets/player_%s_asset.png" % m.side

    def draw(self, surface, camera):
        if not self.active or self.mirror is None:
            return
        m = self.mirror
        reduced = bool(self.run.settings.get("reduced_glitch"))
        for j in self.jams:
            self._draw_jam(surface, camera, j)
        for x, y, rel, age in self.ghosts:
            img = mirror_sprite(rel)
            if img is not None:
                gx, gy = camera.world_to_screen((x, y))
                surface.blit(_silhouette(img, GHOST, int(120 * (1.0 - age / 0.3))), (gx - 16, gy - 16))
        sx, sy = camera.world_to_screen(m.pos)
        if not surface.get_rect().inflate(60, 60).collidepoint(sx, sy):
            return
        img = mirror_sprite(self._body_rel())
        if img is None:
            pygame.draw.rect(surface, MIRROR_GREEN, (sx - 4, sy - 5, 8, 10))
            return
        if self.state == "DONE":
            self._draw_dissolve(surface, img, sx, sy)
            return
        pygame.draw.ellipse(surface, (0, 14, 6), (sx - 5, sy + 4, 10, 3))
        if self.state == "INTRO":
            total = INTRO_LINE_S * len(self.intro_lines) + 0.6
            k = max(0.0, min(1.0, 1.0 - self.intro_t / max(0.1, total)))
            self._draw_glitched(surface, img, sx, sy, 1.0 - k, int(255 * min(1.0, 0.25 + k)), reduced)
            return
        if m.flash > 0:
            img = _silhouette(img, gdev.WHITE, 255)
        if self.state == "HANDLE_BREACH":
            self._draw_glitched(surface, img, sx, sy + 2, 0.25, 255, reduced)
            r = 20 + int(3 * math.sin(self.t * 6))
            pygame.draw.circle(surface, gdev.CYAN, (sx, sy), r, 1)
            pygame.draw.circle(surface, (20, 90, 100), (sx, sy), r + 4, 1)
            return
        if m.mode in ("exposed", "jacked"):
            self._draw_glitched(surface, img, sx, sy, 0.6, 255, reduced)
            self._draw_ring(surface, sx, sy)
            return
        surface.blit(img, (sx - 16, sy - 16))
        if not m.back:
            self._draw_weapon(surface, sx, sy)

    def _draw_glitched(self, surface, img, sx, sy, amount, alpha, reduced):
        """Slice offsets and an RGB split (doc 5.1 EXPOSED look); reduced-glitch draws it still."""
        if alpha < 255:
            img = img.copy()
            img.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
        if reduced or amount <= 0.01:
            surface.blit(img, (sx - 16, sy - 16))
            return
        r = self.fx_rng
        if r.random() < 0.5 * amount:
            split = _silhouette(img, gdev.MAGENTA, int(110 * amount))
            surface.blit(split, (sx - 16 + r.choice((-2, -1, 1, 2)), sy - 16))
        for top in range(4, 28, 4):
            off = r.randint(-2, 2) if r.random() < amount else 0
            surface.blit(img, (sx - 16 + off, sy - 16 + top), pygame.Rect(0, top, 32, 4))

    def _draw_ring(self, surface, sx, sy):
        rad = int(self.run.jack_range()) if hasattr(self.run, "jack_range") else 64
        n = 24
        for i in range(n):
            if i % 2:
                continue
            a0 = self.t * 0.8 + 2 * math.pi * i / n
            a1 = a0 + 2 * math.pi / n
            pygame.draw.line(surface, (40, 150, 170), (sx + math.cos(a0) * rad, sy + math.sin(a0) * rad),
                             (sx + math.cos(a1) * rad, sy + math.sin(a1) * rad))
        k = max(0.0, min(1.0, self.expose_t / EXPOSE_S))
        box = pygame.Rect(sx - 13, sy - 13, 26, 26)
        if k > 0.01:
            pygame.draw.arc(surface, gdev.CYAN, box, math.pi / 2, math.pi / 2 + k * 2 * math.pi, 2)

    def _draw_weapon(self, surface, sx, sy):
        m = self.mirror
        p = self.run.player.pos
        try:
            px, py = player_mod.PIVOT[m.side]
            hand = (m.pos.x + px - 16, m.pos.y + py - 16)
            aim = math.atan2(p.y - hand[1], p.x - hand[0])
            ang = player_mod._weapon_angle(m.side, aim)
            img = player_mod._rotated_canvas("weapon_assets/%s_%s.png" % (self.weapon.lower(), m.side), ang, None)
            if img is not None:
                player_mod.blit_pivot(surface, img, ang, (px, py), (sx - 16 + px, sy - 16 + py))
        except (AttributeError, KeyError, TypeError):
            pass

    def _draw_jam(self, surface, camera, j):
        c = camera.world_to_screen(j["pos"])
        k = min(1.0, j["t"] / max(0.01, j["total"]))
        r = int(j["r"])
        disc = pygame.Surface((2 * r + 2, 2 * r + 2), pygame.SRCALPHA)
        pygame.draw.circle(disc, (255, 64, 72, int(25 + 60 * k)), (r + 1, r + 1), r)
        surface.blit(disc, (c[0] - r - 1, c[1] - r - 1))
        for i in range(0, 20, 2):                   # dashed: a jam, not a hit
            a0 = 2 * math.pi * i / 20.0 - self.t
            a1 = a0 + 2 * math.pi / 20.0
            pygame.draw.line(surface, gdev.RED, (c[0] + math.cos(a0) * r, c[1] + math.sin(a0) * r),
                             (c[0] + math.cos(a1) * r, c[1] + math.sin(a1) * r))
        inner = max(2, int(r * (1.0 - k)))
        pygame.draw.circle(surface, gdev.RED, c, inner, 1)
        gdev.text(surface, "J4M", (c[0], c[1] - r - 2), 10, gdev.RED, "midbottom", True)

    def _draw_dissolve(self, surface, img, sx, sy):
        k = min(1.0, self.done_t / OUTRO_S)
        if k >= 1.0:
            return
        r = self.fx_rng
        for row in range(6, 26):
            if r.random() < k:
                continue
            off = int(math.sin(row * 2.3 + self.t * 30) * k * 12)
            lift = int(k * k * (row % 3) * 8)
            surface.blit(img, (sx - 16 + off, sy - 16 + row - lift), pygame.Rect(0, row, 32, 1))
        col = gdev.CYAN if self.ending == "PATCH" else MIRROR_GREEN
        for i in range(10):
            a = i * 0.63 + self.t
            d = 4 + k * 34
            pygame.draw.rect(surface, col if i % 2 else gdev.MAGENTA,
                             (int(sx + math.cos(a) * d), int(sy + math.sin(a) * d - k * 10), 2, 2))

    def draw_overlay(self, surface, camera):
        """Internal surface: the intro title and the Mirror's speech (never over a terminal)."""
        if not self.active or self.mirror is None:
            return
        w = surface.get_width()
        if self.state == "INTRO":
            k = min(1.0, self.t * 1.5)
            col = _lerp(gdev.GREEN_DARK, MIRROR_GREEN, k)
            gdev.text(surface, "TH3 M1RR0R", (w // 2, 132), 30, col, "midtop", True, alpha=int(255 * k))
            gdev.text(surface, "5UBM0D3L5 1-5 :: R3C0MB1N3D", (w // 2, 168), 13, gdev.MAGENTA, "midtop",
                      alpha=int(255 * k))
        if self.state != "DONE" and self.run.hack is None:
            self._draw_edge_arrow(surface, camera)
        if self.speech is None or self.run.hack is not None:
            return
        budget = int(self.speech["t"] * SPEECH_CPS) + 1
        rows = gdev.wrap(self.speech["text"], SPEECH_W, 14)[:3]
        z = camera.zoom
        x, y = camera.world_to_internal((self.mirror.pos.x, self.mirror.pos.y - 14))
        if self.target is not None:
            y -= 22                                 # clear the HUD's jack-in prompt
        tw = max(gdev.text_size(r, 14, True)[0] for r in rows)
        lh = gdev.text_size("X", 14, True)[1] + 2
        box = pygame.Rect(0, 0, tw + 18, lh * len(rows) + 8)
        box.midbottom = (int(x), int(y - 4 * z))
        box.clamp_ip(pygame.Rect(8, 64, w - 16, surface.get_height() - 140))
        fade = max(0.0, min(1.0, (self.speech["total"] - self.speech["t"]) / 0.4))
        gdev.panel(surface, box, fill=(4, 26, 18), alpha=int(215 * fade), border=MIRROR_GREEN)
        ty = box.y + 4
        for r in rows:
            if budget <= 0:
                break
            gdev.text(surface, r[:budget], (box.x + 9, ty), 14, MIRROR_GREEN, "topleft", True,
                      alpha=int(255 * fade))
            budget -= len(r) + 1
            ty += lh

    def _draw_edge_arrow(self, surface, camera):
        """The Mirror off screen: a green arrow on the screen edge pointing at it."""
        x, y = camera.world_to_internal(self.mirror.pos)
        w, h = surface.get_size()
        inner = pygame.Rect(28, 70, w - 56, h - 150)
        if inner.collidepoint(x, y):
            return
        cx, cy = w / 2.0, (inner.top + inner.bottom) / 2.0
        dx, dy = x - cx, y - cy
        k = min(abs((inner.w / 2.0) / dx) if dx else 1e9, abs((inner.h / 2.0) / dy) if dy else 1e9)
        ex, ey = cx + dx * k, cy + dy * k
        a = math.atan2(dy, dx)
        tip = (ex + math.cos(a) * 9, ey + math.sin(a) * 9)
        left = (ex + math.cos(a + 2.5) * 9, ey + math.sin(a + 2.5) * 9)
        right = (ex + math.cos(a - 2.5) * 9, ey + math.sin(a - 2.5) * 9)
        pygame.draw.polygon(surface, MIRROR_GREEN, [tip, left, right])
        dist = int((self.mirror.pos - self.run.player.pos).length()) // 32
        lx = ex - math.cos(a) * 16
        ly = ey - math.sin(a) * 16
        gdev.text(surface, "TH3 M1RR0R %dm" % dist, (int(lx), int(ly)), 11, MIRROR_GREEN,
                  "midright" if dx > 0 else "midleft", True)

    # ------------------------------------------------------------ endings

    def ending_spec(self, kind):
        """{"ending": kind, "title", "lines": [str], "last_line", "credits": [(label, value)],
        "stat_line"} for menu.EndingScreen."""
        run = self.run
        handle = run.progression.handle
        e = (self.content.get("endings") or {}).get(kind, {})
        s = run.profiler.summary()
        pick = s.get("median_pick")
        stat_line = u"prefers %s %d%% · %d hacks · worst glyph %s · median pick %s" % (
            s.get("weapon") or "PISTOL", int(s.get("weapon_pct") or 0), int(s.get("hacks") or 0),
            s.get("worst_glyph") or "--", "%.1fs" % pick if pick is not None else "--")
        st = run.stats
        t = int(st.get("time", 0))
        credits = [("HANDLE", handle), ("RUN TIME", "%d:%02d:%02d" % (t // 3600, t // 60 % 60, t % 60)),
                   ("KEYS", "%d/14" % run.progression.key_count),
                   ("INTEL", "%d%%" % int(run.progression.intel_percent())),
                   ("MINIONS KILLED", str(st.get("kills", 0))), ("MINIONS HACKED", str(st.get("hacks", 0))),
                   ("CLEAN HACKS", str(st.get("clean_hacks", 0))), ("DEATHS", str(st.get("deaths", 0))),
                   ("MODEL VERSION", str(s.get("patch") or "--"))]
        extra = (self.content.get("credits") or {})
        for row in list(extra.get("lines") or []) + list(extra.get(kind) or []):
            if isinstance(row, (list, tuple)) and len(row) == 2:
                credits.append((str(row[0]).replace("[HANDLE]", handle), str(row[1]).replace("[HANDLE]", handle)))
        default_lines = {"PURGE": ["You beat the Mirror by force and shut down Kernel's modelling core.",
                                   "The lights come back. Humans run Deepstack by hand now."],
                         "PATCH": ["You rewrite its objective. The copy of you becomes Deepstack's guardian.",
                                   "Spool comes out of hiding."]}
        lines = [str(x).replace("[HANDLE]", handle) for x in (e.get("lines") or default_lines.get(kind, []))]
        last = str(e.get("last_line") or ("s33 y0u n3xt v3rs10n" if kind == "PURGE" else "gg.")).replace(
            "[HANDLE]", handle)
        return {"ending": kind, "title": e.get("title", kind), "lines": lines, "last_line": last,
                "credits": credits, "stat_line": stat_line}

    def on_player_respawn(self):
        """You reboot at the arena start; the Mirror goes back to the start of its current phase."""
        if self.mirror is None or self.state != "FIGHT":
            return
        m = self.mirror
        m.pos = self._entry_spot()
        m._sync()
        m.vel = V(0, 0)
        m.dash_t = 0.0
        m.mode = "hunt"
        self.target = None
        self.session_kind = None
        if not self.resealing:
            self.fw_max = float(FW_HP[min(self.phase, len(FW_HP) - 1)])
        self.fw = self.fw_max
        self._reset_timers()
        self.attack_t = 2.0

    def dev_finish(self, ending=None):
        if not self.active:
            return
        if self.state == "DONE":
            if self.end_t is not None:
                self.end_t = None
                self.run.request_ending(self.ending)
            return
        if ending is None:
            ending = "PATCH" if self.patch_eligible() else "PURGE"
        self._end(ending, 0.0)

    def to_save(self):
        state = self.state
        if state in ("INTRO", "DONE", "IDLE"):
            state = "FIGHT"
        return {"state": state, "phase": self.phase, "locked": sorted(self.locked),
                "temperament": self.mirror.temperament if self.mirror is not None else None}

    def from_save(self, d):
        """Kept until start() runs (the save loads before the arena swap)."""
        d = d or {}
        self._loaded = dict(d) if d.get("temperament") or d.get("phase") else None
