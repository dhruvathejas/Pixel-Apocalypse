"""Daemon boss fights (doc 7): the zone fight lifecycle, the boss loader, the modifier library and
script hooks. Every Daemon is data in bosses/*.json; V3|2D1C7 adds the one-off bosses/verdict.py.
The rack is the boss's body (no boss sprites): its bars are its ports and its cores.

Lifecycle per rack (Boss.state):
    DORMANT       player outside the Influence Zone
    ZONE          player in the zone: waves via the Director; hacks of this rack's minions already
                  cost the firewall points (carry-over, doc 7.2); Key carriers live here too
    SIEGE         fight started (player within fight_trigger_px of the shell and the act is open):
                  firewall up, rack invulnerable, waves + phase attacks
    EXPOSURE      firewall 0: the phase's core bar is hittable for exposure_window_s. A core bar
                  sits inside the shell, so a shot that stops on the shell face in front of it (its
                  "lane", see face_lane) counts too. Broken in time -> next phase; window over ->
                  firewall back to half, the same phase repeats, the core keeps its damage
    BREACH_READY  last core broken: cyan breach port at that bar's spawn_exit; hold F there
    BREACH        a BreachSession is open on this rack
    CORE_DUMP     story scene requested; Key / Exploit granted through the boss_defeated event
    CLEARED       ports dark, zone safe (Key carriers may still re-spawn, doc 5.2)
Times: everything counts in WORLD time (update gets wdt) except what the doc marks real time
(V3|2D1C7's verdict clock and the prediction text), which uses real_dt.
Drawing: draw() is on the world layer (camera.world_to_screen); draw_overlay() is on the internal
surface (camera.world_to_internal).
"""
import glob
import importlib.util
import math
import os

import pygame

import gdev
import hack
import minions as minions_mod
import player as player_mod
import profiler as profiler_mod

V = pygame.math.Vector2

DORMANT = "DORMANT"
ZONE = "ZONE"
SIEGE = "SIEGE"
EXPOSURE = "EXPOSURE"
BREACH_READY = "BREACH_READY"
BREACH = "BREACH"
CORE_DUMP = "CORE_DUMP"
CLEARED = "CLEARED"
FIGHT_STATES = (SIEGE, EXPOSURE, BREACH_READY, BREACH)

BOSS_DIR = "bosses"
ARCHETYPE_KIND = {"B1T": "b1t", "P1NG": "p1ng", "PR0B3": "pr0b3", "F1R3W4LL": "f1r3w4ll", "W0RM": "w0rm"}
FIGHT_TRIGGER_PX = 380
ZONE_FIRST_WAVE_S = 1.2         # after entering the zone
ZONE_WAVE_S = (12.0, 16.0)       # calm between zone packs (world s)
SIEGE_WAVE_S = (9.0, 12.0)       # between fight waves, divided by the phase's spawn_rate
WAVE_RETRY_S = 1.5              # Director said no / no room: ask again this soon
CORRUPTION_SPAWN = 0.2          # waves come up to this much faster at CORRUPTION 100
WAVE_SIZE = {"vent_burst": (2, 3), "trickle": (2, 3), "ring": (2, 4)}
ZONE_CAP_MIN = 3                # zone (pre-fight) cap: max(this, max_alive // 2)
FIRST_ATTACK_S = 4.0            # first phase attack after a fight / phase starts
ATTACK_CADENCE_S = (8.0, 5.5)   # phase 0 .. later phases (world s), 5.5-8 s band
CADENCE_JITTER_S = 0.4
PLAN_LEN = 6                    # attacks planned ahead (visible_queue shows 3, or 5 in the last phase)
KILL_HEAL = 0.0                # each weapon kill in the fight: +1% of the core's HP ...
KILL_HEAL_CAP = 0.10            # ... up to +10% per phase (doc 5.3)
BREACH_FAIL_FIREWALL = 1        # a failed breach regenerates 1 point (doc 7.2 said 2)
PREDICTION_S = 2.2              # PR3D1C710N text after a core hack (real s)
REACH_FREE_PX = 120             # EXPOSURE: a core face this close needs no walk-in time
REACH_MAX_S = 6.0               # ... and the window gets at most this much extra for the walk
LANE_PAD = 6                    # a core's lane reaches this far past the shell face
FIREWALL_ORANGE = (255, 140, 40)
DIALECT_FALLBACK = ["|2", "|<", "|_", "(", "|-|"]   # mirror_dialect before the Codex has data
DIALECT_MIN_W = 0.2             # a dialect glyph thinned below this weight drops out

ATTACK_LABELS = {"radial_burst": "R4D14L BUR5T", "port_beam": "P0RT B34M", "port_overload": "P0RT 0V3RL04D",
                 "packet_rain": "P4CK3T R41N", "blackout": "BL4CK0UT", "firewall_dome": "F1R3W4LL D0M3"}
EXPLOIT_TEXT = {"LEXICON": "the first option set of every hack and breach chunk starts with 1 decoy struck",
                "WIDE_PORT": "jack-in range 64px -> 96px",
                "TRACE_DAMPER": "-25% TRACE per wrong pick: three strikes become four",
                "CHAIN_DAEMON": "OVERLOAD radius +50%",
                "LONG_TURN": "TURN lasts 30s",
                "REMOTE_JACK": "hack a pinned target from range"}


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def _scale(c, k):
    return (min(255, int(c[0] * k)), min(255, int(c[1] * k)), min(255, int(c[2] * k)))


def leet_dir(name):
    """"DOWN-LEFT" -> "D0WN-L3FT" (prediction text, doc 9.5)."""
    return str(name).replace("E", "3").replace("I", "1").replace("O", "0")


def kind_cost(kind):
    return int(minions_mod.KINDS.get(kind, {}).get("cost", 1))


# ---------------------------------------------------------------- loading

def shipped_kinds():
    """Minion archetypes that exist in minions.KINDS (unshipped ones drop out of spawn tables)."""
    return set(minions_mod.KINDS)


def filter_spawn_table(table):
    """{archetype: weight} -> {kind: weight}, unshipped dropped, renormalised (doc 7.1)."""
    have = shipped_kinds()
    out = dict((ARCHETYPE_KIND.get(k, k.lower()), float(w)) for k, w in (table or {}).items()
               if ARCHETYPE_KIND.get(k, k.lower()) in have)
    total = sum(out.values())
    if total <= 0:
        return {"b1t": 1.0}
    return dict((k, w / total) for k, w in out.items())


def load_boss_defs():
    """{boss id: dict} from bosses/*.json (id defaults to the file name)."""
    out = {}
    for f in sorted(glob.glob(gdev.path(BOSS_DIR, "*.json"))):
        d = gdev.load_json(os.path.join(BOSS_DIR, os.path.basename(f)))
        if not isinstance(d, dict):
            print("[bosses] could not read %s" % f)
            continue
        bid = d.get("id") or os.path.basename(f)[:-5]
        d["id"] = bid
        out[bid] = d
    return out


def load_script(rel_path):
    """A boss script module (hooks on_phase / on_tick / on_hack / on_hack_end / hud_queue), or None."""
    if not rel_path:
        return None
    full = gdev.path(rel_path)
    if not os.path.isfile(full):
        print("[bosses] missing script %s" % rel_path)
        return None
    spec = importlib.util.spec_from_file_location("boss_script_" + os.path.basename(full)[:-3], full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def face_lane(rack, bar):
    """(lane, conduit, face) world Rects for a core bar. Bars sit inside the shell, so a shot from
    outside stops on the shell face; the lane is the bar plus the strip of shell between it and the
    face its spawn_exit is on, a little past the face. conduit is that strip without the bar; face
    is a thin rect along the shell edge where shots land."""
    sh, br = rack.shell, bar.rect
    ex, ey = bar.spawn_exit
    if ey >= sh.bottom:
        lane = pygame.Rect(br.left, br.top, br.w, sh.bottom + LANE_PAD - br.top)
        conduit = pygame.Rect(br.left, br.bottom, br.w, max(0, sh.bottom - br.bottom))
        face = pygame.Rect(br.left, sh.bottom - 2, br.w, 4)
    elif ey <= sh.top:
        lane = pygame.Rect(br.left, sh.top - LANE_PAD, br.w, br.bottom - sh.top + LANE_PAD)
        conduit = pygame.Rect(br.left, sh.top, br.w, max(0, br.top - sh.top))
        face = pygame.Rect(br.left, sh.top - 2, br.w, 4)
    elif ex <= sh.left:
        lane = pygame.Rect(sh.left - LANE_PAD, br.top, br.right - sh.left + LANE_PAD, br.h)
        conduit = pygame.Rect(sh.left, br.top, max(0, br.left - sh.left), br.h)
        face = pygame.Rect(sh.left - 2, br.top, 4, br.h)
    else:
        lane = pygame.Rect(br.left, br.top, sh.right + LANE_PAD - br.left, br.h)
        conduit = pygame.Rect(br.right, br.top, max(0, sh.right - br.right), br.h)
        face = pygame.Rect(sh.right - 2, br.top, 4, br.h)
    return lane, conduit, face


# ---------------------------------------------------------------- modifiers

class Modifier(object):
    """Base of the shared modifier library (doc 7.1). Every hook is optional.
    owner is a Boss (or the Mirror, for mirror_dialect). The Run comes from params["run"], or
    owner.run, or owner.mgr.run."""
    id = "base"

    def __init__(self, params, owner):
        self.params = dict(params or {})
        self._run = self.params.pop("run", None)
        self.owner = owner

    @property
    def run(self):
        if self._run is not None:
            return self._run
        r = getattr(self.owner, "run", None)
        if r is None:
            r = getattr(getattr(self.owner, "mgr", None), "run", None)
        return r

    def on_fight_start(self):
        pass

    def on_phase(self, phase_index):
        pass

    def on_tick(self, wdt, real_dt):
        pass

    def on_spawn(self, minion):
        pass

    def on_minion_died(self, minion):
        pass

    def on_minion_hardened(self, minion):
        pass

    def on_hack_start(self, session, minion):
        pass

    def on_hack_finished(self, result, minion):
        pass

    def on_pick(self, data):
        pass

    def on_defeat(self):
        pass

    def wave_groups(self, kinds, tags):
        """Split one wave into [(kinds, tags)] spawn groups, or None to leave it alone."""
        return None

    def hack_context(self, minion):
        return {}

    def attack_params(self, attack_id, params):
        return params

    def hud_queue(self):
        return None

    def draw(self, surface, camera):
        pass


class RespawnFromCache(Modifier):
    """R3C4LL (doc 7.3). Each wave has one original (white 0x plate, drawn by minions.py). A wave
    member killed by a weapon comes back as a cached copy from a port after respawn_delay_s, while
    the wave's original is alive, un-hacked and not HARDENED. Hacking the original flushes the
    cache (every live cached copy of its wave dies, cause "flush"); killing it or a BACKTRACE on it
    only stops the cache. stale_packets: a cached copy's packet is a replay of the last packet
    decoded in this fight (or a fresh one), and pays nothing."""
    id = "respawn_from_cache"

    def __init__(self, params, owner):
        Modifier.__init__(self, params, owner)
        self.waves = {}             # wave_id -> {"open": bool}
        self.pending = []           # [world s left, kind, wave_id]
        self.last_snapshot = None
        self.session = None         # the open session on one of this boss's minions

    def _delay(self):
        return float(self.params.get("respawn_delay_s", 3.0))

    def on_fight_start(self):
        self.last_snapshot = None

    def wave_groups(self, kinds, tags):
        n = max(0, int(self.params.get("originals_per_wave", 1)))
        wid = tags.get("wave_id")
        if wid is None or not kinds:
            return None
        self.waves[wid] = {"open": n > 0}
        if n <= 0:
            return None
        return [(list(kinds[:n]), dict(tags, original=True)), (list(kinds[n:]), dict(tags))]

    def _close(self, wid):
        w = self.waves.get(wid)
        if w is not None:
            w["open"] = False
        self.pending = [p for p in self.pending if p[2] != wid]

    def on_minion_died(self, m):
        wid = getattr(m, "wave_id", None)
        w = self.waves.get(wid)
        if w is None:
            return
        if getattr(m, "original", False):
            if w["open"] and m.dead_cause == "weapon":
                self.run.float_text((m.pos.x, m.pos.y - 14), "C4CH3 0FFL1N3", gdev.AMBER)
            self._close(wid)
            return
        if w["open"] and m.dead_cause == "weapon" and not self.owner.cleared:
            # the respawned copy comes out of its port at respawn_delay_s (the port telegraph included)
            self.pending.append([max(0.0, self._delay() - minions_mod.TELEGRAPH_S), m.kind, wid])
            self.run.float_text((m.pos.x, m.pos.y - 14), "C4CH3D", gdev.MAGENTA)

    def on_minion_hardened(self, m):
        if getattr(m, "original", False):
            m.original = False          # drops its 0x plate
            self._close(getattr(m, "wave_id", None))

    def on_hack_start(self, session, m):
        self.session = session

    def on_hack_finished(self, result, m):
        session, self.session = self.session, None
        ok = result.get("outcome") == "success"
        if ok and not result.get("stale") and not result.get("key_source") and session is not None \
                and hasattr(session, "snapshot"):
            try:
                self.last_snapshot = session.snapshot()
            except Exception as err:          # a replay is a nicety; never break the fight over it
                print("[bosses] snapshot failed: %s" % err)
        wid = getattr(m, "wave_id", None)
        if ok and getattr(m, "original", False) and wid in self.waves:
            flushed = 0
            for x in self.run.minions.live(self.owner.rack.id):
                if x is not m and getattr(x, "wave_id", None) == wid and getattr(x, "cached", False) \
                        and not x.jacked:
                    x.kill("flush")
                    flushed += 1
            self._close(wid)
            self.run.float_text((m.pos.x, m.pos.y - 14), "C4CH3 FLU5H3D" + (" x%d" % flushed if flushed else ""),
                                gdev.CYAN, 1.4)

    def on_tick(self, wdt, real_dt):
        if not self.pending or wdt <= 0:
            return
        boss = self.owner
        for p in self.pending:
            p[0] -= wdt
        due = [p for p in self.pending if p[0] <= 0]
        self.pending = [p for p in self.pending if p[0] > 0]
        for _t, kind, wid in due:
            if not self.waves.get(wid, {}).get("open") or boss.cleared:
                continue
            self.run.minions.spawn_wave(boss.rack.id, [kind], "trickle",
                                        tags={"boss_id": boss.id, "wave_id": wid, "cached": True})

    def on_defeat(self):
        self.pending = []
        for w in self.waves.values():
            w["open"] = False

    def hack_context(self, m):
        # a Key moved onto a cached copy (doc 5.2) must still pay out its Key: not stale
        if getattr(m, "cached", False) and self.params.get("stale_packets", True) and \
                not getattr(m, "key_source", None):
            return {"stale": True, "replay": self.last_snapshot}
        return {}


class CopyPlayerWeapon(Modifier):
    """CR055H41R (doc 7.3): every attack takes on the profiler's top weapon (PISTOL until it has
    data). attacks.py draws the BOW / PISTOL / KATANA variants."""
    id = "copy_player_weapon"

    def weapon(self):
        run = self.run
        w = run.profiler.top_weapon()[0] if run is not None else None
        return w or "PISTOL"

    def attack_params(self, attack_id, params):
        params["weapon"] = self.weapon()
        return params


class AttackDelay(Modifier):
    """|-|34D1NG (doc 7.3): attacks land delay_s after their telegraph (with aim: lead, the
    telegraph is placed where you will be by then)."""
    id = "attack_delay_s"

    def attack_params(self, attack_id, params):
        params["delay_s"] = float(params.get("delay_s", 0.0)) + float(self.params.get("delay_s", 1.0))
        return params


class VisibleQueue(Modifier):
    """V3|2D1C7 (doc 7.3): the next `show` planned attacks (show_last_phase in the last phase) as
    honest world-time countdowns. The plan itself is the Boss's (Boss.plan), so what the HUD
    shows is exactly what will fire."""
    id = "visible_queue"

    def hud_queue(self):
        boss = self.owner
        plan = getattr(boss, "plan", None)
        if not plan or boss.state not in (SIEGE, EXPOSURE):
            return None
        last = boss.phase >= len(boss.cores) - 1
        n = int(self.params.get("show_last_phase", 5) if last else self.params.get("show", 3))
        out = []
        for e in plan[:n]:
            aid = e["entry"]["id"] if isinstance(e["entry"], dict) else e["entry"]
            out.append({"label": ATTACK_LABELS.get(aid, str(aid).upper()), "t": e["t"],
                        "color": gdev.AMBER, "real_time": False})
        return out


class MirrorDialect(Modifier):
    """D14|_3C7 (doc 7.3), reused by the Mirror (8.4). Its packets are leetified with your
    `worst_glyphs` worst Codex glyphs; its decoys come first from your own misread_history.
    Every correct pick that tested a dialect glyph halves that glyph's weight for the rest of the
    fight (thin_on_correct); below DIALECT_MIN_W the glyph drops out. hack_context() returns
    {"dialect": {"worst", "weights", "history", "decoys_from_history", "thin_on_correct"}}."""
    id = "mirror_dialect"

    def __init__(self, params, owner):
        Modifier.__init__(self, params, owner)
        self.weights = {}

    def reset(self):
        self.weights = {}

    def on_fight_start(self):
        self.reset()

    def worst(self):
        run = self.run
        n = int(self.params.get("worst_glyphs", 5))
        glyphs = []
        if run is not None:
            glyphs = list(run.profiler.worst_glyphs(n + len(self.weights)) or [])
        for g in DIALECT_FALLBACK:          # top up so the Daemon always speaks a dialect
            if len(glyphs) >= n + len(self.weights):
                break
            if g not in glyphs:
                glyphs.append(g)
        out = []
        for g in glyphs:
            w = self.weights.setdefault(g, 1.0)
            if w >= DIALECT_MIN_W:
                out.append(g)
            if len(out) >= n:
                break
        return out

    def dialect(self):
        run = self.run
        worst = self.worst()
        hist = []
        if run is not None:
            hist = [(g, r) for g, r in run.codex.misread_history if r][-60:]
        return {"worst": worst, "weights": dict((g, round(self.weights.get(g, 1.0), 3)) for g in worst),
                "history": hist, "decoys_from_history": bool(self.params.get("decoys_from_history", True)),
                "thin_on_correct": bool(self.params.get("thin_on_correct", True))}

    def hack_context(self, minion):
        return {"dialect": self.dialect()}

    def on_pick(self, data):
        if not data.get("correct") or not self.params.get("thin_on_correct", True):
            return
        for g in data.get("glyphs") or []:
            if g in self.weights:
                self.weights[g] *= 0.5


MODIFIERS = dict((c.id, c) for c in (RespawnFromCache, CopyPlayerWeapon, AttackDelay, VisibleQueue, MirrorDialect))


def make_modifier(spec, owner):
    """{"id": ..., params} (or a bare id string) -> Modifier instance (also used by finale.py).
    An optional "run" key in spec gives the modifier its Run when the owner has no .run."""
    if isinstance(spec, str):
        spec = {"id": spec}
    cls = MODIFIERS.get(spec.get("id"))
    if cls is None:
        print("[bosses] unknown modifier %r" % spec.get("id"))
        return None
    return cls(dict((k, v) for k, v in spec.items() if k != "id"), owner)


# ---------------------------------------------------------------- the breach port

class BreachPort(object):
    """Jack-in target at the last core's spawn_exit (doc 7.2 step 3). Duck-types a Minion for Game."""
    kind = "breach_port"

    def __init__(self, boss, pos):
        self.boss = boss
        self.pos = V(pos)
        self.label = "R4CK_%s :: R00T" % boss.rack.id
        self.prompt = "HOLD [F] ROOT BREACH %s" % boss.leet_name
        self.alive = True
        self.hackable = True
        self.jacked = False


# ---------------------------------------------------------------- one Daemon

class Boss(object):
    """Public surface (contract):
      id, d (the JSON dict), rack (world.Rack), leet_name, plain_name, submodel, act, slot
      state (constants above), cleared (property), firewall, firewall_max, phase (index),
      phases (list), cores [{"bar": index, "hp": float, "max": float, "broken": bool}],
      window_t (EXPOSURE seconds left), adaptation (points), locked_chunks set,
      key_glyph_index, exploit, modifiers [Modifier], script (module|None), port (BreachPort|None)
      plan [{"entry": attack entry, "t": world s until it fires}]   the phase attack schedule
      script_state {}   scratch dict for the script (verdict.py)
      objective_text() -> str, objective_point() -> ((x, y), label)|None, hud_info() -> dict
      start_fight(), defeat(), disengage()
    """

    def __init__(self, mgr, d, rack):
        self.mgr = mgr
        self.run = mgr.run
        self.d = d
        self.id = d["id"]
        self.rack = rack
        self.leet_name = d.get("leet_name", self.id.upper())
        self.plain_name = d.get("plain_name", self.leet_name)
        self.submodel = d.get("submodel", "")
        self.slot = int(d.get("slot", 0))
        self.act = int(d.get("act", 1))
        self.firewall_max = int(d.get("firewall_points", 6))
        self.firewall = self.firewall_max
        self.phases = d.get("phases", [])
        self.phase = 0
        bars = d.get("core_bars")
        if bars is None:
            bars = list(range(len(rack.bars)))
        hp = float(d.get("core_hp", 24))
        self.cores = []
        for i, b in enumerate([b for b in bars if 0 <= b < len(rack.bars)]):
            ph = self.phases[i] if i < len(self.phases) else {}
            m = float(ph.get("core_hp", hp))
            self.cores.append({"bar": b, "hp": m, "max": m, "broken": False})
        self.window_t = 0.0
        self.window_full = 0.0          # this EXPOSURE's whole window (exposure_window_s + the walk)
        self.exposed_core = None        # index into cores of the core this EXPOSURE lit
        self.last_broken = None         # index of the core broken last (the breach port's bar)
        self.adaptation = 0
        self.locked_chunks = set()
        cd = d.get("core_dump", {})
        self.key_glyph_index = cd.get("key_glyph_index")
        self.exploit = cd.get("exploit")
        self.spawn_table = filter_spawn_table(d.get("spawn_table"))
        self.max_alive = int(d.get("max_alive", 12))
        self.pattern = d.get("spawn_pattern", "vent_burst")
        self.modifiers = [m for m in (make_modifier(s, self) for s in d.get("modifiers", [])) if m]
        self.script = load_script(d.get("script"))
        self.script_state = {}
        self.state = DORMANT
        self.port = None
        self.wave_t = ZONE_FIRST_WAVE_S
        self.wave_n = 0
        self.plan = []
        self.attack_i = 0
        self.heal_used = 0.0
        self.patch_card = None
        self.prediction = None          # text, shown for prediction_t real seconds
        self.prediction_t = 0.0
        self._hits_seen = 0
        self._tracked = []              # live player shots last frame (for face-lane hits)
        self._slashes = set()           # id() of katana slashes already counted on the core

    @property
    def cleared(self):
        return self.state == CLEARED

    @property
    def in_fight(self):
        return self.state in FIGHT_STATES

    def fight_allowed(self):
        return self.run.progression.act >= max(1, self.act)

    def owns(self, minion):
        return minion is not None and getattr(minion, "boss_id", None) == self.id

    def on_rack(self, minion):
        return minion is not None and getattr(minion, "rack_id", None) == self.rack.id

    def speaks_for(self, minion):
        """Its own minions, plus anything from its rack while it is not cleared (Key carriers come
        out of its ports untagged): the hack hooks (verdict clock, dialect) apply to them."""
        return self.owns(minion) or (not self.cleared and self.on_rack(minion) and
                                     getattr(minion, "team", "enemy") == "enemy")

    # ------------------------------------------------------------ hooks fan-out

    def _mods(self, hook, *args):
        for m in self.modifiers:
            getattr(m, hook)(*args)

    def _script(self, hook, *args):
        fn = getattr(self.script, hook, None) if self.script is not None else None
        if fn is not None:
            return fn(self, *args)
        return None

    # ------------------------------------------------------------ cores

    def core_index(self):
        """Index into cores of the exposed core during EXPOSURE, else the current phase's core
        (or None once every core is broken)."""
        if not self.cores:
            return None
        e = self.exposed_core
        if e is not None and 0 <= e < len(self.cores) and not self.cores[e]["broken"]:
            return e
        ph = self.phases[self.phase] if self.phase < len(self.phases) else {}
        i = int(ph.get("core", self.phase))
        if 0 <= i < len(self.cores) and not self.cores[i]["broken"]:
            return i
        for j, c in enumerate(self.cores):
            if not c["broken"]:
                return j
        return None

    def core(self):
        i = self.core_index()
        return self.cores[i] if i is not None else None

    def core_bar(self):
        c = self.core()
        return self.rack.bars[c["bar"]] if c is not None else None

    def _phase_spec(self):
        if not self.phases:
            return {}
        return self.phases[min(self.phase, len(self.phases) - 1)]

    def _sync_phase(self):
        """phase = how many cores are broken (EXPOSURE lights whichever whole core is nearest
        the player, so the phases advance in order, not by bar)."""
        n = len([c for c in self.cores if c["broken"]])
        self.phase = max(0, min(n, len(self.cores) - 1))

    def _nearest_core(self):
        """(index, distance) of the unbroken core whose face is nearest the player."""
        p = self.run.player.pos
        best, best_d = None, None
        for i, c in enumerate(self.cores):
            if c["broken"]:
                continue
            face = V(face_lane(self.rack, self.rack.bars[c["bar"]])[2].center)
            d = (face - p).length()
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best, (best_d or 0.0)

    def _half_firewall(self):
        return int(math.ceil(self.firewall_max / 2.0))

    def _set_hittable(self, bar, on):
        if bar is None:
            return
        bar.hittable = bool(on)
        # world.projectile_hit may honour this (a hittable bar catching shots on its face lane);
        # until it does, _core_damage counts shell hits inside the lane itself
        bar.hit_rect = face_lane(self.rack, bar)[0] if on else None

    def _unhittable_all(self):
        for b in self.rack.bars:
            if b.hittable:
                self._set_hittable(b, False)

    # ------------------------------------------------------------ update

    def update(self, wdt, real_dt):
        run = self.run
        if self.state in (CLEARED, CORE_DUMP):
            return
        if self.prediction_t > 0:
            self.prediction_t = max(0.0, self.prediction_t - real_dt)
        p = run.player
        in_zone = not p.dead and self.rack.in_zone(p.pos)
        if self.state == DORMANT:
            if not in_zone:
                return
            self.state = ZONE
            self.wave_t = ZONE_FIRST_WAVE_S
        if self.state == ZONE:
            if not in_zone:
                self.state = DORMANT
                return
            self._tick_hooks(wdt, real_dt)
            self._waves(wdt, zone=True)
            if self._may_start():
                self.start_fight()
            return
        # fight states
        if self.state in (SIEGE, EXPOSURE) and not in_zone and not p.dead:
            self.disengage()                         # left the Influence Zone mid-fight
            return
        self._tick_hooks(wdt, real_dt)
        if in_zone:
            self._waves(wdt, zone=False)
        if self.state == SIEGE:
            self._attacks(wdt)
            if self.firewall <= 0:
                if self.core() is not None:
                    self._begin_exposure()
                else:
                    self._open_breach()
        elif self.state == EXPOSURE:
            self._attacks(wdt)
            self._core_damage()
            c = self.core()
            if c is not None and c["hp"] <= 0:
                self._break_core()
                return
            self.window_t -= wdt
            if self.window_t <= 0:
                self._end_exposure()

    def _tick_hooks(self, wdt, real_dt):
        self._mods("on_tick", wdt, real_dt)
        self._script("on_tick", wdt, real_dt)

    def _may_start(self):
        if not self.fight_allowed():
            return False
        if self.mgr.active is not None and self.mgr.active is not self:
            return False
        trigger = float(self.d.get("fight_trigger_px", FIGHT_TRIGGER_PX))
        return self.rack.shell.inflate(2 * trigger, 2 * trigger).collidepoint(self.run.player.pos)

    # ------------------------------------------------------------ waves

    def _zone_cap(self):
        return max(ZONE_CAP_MIN, self.max_alive // 2)

    def _weights(self):
        """Spawn table with the active Patch Notes counters applied (doc 9.4), renormalised."""
        w = dict(self.spawn_table)
        eff = self.run.profiler.patches.effects()
        for counter, kind in (("FIREWALL_WEIGHT", "f1r3w4ll"), ("MORE_PROBES", "pr0b3"), ("FAST_KITE", "p1ng")):
            v = eff.get(counter)
            if kind in w and isinstance(v, (int, float)):
                w[kind] *= float(v)
        total = sum(w.values()) or 1.0
        return sorted((k, v / total) for k, v in w.items())

    def roll_kinds(self, n):
        rng = self.run.rng
        table = self._weights()
        out = []
        for _ in range(int(n)):
            r = rng.random()
            for k, w in table:
                r -= w
                if r <= 0:
                    out.append(k)
                    break
            else:
                out.append(table[-1][0] if table else "b1t")
        return out

    def _wave_ports(self, pattern):
        """vent_burst: 1-2 ports nearest the player; ring / trickle: let minions.py choose."""
        if pattern != "vent_burst" or not self.rack.bars:
            return None
        p = self.run.player.pos
        near = sorted(range(len(self.rack.bars)),
                      key=lambda i: (V(self.rack.bars[i].spawn_exit) - p).length_squared())
        return near[:self.run.rng.choice((1, 2))]

    def _waves(self, wdt, zone):
        run = self.run
        if run.player.dead or wdt <= 0:
            return
        self.wave_t -= wdt
        if self.wave_t > 0:
            return
        # doc 3: CORRUPTION also drives the spawn rate (x1 at 0 .. x1.5 at 100)
        corrupt = 1.0 + CORRUPTION_SPAWN * max(0.0, min(100.0, run.progression.corruption)) / 100.0
        if zone:
            self.wave_t = run.rng.uniform(*ZONE_WAVE_S) / corrupt
        else:
            rate = float(self._phase_spec().get("spawn_rate", 1.0))
            self.wave_t = run.rng.uniform(*SIEGE_WAVE_S) / max(0.25, rate * corrupt)
        cap = self._zone_cap() if zone else self.max_alive
        room = cap - len(run.minions.live(self.rack.id)) - run.minions.pending(self.rack.id)
        lo, hi = WAVE_SIZE.get(self.pattern, (3, 5))
        if self.pattern == "ring":
            n = max(1, len(self.rack.bars))
            size = n * run.rng.choice((1, 2)) if hi >= 2 * n else n
        else:
            size = run.rng.randint(lo, hi)
        size = min(size, room)
        if size < 2:
            self.wave_t = min(self.wave_t, WAVE_RETRY_S)
            return
        kinds = self.roll_kinds(size)
        if not run.director.request_spawn(sum(kind_cost(k) for k in kinds)):
            self.wave_t = min(self.wave_t, WAVE_RETRY_S)
            return
        self.spawn_wave(kinds, self.pattern)

    def spawn_wave(self, kinds, pattern=None, ports=None):
        """One boss wave through minions.spawn_wave, tagged boss_id + wave_id; modifiers may split
        it into groups (respawn_from_cache's original). Returns how many were queued."""
        pattern = pattern or self.pattern
        self.wave_n += 1
        tags = {"boss_id": self.id, "wave_id": "%s:%d" % (self.id, self.wave_n)}
        groups = [(list(kinds), tags)]
        for m in self.modifiers:
            g = m.wave_groups(groups[0][0], groups[0][1]) if len(groups) == 1 else None
            if g:
                groups = g
        if ports is None:
            ports = self._wave_ports(pattern)
        n = 0
        for ks, tg in groups:
            if ks:
                n += self.run.minions.spawn_wave(self.rack.id, ks, pattern, ports, tg)
        return n

    # ------------------------------------------------------------ phase attacks

    def _cadence(self):
        spec = self._phase_spec()
        if spec.get("attack_cadence_s"):
            base = float(spec["attack_cadence_s"])
        else:
            n = max(1, len(self.cores) - 1)
            k = min(1.0, self.phase / float(n))
            base = ATTACK_CADENCE_S[0] + (ATTACK_CADENCE_S[1] - ATTACK_CADENCE_S[0]) * k
        return max(1.5, base + self.run.rng.uniform(-CADENCE_JITTER_S, CADENCE_JITTER_S))

    def _reset_plan(self, first=FIRST_ATTACK_S):
        self.plan = []
        self.attack_i = 0
        self._fill_plan(first)

    def _fill_plan(self, first=None):
        atks = self._phase_spec().get("attacks", [])
        if not atks:
            return
        while len(self.plan) < PLAN_LEN:
            if self.plan:
                t = self.plan[-1]["t"] + self._cadence()
            else:
                t = first if first is not None else self._cadence()
            self.plan.append({"entry": atks[self.attack_i % len(atks)], "t": t})
            self.attack_i += 1

    def _attacks(self, wdt):
        if wdt <= 0 or self.run.player.dead:
            return
        for e in self.plan:
            e["t"] -= wdt
        while self.plan and self.plan[0]["t"] <= 0:
            self.fire(self.plan.pop(0)["entry"])
        self._fill_plan()

    def nearest_bar(self, pos=None):
        if not self.rack.bars:
            return None
        p = V(pos) if pos is not None else self.run.player.pos
        return min(self.rack.bars, key=lambda b: (V(b.spawn_exit) - p).length_squared())

    def fire(self, entry):
        """Spawn one attack (a name, or {"id", ...params}) through run.attacks, owner = this boss."""
        run = self.run
        if isinstance(entry, dict):
            aid = entry.get("id")
            params = dict((k, v) for k, v in entry.items() if k != "id")
        else:
            aid, params = entry, {}
        bar = self.core_bar() if aid == "port_beam" and self.state == EXPOSURE else None
        bar = bar or self.nearest_bar()
        params.setdefault("aim", "player")
        params.setdefault("rack", self.rack)
        params.setdefault("bar", bar)
        params.setdefault("predict", True)
        params.setdefault("damage", 1)
        if aid == "port_overload":
            params.setdefault("kinds", self.roll_kinds(len(self.rack.bars)))
            params.setdefault("tags", {"boss_id": self.id})
        for m in self.modifiers:
            params = m.attack_params(aid, params) or params
        if aid in ("packet_rain", "blackout"):
            origin = V(run.player.pos)
        elif bar is not None:
            origin = V(bar.spawn_exit)
        else:
            origin = V(self.rack.shell.center)
        return run.attacks.spawn(aid, origin, params, owner=self)

    # ------------------------------------------------------------ EXPOSURE

    def _begin_exposure(self):
        i, dist = self._nearest_core()
        if i is None:
            self._open_breach()
            return
        self.exposed_core = i               # the nearest whole core lights, not a fixed bar
        bar = self.core_bar()
        self.firewall = 0
        self.state = EXPOSURE
        # the window is for shooting, so the walk to the face is added on top (capped)
        walk = max(0.0, dist - REACH_FREE_PX) / player_mod.SPEED
        self.window_t = float(self.d.get("exposure_window_s", 10)) + min(REACH_MAX_S, walk)
        self.window_full = self.window_t
        self._set_hittable(bar, True)
        self._hits_seen = bar.hits
        self._tracked = []
        self._slashes = set()
        self.run.toast("%s :: C0R3 3XP0S3D :: shoot its glowing port" % self.leet_name, gdev.MAGENTA, 2.6)
        self.run.shake(4, 0.25)
        self.run.emit("boss_phase", boss=self, phase=self.phase, state=EXPOSURE)

    def _core_damage(self):
        """This frame's damage to the exposed core: bar.hits delta (shots world.projectile_hit
        stopped on the bar), shots that stopped on the shell inside the core's face lane, and
        katana slashes touching the lane (slashes never reach projectile_hit), once each."""
        c = self.core()
        bar = self.core_bar()
        if c is None or bar is None:
            return
        dmg = max(0, bar.hits - self._hits_seen)
        self._hits_seen = bar.hits
        lane = face_lane(self.rack, bar)[0]
        catch = lane.inflate(10, 10)
        for p in self._tracked:
            wh = p.world_hit
            if not p.alive and wh and wh[0] == "shell" and wh[1] is self.rack and catch.collidepoint(p.pos):
                dmg += max(1, int(p.damage)) * getattr(p, "core_mult", 1)
        shots = [p for p in self.run.player.projectiles if p.team == "player" and p.alive]
        self._tracked = [p for p in shots if p.kind != "slash"]
        live_slashes = set()
        for p in shots:
            if p.kind != "slash":
                continue
            live_slashes.add(id(p))
            if id(p) not in self._slashes and p.rect.colliderect(lane):
                self._slashes.add(id(p))
                dmg += max(1, int(p.damage))
        self._slashes &= live_slashes
        if dmg > 0:
            c["hp"] = max(0.0, c["hp"] - dmg)
            bar.flash = 0.12
            face = face_lane(self.rack, bar)[2]
            self.run.float_text(face.center, "-%d" % dmg, gdev.MAGENTA, 0.5)

    def _end_exposure(self):
        """Window over before the core broke: firewall back to half, the same phase repeats."""
        self._set_hittable(self.core_bar(), False)
        self.exposed_core = None
        self.window_t = 0.0
        self.firewall = self._half_firewall()
        self.state = SIEGE
        self.run.toast("%s :: F1R3W4LL R3G3N3R4T3D (%d)" % (self.leet_name, self.firewall), gdev.AMBER, 2.4)
        self.run.emit("boss_phase", boss=self, phase=self.phase, state=SIEGE)

    def _break_core(self):
        run = self.run
        ci = self.core_index()
        c = self.cores[ci]
        bar = self.rack.bars[c["bar"]]
        self._set_hittable(bar, False)
        c["hp"] = 0.0
        c["broken"] = True
        self.exposed_core = None
        self.last_broken = ci
        self.window_t = 0.0
        run.attacks.clear(self)
        run.shake(7, 0.35)
        run.emit("boss_core_broken", boss=self, core=ci)
        self._show_prediction()
        left = len([x for x in self.cores if not x["broken"]])
        run.toast("%s :: C0R3 %d/%d BR0K3N" % (self.leet_name, len(self.cores) - left, len(self.cores)),
                  gdev.CYAN, 2.6)
        if left == 0:
            self._open_breach()
            return
        self._sync_phase()                       # the next phase whose core is still whole
        self.firewall = self._half_firewall()
        self.heal_used = 0.0
        self.state = SIEGE
        self._reset_plan()
        self._mods("on_phase", self.phase)
        self._script("on_phase", self.phase)
        run.emit("boss_phase", boss=self, phase=self.phase, state=SIEGE)

    def _show_prediction(self):
        guess = self.run.profiler.predictor.predict()
        if not guess:
            return
        tok, prob = guess
        if tok is None:
            return
        name = profiler_mod.DIR_NAMES[int(tok) % 8]
        self.prediction = "PR3D1C710N: %s (%d%%)" % (leet_dir(name), int(round(100 * prob)))
        self.prediction_t = PREDICTION_S

    # ------------------------------------------------------------ transitions

    def start_fight(self):
        if self.state in FIGHT_STATES or self.cleared:
            return
        run = self.run
        self.state = SIEGE
        self.wave_t = 0.5
        self.heal_used = 0.0
        self.mgr.active = self
        self._sync_phase()
        self._reset_plan()
        self._mods("on_fight_start")
        self._mods("on_phase", self.phase)
        self._script("on_phase", self.phase)
        self.patch_card = run.profiler.patches.on_fight_start(self)
        run.emit("boss_fight_started", boss=self, card=self.patch_card)
        run.emit("boss_phase", boss=self, phase=self.phase, state=SIEGE)

    def _open_breach(self):
        """Every core broken: the last core bar becomes the breach port (cyan ring at its exit)."""
        self._unhittable_all()
        for c in self.cores:
            c["broken"] = True
            c["hp"] = 0.0
        self.firewall = 0
        self.window_t = 0.0
        self.plan = []
        self.run.attacks.clear(self)
        self.exposed_core = None
        bar = self.port_bar()
        pos = bar.spawn_exit if bar is not None else self.rack.shell.midbottom
        self.port = BreachPort(self, pos)
        self.state = BREACH_READY
        self.mgr.active = self
        self.run.toast("%s :: R00T P0RT 0P3N :: hold [F] at the cyan ring" % self.leet_name, gdev.CYAN, 3.0)
        self.run.emit("boss_phase", boss=self, phase=self.phase, state=BREACH_READY)

    def port_bar(self):
        """The bar the breach port sits on: the core broken last (the last core bar after a load)."""
        if self.cores:
            i = self.last_broken if self.last_broken is not None else len(self.cores) - 1
            return self.rack.bars[self.cores[i]["bar"]]
        return self.rack.bars[0] if self.rack.bars else None

    def breach_spec(self):
        chunks = list(self.d.get("password_chunks") or [self.d.get("password_word", "ROOT")])
        pre = set(i for i in self.locked_chunks if 0 <= i < len(chunks))
        pid = self.d.get("password_intel_id")
        prestrike = False
        intel = self.run.intel
        if pid and intel.has(pid) and not (intel.entry(pid) or {}).get("redacted"):
            pre |= set(range(int(math.ceil(len(chunks) / 2.0))))
            prestrike = True
        return {"title": "R00T BR34CH :: %s" % self.leet_name, "password_word": self.d.get("password_word", ""),
                "chunks": chunks, "presolved": sorted(pre), "prestrike": prestrike,
                "level": self.run.tuner.breach_level(), "mode": "normal", "boss_id": self.id}

    def on_breach_result(self, result):
        out = result.get("outcome")
        self.locked_chunks |= set(int(i) for i in result.get("locked", []) or [])
        if out == "success":
            self.defeat()
        elif out == "backtrace":
            # doc 7.2: the firewall regenerates 2 points; a short SIEGE, then the port reopens
            self.firewall = BREACH_FAIL_FIREWALL
            self.state = SIEGE
            self.port = None
            self.adaptation += 1
            self.wave_t = min(self.wave_t, 1.0)
            self._reset_plan()
            self.run.emit("boss_phase", boss=self, phase=self.phase, state=SIEGE)
        else:
            self.state = BREACH_READY

    def defeat(self):
        run = self.run
        self.state = CORE_DUMP
        self.port = None
        self.plan = []
        self._unhittable_all()
        self._mods("on_defeat")
        self.script_state.clear()
        run.minions.despawn_rack(self.rack.id)
        run.attacks.clear(self)
        card = run.profiler.patches.on_core_dump(self)
        run.emit("boss_defeated", boss=self)
        run.request_scene(self.core_dump_scene(card))
        self.state = CLEARED
        if self.mgr.active is self:
            self.mgr.active = None

    def core_dump_scene(self, card):
        """Scene spec for menu.TerminalScene (see game.py / the contract)."""
        cd = self.d.get("core_dump", {})
        content = self.mgr.content.get("core_dumps", {}).get(cd.get("text_id"), {})
        stat = self.run.profiler.stat_line(cd.get("stat", "heatmap"))
        head = content.get("header") or "%s :: SUBM0D3L %d/5 [%s]" % (self.leet_name, self.slot, self.submodel)
        lines = list(content.get("lines") or ["SUBM0D3L %d/5 [%s] :: D4T4 C0RRUPT3D." % (self.slot, self.submodel)])
        handle = self.run.handle
        lines = [str(s).replace("[HANDLE]", handle) for s in lines]
        kernel = str(content.get("kernel", "...")).replace("[HANDLE]", handle)
        exploit = str(self.exploit or "")
        card = card or {}
        pages = [{"head": head, "lines": lines, "color": "magenta"},
                 {"head": "KERNEL", "lines": [kernel], "color": "green"},
                 {"head": "Y0UR PR0F1L3 :: %s" % self.submodel, "lines": [stat], "color": "amber", "kind": "stats"},
                 {"head": "KEY RECOVERED", "lines": [], "color": "cyan", "kind": "key"},
                 {"head": "EXPLOIT UNLOCKED", "lines": [exploit.replace("_", " "), EXPLOIT_TEXT.get(exploit, "")],
                  "color": "cyan", "kind": "exploit"},
                 {"head": "PATCH NOTES %s" % card.get("version", ""), "lines": list(card.get("lines", [])) +
                  [card.get("sign", "")], "color": "magenta", "kind": "patch"}]
        return {"kind": "core_dump", "title": "C0R3 DUMP :: %s" % self.leet_name, "pages": pages,
                "key_index": self.key_glyph_index, "on_close": "resume", "boss_id": self.id}

    def disengage(self):
        """Player died or left the zone mid-fight: back to DORMANT. Broken cores, locked chunks and
        the firewall are kept as they are (doc 7.2 has no regen for leaving). Leaving during
        EXPOSURE loses the window, which is the window-closed rule: the firewall regenerates to
        half. An open breach port stays open (not handled here)."""
        if self.state in (SIEGE, EXPOSURE):
            if self.state == EXPOSURE:
                self.firewall = max(self.firewall, self._half_firewall())
            self._set_hittable(self.core_bar(), False)
            self.exposed_core = None
            self.state = DORMANT
            self.window_t = 0.0
            self.plan = []
            self.run.attacks.clear(self)
            if self.mgr.active is self:
                self.mgr.active = None
            self.run.emit("boss_phase", boss=self, phase=self.phase, state=DORMANT)

    def saved_firewall(self):
        """The firewall a save stores: a save made mid-EXPOSURE loads like a disengage (the
        window is lost, so the firewall is back at half), never as a free EXPOSURE."""
        if self.state == EXPOSURE:
            return max(self.firewall, self._half_firewall())
        return self.firewall

    def force_dormant(self):
        """Dev / tests: stop whatever this boss is doing (keeps its progress)."""
        if self.cleared:
            return
        self.disengage()
        self.port = None
        self._unhittable_all()
        if self.state in (BREACH_READY, BREACH, ZONE):
            self.state = DORMANT
        if self.mgr.active is self:
            self.mgr.active = None

    # ------------------------------------------------------------ events

    def on_event(self, name, data):
        if self.cleared:
            return
        run = self.run
        if name == "hack_finished" and data.get("provider") == "minion":
            m = data.get("target")
            res = data.get("result") or {}
            ok = res.get("outcome") == "success"
            if self.on_rack(m):
                if ok and not res.get("stale"):
                    self.firewall = max(0, self.firewall - 1)
                    if self.in_fight:
                        self._show_prediction()
                elif res.get("outcome") == "backtrace":
                    self.adaptation += 1
            if self.speaks_for(m):
                self._mods("on_hack_finished", res, m)
                self._script("on_hack_end", res, m)
        elif name == "hack_started" and data.get("provider") == "minion":
            m = data.get("target")
            if self.speaks_for(m):
                self._mods("on_hack_start", data.get("session"), m)
                self._script("on_hack", data.get("session"), m)
        elif name == "pick":
            s = data.get("session")
            if self.speaks_for(getattr(s, "minion", None)):
                self._mods("on_pick", data)
        elif name == "minion_spawned":
            if self.owns(data.get("minion")):
                self._mods("on_spawn", data["minion"])
        elif name == "minion_died":
            if self.owns(data.get("minion")):
                self._mods("on_minion_died", data["minion"])
        elif name == "minion_hardened":
            if self.owns(data.get("minion")):
                self._mods("on_minion_hardened", data["minion"])
        elif name == "minion_killed":
            if self.state in (SIEGE, EXPOSURE) and self.on_rack(data.get("minion")):
                self._kill_heal()
        elif name == "probe_uploaded":
            if self.on_rack(data.get("minion")):
                self.adaptation += 1

    def _kill_heal(self):
        """doc 5.3: each weapon kill in the fight adds 1% of the core's HP, capped at +10% per phase."""
        c = self.core()
        if c is None:
            return
        room = KILL_HEAL_CAP * c["max"] - self.heal_used
        amt = min(KILL_HEAL * c["max"], room, c["max"] - c["hp"])
        if amt <= 0:
            return
        c["hp"] += amt
        self.heal_used += amt

    def on_player_respawn(self):
        """Death during a fight: disengage. Broken cores and locked chunks are kept."""
        if self.state in (SIEGE, EXPOSURE):
            self.disengage()
        elif self.state in (BREACH, BREACH_READY):
            self.state = BREACH_READY
        elif self.state == ZONE:
            self.state = DORMANT
        self.script_state.pop("entry", None)
        if self.mgr.active is self and self.state != BREACH_READY:
            self.mgr.active = None

    # ------------------------------------------------------------ HUD / objective

    def objective_text(self):
        if self.state == SIEGE:
            return "OBJECTIVE  hack %s's minions to drop its FIREWALL  (%d/%d left)" % (
                self.leet_name, self.firewall, self.firewall_max)
        if self.state == EXPOSURE:
            c = self.core() or {"hp": 0, "max": 1}
            return "OBJECTIVE  CORE EXPOSED: shoot the glowing port  %d%%  %.1fs" % (
                int(100 * c["hp"] / max(0.01, c["max"])), max(0.0, self.window_t))
        if self.state == BREACH_READY:
            return "OBJECTIVE  hold [F] at the cyan breach port: ROOT BREACH %s" % self.leet_name
        if self.state == BREACH:
            return "OBJECTIVE  ROOT BREACH %s: read the password" % self.leet_name
        return "OBJECTIVE  %s" % self.leet_name

    def objective_point(self):
        if self.port is not None:
            return (tuple(self.port.pos), "BREACH PORT")
        if self.state == EXPOSURE and self.core_bar() is not None:
            return (tuple(face_lane(self.rack, self.core_bar())[2].center), "C0R3")
        return None

    def hud_queue(self):
        out = []
        q = self._script("hud_queue")
        if q:
            out.extend(q)
        for m in self.modifiers:
            q = m.hud_queue()
            if q:
                out.extend(q)
        return out or None

    def hud_info(self):
        return {"name": self.leet_name, "plain": self.plain_name,
                "subtitle": "SUBM0D3L %d/5 [%s]" % (self.slot, self.submodel),
                "state": self.state, "firewall": self.firewall, "firewall_max": self.firewall_max,
                "cores": [{"hp": c["hp"], "max": c["max"], "broken": c["broken"]} for c in self.cores],
                "phase": self.core_index() if self.core_index() is not None else self.phase,
                "phases": len(self.cores) or len(self.phases),
                "window": self.window_t if self.state == EXPOSURE else None,
                "window_total": self.window_full or float(self.d.get("exposure_window_s", 10)),
                "queue": self.hud_queue(), "adaptation": self.adaptation,
                "prediction": self.prediction if self.prediction_t > 0 else None}

    # ------------------------------------------------------------ drawing (world layer)

    def draw(self, surface, camera, hacking=False):
        rk = self.rack
        view = camera.view_rect
        if not rk.shell.inflate(160, 160).colliderect(view):
            return
        ox, oy = camera.offset
        t = self.run.time
        if self.state == CLEARED:
            for b in rk.bars:
                self._draw_dark_bar(surface, b, ox, oy, view, crack=False)
            return
        if not hacking and self.firewall > 0 and self.state in (ZONE, SIEGE):
            self._draw_shimmer(surface, ox, oy, view, t)
        for c in self.cores:
            if c["broken"]:
                self._draw_dark_bar(surface, rk.bars[c["bar"]], ox, oy, view, crack=True)
        if self.state == EXPOSURE and self.core_bar() is not None:
            self._draw_lane(surface, self.core_bar(), ox, oy, t, gdev.MAGENTA, self.core())
        if self.port is not None:
            bar = self.port_bar()
            if bar is not None:
                self._draw_lane(surface, bar, ox, oy, t, gdev.CYAN, None)
            self._draw_port(surface, camera, t)
        for m in self.modifiers:
            m.draw(surface, camera)

    def _draw_shimmer(self, surface, ox, oy, view, t):
        """Firewall up: orange scan lines drift across the shell, stronger with more points left."""
        rk = self.rack
        clip = rk.shell.clip(view)
        if clip.w <= 0 or clip.h <= 0:
            return
        k = self.firewall / float(max(1, self.firewall_max))
        pulse = 0.5 + 0.5 * math.sin(t * 3.0)
        strength = (1.0 if self.state == SIEGE else 0.55) * k
        col = _mix(rk.color, FIREWALL_ORANGE, 0.10 + 0.22 * strength * (0.6 + 0.4 * pulse))
        sc = clip.move(-ox, -oy)
        old = surface.get_clip()
        surface.set_clip(sc)
        step = 18
        shift = int(t * 22) % step
        # diagonal lines, anchored to world coordinates so they do not swim with the camera
        x0 = sc.left - sc.h - ((clip.left + clip.top) % step) + shift
        for x in range(x0, sc.right + step, step):
            pygame.draw.line(surface, col, (x, sc.bottom), (x + sc.h, sc.top))
        surface.set_clip(old)
        edge = _mix(rk.color, FIREWALL_ORANGE, 0.35 + 0.45 * strength * pulse)
        pygame.draw.rect(surface, edge, rk.shell.move(-ox, -oy), 2 if self.state == SIEGE else 1)
        label = "%s :: F1R3W4LL %d/%d" % (self.leet_name, self.firewall, self.firewall_max)
        pos = (rk.shell.left + 10 - ox, rk.shell.top + 22 - oy)
        if view.collidepoint(rk.shell.left + 10, rk.shell.top + 22):
            gdev.text(surface, label, pos, 10, edge, bold=True)

    def _draw_dark_bar(self, surface, b, ox, oy, view, crack):
        """A port gone dark (cleared rack) or a broken core (dark with a magenta crack)."""
        if not b.rect.colliderect(view.inflate(16, 16)):
            return
        br = b.rect.move(-ox, -oy)
        dark = _scale(self.rack.color, 0.45)
        pygame.draw.rect(surface, _scale(self.rack.color, 0.7), br.inflate(6, 6))
        pygame.draw.rect(surface, dark, br)
        pygame.draw.rect(surface, _scale(self.rack.color, 0.9), br, 1)
        if crack:
            col = _mix(dark, gdev.MAGENTA, 0.45)
            pts = []
            n = max(3, br.h // 40)
            for i in range(n + 1):
                x = br.centerx + (br.w // 4 if i % 2 else -br.w // 4)
                pts.append((x, br.top + br.h * i // n))
            pygame.draw.lines(surface, col, False, pts, 1)

    def _draw_lane(self, surface, bar, ox, oy, t, color, core):
        """The exposed core's conduit to the shell face (where shots land) and its HP strip;
        cyan for the breach port's conduit."""
        _lane, conduit, face = face_lane(self.rack, bar)
        hot = 0.5 + 0.5 * math.sin(t * 7.0)
        if conduit.w > 0 and conduit.h > 0:
            cr = conduit.move(-ox, -oy)
            deep = _scale(color, 0.22)
            pygame.draw.rect(surface, _mix(deep, color, 0.08 + 0.12 * hot), cr)
            pygame.draw.rect(surface, _scale(color, 0.55), cr, 1)
            # data pulses flowing out toward the face
            rung = _mix(deep, color, 0.55)
            vertical = conduit.h >= conduit.w
            span = conduit.h if vertical else conduit.w
            off = int(t * 60) % 12
            out_dir = 1
            sh = self.rack.shell
            ex, ey = bar.spawn_exit
            if (vertical and ey <= sh.top) or (not vertical and ex <= sh.left):
                out_dir = -1
            for s in range(0, span, 12):
                d = (s + off) % span if out_dir > 0 else (span - (s + off) % span)
                if vertical:
                    pygame.draw.line(surface, rung, (cr.left + 2, cr.top + d), (cr.right - 3, cr.top + d))
                else:
                    pygame.draw.line(surface, rung, (cr.left + d, cr.top + 2), (cr.left + d, cr.bottom - 3))
        fr = face.move(-ox, -oy)
        pygame.draw.rect(surface, gdev.WHITE if hot > 0.7 else color, fr)
        if core is not None:
            k = max(0.0, min(1.0, core["hp"] / max(0.01, core["max"])))
            strip = fr.copy()
            if fr.w >= fr.h:
                strip.y += 5 if bar.spawn_exit[1] >= self.rack.shell.bottom else -5
                strip.h = 2
                pygame.draw.rect(surface, _scale(color, 0.4), strip)
                strip.w = int(fr.w * k)
            else:
                strip.x += 5 if bar.spawn_exit[0] >= self.rack.shell.right else -5
                strip.w = 2
                pygame.draw.rect(surface, _scale(color, 0.4), strip)
                strip.h = int(fr.h * k)
            pygame.draw.rect(surface, gdev.WHITE, strip)

    def _draw_port(self, surface, camera, t):
        sx, sy = camera.world_to_screen(self.port.pos)
        k = 0.5 + 0.5 * math.sin(t * 6)
        r = int(12 + 3 * k)
        pygame.draw.circle(surface, _scale(gdev.CYAN, 0.45), (sx, sy), r + 4, 1)
        pygame.draw.circle(surface, gdev.CYAN, (sx, sy), r, 2)
        for i in range(4):
            a = t * 2.0 + i * math.pi / 2
            pygame.draw.line(surface, gdev.CYAN, (sx + math.cos(a) * (r + 2), sy + math.sin(a) * (r + 2)),
                             (sx + math.cos(a) * (r + 7), sy + math.sin(a) * (r + 7)), 1)
        pygame.draw.circle(surface, gdev.WHITE, (sx, sy), 2)

    def draw_overlay(self, surface, camera):
        """Internal-surface labels: the core's HP and window at its face, the breach port's tag."""
        if self.state == EXPOSURE and self.core_bar() is not None:
            c = self.core()
            face = face_lane(self.rack, self.core_bar())[2]
            pos = camera.world_to_internal(face.center)
            if surface.get_rect().inflate(-20, -20).collidepoint(pos):
                txt = "C0R3 %d%%  %.1fs" % (int(100 * c["hp"] / max(0.01, c["max"])), max(0.0, self.window_t))
                below = self.core_bar().spawn_exit[1] >= self.rack.shell.bottom
                gdev.text(surface, txt, (pos[0], pos[1] + (14 if below else -14)), 12, gdev.MAGENTA,
                          "midtop" if below else "midbottom", True)
        near = (self.run.player.pos - self.port.pos).length() < 120 if self.port is not None else True
        if self.port is not None and self.state == BREACH_READY and not near:     # close up, the HUD prompt says it
            pos = camera.world_to_internal(self.port.pos)
            if surface.get_rect().inflate(-20, -20).collidepoint(pos):
                gdev.text(surface, "R00T P0RT", (pos[0], pos[1] - 18 * camera.zoom), 12, gdev.CYAN,
                          "midbottom", True)


# ---------------------------------------------------------------- manager

class BossManager(object):
    """Public surface (contract):
      defs {id: dict}, bosses {id: Boss}, by_rack {rack id: Boss}, content (intel.json dict)
      active -> Boss|None        the boss in SIEGE/EXPOSURE/BREACH_READY/BREACH (one at a time)
      update(wdt, real_dt), on_event(name, data)
      hack_context(minion) -> dict           (modifier contributions: stale replay, dialect...)
      find_jack_target(pos, range) -> BreachPort|None, open_session(target) -> BreachSession,
      finish_session(target, result)
      hud_info() -> dict|None, draw(world_surface, camera), draw_overlay(surface, camera)
      is_cleared(boss_id) -> bool, cleared -> set of ids
      on_player_respawn()
      start_fight(boss_id), dev_skip_phase(), dev_open_breach(boss_id=None)   (tests / --dev)
      to_save() -> dict, from_save(d)
    """

    def __init__(self, run):
        self.run = run
        self.content = gdev.load_json("data/intel.json", {}) or {}
        self.defs = load_boss_defs()
        self.bosses = {}
        self.by_rack = {}
        self.active = None
        for bid, d in sorted(self.defs.items(), key=lambda kv: kv[1].get("slot", 0)):
            rack = run.world.rack(d.get("rack_id"))
            if rack is None:
                print("[bosses] %s: no rack %s on this map" % (bid, d.get("rack_id")))
                continue
            b = Boss(self, d, rack)
            self.bosses[bid] = b
            self.by_rack[rack.id] = b

    @property
    def cleared(self):
        return set(b.id for b in self.bosses.values() if b.cleared)

    def is_cleared(self, boss_id):
        b = self.bosses.get(boss_id)
        return b is not None and b.cleared

    def update(self, wdt, real_dt):
        if self.run.in_deep_core:
            return
        for b in self.bosses.values():
            b.update(wdt, real_dt)
        if self.active is not None and not self.active.in_fight:
            self.active = None

    def on_event(self, name, data):
        if name == "deep_core_entered":
            for b in self.bosses.values():
                b.force_dormant()
            self.active = None
            return
        for b in self.bosses.values():
            b.on_event(name, data)

    def hack_context(self, minion):
        ctx = {}
        b = self.bosses.get(getattr(minion, "boss_id", None) or "")
        if b is None:
            b = self.by_rack.get(getattr(minion, "rack_id", None) or "")
            if b is not None and not b.speaks_for(minion):
                b = None
        if b is not None and not b.cleared:
            for m in b.modifiers:
                ctx.update(m.hack_context(minion) or {})
        return ctx

    # ------------------------------------------------------------ jack provider (breach ports)

    def find_jack_target(self, pos, max_range):
        b = self.active
        if self.run.in_deep_core or b is None or b.state != BREACH_READY or b.port is None:
            return None
        if (b.port.pos - V(pos)).length() <= max_range:
            return b.port
        return None

    def open_session(self, target):
        b = target.boss
        b.state = BREACH
        run = self.run
        ctx = run.base_hack_ctx(target)
        ctx["title"] = "R00T BR34CH :: %s" % b.leet_name
        return hack.BreachSession(b.breach_spec(), run.settings, run.codex, run.rng, ctx)

    def finish_session(self, target, result):
        target.boss.on_breach_result(result)

    # ------------------------------------------------------------ HUD / drawing

    def hud_info(self):
        return self.active.hud_info() if self.active is not None else None

    def draw(self, surface, camera):
        if self.run.in_deep_core:
            return
        hacking = self.run.hack is not None
        for b in self.bosses.values():
            b.draw(surface, camera, hacking)

    def draw_overlay(self, surface, camera):
        if self.active is not None and not self.run.in_deep_core:
            self.active.draw_overlay(surface, camera)

    def on_player_respawn(self):
        for b in self.bosses.values():
            b.on_player_respawn()
        if self.active is not None and not self.active.in_fight:
            self.active = None

    # ------------------------------------------------------------ dev / tests

    def start_fight(self, boss_id):
        b = self.bosses.get(boss_id)
        if b is None or b.cleared:
            return None
        if b.state in FIGHT_STATES:
            return b
        if self.active is not None and self.active is not b:
            self.active.force_dormant()
            self.active = None
        b.state = ZONE
        b.start_fight()
        return b

    def dev_skip_phase(self):
        """F8 / tests. EXPOSURE: break the exposed core (next phase, or the breach port after the
        last one). SIEGE: skip the rest of the fight straight to the breach port."""
        b = self.active
        if b is None:
            return
        if b.state == EXPOSURE and b.core() is not None:
            b.core()["hp"] = 0.0
            b._break_core()
        elif b.state == SIEGE:
            b._open_breach()

    def dev_open_breach(self, boss_id=None):
        b = self.bosses.get(boss_id) if boss_id else self.active
        if b is None or b.cleared:
            return None
        if b.state not in FIGHT_STATES:
            self.start_fight(b.id)
        if b.state not in (BREACH_READY, BREACH):
            b._open_breach()
        return b

    # ------------------------------------------------------------ save

    def to_save(self):
        return {"cleared": sorted(self.cleared),
                "firewall": dict((b.id, b.saved_firewall()) for b in self.bosses.values()),
                "locked_chunks": dict((b.id, sorted(b.locked_chunks)) for b in self.bosses.values()),
                "broken": dict((b.id, [c["broken"] for c in b.cores]) for b in self.bosses.values()),
                "core_hp": dict((b.id, [round(c["hp"], 2) for c in b.cores]) for b in self.bosses.values()),
                "adaptation": dict((b.id, b.adaptation) for b in self.bosses.values())}

    def from_save(self, d):
        d = d or {}
        cleared = set(d.get("cleared", []))
        for bid, b in self.bosses.items():
            b.force_dormant()
            b.state = CLEARED if bid in cleared else DORMANT
            if bid in d.get("firewall", {}):
                b.firewall = max(0, min(b.firewall_max, int(d["firewall"][bid])))
            b.locked_chunks = set(int(i) for i in d.get("locked_chunks", {}).get(bid, []))
            b.adaptation = int(d.get("adaptation", {}).get(bid, b.adaptation))
            broken = d.get("broken", {}).get(bid, [])
            hps = d.get("core_hp", {}).get(bid, [])
            for i, c in enumerate(b.cores):
                if i < len(broken):
                    c["broken"] = bool(broken[i])
                if i < len(hps):
                    c["hp"] = max(0.0, min(c["max"], float(hps[i])))
                if c["broken"]:
                    c["hp"] = 0.0
            b._sync_phase()
            if not b.cleared and b.firewall <= 0 and not all(c["broken"] for c in b.cores):
                b.firewall = b._half_firewall()      # an old save made mid-EXPOSURE
        self.active = None
        # a Daemon saved with every core broken but not breached: its port is waiting
        for b in sorted(self.bosses.values(), key=lambda x: x.slot):
            if not b.cleared and b.cores and all(c["broken"] for c in b.cores) and self.active is None:
                b._open_breach()
