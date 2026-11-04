"""Progression: Keys, acts, gates, Key sources, the Key re-spawn rule, Spool's cache, the handle,
node visits / saves and run stats (doc 4, 5.2, 8.2, 8.3).

Key placement (doc 8.2), as the Key SOURCE registry (intel.json "key_sources" overrides it):
    prologue        1 scripted carrier from Rack A's ports (F1R3W4LL, or B1T while it is not shipped)
    Act I   (A, B)  boss:r3c4ll + boss:cr055h41r + fw:A1, fw:B1, fw:B2           -> 6 total
    Act II  (C, E)  boss:h34d1ng + boss:v3rd1c7 + fw:C1, fw:E1 + cache           -> 11 total
    Act III (D)     boss:d14l3c7 + fw:D1, fw:D2                                  -> 14 total
Gates: Act II doors 4 Keys, Act III doors 8, Deep Core hatch 12.
Glyph indices: a boss Key de-leets its JSON core_dump.key_glyph_index; every other source
takes the next unused index from intel.json "key_glyph_order" (default below). Whichever Key
is collected 14th plays the Key 14 scene (after any scene its own source opened).

Carriers: in a zone, the first open carrier source of that zone (registry order) sends one
Key carrier out of the rack's ports CARRIER_DELAY_S after you enter (the countdown pauses while
you are away), and again that long after its Key was taken. They keep coming after the Daemon
is cleared (its ports are dark, but a carrier still flickers out).

Key re-spawn rule (doc 5.2, no softlocks). When a carrier is lost (killed, flushed, despawned,
or HARDENED by a BACKTRACE), its Key moves to the nearest live minion of the same kind in that
zone; with none, one of the rack's ports re-spawns a single carrier after CARRIER_RESPAWN_S,
repeating until the Key is taken; if the zone cannot spawn (no rack ports, or the zone stays
full for SHARD_AFTER_FAILS tries) the Key drops as a hackable data shard (KeyShard) at the
Clean Node terminal nearest that zone.

Acts (ints): 0 PROLOGUE, 1 ACT I, 2 ACT II, 3 ACT III, 4 DEEP CORE.
"""
import pygame

import gdev
import hack
import minions as minions_mod

NAME_PLAIN = "ULTIMATEHACKER"
GATES = {"act2": 4, "act3": 8, "deep_core": 12}
DEFAULT_KEY_GLYPH_ORDER = [0, 2, 3, 5, 6, 8, 9, 11, 12]
ACT_NAMES = {0: "PROLOGUE", 1: "ACT I :: GRAFFITI", 2: "ACT II :: THIN AIR", 3: "ACT III :: SIGNATURE",
             4: "DEEP CORE"}
EXPLOITS = ["LEXICON", "WIDE_PORT", "TRACE_DAMPER", "CHAIN_DAEMON", "LONG_TURN", "REMOTE_JACK"]
EXPLOIT_LABELS = {"LEXICON": "LEXICON", "WIDE_PORT": "WIDE PORT", "TRACE_DAMPER": "TRACE DAMPER",
                  "CHAIN_DAEMON": "CHAIN DAEMON", "LONG_TURN": "LONG TURN", "REMOTE_JACK": "REMOTE JACK"}
EXPLOIT_REFUND = 5              # a Core Dump tool you already own refunds its shard price (doc 10)
CARRIER_DELAY_S = 20.0          # world seconds in a zone before its next Key carrier comes out
CARRIER_RESPAWN_S = 3.0         # after a carrier is lost (doc 5.2 re-spawn rule), port flicker included
PORT_FLICKER_S = getattr(minions_mod, "TELEGRAPH_S", 1.0)
PROLOGUE_DELAY_S = 2.0
SHARD_AFTER_FAILS = 3           # port re-spawns refused this many times in a row -> node shard
NODE_VISIT_PX = 140             # within this of a node terminal = a node visit (heal + save)
CORRUPTION_START = 20.0
CORRUPTION_RATE = 0.01          # per world second, x(1 + 0.25 x act): Kernel's compute drain
CORRUPTION_BACKTRACE = 2.0
CORRUPTION_HACK = 0.5
CORRUPTION_KEY = 3.0
CORRUPTION_DAEMON = 10.0
PATCH_INTEL_PCT = 40.0          # doc 8.4: all 14 Keys and this much CHATTER for the PATCH ending
REGION_INDEX = {"act1": 1, "act2": 2, "act3": 3}

DEFAULT_KEY_SOURCES = [
    {"id": "prologue", "kind": "scripted", "zone": "A", "act": 0},
    {"id": "fw:A1", "kind": "carrier", "zone": "A", "act": 1},
    {"id": "fw:B1", "kind": "carrier", "zone": "B", "act": 1},
    {"id": "fw:B2", "kind": "carrier", "zone": "B", "act": 1},
    {"id": "boss:r3c4ll", "kind": "boss", "zone": "A", "act": 1},
    {"id": "boss:cr055h41r", "kind": "boss", "zone": "B", "act": 1},
    {"id": "fw:C1", "kind": "carrier", "zone": "C", "act": 2},
    {"id": "fw:E1", "kind": "carrier", "zone": "E", "act": 2},
    {"id": "boss:h34d1ng", "kind": "boss", "zone": "C", "act": 2},
    {"id": "boss:v3rd1c7", "kind": "boss", "zone": "E", "act": 2},
    {"id": "cache", "kind": "cache", "zone": "cache", "act": 2, "requires": ["h34d1ng", "v3rd1c7"]},
    {"id": "fw:D1", "kind": "carrier", "zone": "D", "act": 3},
    {"id": "fw:D2", "kind": "carrier", "zone": "D", "act": 3},
    {"id": "boss:d14l3c7", "kind": "boss", "zone": "D", "act": 3},
]

# Used for a source whose KEY entry is not in intel.json yet.
FALLBACK_KEY_FRAGMENT = {"id": "KEY-X", "tier": "KEY", "title": "KEY PACKET",
                         "words": [["F", "1", "R", "5", "7"], {"c": ["W", "0", "R", "M"], "target": True},
                                   ["0", "R", "1", "G", "1", "N"], {"c": ["1", "N", "7", "3", "R", "N", "4", "L"],
                                                                   "target": True, "p": "."}],
                         "reveal": "first worm origin: INTERNAL."}
SOURCE_ACT = {0: "P", 1: "I", 2: "II", 3: "III"}


def load_content():
    d = gdev.load_json("data/intel.json", {}) or {}
    return d if isinstance(d, dict) else {}


class CacheTerminal(object):
    """Spool's cache as a jack-in target (doc 3.1). Hold F once both Act II Daemons are cleared."""
    kind = "cache"
    rack_id = None

    def __init__(self, pos):
        self.pos = pygame.math.Vector2(pos)
        self.label = "SP00L'S C4CH3"
        self.prompt = "HOLD [F] JACK INTO SP00L'S C4CH3"
        self.alive = True
        self.hackable = True
        self.jacked = False


class KeyShard(object):
    """doc 5.2 last resort: a Key whose zone cannot spawn its carrier waits at a Clean Node
    terminal as a hackable data shard (a jack target of this module)."""
    kind = "shard"
    rack_id = None

    def __init__(self, source_id, node_id, pos):
        self.source_id = source_id
        self.node_id = node_id
        self.pos = pygame.math.Vector2(pos)
        self.label = "K3Y 5H4RD"
        self.prompt = "HOLD [F] JACK INTO THE KEY SHARD"
        self.alive = True
        self.hackable = True
        self.jacked = False
        self.t = 0.0

    def draw(self, surface, camera):
        """A small bobbing cyan diamond in a pulsing ring (world layer; the HUD prompt and the
        cyan edge arrow name it)."""
        sx, sy = camera.world_to_screen(self.pos)
        sx, sy = int(sx), int(sy)
        bob = int(round(2 * pygame.math.Vector2(1, 0).rotate(self.t * 160).y))
        r = 9 + int(self.t * 6) % 4
        pygame.draw.circle(surface, (0, 90, 100), (sx, sy + 4), r, 1)
        pts = [(sx, sy - 7 + bob), (sx + 5, sy + bob), (sx, sy + 7 + bob), (sx - 5, sy + bob)]
        pygame.draw.polygon(surface, (0, 60, 70), pts)
        pygame.draw.polygon(surface, gdev.CYAN, pts, 1)
        pygame.draw.line(surface, gdev.CYAN, (sx, sy - 3 + bob), (sx, sy + 3 + bob))


class Progression(object):
    """Public surface (contract):
      handle str (A-Z, 3-12)          keys {glyph index: source id}       key_count (property)
      key_order [index]               collected (set of source ids)       act (property, 0..4)
      exploits set, shards int, corruption float 0..100, last_node "N1"|"N2"
      key14_played bool               sources [dict] (Key source registry)   glyph_order [index]
      zone (rack id the player is in)|None, carriers {source id: state}, opened_gates set
      node_shards {source id: node id}  Keys waiting at a Clean Node as data shards (doc 5.2)
      act_name() / zone_label() / objective_text() / objective_point() -> ((x, y), label)|None
      carrier_points() -> [((x, y), label)]  live Key carriers, then Key shards (HUD arrows)
      gate_open(gate) -> bool         "act2"|"act3"|"deep_core"
      grant_key(source_id, index=None, quiet=False) -> index|None
      unlock_exploit(name, refund=True), has_exploit(name), add_shards(n)
      respawn_point() -> (x, y), respawn_label() -> str
      intel_percent() -> 0..100 (decoded CHATTER / all CHATTER in intel.json), patch_ready() -> bool
      update(dt, wdt), on_event(name, data), hack_context(minion) -> dict
      find_jack_target(pos, range) / open_session(target) / finish_session(target, result)
                                      (Spool's cache and Key shards)
      key_text(source_id) -> dict (KEY fragment incl. "reveal"), key14_scene() -> scene spec
      run_stats() -> dict             run counters + Keys / intel / shards / act times (credits)
      draw(world_surface, camera)     Key shards at the Clean Nodes (world layer)
      to_save() -> dict, from_save(d)
    """

    def __init__(self, run, handle="TECH"):
        self.run = run
        self.handle = str(handle or "TECH").upper()
        self.content = load_content()
        self.sources = list(self.content.get("key_sources") or DEFAULT_KEY_SOURCES)
        self.glyph_order = list(self.content.get("key_glyph_order") or DEFAULT_KEY_GLYPH_ORDER)
        self.keys = {}
        self.key_order = []
        self.collected = set()
        self.exploits = set()
        self.shards = 0
        self.corruption = CORRUPTION_START
        self.last_node = "N1"
        self.key14_played = False
        self.zone = None
        self.carriers = {}              # source id -> {"t": s until a spawn, "lost": n, "fails": n}
        self.node_shards = {}           # source id -> node id (doc 5.2 last resort)
        self.opened_gates = set()
        self.act_times = {}             # str(act) -> run.time it was first reached
        self.counts = {"carriers_lost": 0, "keys_moved": 0, "port_respawns": 0, "shards_dropped": 0}
        self._near_node = None
        self._nodes_primed = False      # the first node check (new run / load) is silent
        self._key14_due = False
        self._cache_target = None
        self._shard_targets = {}
        self._t = 0.0

    # ------------------------------------------------------------ queries

    @property
    def key_count(self):
        return len(self.keys)

    @property
    def act(self):
        if getattr(self.run, "in_deep_core", False):
            return 4
        n = self.key_count
        if n == 0:
            return 0
        if n < GATES["act2"]:
            return 1
        if n < GATES["act3"]:
            return 2
        return 3

    def act_name(self):
        return ACT_NAMES.get(self.act, "")

    def gate_open(self, gate):
        return self.key_count >= GATES.get(gate, 99)

    def has_exploit(self, name):
        return name in self.exploits

    def _bosses(self):
        return getattr(self.run, "bosses", None)

    def place_act(self):
        """The act of where the player stands (doc 7.3: the zone's Daemon, else the map region),
        for labels only; gating uses act (the Key count)."""
        if self.act in (0, 4):
            return self.act
        mgr = self._bosses()
        boss = mgr.by_rack.get(self.zone) if (mgr is not None and self.zone) else None
        if boss is not None:
            return boss.act
        try:
            p = self.run.player.pos
            region = REGION_INDEX.get(self.run.world.region_of((p.x, p.y)))
        except (AttributeError, TypeError, KeyError):
            region = None
        return region or self.act

    def zone_label_forms(self):
        """The zone label, longest first (the HUD uses the first one that fits beside the name bar)."""
        act = ACT_NAMES.get(self.place_act(), self.act_name())
        if self.act == 4:
            return [act]
        if self.zone:
            mgr = self._bosses()
            boss = mgr.by_rack.get(self.zone) if mgr is not None else None
            name = boss.leet_name if boss else ""
            tail = " // CL34N" if boss is not None and boss.cleared else ""
            rack = "R4CK_%s" % self.zone
            return ["%s // %s %s%s" % (act, rack, name, tail), "%s %s%s" % (rack, name, tail),
                    "%s%s" % (rack, tail)]
        return [act, act.split(" :: ")[0]]

    def zone_label(self):
        return self.zone_label_forms()[0]

    def source(self, sid):
        for s in self.sources:
            if s["id"] == sid:
                return s
        return None

    def key_text(self, sid):
        """The KEY fragment of a source (intel.json "keys"), or a fallback packet."""
        for k in self.content.get("keys", []):
            if k.get("source") == sid:
                return k
        fb = dict(FALLBACK_KEY_FRAGMENT)
        if sid != "prologue":
            fb["reveal"] = ""
        return fb

    def key_fragment(self, sid):
        """key_text(sid) as a packet fragment with a unique id, tier KEY, act and zone."""
        frag = dict(self.key_text(sid))
        if frag.get("id") in (None, "", FALLBACK_KEY_FRAGMENT["id"]):
            frag["id"] = "KEY-" + str(sid).upper().replace(":", "-")
        frag["tier"] = "KEY"
        src = self.source(sid) or {}
        frag.setdefault("act", SOURCE_ACT.get(src.get("act"), "I"))
        frag.setdefault("zone", src.get("zone"))
        frag.setdefault("sources", ["F1R3W4LL"])
        frag.setdefault("author", "")
        return frag

    def _chatter_ids(self):
        frags = self.content.get("fragments") or getattr(self.run.intel, "fragments", [])
        return set(f.get("id") for f in frags if str(f.get("tier", "CHATTER")).upper() == "CHATTER")

    def intel_percent(self):
        ids = self._chatter_ids()
        if not ids:
            return 0.0
        done = set(e["id"] for e in self.run.intel.entries if e["id"] in ids and not e.get("redacted"))
        return 100.0 * len(done) / len(ids)

    def patch_ready(self):
        """doc 8.4: all 14 Keys and at least 40% of the intel -> the PATCH ending is possible."""
        return self.key_count >= len(NAME_PLAIN) and self.intel_percent() >= PATCH_INTEL_PCT

    def respawn_point(self):
        return self.run.world.respawn_point(self.last_node)

    def respawn_label(self):
        if self.act == 4:
            return "THE DEEP CORE"
        return "CLEAN NODE %s" % self.last_node[1:]

    def run_stats(self):
        st = dict(self.run.stats)
        st.update({"keys": self.key_count, "intel_pct": int(round(self.intel_percent())), "shards": self.shards,
                   "exploits": sorted(self.exploits), "corruption": int(self.corruption),
                   "act_times": dict(self.act_times), "handle": self.handle})
        st.update(self.counts)
        return st

    # ------------------------------------------------------------ keys

    def _next_index(self, preferred=None):
        if preferred is not None and preferred not in self.keys:
            return preferred
        for i in self.glyph_order:
            if i not in self.keys:
                return i
        for i in range(len(NAME_PLAIN)):
            if i not in self.keys:
                return i
        return None

    def grant_key(self, source_id, index=None, quiet=False):
        """Recover one name-bar Key. Emits key_collected; the 14th queues the Key 14 scene."""
        if source_id in self.collected and not source_id.startswith("dev"):
            return None
        idx = self._next_index(index)
        if idx is None:
            return None
        self.collected.add(source_id)
        self.keys[idx] = source_id
        self.key_order.append(idx)
        self.run.key_anim[idx] = 0.0
        self.corruption = max(0.0, self.corruption - CORRUPTION_KEY)
        self.node_shards.pop(source_id, None)
        self._shard_targets.pop(source_id, None)
        for m in self.run.minions.minions:          # a TURNED / chained carrier stops carrying it
            if getattr(m, "key_source", None) == source_id:
                m.key_source = None
        frag = self.key_text(source_id) if not source_id.startswith(("boss:", "dev")) else {}
        self.run.emit("key_collected", index=idx, source=source_id, count=self.key_count,
                      reveal=frag.get("reveal", ""), quiet=quiet)
        if self.key_count == len(NAME_PLAIN) and not self.key14_played:
            # next PLAY frame: a Core Dump or cache scene this Key opened plays first
            self._key14_due = True
        return idx

    def key14_scene(self):
        k = self.content.get("key14") or {}
        lines = [str(x).replace("[HANDLE]", self.handle) for x in
                 (k.get("lines") or ["[LOG 0x0F2 :: SP00L -> %s]" % self.handle, "gg ultimate hacker :P"])]
        return {"kind": "key14", "title": k.get("title", "KEY 14/14 :: CHAT LOG"),
                "pages": [{"head": "EVIDENCE", "lines": lines, "color": "cyan"},
                          {"head": "N4M3 B4R", "lines": ["THE NAME RE-ENCODES.", self.handle], "color": "magenta",
                           "kind": "handle"}],
                "on_close": "resume"}

    def unlock_exploit(self, name, refund=True):
        if not name:
            return
        if name in self.exploits:
            if refund:
                self.shards += EXPLOIT_REFUND
                self.run.toast("%s ALREADY OWNED :: +%d SHARDS" % (EXPLOIT_LABELS.get(name, name), EXPLOIT_REFUND),
                               gdev.CYAN, 2.6)
            return
        self.exploits.add(name)
        self.run.emit("exploit_unlocked", exploit=name)

    def add_shards(self, n):
        self.shards += int(n)

    # ------------------------------------------------------------ objective

    def _route(self, pt, label):
        """Through a blast door when the point is in another act's region (doc 3.1 barriers)."""
        w = self.run.world
        p = self.run.player.pos
        try:
            a = REGION_INDEX.get(w.region_of((p.x, p.y)))
            b = REGION_INDEX.get(w.region_of((int(pt[0]), int(pt[1]))))
        except (AttributeError, TypeError, KeyError):
            return (tuple(pt), label)
        if a is None or b is None or a == b:
            return (tuple(pt), label)
        door = w.door("act%d" % (a + 1 if b > a else a))
        if door is None:
            return (tuple(pt), label)
        return (tuple(door.rect.center), door.label or "BL4ST D00R")

    def _next_boss(self):
        """The nearest Daemon the player may fight now (same region first)."""
        run = self.run
        mgr = self._bosses()
        if mgr is None:
            return None
        here = None
        try:
            here = run.world.region_of((run.player.pos.x, run.player.pos.y))
        except AttributeError:
            pass
        best, best_k = None, None
        for b in mgr.bosses.values():
            if b.cleared or b.act > max(1, self.act):
                continue
            c = pygame.math.Vector2(b.rack.zone_center)
            other = 0
            if here is not None:
                try:
                    other = 0 if run.world.region_of((c.x, c.y)) == here else 1
                except AttributeError:
                    other = 0
            k = (other, (c - run.player.pos).length_squared())
            if best_k is None or k < best_k:
                best, best_k = b, k
        return best

    def _open_sources(self, zone=None):
        """Carrier / scripted sources whose Key can be found now (registry order)."""
        out = []
        for s in self.sources:
            if s.get("kind") not in ("scripted", "carrier") or s["id"] in self.collected:
                continue
            if zone is not None and s.get("zone") != zone:
                continue
            if s.get("kind") == "scripted":
                if self.key_count > 0:
                    continue
            elif self.act == 0 or int(s.get("act", 1)) > self.act:
                continue
            out.append(s)
        return out

    def _next_carrier_zone(self):
        """Rack id of the first open carrier source, the current zone's first."""
        srcs = [s for s in self._open_sources() if s["id"] not in self.node_shards]
        if not srcs:
            return None
        for s in srcs:
            if s.get("zone") == self.zone:
                return s["zone"]
        return srcs[0].get("zone")

    def _gate_tail(self):
        nxt = [g for g in ("act2", "act3", "deep_core") if not self.gate_open(g)]
        if not nxt:
            return ""
        gate = nxt[0]
        name = {"act2": "ACT II doors", "act3": "ACT III doors", "deep_core": "DEEP CORE"}[gate]
        return "  ::  %s at %d" % (name, GATES[gate])

    def _rack_port(self, rack):
        """The rack's port (bar spawn_exit) nearest the player, or its shell centre."""
        p = self.run.player.pos
        if rack.bars:
            return tuple(min((pygame.math.Vector2(b.spawn_exit) for b in rack.bars),
                             key=lambda v: (v - p).length_squared()))
        return tuple(rack.shell.center)

    def _carrier_wait(self, zone):
        """Seconds until this zone's next Key carrier leaves the ports (None: one is out or none due)."""
        srcs = [s for s in self._open_sources(zone) if s["id"] not in self.node_shards]
        if not srcs:
            return None
        sid = srcs[0]["id"]
        if self._live_carrier(sid) is not None:
            return None
        if self._pending_carrier(sid):
            return 0.0
        st = self.carriers.get(sid)
        return max(0.0, st["t"]) if st else CARRIER_DELAY_S

    def _keys_findable(self):
        """True while some Key source can still be found on this map (Daemon, carrier, cache, shard)."""
        if self.node_shards or self._next_carrier_zone() is not None:
            return True
        if "cache" not in self.collected and self.source("cache") is not None:
            return True
        mgr = self._bosses()
        return mgr is not None and any(not b.cleared for b in mgr.bosses.values())

    def _hunt_text(self, head):
        """The next Key to go for (cache, shard, Daemon, carrier), or None."""
        tail = self._gate_tail()
        if self._cache_unlocked() and "cache" not in self.collected:
            return "%s  ::  SP00L'S C4CH3 unsealed (east wall): jack in%s" % (head, tail)
        if self.node_shards:
            node = sorted(self.node_shards.values())[0]
            return "%s  ::  a KEY shard waits at CLEAN NODE %s%s" % (head, node[1:], tail)
        target = self._next_boss()
        if target is not None:
            return "%s  ::  hack minions, take down %s (RACK %s)%s" % (head, target.leet_name, target.rack.id, tail)
        z = self._next_carrier_zone()
        if z is not None:
            if z == self.zone:
                wait = self._carrier_wait(z)
                if wait is not None and wait > 0:
                    return "%s  ::  a KEY carrier compiles at RACK %s's ports (%ds): stay in the zone%s" % (
                        head, z, int(wait + 0.99), tail)
                return "%s  ::  a KEY carrier is out in RACK %s's zone: hack it%s" % (head, z, tail)
            return "%s  ::  a KEY carrier walks RACK %s's zone%s" % (head, z, tail)
        return None

    def objective_text(self):
        run = self.run
        if self.act == 4:
            info = run.finale.hud_info() if hasattr(run, "finale") else None
            return "OBJECTIVE  face THE MIRROR" if info else "OBJECTIVE  the Deep Core"
        mgr = self._bosses()
        boss = mgr.active if mgr is not None else None
        if boss is not None:
            return boss.objective_text()
        if self.act == 0:
            rack = run.world.rack("A")
            carrier = self._live_carrier("prologue")
            if "prologue" in self.node_shards:
                return "OBJECTIVE  the KEY waits as a data shard at CLEAN NODE %s" % self.node_shards["prologue"][1:]
            if rack is not None and not rack.in_zone(run.player.pos):
                return "OBJECTIVE  reach RACK A :: Kernel reports an intruder"
            if carrier is not None:
                return "OBJECTIVE  a minion carries a KEY: strip its firewall, hack it"
            return "OBJECTIVE  watch RACK A's ports"
        n = self.key_count
        head = "KEYS %d/14" % n
        if self.gate_open("deep_core"):
            left = len(NAME_PLAIN) - n
            if left > 0 and self._keys_findable():
                # doc 8.4: the hatch is one way and PATCH needs every Key; keep pointing at them
                hunt = self._hunt_text("%s (%d for PATCH)" % (head, left))
                if hunt:
                    return hunt.replace("hack minions, take down", "take down") + "  ::  hatch: PURGE only"
            if left > 0:
                return "%s  ::  INTEL %d%%  ::  the DEEP CORE hatch is open (bottom edge): PURGE only" % (
                    head, int(self.intel_percent()))
            if self.intel_percent() < PATCH_INTEL_PCT:
                return "%s  ::  INTEL %d%% (PATCH needs %d%%: decode chatter)  ::  hatch open (bottom edge)" % (
                    head, int(self.intel_percent()), int(PATCH_INTEL_PCT))
            return "%s  ::  INTEL %d%%  ::  descend: the DEEP CORE hatch (bottom edge)" % (
                head, int(self.intel_percent()))
        hunt = self._hunt_text(head)
        if hunt:
            return hunt
        return "%s  ::  find the remaining KEYS%s" % (head, self._gate_tail())

    def objective_point(self):
        run = self.run
        if self.act == 4:
            return None
        mgr = self._bosses()
        boss = mgr.active if mgr is not None else None
        if boss is not None:
            return boss.objective_point()
        if self.act == 0:
            rack = run.world.rack("A")
            if rack is None:
                return None
            if "prologue" in self.node_shards:
                t = self._shard_target("prologue")
                return (tuple(t.pos), "KEY SHARD") if t is not None else None
            if rack.in_zone(run.player.pos):
                c = self._live_carrier("prologue")
                if c is not None:
                    return (tuple(c.pos), "KEY")
                return (self._rack_port(rack), "P0RTS")
            return (rack.zone_center, "RACK A")
        hatch = [i for i in run.world.interactables if i.kind == "hatch"]
        if self.gate_open("deep_core") and hatch:
            if self.key_count >= len(NAME_PLAIN) or not self._keys_findable():
                return self._route(hatch[0].pos, "DEEP CORE")
        if self._cache_unlocked() and "cache" not in self.collected:
            it = [i for i in run.world.interactables if i.kind == "cache"]
            if it:
                return self._route(it[0].pos, "SP00L'S C4CH3")
        if self.node_shards:
            sid = sorted(self.node_shards)[0]
            t = self._shard_target(sid)
            if t is not None:
                return self._route(t.pos, "KEY SHARD")
        b = self._next_boss()
        if b is not None:
            if b.rack.in_zone(run.player.pos):
                # in its zone but not fighting yet: point at the rack (hidden once it is on screen)
                return (self._rack_port(b.rack), b.leet_name)
            return self._route(b.rack.zone_center, "RACK %s" % b.rack.id)
        z = self._next_carrier_zone()
        rack = run.world.rack(z) if z else None
        if rack is not None:
            if not rack.in_zone(run.player.pos):
                return self._route(rack.zone_center, "RACK %s" % rack.id)
            if self._live_carrier_in(z) is None:
                return (self._rack_port(rack), "P0RTS")      # the carrier comes out here
        return None

    def _live_carrier_in(self, zone):
        for s in self._open_sources(zone):
            m = self._live_carrier(s["id"])
            if m is not None:
                return m
        return None

    def carrier_points(self):
        out = []
        for m in self.run.minions.minions:
            if m.alive and getattr(m, "key_source", None):
                out.append((tuple(m.pos), "KEY"))
        if not self.run.in_deep_core:
            for sid in sorted(self.node_shards):
                t = self._shard_target(sid)
                if t is not None:
                    out.append((tuple(t.pos), "KEY SHARD"))
        return out

    # ------------------------------------------------------------ update

    def update(self, dt, wdt):
        run = self.run
        self._t += dt
        if self._key14_due and getattr(run, "key_banner", None) is None and getattr(run, "key_freeze", 0) <= 0:
            # after the KEY 14/14 banner (a Core Dump / cache scene this Key opened plays first)
            self._key14_due = False
            self.key14_played = True
            run.request_scene(self.key14_scene())
        a = str(self.act)
        if a not in self.act_times:
            self.act_times[a] = round(run.time, 1)
        if run.in_deep_core:
            return
        w = run.world
        w.hatch_open = self.gate_open("deep_core")
        mgr = self._bosses()
        if mgr is not None:
            w.cleared_racks = set(b.rack.id for b in mgr.bosses.values() if b.cleared)
        w.cache_state = "empty" if "cache" in self.collected else ("open" if self._cache_unlocked() else "sealed")
        w.act = self.act
        for t in self._shard_targets.values():
            t.t += dt
        p = run.player
        z = w.zone_of(p.pos) if not p.dead else None
        if z != self.zone:
            if self.zone is not None:
                run.emit("zone_left", rack_id=self.zone)
            self.zone = z
            if z is not None:
                run.emit("zone_entered", rack_id=z)
        for d in w.doors:
            if self.key_count >= d.keys and not d.open:
                w.set_door_open(d.id, True)
                run.emit("door_opened", door_id=d.id)
        for gate in ("act2", "act3", "deep_core"):
            if self.gate_open(gate) and gate not in self.opened_gates:
                self.opened_gates.add(gate)
                run.emit("gate_opened", gate=gate)
        self._update_nodes()
        self._update_carriers(wdt)
        rate = CORRUPTION_RATE * (1.0 + 0.25 * self.act)
        self.corruption = min(100.0, self.corruption + rate * wdt)

    def _update_nodes(self):
        run = self.run
        p = run.player
        near = None
        for it in run.world.interactables:
            if it.kind == "node_terminal" and not p.dead and (it.pos - p.pos).length() < NODE_VISIT_PX:
                near = it.node_id
        if not self._nodes_primed:              # new run / loaded save: already standing there
            self._nodes_primed = True
            self._near_node = near
            return
        if near is not None and near != self._near_node:
            self.last_node = near
            p.heal(p.max_hp)
            run.emit("node_visited", node_id=near)
            run.request_save("node")
        self._near_node = near

    def _live_carrier(self, sid):
        for m in self.run.minions.minions:
            if m.alive and getattr(m, "key_source", None) == sid:
                return m
        return None

    def _pending_carrier(self, sid):
        for o in self.run.minions.orders:
            if len(o) > 4 and o[4] and o[4].get("key_source") == sid:
                return True
        return False

    def _carrier_kind(self):
        return "f1r3w4ll" if "f1r3w4ll" in minions_mod.KINDS else "b1t"

    def _update_carriers(self, wdt):
        run = self.run
        if run.player.dead or self.zone is None:
            return
        srcs = self._open_sources(self.zone)
        if not srcs:
            return
        s = srcs[0]                                     # one carrier per zone at a time
        sid = s["id"]
        if sid in self.node_shards or self._live_carrier(sid) is not None or self._pending_carrier(sid):
            return
        st = self.carriers.setdefault(sid, {"t": PROLOGUE_DELAY_S if s.get("kind") == "scripted"
                                            else CARRIER_DELAY_S, "lost": 0, "fails": 0})
        st["t"] -= wdt
        if st["t"] > 0:
            return
        st["t"] = CARRIER_RESPAWN_S - PORT_FLICKER_S    # used if this one is lost without a trace
        rack = run.world.rack(s.get("zone"))
        n = 0
        if rack is not None and rack.bars:
            n = run.minions.spawn_wave(rack.id, [self._carrier_kind()], "trickle", tags={"key_source": sid})
        if n:
            st["fails"] = 0
            if st.get("lost"):
                self.counts["port_respawns"] += 1
            return
        st["fails"] = st.get("fails", 0) + 1
        if rack is None or not rack.bars or st["fails"] >= SHARD_AFTER_FAILS:
            self._drop_shard(sid, s.get("zone"))

    def _carrier_lost(self, m, sid):
        """doc 5.2: the Key moves to another live minion of the same kind in that zone, or a
        port re-spawns a carrier after CARRIER_RESPAWN_S."""
        run = self.run
        m.key_source = None
        st = self.carriers.setdefault(sid, {"t": CARRIER_RESPAWN_S - PORT_FLICKER_S, "lost": 0, "fails": 0})
        st["lost"] = st.get("lost", 0) + 1
        self.counts["carriers_lost"] += 1
        heir, best = None, None
        for o in run.minions.minions:
            if o is m or not o.alive or o.kind != m.kind or o.team != "enemy" or o.rack_id != m.rack_id:
                continue
            if o.hardened or getattr(o, "cached", False) or o.jacked or getattr(o, "key_source", None):
                continue
            d = (o.pos - m.pos).length_squared()
            if best is None or d < best:
                heir, best = o, d
        if heir is not None:
            heir.key_source = sid
            self.counts["keys_moved"] += 1
            run.float_text((heir.pos.x, heir.pos.y - 18), "KEY M0V3D", gdev.CYAN, 1.2)
            return
        st["t"] = CARRIER_RESPAWN_S - PORT_FLICKER_S    # the port flickers the rest of the 3 s

    def _drop_shard(self, sid, zone):
        """Last resort: the Key waits at the Clean Node terminal nearest its zone."""
        run = self.run
        terms = [i for i in run.world.interactables if i.kind == "node_terminal" and i.node_id]
        if not terms:
            return
        rack = run.world.rack(zone) if zone else None
        ref = pygame.math.Vector2(rack.zone_center) if rack is not None else run.player.pos
        it = min(terms, key=lambda i: (i.pos - ref).length_squared())
        self.node_shards[sid] = it.node_id
        self.counts["shards_dropped"] += 1
        self._shard_targets.pop(sid, None)
        run.toast("KEY SH4RD SPOOLED TO CLEAN NODE %s" % it.node_id[1:], gdev.CYAN, 3.0)

    # ------------------------------------------------------------ events

    def on_event(self, name, data):
        run = self.run
        if name == "hack_finished":
            res = data.get("result") or {}
            outcome = res.get("outcome")
            if outcome == "success" and not res.get("stale"):
                self.corruption = max(0.0, self.corruption - CORRUPTION_HACK)
            elif outcome == "backtrace":
                self.corruption = min(100.0, self.corruption + CORRUPTION_BACKTRACE)
            if data.get("provider") != "minion" or outcome != "success":
                return
            sid = res.get("key_source")
            if sid:
                self.grant_key(sid)
            if not res.get("stale"):
                n = 0
                if res.get("payload") in (None, "EXTRACT"):
                    n += 1
                if res.get("clean"):
                    n += 1
                self.add_shards(n * max(1, data.get("chain", 0) + 1))
        elif name in ("minion_died", "minion_hardened"):
            m = data.get("minion")
            sid = getattr(m, "key_source", None)
            if not sid:
                return
            if sid in self.collected:
                m.key_source = None
            elif name == "minion_hardened" or m.dead_cause != "extract":
                self._carrier_lost(m, sid)
        elif name == "deep_core_entered":
            self.zone = None
        elif name == "boss_defeated":
            boss = data["boss"]
            self.grant_key("boss:" + boss.id, boss.key_glyph_index, quiet=True)
            self.unlock_exploit(boss.exploit)
            self.corruption = max(0.0, self.corruption - CORRUPTION_DAEMON)

    def hack_context(self, minion):
        sid = getattr(minion, "key_source", None)
        if not sid or sid in self.collected:
            return {}
        ctx = {"fragment": self.key_fragment(sid), "key_source": sid, "stale": False, "replay": None}
        if getattr(minion, "kind", "") == "f1r3w4ll":
            ctx["two_part"] = True
        return ctx

    # ------------------------------------------------------------ jack provider (cache, Key shards)

    def _cache_unlocked(self):
        b = self._bosses()
        if b is None:
            return False
        req = (self.source("cache") or {}).get("requires", ["h34d1ng", "v3rd1c7"])
        return all(b.is_cleared(r) for r in req)

    def _shard_target(self, sid):
        node_id = self.node_shards.get(sid)
        if node_id is None:
            return None
        t = self._shard_targets.get(sid)
        if t is not None and t.node_id == node_id:
            return t
        w = self.run.world
        terms = [i for i in w.interactables if i.kind == "node_terminal" and i.node_id == node_id]
        if not terms:
            return None
        base = terms[0].pos
        k = sorted(self.node_shards).index(sid)
        pos = base
        for dx in (-90 - 40 * k, 90 + 40 * k, -130 - 40 * k, 130 + 40 * k):
            q = (int(base.x + dx), int(base.y))
            if w.rect_free(pygame.Rect(q[0] - 8, q[1] - 8, 16, 16)):
                pos = q
                break
        t = KeyShard(sid, node_id, pos)
        self._shard_targets[sid] = t
        return t

    def find_jack_target(self, pos, max_range):
        if self.run.in_deep_core:
            return None
        p = pygame.math.Vector2(pos)
        best, best_d = None, None
        if "cache" not in self.collected and self._cache_unlocked():
            it = [i for i in self.run.world.interactables if i.kind == "cache"]
            if it and (it[0].pos - p).length() <= max_range + it[0].radius:
                if self._cache_target is None:
                    self._cache_target = CacheTerminal(it[0].pos)
                best, best_d = self._cache_target, (it[0].pos - p).length()
        for sid in sorted(self.node_shards):
            t = self._shard_target(sid)
            if t is None:
                continue
            d = (t.pos - p).length()
            if d <= max_range and (best_d is None or d < best_d):
                best, best_d = t, d
        return best

    def open_session(self, target):
        run = self.run
        ctx = run.base_hack_ctx(target)
        if getattr(target, "kind", "") == "shard":
            sid = target.source_id
            ctx.update({"fragment": self.key_fragment(sid), "key_source": sid, "title": "K3Y 5H4RD"})
        else:
            frag = dict(self.key_text("cache"))
            if frag.get("id") in (None, "", FALLBACK_KEY_FRAGMENT["id"]):
                frag["id"] = "KEY-CACHE"
            frag["tier"] = "KEY"
            ctx.update({"fragment": frag, "key_source": "cache", "title": "SP00L'S C4CH3"})
        return hack.HackSession(target, run.settings, run.codex, run.intel, run.rng, ctx)

    def finish_session(self, target, result):
        if result.get("outcome") != "success":
            return
        if getattr(target, "kind", "") == "shard":
            self.grant_key(target.source_id)
            return
        self.grant_key("cache", quiet=True)
        k = self.key_text("cache")
        lines = [str(s).replace("[HANDLE]", self.handle) for s in
                 (k.get("scene") or ["its not me. its u. well. /-\\ u."])]
        self.run.request_scene({"kind": "cache", "title": "SP00L'S C4CH3",
                                "pages": [{"head": "FROM: SP00L", "lines": lines, "color": "cyan"},
                                          {"head": "KEY RECOVERED", "lines": [], "color": "cyan", "kind": "key"}],
                                "key_index": self.key_order[-1] if self.key_order else None,
                                "on_close": "resume"})

    # ------------------------------------------------------------ drawing

    def draw(self, surface, camera):
        """Key shards waiting at Clean Nodes (world layer; Game calls it after world.draw)."""
        if self.run.in_deep_core:
            return
        for sid in sorted(self.node_shards):
            t = self._shard_target(sid)
            if t is not None and not t.jacked:
                t.draw(surface, camera)

    # ------------------------------------------------------------ save

    def to_save(self):
        return {"handle": self.handle, "keys": dict((str(k), v) for k, v in self.keys.items()),
                "key_order": list(self.key_order), "collected": sorted(self.collected),
                "exploits": sorted(self.exploits), "shards": self.shards, "corruption": round(self.corruption, 2),
                "last_node": self.last_node, "key14_played": self.key14_played,
                "opened_gates": sorted(self.opened_gates),
                "carriers": dict((k, dict(v)) for k, v in self.carriers.items()),
                "node_shards": dict(self.node_shards), "act_times": dict(self.act_times),
                "counts": dict(self.counts)}

    def from_save(self, d):
        d = d or {}
        self.handle = str(d.get("handle", self.handle)).upper()
        self.keys = dict((int(k), v) for k, v in d.get("keys", {}).items())
        self.key_order = [int(i) for i in d.get("key_order", sorted(self.keys)) if int(i) in self.keys]
        self.collected = set(d.get("collected", [])) | set(self.keys.values())
        self.exploits = set(e for e in d.get("exploits", []) if e in EXPLOITS)
        self.shards = int(d.get("shards", 0))
        self.corruption = float(d.get("corruption", CORRUPTION_START))
        self.last_node = d.get("last_node", "N1")
        self.key14_played = bool(d.get("key14_played", False))
        self._key14_due = self.key_count >= len(NAME_PLAIN) and not self.key14_played
        self.opened_gates = set(d.get("opened_gates", [])) | set(g for g in GATES if self.gate_open(g))
        self.carriers = {}
        for sid, st in (d.get("carriers") or {}).items():
            if sid not in self.collected and isinstance(st, dict):
                # a carrier that was out when the run saved comes back out of its port
                self.carriers[sid] = {"t": float(st.get("t", CARRIER_DELAY_S)), "lost": int(st.get("lost", 0)),
                                      "fails": 0}
        self.node_shards = dict((k, v) for k, v in (d.get("node_shards") or {}).items() if k not in self.collected)
        self.act_times = dict(d.get("act_times") or {})
        self.counts.update(dict((k, int(v)) for k, v in (d.get("counts") or {}).items() if k in self.counts))
        self._shard_targets = {}
        self._cache_target = None
        self._nodes_primed = False
        self.zone = None
        for door in self.run.world.doors:
            if self.key_count >= door.keys:
                self.run.world.set_door_open(door.id, True, animate=False)   # no replayed opening on load


def handle_ok(handle):
    """doc 8.1: letters A-Z only, 3-12 long."""
    h = str(handle or "")
    return 3 <= len(h) <= 12 and all("A" <= c <= "Z" for c in h)
