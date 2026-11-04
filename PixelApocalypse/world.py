"""World (map rects, collision, drawing) and Camera.

The world is built from data/map_data.json (written by tools/slice_map.py and
hand-checked). bg/map.png is NEVER loaded at runtime: the map art is a few dozen
flat rectangles, so it is redrawn every frame with pygame.draw.rect, culled to
the camera. Everything outside the art bounds is the DATA LOSS VOID: black,
glitchy, and an invisible wall.

All coordinates are world (map) pixels; rects are pygame.Rect.
"""
import math
import random

import pygame

import gdev

MAP_FILE = "data/map_data.json"

# Used only when data/map_data.json is missing, so the game still runs.
PLACEHOLDER_MAP = {
    "source": "placeholder",
    "size": [10000, 10000],
    "art_bounds": [0, 0, 8192, 8192],
    "floor_color": [2, 33, 1],
    "walls": [{"rect": [0, 0, 8192, 205], "color": [26, 99, 65]},
              {"rect": [0, 205, 128, 7987], "color": [26, 99, 65]}],
    "racks": [{"id": "A", "shell": [1090, 858, 1919, 1010], "color": [26, 99, 65],
               "bars": [{"rect": [1346 + i * 420, 1114, 200, 498], "color": [64, 152, 94],
                         "spawn_exit": [1446 + i * 420, 1892]} for i in range(4)],
               "zone_center": [2049, 1363], "zone_radius": 1200}],
    "nodes": [{"id": "N1", "role": "clean_node", "rect": [1090, 2985, 546, 585],
               "color": [26, 99, 65]}],
    "blocks": [],
    "player_start": [1363, 2905],
}

# Cable trunks (doc 3.2): 32px strips between nodes and racks, 1.6x walking speed.
# map_data.json may carry its own "trunks" list (polylines of [x, y] points);
# otherwise these L-shaped paths are used. They only make sense on the real map,
# so they are skipped when a point lands inside a solid.
TRUNK_WIDTH = 32
TRUNK_SPEED = 1.6
DEFAULT_TRUNKS = [
    [[1363, 2985], [1363, 1868]],                       # Clean Node 1 -> Rack A
    [[1636, 3277], [5270, 3277], [5270, 3353]],         # Clean Node 1 -> Clean Node 2
    [[5270, 3353], [5270, 2240], [6208, 2240], [6208, 2042]],   # Clean Node 2 -> Rack B
    [[4977, 3577], [3065, 3577], [3065, 4552]],         # Clean Node 2 -> Rack C
]

VOID_PEEK = 128         # how far past the art bounds the camera may look (shows the void)

# Colours drawn in code (the map's own colours come from map_data.json)
TRACE_COL = (6, 46, 8)          # circuit traces on the floor
VIA_COL = (10, 60, 14)
NODE_HALO = (5, 46, 9)          # lit floor around Clean Nodes
ZONE_TINT = (7, 0, 5)           # added to the floor colour inside an Influence Zone (faint magenta)
ZONE_RING = (96, 22, 80)
TRUNK_COL = (6, 50, 18)
TRUNK_CABLE = (18, 84, 44)
VOID_HEX = (18, 62, 34)
VOID_HEX_HOT = (90, 20, 74)
CLEAR_TINT = (2, 13, 7)         # added to the floor colour inside a CLEARED rack's zone (safe, lit)
CLEAR_RING = (34, 120, 70)
BARRIER_COL = (30, 88, 60)      # act firewalls (map_data may override per slab)
DOOR_DARK = (60, 8, 30)         # closed blast door: dark red, strobing toward RED / MAGENTA
APRON_COL = (4, 26, 6)          # floor plate in front of the hatch and the cache
GLITCH_GLYPHS = "#%&@$!?/\\|<>01_"

# Deep Core (kind "deep_core"): the cathedral look
DC_RING = (8, 50, 42)
DC_RIB = (5, 32, 28)
DC_TILE = (4, 27, 23)
DC_NAVE = (3, 28, 24)
DC_PULSE = (40, 170, 150)


def _hash(a, b, c=0):
    """Cheap deterministic hash of three ints -> 0..2**32-1 (void noise)."""
    h = (a * 73856093) ^ (b * 19349663) ^ (c * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    return (h ^ (h >> 16)) & 0xFFFFFFFF


def _scale(c, k):
    return (min(255, int(c[0] * k)), min(255, int(c[1] * k)), min(255, int(c[2] * k)))


def _mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def _padlock(surface, center, col):
    """A tiny padlock (10x12) centred on a world-layer point: square shackle, solid body."""
    x, y = int(center[0]), int(center[1])
    pygame.draw.lines(surface, col, False, [(x - 3, y - 1), (x - 3, y - 5), (x + 2, y - 5), (x + 2, y - 1)], 2)
    pygame.draw.rect(surface, col, (x - 5, y - 1, 10, 7))
    pygame.draw.rect(surface, (20, 0, 10), (x - 1, y + 1, 2, 3))


class Bar(object):
    """A rack's data port. hittable=True while it is acting as an exposed core.

    flicker: seconds of spawn telegraph left. minions.py SETS it; World.update
    counts it down and World.draw shows it (magenta strobe + marker at spawn_exit).
    """
    def __init__(self, d, index):
        self.index = index
        self.id = d.get("id", str(index))
        self.rect = pygame.Rect(d["rect"])
        self.color = tuple(d.get("color", (64, 152, 94)))
        self.spawn_exit = tuple(d.get("spawn_exit", self.rect.midbottom))
        self.hittable = bool(d.get("hittable", False))
        self.flicker = 0.0      # spawn telegraph (minions set, world counts down)
        self.flash = 0.0        # white flash after a projectile hit
        self.hits = 0           # damage taken while hittable (for boss code later)


class Rack(object):
    def __init__(self, d):
        self.id = d["id"]
        self.shell = pygame.Rect(d["shell"])
        self.color = tuple(d.get("color", (26, 99, 65)))
        self.bars = [Bar(b, i) for i, b in enumerate(d.get("bars", []))]
        self.zone_center = tuple(d.get("zone_center", self.shell.center))
        self.zone_radius = float(d.get("zone_radius", 1200))
        # dotted zone border, precomputed in world pixels (culled when drawn)
        n = max(24, int(2 * math.pi * self.zone_radius / 40))
        cx, cy = self.zone_center
        self.ring = [(int(cx + math.cos(i * 2 * math.pi / n) * self.zone_radius),
                      int(cy + math.sin(i * 2 * math.pi / n) * self.zone_radius)) for i in range(n)]
        r = int(self.zone_radius)
        self.zone_rect = pygame.Rect(int(cx) - r, int(cy) - r, 2 * r, 2 * r)

    def in_zone(self, pos):
        dx = pos[0] - self.zone_center[0]
        dy = pos[1] - self.zone_center[1]
        return dx * dx + dy * dy <= self.zone_radius * self.zone_radius


class Node(object):
    def __init__(self, d):
        self.id = d["id"]
        self.role = d.get("role", "")
        self.rect = pygame.Rect(d["rect"])
        self.color = tuple(d.get("color", (26, 99, 65)))


class Door(object):
    """A blast door: the gap in an act barrier. Solid (and stops shots) while closed.
    Progression opens it for good once the Key count reaches `keys`; it then slides apart
    over OPEN_ANIM_S (drawn only: the solid is gone the moment it opens).

      id, rect, keys (Keys needed), label (long name), sign ("ACT II // 4 KEYS"), open, color
      anim    seconds of the opening animation left (World.update counts it down)
      flash   white flash after a shot hits it while closed
    """
    OPEN_ANIM_S = 1.4

    def __init__(self, d):
        self.id = d["id"]
        self.rect = pygame.Rect(d["rect"])
        self.keys = int(d.get("keys", 0))
        self.label = d.get("label", self.id.upper())
        self.sign = d.get("sign", "%s // %d KEYS" % (self.label, self.keys))
        self.open = bool(d.get("open", False))
        self.color = tuple(d.get("color", (90, 30, 70)))
        self.anim = 0.0
        self.flash = 0.0

    @property
    def short(self):
        """"ACT II" from "ACT II // 4 KEYS"."""
        return self.sign.split("//")[0].strip()


class Interactable(object):
    """Something F interacts with (not a minion): a Clean Node terminal, Spool's cache, the hatch.
      id, kind "node_terminal"|"cache"|"hatch", pos (Vector2), radius, node_id (terminals only)."""
    def __init__(self, iid, kind, pos, radius=40, node_id=None):
        self.id = iid
        self.kind = kind
        self.pos = pygame.math.Vector2(pos)
        self.radius = radius
        self.node_id = node_id


# Used only when map_data.json has no "deep_core" section (the real one is hand-authored there).
DEEP_CORE_PLACEHOLDER = {
    "source": "placeholder",
    "size": [1600, 1100],
    "art_bounds": [0, 0, 1600, 1100],
    "floor_color": [2, 22, 18],
    "walls": [{"rect": [0, 0, 1600, 64], "color": [20, 80, 70]},
              {"rect": [0, 64, 64, 1036], "color": [20, 80, 70]},
              {"rect": [1536, 64, 64, 1036], "color": [20, 80, 70]},
              {"rect": [64, 1036, 1472, 64], "color": [20, 80, 70]}],
    "racks": [], "nodes": [], "blocks": [], "trunks": [],
    "player_start": [800, 900],
    "arena": {"mirror_start": [800, 400], "ports": [[300, 200], [1300, 200], [300, 800], [1300, 800]],
              "core": [720, 160, 160, 80]},
}


class World(object):
    """Public surface:
      size (w, h), art_bounds Rect, floor_color, walls [(Rect, color)],
      racks [Rack], nodes [Node], blocks [(Rect, color)], solids [Rect], player_start (x, y),
      trunks [Rect]
      rack(id) -> Rack|None, node(id) -> Node|None
      respawn_point() -> (x, y) free floor point at Clean Node 1
      move_and_collide(rect, dx, dy) -> new Rect (moves X then Y, slides along solids)
      move_pos(x, y, w, h, dx, dy) -> (x, y)  same, but with float centre positions
                              (use this for anything slower than 1px per frame)
      rect_free(rect) -> bool, point_solid(x, y) -> bool (solid or outside art bounds)
      on_trunk(pos) -> bool, segment_clear(a, b, step=8) -> bool (line of sight)
      projectile_hit(rect) -> None | ("bar", rack, bar) | ("shell", rack, None) | ("wall", None, None)
                              (hittable bars are tested before shells; leaving art bounds = "wall")
      update(dt), draw(surface, camera)   floor, zone tint, trunks, walls, racks, nodes (culled) + void
      draw_void(surface, camera)

    Gated regions (doc 3.1, 8.2): the open map is split by act barriers (firewall slabs,
    solid) into act1 (Racks A, B, Clean Nodes 1 and 2), act2 (Racks C, E, Spool's cache,
    the Deep Core hatch) and act3 (Rack D). Each region is reachable only through its blast
    door, which progression opens at the Key gate:
      kind "overworld"|"deep_core"
      barriers [(Rect, color)]   the act firewalls (always solid)
      doors [Door]            blast doors: id, rect, keys (Keys needed), label, sign, open, anim
      door(id) -> Door|None, door_open(id) -> bool, set_door_open(id, open, animate=True)
                              (rebuilds solids; opening plays the slide-apart animation)
      interactables [Interactable]   F targets: id, kind "node_terminal"|"cache"|"hatch", pos, radius, node_id
      interactable_near(pos, radius=None) -> Interactable|None
      respawn_point(node_id=None) -> (x, y)   free floor next to that Clean Node (default N1)
      zone_of(pos) -> rack id whose Influence Zone holds pos (nearest centre wins) or None
      region_of(pos) -> "act1"|"act2"|"act3"   which gated region a point is in
                              ("deep_core" in the arena; a point on a barrier gets the nearest region)
      World.deep_core() -> World   the Deep Core arena (a separate small map, kind "deep_core")
      arena: dict for the Deep Core (from map_data["deep_core"]): {"mirror_start", "ports": [[x, y]], "core": [x, y, w, h]}

    Set by Game every frame (drawing only):
      corruption 0..100     diegetic glitch intensity (signs and rack labels lose characters)
      reduced_glitch        True: no jitter / RGB ghost on those labels (the setting)
      hatch_open            True once the Deep Core gate is open (the hatch shows 0P3N)
      cleared_racks         set of rack ids whose Daemon is cleared (zone lit and safe, ports dark)
      cache_state           "sealed" | "open" | "empty"   Spool's cache terminal
      act                   0..4, the DATA LOSS edge crumbles a little deeper each act
    """
    TRUNK_SPEED = TRUNK_SPEED       # walking speed multiplier on a cable trunk

    def __init__(self, map_data=None):
        if map_data is None:
            map_data = PLACEHOLDER_MAP
        self.data = map_data
        self.size = tuple(map_data.get("size", (10000, 10000)))
        self.art_bounds = pygame.Rect(map_data.get("art_bounds", (0, 0, 8192, 8192)))
        self.floor_color = tuple(map_data.get("floor_color", (2, 33, 1)))
        self.walls = [(pygame.Rect(w["rect"]), tuple(w.get("color", (26, 99, 65))))
                      for w in map_data.get("walls", [])]
        self.racks = [Rack(r) for r in map_data.get("racks", [])]
        self.nodes = [Node(n) for n in map_data.get("nodes", [])]
        self.blocks = [(pygame.Rect(b["rect"]), tuple(b.get("color", (26, 99, 65))))
                       for b in map_data.get("blocks", [])]
        self.block_ids = [b.get("id", "") for b in map_data.get("blocks", [])]
        self.player_start = tuple(map_data.get("player_start", self.art_bounds.center))
        self.kind = map_data.get("kind", "overworld")
        self.barriers = [(pygame.Rect(b["rect"]), tuple(b.get("color", (40, 70, 52))))
                         for b in map_data.get("barriers", [])]
        self.doors = [Door(d) for d in map_data.get("doors", [])]
        self.regions = [(r["id"], pygame.Rect(r["rect"])) for r in map_data.get("regions", [])]
        self.arena = map_data.get("arena", {})
        self.chamber = map_data.get("chamber")      # Deep Core: {"center", "radii"} (drawing only)
        self.corruption = 0.0
        self.reduced_glitch = False
        self.hatch_open = False
        self.cleared_racks = set()
        self.cache_state = "sealed"
        self.act = 0
        self._rebuild_solids()
        self.interactables = self._build_interactables(map_data)
        self.trunks = self._build_trunks(map_data.get("trunks", DEFAULT_TRUNKS))
        self.trunk_paths = []       # [(a, b)] segment end points, for the data-pulse drawing
        for pts in map_data.get("trunks", DEFAULT_TRUNKS):
            for a, b in zip(pts, pts[1:]):
                self.trunk_paths.append((tuple(a), tuple(b)))
        if not self.trunks:
            self.trunk_paths = []
        self.time = 0.0
        self._detail = {}           # floor circuit traces, per 256px cell (cached)
        self.zone_floor = tuple(min(255, a + b) for a, b in zip(self.floor_color, ZONE_TINT))
        self.clear_floor = tuple(min(255, a + b) for a, b in zip(self.floor_color, CLEAR_TINT))

    # -------------------------------------------------------------- gates, interactables, regions

    def _rebuild_solids(self):
        """Solids = walls, rack shells, nodes, blocks, barriers and every CLOSED door."""
        self.solids = [r for r, _c in self.walls] + [r.shell for r in self.racks] + \
                      [n.rect for n in self.nodes] + [r for r, _c in self.blocks] + \
                      [r for r, _c in self.barriers] + [d.rect for d in self.doors if not d.open]

    def _build_interactables(self, data):
        out = []
        for d in data.get("interactables", []):
            out.append(Interactable(d["id"], d["kind"], d["pos"], d.get("radius", 40), d.get("node_id")))
        if out or "interactables" in data:
            return out
        # No list in the map file (placeholder map): derive them from the nodes and blocks.
        for n in self.nodes:
            if n.role == "clean_node":
                out.append(Interactable("term_" + n.id, "node_terminal", (n.rect.centerx, n.rect.top - 24),
                                        48, n.id))
        for r, bid in zip([b for b, _c in self.blocks], self.block_ids):
            if bid == "deep_core_hatch":
                out.append(Interactable("hatch", "hatch", (r.centerx, r.top - 24), 60))
            elif bid == "spool_cache":
                out.append(Interactable("cache", "cache", (r.left - 24, r.centery), 48))
        return out

    def door(self, door_id):
        for d in self.doors:
            if d.id == door_id:
                return d
        return None

    def door_open(self, door_id):
        d = self.door(door_id)
        return d is None or d.open

    def set_door_open(self, door_id, is_open, animate=True):
        """Open (or close) a door and rebuild the solids. Opening plays the slide-apart
        animation unless animate=False (e.g. when a save is loaded)."""
        d = self.door(door_id)
        if d is not None and d.open != bool(is_open):
            d.open = bool(is_open)
            d.anim = Door.OPEN_ANIM_S if (d.open and animate) else 0.0
            self._rebuild_solids()

    def interactable_near(self, pos, radius=None):
        best, best_d = None, None
        for it in self.interactables:
            r = radius if radius is not None else it.radius
            d = (it.pos[0] - pos[0]) ** 2 + (it.pos[1] - pos[1]) ** 2
            if d <= r * r and (best_d is None or d < best_d):
                best, best_d = it, d
        return best

    def zone_of(self, pos):
        best, best_d = None, None
        for rk in self.racks:
            if rk.in_zone(pos):
                d = (pos[0] - rk.zone_center[0]) ** 2 + (pos[1] - rk.zone_center[1]) ** 2
                if best_d is None or d < best_d:
                    best, best_d = rk.id, d
        return best

    def region_of(self, pos):
        """Which gated region holds pos: the first map_data "regions" rect that contains it,
        else (a point on a barrier) the nearest one. "deep_core" in the arena."""
        if self.kind == "deep_core":
            return "deep_core"
        x, y = pos[0], pos[1]
        best, best_d = None, None
        for rid, r in self.regions:
            if r.collidepoint(x, y):
                return rid
            dx = max(r.left - x, 0, x - r.right)
            dy = max(r.top - y, 0, y - r.bottom)
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best, best_d = rid, d
        if best is not None:
            return best
        # no regions in the map file (placeholder map): the doc 3.1 layout
        if y < 4120:
            return "act1"
        if x > 5300 and y > 6100:
            return "act3"
        return "act2"

    @classmethod
    def deep_core(cls):
        """The Deep Core arena: a separate small World (doc 3.1, 8.4)."""
        data = gdev.load_json(MAP_FILE) or {}
        arena = data.get("deep_core") or DEEP_CORE_PLACEHOLDER
        w = cls(dict(arena, kind="deep_core"))
        return w

    @classmethod
    def load(cls):
        """World from data/map_data.json, or the placeholder map if the file is missing."""
        data = gdev.load_json(MAP_FILE)
        if data is None:
            print("[world] %s not found, using placeholder map" % MAP_FILE)
        return cls(data)

    def _build_trunks(self, paths):
        rects = []
        h = TRUNK_WIDTH // 2
        for pts in paths:
            for a, b in zip(pts, pts[1:]):
                x0, x1 = sorted((int(a[0]), int(b[0])))
                y0, y1 = sorted((int(a[1]), int(b[1])))
                rects.append(pygame.Rect(x0 - h, y0 - h, x1 - x0 + TRUNK_WIDTH, y1 - y0 + TRUNK_WIDTH))
        # Trunks are drawn under solids, so touching them is fine; but if a whole
        # segment lies inside a solid the path does not fit this map: drop them all.
        for r in rects:
            if not self.art_bounds.contains(r) or any(s.contains(r) for s in self.solids):
                return []
        return rects

    def rack(self, rack_id):
        for r in self.racks:
            if r.id == rack_id:
                return r
        return None

    def node(self, node_id):
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def respawn_point(self, node_id=None):
        """Free floor right next to a Clean Node (default N1; above it first, like player_start)."""
        if self.kind == "deep_core":
            return tuple(self.player_start)
        n = self.node(node_id or "N1")
        if n is None:
            for nd in self.nodes:
                if nd.role == "clean_node":
                    n = nd
                    break
        if n is None:
            return self.player_start
        r = n.rect
        # player_start is the preferred spot when it is next to the node
        sx, sy = self.player_start
        if n.id == "N1" and r.inflate(240, 240).collidepoint(sx, sy) and self.rect_free(pygame.Rect(sx - 8, sy - 8, 16, 16)):
            return (sx, sy)
        for p in ((r.centerx, r.top - 40), (r.centerx, r.bottom + 40),
                  (r.right + 40, r.centery), (r.left - 40, r.centery)):
            if self.rect_free(pygame.Rect(p[0] - 8, p[1] - 8, 16, 16)):
                return p
        return self.player_start

    # -------------------------------------------------------------- collision

    def move_and_collide(self, rect, dx, dy):
        """Integer version (kept for the contract): moves a Rect X then Y and slides.
        Sub-pixel steps round to 0 here; use move_pos for slow movers."""
        r = pygame.Rect(rect)
        cx, cy = self.move_pos(r.centerx, r.centery, r.w, r.h, dx, dy)
        r.center = (int(round(cx)), int(round(cy)))
        return r

    def move_pos(self, x, y, w, h, dx, dy):
        """Move a w x h box centred on float (x, y) by (dx, dy): X first, then Y,
        stopping flush against solids (so it slides along them) and inside the
        art bounds. Returns the new float centre (x, y)."""
        hw, hh = w / 2.0, h / 2.0
        if dx:
            x += dx
            for s in self.solids:
                if x - hw < s.right and x + hw > s.left and y - hh < s.bottom and y + hh > s.top:
                    if dx > 0:
                        x = s.left - hw
                    else:
                        x = s.right + hw
        if dy:
            y += dy
            for s in self.solids:
                if x - hw < s.right and x + hw > s.left and y - hh < s.bottom and y + hh > s.top:
                    if dy > 0:
                        y = s.top - hh
                    else:
                        y = s.bottom + hh
        b = self.art_bounds
        x = max(b.left + hw, min(x, b.right - hw))
        y = max(b.top + hh, min(y, b.bottom - hh))
        return x, y

    def rect_free(self, rect):
        """True if rect is inside the art bounds and touches no solid."""
        if not self.art_bounds.contains(rect):
            return False
        return rect.collidelist(self.solids) < 0

    def point_solid(self, x, y):
        if not self.art_bounds.collidepoint(x, y):
            return True
        for s in self.solids:
            if s.collidepoint(x, y):
                return True
        return False

    def segment_clear(self, a, b, step=8):
        """Line of sight between two world points (samples every `step` px)."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = max(1, int(math.hypot(dx, dy) / step))
        for i in range(n + 1):
            if self.point_solid(a[0] + dx * i / n, a[1] + dy * i / n):
                return False
        return True

    def on_trunk(self, pos):
        x, y = pos[0], pos[1]
        for r in self.trunks:
            if r.collidepoint(x, y):
                return True
        return False

    def projectile_hit(self, rect):
        if not self.art_bounds.contains(rect):
            return ("wall", None, None)
        for rk in self.racks:
            for b in rk.bars:
                # an exposed core bar sits inside the shell: bosses.py publishes its face lane as
                # hit_rect, so a shot that reaches the shell face in front of it hits the core
                if b.hittable and (getattr(b, "hit_rect", None) or b.rect).colliderect(rect):
                    return ("bar", rk, b)
        for rk in self.racks:
            if rk.shell.colliderect(rect):
                return ("shell", rk, None)
        for d in self.doors:
            if not d.open and d.rect.colliderect(rect):
                d.flash = 0.08
                return ("wall", None, None)
        for s in self.solids:
            if s.colliderect(rect):
                return ("wall", None, None)
        return None

    # -------------------------------------------------------------- update

    def update(self, dt):
        self.time += dt
        for rk in self.racks:
            for b in rk.bars:
                if b.flicker > 0:
                    b.flicker = max(0.0, b.flicker - dt)
                if b.flash > 0:
                    b.flash = max(0.0, b.flash - dt)
        for d in self.doors:
            if d.anim > 0:
                d.anim = max(0.0, d.anim - dt)
            if d.flash > 0:
                d.flash = max(0.0, d.flash - dt)

    # -------------------------------------------------------------- drawing

    def draw(self, surface, camera):
        if self.kind == "deep_core":
            self._draw_deep_core(surface, camera)
            return
        view = camera.view_rect
        ox, oy = camera.offset
        floor = self.art_bounds.clip(view)
        if floor.w > 0 and floor.h > 0:
            surface.fill(self.floor_color, floor.move(-ox, -oy))
            self._draw_zones(surface, view, ox, oy, floor)
            self._draw_floor_detail(surface, view, ox, oy)
            for n in self.nodes:
                if n.role == "clean_node":
                    halo = n.rect.inflate(56, 56)
                    if halo.colliderect(view):
                        pygame.draw.rect(surface, NODE_HALO, halo.move(-ox, -oy), border_radius=10)
            self._draw_trunks(surface, view, ox, oy)

        for (r, c) in self.walls:
            if r.colliderect(view):
                self._draw_block(surface, r, c, ox, oy, view)
        for i, (r, c) in enumerate(self.blocks):
            bid = self.block_ids[i] if i < len(self.block_ids) else ""
            if bid == "deep_core_hatch":
                if r.inflate(0, 120).colliderect(view):
                    self._draw_hatch(surface, r, c, ox, oy)
            elif bid == "spool_cache":
                if r.inflate(400, 80).colliderect(view):
                    self._draw_cache(surface, r, c, ox, oy)
            elif r.colliderect(view):
                self._draw_block(surface, r, c, ox, oy, view)
        for (r, c) in self.barriers:
            if r.colliderect(view):
                self._draw_barrier(surface, r, c, ox, oy, view)
        for d in self.doors:
            if d.rect.inflate(120, 120).colliderect(view):
                self._draw_door(surface, d, ox, oy)
        for n in self.nodes:
            if n.rect.colliderect(view):
                self._draw_node(surface, n, ox, oy, view)
        for rk in self.racks:
            if rk.shell.colliderect(view.inflate(64, 64)):
                self._draw_rack(surface, rk, ox, oy, view)
        self.draw_void(surface, camera)

    # -------------------------------------------------------------- diegetic text

    def corrupt_text(self, s, salt=0):
        """Diegetic text under CORRUPTION: characters drop out into glitch glyphs (re-rolled
        4x a second). Below 5% nothing happens; at 100% about a third of the characters go."""
        c = self.corruption
        if c < 5:
            return s
        tick = int(self.time * 4)
        p = int(c * 3.2)            # per mille
        out = []
        for i, ch in enumerate(s):
            if ch != " " and _hash(i, salt, tick) % 1000 < p:
                out.append(GLITCH_GLYPHS[_hash(i, salt, tick + 7) % len(GLITCH_GLYPHS)])
            else:
                out.append(ch)
        return "".join(out)

    def _label(self, surface, s, pos, color, align="topleft", size=10, glitch=True):
        """World-layer label. glitch=True marks diegetic text: it corrupts with CORRUPTION and,
        unless reduced_glitch, jitters with a magenta ghost now and then."""
        if glitch and self.corruption >= 5:
            salt = len(s) * 131 + sum(ord(ch) for ch in s[:6])
            s = self.corrupt_text(s, salt)
            if not self.reduced_glitch and self.corruption >= 25:
                h = _hash(salt, int(self.time * 10), 3)
                if h % 100 < self.corruption * 0.25:
                    dx = 1 if (h >> 8) & 1 else -1
                    gdev.text(surface, s, (pos[0] + dx * 2, pos[1]), size, _mix(color, gdev.MAGENTA, 0.6),
                              align, bold=True)
                    pos = (pos[0] - dx, pos[1])
        gdev.text(surface, s, pos, size, color, align, bold=True)

    def _draw_block(self, surface, r, c, ox, oy, view):
        sr = r.move(-ox, -oy)
        pygame.draw.rect(surface, c, sr)
        pygame.draw.rect(surface, _scale(c, 1.3), sr, 1)
        if r.w > 12 and r.h > 12:
            pygame.draw.rect(surface, _scale(c, 0.75), sr.inflate(-6, -6), 1)

    def _draw_node(self, surface, n, ox, oy, view):
        sr = n.rect.move(-ox, -oy)
        c = n.color
        pygame.draw.rect(surface, c, sr)
        pygame.draw.rect(surface, _scale(c, 1.5), sr, 1)
        pygame.draw.rect(surface, _scale(c, 0.75), sr.inflate(-8, -8), 1)
        if n.role == "clean_node":
            # a calm "lit" terminal on the node face
            pulse = 0.5 + 0.5 * math.sin(self.time * 1.4)
            col = _mix(_scale(c, 1.6), gdev.GREEN, 0.25 + 0.2 * pulse)
            num = n.id[1:] if n.id.startswith("N") else n.id
            self._label(surface, "CLEAN NODE %s" % num, (sr.centerx, sr.top + 14), col, "midtop", 12,
                        glitch=False)
            self._label(surface, "save // heal // terminal", (sr.centerx, sr.top + 30), _scale(c, 1.5),
                        "midtop", 10, glitch=False)
            scr = pygame.Rect(0, 0, 60, 36)
            scr.center = (sr.centerx, sr.top + 72)
            pygame.draw.rect(surface, (4, 22, 10), scr)
            pygame.draw.rect(surface, col, scr, 1)
            if int(self.time * 2) % 2 == 0:
                pygame.draw.rect(surface, col, (scr.x + 6, scr.y + 24, 6, 2))
            gdev.text(surface, ">_", (scr.x + 6, scr.y + 6), 10, col)

    def _draw_rack(self, surface, rk, ox, oy, view):
        c = rk.color
        cleared = rk.id in self.cleared_racks
        sr = rk.shell.move(-ox, -oy)
        pygame.draw.rect(surface, c, sr)
        # server-unit seams across the shell face (only the visible part)
        vis = rk.shell.clip(view)
        if vis.w > 0 and vis.h > 0:
            seam = _scale(c, 0.85)
            y0 = rk.shell.top + 24 - ((rk.shell.top + 24 - vis.top) // 24) * 24
            if y0 < vis.top:
                y0 += 24
            for y in range(y0, vis.bottom, 24):
                pygame.draw.line(surface, seam, (vis.left - ox, y - oy), (vis.right - 1 - ox, y - oy))
        pygame.draw.rect(surface, _scale(c, 1.35), sr, 1)
        pygame.draw.rect(surface, _scale(c, 0.7), sr.inflate(-10, -10), 1)
        if cleared:
            self._label(surface, "R4CK_%s // CL34N" % rk.id, (sr.left + 10, sr.top + 9),
                        _mix(_scale(c, 1.6), gdev.GREEN, 0.35))
        else:
            self._label(surface, "R4CK_%s" % rk.id, (sr.left + 10, sr.top + 9), _scale(c, 1.6))

        for b in rk.bars:
            br = b.rect.move(-ox, -oy)
            if not b.rect.colliderect(view.inflate(32, 32)):
                if b.flicker > 0:
                    self._draw_exit_marker(surface, b, ox, oy)
                continue
            # slow glow: each port breathes at its own phase
            k = 0.5 + 0.5 * math.sin(self.time * 1.6 + b.index * 1.3)
            col = _scale(b.color, 0.92 + 0.22 * k)
            glow = _mix(c, b.color, 0.45 + 0.15 * k)
            if cleared and not b.hittable and b.flicker <= 0:
                # the Daemon is gone: its ports are dark, no data flows
                dark = _mix(c, b.color, 0.25)
                pygame.draw.rect(surface, _scale(c, 0.8), br.inflate(6, 6))
                pygame.draw.rect(surface, dark, br)
                pygame.draw.rect(surface, _scale(dark, 1.2), br, 1)
                continue
            if b.hittable:
                hot = 0.5 + 0.5 * math.sin(self.time * 7.0)
                col = _mix(b.color, gdev.MAGENTA, 0.35 + 0.35 * hot)
                glow = _mix(c, gdev.MAGENTA, 0.5)
            if b.flicker > 0 and int(self.time * 20) % 2 == 0:
                col = gdev.MAGENTA
                glow = _mix(c, gdev.MAGENTA, 0.6)
            if b.flash > 0:
                col = gdev.WHITE
            pygame.draw.rect(surface, glow, br.inflate(6, 6))
            pygame.draw.rect(surface, col, br)
            # data flowing down the port: faint rungs that scroll
            vis = b.rect.clip(view)
            if vis.w > 4 and vis.h > 0:
                rung = _scale(col, 1.18)
                off = int(self.time * 26) % 14
                y = b.rect.top + off
                if y < vis.top:
                    y += ((vis.top - y) // 14 + 1) * 14
                while y < vis.bottom:
                    pygame.draw.line(surface, rung, (vis.left + 3 - ox, y - oy), (vis.right - 4 - ox, y - oy))
                    y += 14
            pygame.draw.rect(surface, _scale(col, 1.25), br, 1)
            if b.flicker > 0 or b.hittable:
                self._draw_exit_marker(surface, b, ox, oy)

    def _draw_exit_marker(self, surface, b, ox, oy):
        """Spawn telegraph at a port's exit point (magenta, pulsing)."""
        if b.flicker <= 0:
            return
        x, y = int(b.spawn_exit[0] - ox), int(b.spawn_exit[1] - oy)
        k = (self.time * 3.0) % 1.0
        pygame.draw.circle(surface, gdev.MAGENTA, (x, y), int(4 + 10 * k), 1)
        if int(self.time * 12) % 2 == 0:
            pygame.draw.circle(surface, gdev.MAGENTA, (x, y), 3)

    # -------------------------------------------------------------- gates: barriers, doors, hatch, cache

    def _draw_barrier(self, surface, r, c, ox, oy, view):
        """An act firewall: a wall-coloured slab of staggered blocks, a scan light running
        along it and a F1R3W4LL stencil every 640px. Only the visible part is drawn."""
        vis = r.clip(view)
        if vis.w <= 0 or vis.h <= 0:
            return
        sr = vis.move(-ox, -oy)
        pygame.draw.rect(surface, c, sr)
        hi, lo, seam = _scale(c, 1.35), _scale(c, 0.68), _scale(c, 0.82)
        step = 32
        horiz = r.w >= r.h
        if horiz:
            top, bot, mid = r.top - oy, r.bottom - 1 - oy, r.centery - oy
            pygame.draw.line(surface, hi, (sr.left, top), (sr.right - 1, top))
            pygame.draw.line(surface, lo, (sr.left, bot), (sr.right - 1, bot))
            pygame.draw.line(surface, lo, (sr.left, mid), (sr.right - 1, mid))
            x0 = vis.left - (vis.left - r.left) % step
            for x in range(x0, vis.right + step, step):
                pygame.draw.line(surface, seam, (x - ox, top + 2), (x - ox, mid - 1))
                pygame.draw.line(surface, seam, (x + step // 2 - ox, mid + 1), (x + step // 2 - ox, bot - 2))
            # scan light: a short bright run sliding along the top rail
            p = r.left + (self.time * 160.0) % max(1, r.w)
            for q in (p, p - r.w / 2.0 if p - r.w / 2.0 >= r.left else p + r.w / 2.0):
                if vis.left - 24 <= q <= vis.right:
                    pygame.draw.line(surface, _scale(c, 1.9), (int(q) - ox, top + 1), (int(q) + 24 - ox, top + 1))
            length, start = r.w, r.left
        else:
            lft, rgt, mid = r.left - ox, r.right - 1 - ox, r.centerx - ox
            pygame.draw.line(surface, hi, (lft, sr.top), (lft, sr.bottom - 1))
            pygame.draw.line(surface, lo, (rgt, sr.top), (rgt, sr.bottom - 1))
            pygame.draw.line(surface, lo, (mid, sr.top), (mid, sr.bottom - 1))
            y0 = vis.top - (vis.top - r.top) % step
            for y in range(y0, vis.bottom + step, step):
                pygame.draw.line(surface, seam, (lft + 2, y - oy), (mid - 1, y - oy))
                pygame.draw.line(surface, seam, (mid + 1, y + step // 2 - oy), (rgt - 2, y + step // 2 - oy))
            p = r.top + (self.time * 160.0) % max(1, r.h)
            if vis.top - 24 <= p <= vis.bottom:
                pygame.draw.line(surface, _scale(c, 1.9), (lft + 1, int(p) - oy), (lft + 1, int(p) + 24 - oy))
            length, start = r.h, r.top
        # stencils (diegetic: they corrupt)
        for k in range(320, length, 640):
            if horiz:
                pos = (start + k, r.centery)
            else:
                pos = (r.centerx, start + k)
            if view.collidepoint(pos):
                spos = (pos[0] - ox, pos[1] - oy)
                plate = pygame.Rect(0, 0, 58, 14) if horiz else pygame.Rect(0, 0, 14, 58)
                plate.center = spos
                pygame.draw.rect(surface, _scale(c, 0.6), plate)
                if horiz:
                    self._label(surface, "F1R3W4LL", spos, _scale(c, 1.7), "center", 9)
                else:
                    for i, chx in enumerate("FW"):
                        self._label(surface, chx, (spos[0], spos[1] - 10 + i * 18), _scale(c, 1.7), "center", 9)

    def _door_slab(self, surface, sr, d, opening=False):
        """The door itself (or one sliding half of it): dark red, strobing, hazard stripes."""
        if sr.w <= 0 or sr.h <= 0:
            return
        pulse = 0.5 + 0.5 * math.sin(self.time * 5.0)
        if opening:
            base = _mix(DOOR_DARK, gdev.CYAN, 0.3)
            edge = gdev.CYAN
        else:
            base = _mix(DOOR_DARK, gdev.RED, 0.2 + 0.25 * pulse)
            edge = _mix(gdev.MAGENTA, gdev.RED, pulse)
        if d.flash > 0:
            base = _mix(base, gdev.WHITE, 0.55)
        pygame.draw.rect(surface, base, sr)
        stripe = _mix(base, gdev.MAGENTA if not opening else gdev.CYAN, 0.45)
        old = surface.get_clip()
        surface.set_clip(sr.clip(old))
        off = int(self.time * 18) % 14
        for x in range(sr.left - sr.h - 14 + off, sr.right + 14, 14):
            pygame.draw.line(surface, stripe, (x, sr.bottom), (x + sr.h, sr.top), 4)
        surface.set_clip(old)
        pygame.draw.rect(surface, edge, sr, 1)

    def _draw_door(self, surface, d, ox, oy):
        r = d.rect
        sr = r.move(-ox, -oy)
        horiz = r.w >= r.h
        green = _mix(BARRIER_COL, gdev.GREEN, 0.4)
        # emitter posts at both ends of the gap (part of the barrier)
        if horiz:
            posts = (pygame.Rect(sr.left - 6, sr.top - 3, 6, sr.h + 6), pygame.Rect(sr.right, sr.top - 3, 6, sr.h + 6))
        else:
            posts = (pygame.Rect(sr.left - 3, sr.top - 6, sr.w + 6, 6), pygame.Rect(sr.left - 3, sr.bottom, sr.w + 6, 6))
        if d.open:
            # the gap is plain floor (trunks show through), framed by threshold rails and ticks
            rail = _scale(BARRIER_COL, 0.9)
            if horiz:
                pygame.draw.line(surface, rail, (sr.left, sr.top), (sr.right - 1, sr.top))
                pygame.draw.line(surface, rail, (sr.left, sr.bottom - 1), (sr.right - 1, sr.bottom - 1))
                for x in range(sr.left + 8, sr.right - 4, 16):
                    pygame.draw.line(surface, rail, (x, sr.centery), (x + 6, sr.centery))
            else:
                pygame.draw.line(surface, rail, (sr.left, sr.top), (sr.left, sr.bottom - 1))
                pygame.draw.line(surface, rail, (sr.right - 1, sr.top), (sr.right - 1, sr.bottom - 1))
                for y in range(sr.top + 8, sr.bottom - 4, 16):
                    pygame.draw.line(surface, rail, (sr.centerx, y), (sr.centerx, y + 6))
            if d.anim > 0:
                k = d.anim / Door.OPEN_ANIM_S          # 1 -> 0
                e = k * k                               # fast start, slow settle into the walls
                if horiz:
                    half = int(r.w / 2.0 * e)
                    halves = (pygame.Rect(sr.left, sr.top, half, sr.h), pygame.Rect(sr.right - half, sr.top, half, sr.h))
                else:
                    half = int(r.h / 2.0 * e)
                    halves = (pygame.Rect(sr.left, sr.top, sr.w, half), pygame.Rect(sr.left, sr.bottom - half, sr.w, half))
                for h in halves:
                    self._door_slab(surface, h, d, opening=True)
                # sparks along the opening edges
                rng = random.Random(int(self.time * 30))
                for h in halves:
                    edge_x = h.right if (horiz and h.left == sr.left) else h.left
                    for _ in range(4):
                        if horiz:
                            p = (edge_x + rng.randrange(-3, 4), h.top + rng.randrange(0, max(1, h.h)))
                        else:
                            ey = h.bottom if h.top == sr.top else h.top
                            p = (h.left + rng.randrange(0, max(1, h.w)), ey + rng.randrange(-3, 4))
                        pygame.draw.rect(surface, gdev.CYAN if rng.random() < 0.6 else gdev.WHITE, (p[0], p[1], 2, 2))
                for p in posts:
                    pygame.draw.rect(surface, gdev.CYAN, p)
                sign, col = "ACCESS GRANTED", gdev.CYAN
            else:
                for p in posts:
                    pygame.draw.rect(surface, green, p)
                sign, col = "%s // 0P3N" % d.short, _scale(green, 0.9)
        else:
            self._door_slab(surface, sr, d)
            pulse = 0.5 + 0.5 * math.sin(self.time * 5.0)
            for p in posts:
                pygame.draw.rect(surface, _mix(gdev.MAGENTA, gdev.RED, pulse), p)
            # a padlock in the middle
            pygame.draw.rect(surface, (20, 0, 10), (sr.centerx - 8, sr.centery - 7, 16, 16))
            _padlock(surface, (sr.centerx, sr.centery + 1), gdev.AMBER)
            sign, col = d.sign, _mix(gdev.RED, gdev.MAGENTA, pulse)
        if horiz:
            self._label(surface, sign, (sr.centerx, sr.top - 6), col, "midbottom", 10)
            self._label(surface, sign, (sr.centerx, sr.bottom + 6), col, "midtop", 10)
        else:
            self._label(surface, sign, (sr.left - 8, sr.centery), col, "midright", 10)
            self._label(surface, sign, (sr.right + 8, sr.centery), col, "midleft", 10)

    def _draw_hatch(self, surface, r, c, ox, oy):
        """The Deep Core hatch on the bottom edge: a shuttered slab, a striped apron in front
        and an access panel at the middle (where F works). Sealed: red lights. Open: cyan."""
        sr = r.move(-ox, -oy)
        t = self.time
        is_open = self.hatch_open
        # apron
        ap = pygame.Rect(sr.left, sr.top - 56, sr.w, 56)
        pygame.draw.rect(surface, APRON_COL, ap)
        pygame.draw.rect(surface, _scale(c, 0.9), ap, 1)
        tick = _scale(c, 0.8)
        for x in range(ap.left + 8, ap.right - 8, 24):
            pygame.draw.line(surface, tick, (x, ap.top + 3), (x + 10, ap.top + 3), 2)
        if is_open:
            k = (t * 1.2) % 1.0
            for x in range(ap.left + 80, ap.right - 40, 160):
                for j in range(2):
                    kk = (k + j * 0.5) % 1.0
                    y = ap.top + 10 + int(kk * 32)
                    col = _mix(APRON_COL, gdev.CYAN, 1.0 - kk)
                    pygame.draw.lines(surface, col, False, [(x - 10, y), (x, y + 8), (x + 10, y)], 2)
        # the slab: shutter slats
        pygame.draw.rect(surface, c, sr)
        slat = _scale(c, 0.7)
        for x in range(sr.left + 12, sr.right, 24):
            pygame.draw.line(surface, slat, (x, sr.top + 5), (x, sr.bottom - 1))
        pygame.draw.rect(surface, _scale(c, 1.3), sr, 1)
        # status lights along the top edge
        n = max(1, r.w // 48)
        for i in range(n):
            x = sr.left + 24 + i * 48
            if is_open:
                k = 0.5 + 0.5 * math.sin(t * 4.0 - i * 0.5)
                col = _mix(_scale(c, 0.8), gdev.CYAN, k)
            else:
                col = gdev.RED if (int(t * 2) + i) % 6 == 0 else (70, 10, 22)
            pygame.draw.rect(surface, col, (x - 3, sr.top + 1, 6, 3))
        if is_open:
            # light leaking up between the slats
            k = 0.5 + 0.5 * math.sin(t * 3.0)
            for x in range(sr.left + 12, sr.right, 24):
                pygame.draw.line(surface, _mix(c, gdev.CYAN, 0.3 + 0.3 * k), (x, sr.top + 6), (x, sr.bottom - 1))
        # access panel (the F spot)
        pn = pygame.Rect(0, 0, 76, 20)
        pn.midbottom = (sr.centerx, sr.top - 4)
        pygame.draw.rect(surface, (3, 16, 12), pn)
        acc = gdev.CYAN if is_open else _mix(DOOR_DARK, gdev.MAGENTA, 0.6)
        pygame.draw.rect(surface, acc, pn, 1)
        if is_open:
            gdev.text(surface, ">_ D3SC3ND", (pn.x + 6, pn.y + 4), 9, gdev.CYAN)
        else:
            for i in range(12):
                pygame.draw.rect(surface, (60, 14, 40), (pn.x + 6 + i * 5, pn.y + 8, 3, 4))
        # labels above the apron
        if is_open:
            col = _mix(gdev.CYAN, gdev.WHITE, 0.25 + 0.25 * math.sin(t * 3.0))
            self._label(surface, "D33P C0R3 // 0P3N", (sr.centerx, ap.top - 4), col, "midbottom", 10)
        else:
            col = _mix(_scale(c, 1.6), gdev.MAGENTA, 0.35)
            self._label(surface, "D33P C0R3 // S34L3D", (sr.centerx, ap.top - 14), col, "midbottom", 10)
            self._label(surface, "12 K3Y5 R3QU1R3D", (sr.centerx, ap.top - 4), _scale(c, 1.5), "midbottom", 8)

    def _draw_cache(self, surface, r, c, ox, oy):
        """Spool's cache on the right edge: a sealed terminal in the wall, a floor plate in
        front of it. cache_state picks the look: sealed (amber lock), open (cyan), empty (dim)."""
        sr = r.move(-ox, -oy)
        t = self.time
        state = self.cache_state
        accent = {"open": gdev.CYAN, "empty": gdev.GREEN_DIM}.get(state, gdev.AMBER)
        ap = pygame.Rect(sr.left - 60, sr.centery - 64, 60, 128)
        pygame.draw.rect(surface, APRON_COL, ap)
        pygame.draw.rect(surface, _scale(c, 0.9), ap, 1)
        for y in range(ap.top + 8, ap.bottom - 4, 16):
            pygame.draw.line(surface, _scale(c, 0.75), (ap.left + 4, y), (ap.left + 12, y), 2)
        # the block, with seams
        pygame.draw.rect(surface, c, sr)
        for y in range(sr.top + 24, sr.bottom, 24):
            pygame.draw.line(surface, _scale(c, 0.8), (sr.left + 2, y), (sr.right - 1, y))
        pygame.draw.rect(surface, _scale(c, 1.3), sr, 1)
        # a light strip down the face
        if state == "open":
            off = int(t * 40) % 16
            for y in range(sr.top + off, sr.bottom, 16):
                pygame.draw.line(surface, gdev.CYAN, (sr.left + 1, y), (sr.left + 1, y + 6))
        elif state == "sealed":
            col = _mix(_scale(c, 0.8), gdev.AMBER, 0.35 + 0.25 * math.sin(t * 2.0))
            pygame.draw.line(surface, col, (sr.left + 1, sr.top + 2), (sr.left + 1, sr.bottom - 3))
        # the terminal screen in the wall
        scr = pygame.Rect(sr.left + 3, sr.centery - 22, max(4, sr.w - 6), 44)
        pygame.draw.rect(surface, (3, 14, 10), scr)
        pygame.draw.rect(surface, accent, scr, 1)
        if state == "sealed":
            _padlock(surface, (scr.centerx, scr.centery), gdev.AMBER)
        elif state == "open":
            if int(t * 2) % 2 == 0:
                pygame.draw.rect(surface, gdev.CYAN, (scr.x + 3, scr.centery + 6, 6, 2))
            gdev.text(surface, ">", (scr.x + 3, scr.y + 6), 10, gdev.CYAN)
        # labels on the floor plate side
        name_col = _mix(_scale(c, 1.6), accent, 0.4)
        self._label(surface, "SP00L'S C4CH3", (ap.left - 6, ap.top + 10), name_col, "topright", 10)
        line = {"open": "0P3N >_", "empty": "3MPTY"}.get(state, "S34L3D")
        col = accent
        if state == "open":
            col = _mix(gdev.CYAN, gdev.WHITE, 0.25 + 0.25 * math.sin(t * 3.0))
        self._label(surface, line, (ap.left - 6, ap.top + 24), col, "topright", 9)

    # -------------------------------------------------------------- the Deep Core (kind "deep_core")

    def _draw_deep_core(self, surface, camera):
        """The cathedral: an oval chamber of stepped walls around a floor of inlaid rings and
        ribs, a nave of cables running from the lift up to the Mirror's core, cover pillars
        with lamps, and the arena's spawn sockets."""
        view = camera.view_rect
        ox, oy = camera.offset
        t = self.time
        b = self.art_bounds
        floor = b.clip(view)
        if floor.w > 0 and floor.h > 0:
            surface.fill(self.floor_color, floor.move(-ox, -oy))
            # floor tiles: a faint 64px grid with a stud at every crossing
            g = 64
            for x in range(floor.left - floor.left % g, floor.right, g):
                pygame.draw.line(surface, DC_TILE, (x - ox, floor.top - oy), (x - ox, floor.bottom - oy))
            for y in range(floor.top - floor.top % g, floor.bottom, g):
                pygame.draw.line(surface, DC_TILE, (floor.left - ox, y - oy), (floor.right - ox, y - oy))
                for x in range(floor.left - floor.left % g, floor.right, g):
                    pygame.draw.rect(surface, DC_RING, (x - ox - 1, y - oy - 1, 3, 3))
        ch = self.chamber or {}
        cx, cy = ch.get("center", b.center)
        rx, ry = ch.get("radii", (b.w * 0.4, b.h * 0.4))
        core = pygame.Rect(self.arena.get("core", (cx - 60, cy - ry, 120, 50)))
        # inlaid rings, and one pulse ring travelling outward from the middle
        for i in range(1, 6):
            k = i / 5.6
            er = pygame.Rect(0, 0, int(2 * rx * k), int(2 * ry * k))
            er.center = (int(cx - ox), int(cy - oy))
            pygame.draw.ellipse(surface, DC_RING, er, 1)
        k = (t * 0.22) % 1.0
        er = pygame.Rect(0, 0, max(2, int(2 * rx * k)), max(2, int(2 * ry * k)))
        er.center = (int(cx - ox), int(cy - oy))
        pygame.draw.ellipse(surface, _mix(DC_RING, DC_PULSE, (1.0 - k) * 0.6), er, 1)
        # ribs: spokes from the core out to the walls
        hub = (core.centerx, core.bottom)
        for i in range(17):
            a = math.pi * (0.04 + 0.92 * i / 16.0)        # fan downward from the core
            end = (cx + math.cos(a) * rx * 1.05, cy + math.sin(a) * ry * 1.05)
            pygame.draw.line(surface, DC_RIB, (hub[0] - ox, hub[1] - oy), (int(end[0]) - ox, int(end[1]) - oy))
        # the nave: cables from the lift up to the core, data pulses flowing up
        nave = pygame.Rect(int(cx) - 36, core.bottom, 72, b.bottom - core.bottom)
        if nave.colliderect(view):
            pygame.draw.rect(surface, DC_NAVE, nave.move(-ox, -oy))
            for dx in (-22, -8, 8, 22):
                x = nave.centerx + dx - ox
                pygame.draw.line(surface, DC_RING, (x, nave.top - oy), (x, nave.bottom - oy))
            d = (t * 120.0) % 96.0
            y = nave.bottom - d
            while y > nave.top:
                if view.collidepoint(nave.centerx, y):
                    for dx in (-22, 22):
                        pygame.draw.rect(surface, DC_PULSE, (nave.centerx + dx - ox - 1, int(y) - oy - 2, 3, 4))
                y -= 96.0
        # spawn sockets (where the Mirror's reflections come out)
        for p in self.arena.get("ports", []):
            if view.collidepoint(p):
                sp = (int(p[0]) - ox, int(p[1]) - oy)
                pygame.draw.circle(surface, (3, 12, 10), sp, 13)
                pygame.draw.circle(surface, DC_RING, sp, 13, 1)
                kk = 0.5 + 0.5 * math.sin(t * 2.0 + p[0] * 0.01)
                pygame.draw.circle(surface, _mix(DC_RING, gdev.MAGENTA, 0.25 + 0.3 * kk), sp, 3)
        # walls (stepped oval) and blocks
        for (r, c) in self.walls:
            if r.colliderect(view):
                self._draw_block(surface, r, c, ox, oy, view)
        for i, (r, c) in enumerate(self.blocks):
            if not r.inflate(80, 80).colliderect(view):
                continue
            bid = self.block_ids[i] if i < len(self.block_ids) else ""
            sr = r.move(-ox, -oy)
            if bid.startswith("pillar"):
                pygame.draw.rect(surface, _scale(c, 0.5), sr.move(0, 4))         # shadow
                self._draw_block(surface, r, c, ox, oy, view)
                kk = 0.5 + 0.5 * math.sin(t * 1.5 + i)
                lamp = pygame.Rect(0, 0, 10, 10)
                lamp.center = sr.center
                pygame.draw.rect(surface, _mix(_scale(c, 1.2), DC_PULSE, 0.4 + 0.4 * kk), lamp)
                pygame.draw.rect(surface, _scale(c, 1.6), lamp, 1)
            elif bid == "mirror_core":
                self._draw_mirror_core(surface, r, c, sr)
            elif bid == "lift":
                self._draw_block(surface, r, c, ox, oy, view)
                for x in range(sr.left + 6, sr.right - 4, 10):
                    pygame.draw.line(surface, _scale(c, 0.7), (x, sr.top + 4), (x, sr.bottom - 4))
                self._label(surface, "L1FT // S34L3D", (sr.centerx, sr.top - 4), _scale(c, 1.8), "midbottom", 9)
            else:
                self._draw_block(surface, r, c, ox, oy, view)
        self.draw_void(surface, camera)

    def _draw_mirror_core(self, surface, r, c, sr):
        """Kernel's modelling core at the top of the nave: a pulsing slab with an eye."""
        t = self.time
        k = 0.5 + 0.5 * math.sin(t * 2.2)
        pygame.draw.rect(surface, _mix(c, gdev.MAGENTA, 0.15 * k), sr.inflate(10, 10), border_radius=4)
        pygame.draw.rect(surface, c, sr)
        pygame.draw.rect(surface, _scale(c, 1.5), sr, 1)
        for y in range(sr.top + 6, sr.bottom - 2, 6):
            pygame.draw.line(surface, _scale(c, 0.8), (sr.left + 4, y), (sr.right - 5, y))
        eye = pygame.Rect(0, 0, 44, 18)
        eye.center = sr.center
        pygame.draw.ellipse(surface, (4, 18, 16), eye)
        pygame.draw.ellipse(surface, _mix(DC_PULSE, gdev.MAGENTA, k * 0.5), eye, 1)
        pupil = (eye.centerx + int(math.sin(t * 0.7) * 10), eye.centery)
        pygame.draw.circle(surface, _mix(gdev.CYAN, gdev.MAGENTA, k), pupil, 4)
        self._label(surface, "M1RR0R // C0R3", (sr.centerx, sr.bottom + 6), _scale(c, 1.8), "midtop", 9)

    def _draw_floor_detail(self, surface, view, ox, oy):
        """Faint circuit traces and vias: the floor is a giant circuit board."""
        cell = 256
        b = self.art_bounds
        for cy in range(view.top // cell, view.bottom // cell + 1):
            for cx in range(view.left // cell, view.right // cell + 1):
                d = self._detail.get((cx, cy))
                if d is None:
                    d = self._make_detail(cx, cy, cell)
                    self._detail[(cx, cy)] = d
                for (a, e) in d[0]:
                    if b.collidepoint(a) and b.collidepoint(e):
                        pygame.draw.line(surface, TRACE_COL, (a[0] - ox, a[1] - oy), (e[0] - ox, e[1] - oy))
                for p in d[1]:
                    if b.collidepoint(p):
                        pygame.draw.rect(surface, VIA_COL, (p[0] - ox - 1, p[1] - oy - 1, 3, 3))

    def _make_detail(self, cx, cy, cell):
        rng = random.Random(cx * 7919 + cy * 104729 + 17)
        lines, vias = [], []
        for _ in range(rng.randint(1, 3)):
            x = cx * cell + rng.randrange(8, cell - 8, 8)
            y = cy * cell + rng.randrange(8, cell - 8, 8)
            # an L-shaped trace with a 45-degree knee, ending in vias
            l1 = rng.choice((-1, 1)) * rng.randrange(24, 120, 8)
            l2 = rng.choice((-1, 1)) * rng.randrange(16, 80, 8)
            knee = 8 if l2 > 0 else -8
            if rng.random() < 0.5:
                p1 = (x + l1, y)
                p2 = (p1[0] + (8 if l1 > 0 else -8), y + knee)
                p3 = (p2[0], y + l2)
            else:
                p1 = (x, y + l1)
                p2 = (x + knee, p1[1] + (8 if l1 > 0 else -8))
                p3 = (x + l2, p2[1])
            lines += [((x, y), p1), (p1, p2), (p2, p3)]
            vias += [(x, y), p3]
        for _ in range(rng.randint(0, 4)):
            vias.append((cx * cell + rng.randrange(0, cell, 8), cy * cell + rng.randrange(0, cell, 8)))
        return (lines, vias)

    def _draw_trunks(self, surface, view, ox, oy):
        for r in self.trunks:
            if r.colliderect(view):
                sr = r.move(-ox, -oy)
                pygame.draw.rect(surface, TRUNK_COL, sr)
                if r.w > r.h:
                    for dy in (9, 22):
                        pygame.draw.line(surface, TRUNK_CABLE, (sr.left, sr.top + dy), (sr.right - 1, sr.top + dy))
                else:
                    for dx in (9, 22):
                        pygame.draw.line(surface, TRUNK_CABLE, (sr.left + dx, sr.top), (sr.left + dx, sr.bottom - 1))
        # data pulses running along the cables
        for a, e in self.trunk_paths:
            dx, dy = e[0] - a[0], e[1] - a[1]
            length = math.hypot(dx, dy)
            if length < 1:
                continue
            seg = pygame.Rect(min(a[0], e[0]) - 16, min(a[1], e[1]) - 16, abs(dx) + 32, abs(dy) + 32)
            if not seg.colliderect(view):
                continue
            ux, uy = dx / length, dy / length
            d = (self.time * 90.0) % 160.0
            while d < length:
                px, py = a[0] + ux * d, a[1] + uy * d
                if view.collidepoint(px, py):
                    # sit on one of the two cables (7px to the side of the centre line)
                    qx, qy = int(px - ox + uy * 7), int(py - oy - ux * 7)
                    pygame.draw.rect(surface, (40, 140, 70), (qx - 1, qy - 1, 3, 3))
                d += 160.0

    def _draw_zones(self, surface, view, ox, oy, floor):
        """Influence Zones: the floor inside is a faintly magenta shade, with a
        dotted border. Drawn as solid colour (no blending), before the details."""
        fr = floor.move(-ox, -oy)
        # cleared (safe, lit) zones first, so a live Daemon's tint wins where two zones overlap
        order = sorted(self.racks, key=lambda rk: rk.id not in self.cleared_racks)
        for rk in order:
            if not rk.zone_rect.colliderect(view):
                continue
            cleared = rk.id in self.cleared_racks
            tint = self.clear_floor if cleared else self.zone_floor
            cx, cy = rk.zone_center
            rr = rk.zone_radius * rk.zone_radius
            corners = (view.topleft, view.topright, view.bottomleft, view.bottomright)
            if all((p[0] - cx) ** 2 + (p[1] - cy) ** 2 <= rr for p in corners):
                surface.fill(tint, fr)
                continue
            old = surface.get_clip()
            surface.set_clip(fr.clip(old))
            pygame.draw.circle(surface, tint, (int(cx - ox), int(cy - oy)), int(rk.zone_radius))
            surface.set_clip(old)
            if cleared:
                # a calm, solid green border: this territory is yours now
                for i, p in enumerate(rk.ring):
                    if i % 2 == 0 and view.collidepoint(p):
                        pygame.draw.rect(surface, CLEAR_RING, (p[0] - ox - 1, p[1] - oy - 1, 3, 2))
                continue
            phase = int(self.time * 2)
            for i, p in enumerate(rk.ring):
                if (i + phase) % 3 and view.collidepoint(p):
                    pygame.draw.rect(surface, ZONE_RING, (p[0] - ox - 1, p[1] - oy - 1, 2, 2))

    def draw_void(self, surface, camera):
        """DATA LOSS VOID: black outside the art bounds, with drifting hex,
        glitch tears and a crumbling edge. It is also an invisible wall."""
        view = camera.view_rect
        if self.art_bounds.contains(view):
            return
        ox, oy = camera.offset
        b = self.art_bounds
        t = self.time
        strips = []
        if view.left < b.left:
            strips.append(pygame.Rect(view.left, view.top, b.left - view.left, view.h))
        if view.right > b.right:
            strips.append(pygame.Rect(b.right, view.top, view.right - b.right, view.h))
        if view.top < b.top:
            strips.append(pygame.Rect(view.left, view.top, view.w, b.top - view.top))
        if view.bottom > b.bottom:
            strips.append(pygame.Rect(view.left, b.bottom, view.w, view.bottom - b.bottom))
        for s in strips:
            surface.fill((0, 0, 0), s.move(-ox, -oy))

        # drifting hex: one glyph per 48x24 cell, slowly falling, re-rolled every ~2s
        cw, ch = 48, 24
        drift = t * 9.0
        for s in strips:
            for gy in range(int((s.top - drift) // ch) - 1, int((s.bottom - drift) // ch) + 1):
                for gx in range(s.left // cw, s.right // cw + 1):
                    phase = (gx * 31 + gy * 17) % 7
                    h = _hash(gx, gy, int((t + phase) / 2.0))
                    if h % 100 >= 35:
                        continue
                    x = gx * cw + (h >> 8) % (cw - 14)
                    y = int(gy * ch + drift)
                    if not s.collidepoint(x, y) or b.collidepoint(x, y):
                        continue
                    col = VOID_HEX_HOT if (h >> 16) % 100 < 12 else VOID_HEX
                    gdev.text(surface, "%02X" % ((h >> 24) & 0xFF), (x - ox, y - oy), 10, col)
            # a faint DATA LOSS stamp now and then
            if s.w >= 90 and s.h >= 40:
                lx = s.centerx - (s.centerx % 512) + 256 if s.w > 512 else s.centerx
                ly = s.centery - (s.centery % 512) + 256 if s.h > 512 else s.centery
                if s.collidepoint(lx, ly) and int(t * 0.7 + lx * 0.01) % 3 != 0:
                    gdev.text(surface, "DATA LOSS", (lx - ox, ly - oy), 10, (40, 26, 38), "center", True)

        # glitch tears: short horizontal lines that jump around ~10 times a second
        rng = random.Random(int(t * 10))
        for s in strips:
            for _ in range(max(1, (s.w * s.h) // 18000)):
                y = rng.randrange(s.top, max(s.top + 1, s.bottom))
                x = rng.randrange(s.left, max(s.left + 1, s.right))
                w = rng.randrange(8, 90)
                col = (60, 12, 50) if rng.random() < 0.5 else (12, 50, 26)
                pygame.draw.line(surface, col, (x - ox, y - oy), (min(x + w, s.right) - ox, y - oy))

        # crumbling edge: chunks of floor fall off into the void (changes ~4x a second)
        er = random.Random(int(t * 4))
        step = 8
        act = max(0, min(4, int(self.act)))
        deep = 14 + 6 * act         # the band thickens each act (doc 3.1): debris further out...
        bite = 4 * act              # ...and the void bites into the floor edge
        if b.right < view.right and b.right >= view.left:
            for y in range(view.top - view.top % step, view.bottom, step):
                n = er.randrange(0, deep) if er.random() < 0.6 else 0
                if n and b.top <= y < b.bottom:
                    surface.fill(self.floor_color, (b.right - ox, y - oy, n, step - er.randrange(0, 4)))
                if bite and b.top <= y < b.bottom and er.random() < 0.5:
                    m = er.randrange(1, bite + 1)
                    surface.fill((0, 0, 0), (b.right - m - ox, y - oy, m, step))
                if er.random() < 0.15:
                    surface.fill((60, 12, 50), (b.right - ox + n + er.randrange(0, 20), y - oy, 2, 2))
        if b.bottom < view.bottom and b.bottom >= view.top:
            for x in range(view.left - view.left % step, view.right, step):
                n = er.randrange(0, deep) if er.random() < 0.6 else 0
                if n and b.left <= x < b.right:
                    surface.fill(self.floor_color, (x - ox, b.bottom - oy, step - er.randrange(0, 4), n))
                if bite and b.left <= x < b.right and er.random() < 0.5:
                    m = er.randrange(1, bite + 1)
                    surface.fill((0, 0, 0), (x - ox, b.bottom - m - oy, step, m))
                if er.random() < 0.15:
                    surface.fill((60, 12, 50), (x - ox, b.bottom - oy + n + er.randrange(0, 20), 2, 2))


class Camera(object):
    """Follows a target with an 80x60 deadzone and a 0.15 lerp, clamped to bounds.

    The clamp box is the bounds grown by VOID_PEEK on every side, so the edge of
    the DATA LOSS VOID is visible when you walk up to it.

    x, y           top-left of the view in world pixels (floats)
    offset         (int x, int y) -- subtract from world positions to draw
    view_rect      pygame.Rect of the visible world
    update(dt, target, deadzone=True, lerp=None)   target is (x, y) in world pixels
    snap(target)         jump straight to the target (respawn, new run)
    zoom                 the world layer is drawn at INTERNAL_SIZE / zoom and scaled up
    world_to_screen(p)   world -> world-layer pixels (what world/minion/player drawing uses)
    screen_to_world(p)   world-layer pixels -> world
    world_to_internal(p) world -> internal-surface pixels (HUD, hack window, floaters)
    internal_to_world(p) internal-surface pixels -> world (mouse positions are
                         already internal; Game converts window coords)
    """
    DEADZONE = (80, 60)
    LERP = 0.15

    def __init__(self, bounds, zoom=1, margin=VOID_PEEK):
        self.bounds = pygame.Rect(bounds).inflate(2 * margin, 2 * margin)
        self.zoom = 1
        self.w, self.h = gdev.INTERNAL_SIZE
        self.x = 0.0
        self.y = 0.0
        self.focus_x = 0.0      # point the camera wants to centre on
        self.focus_y = 0.0
        self.set_zoom(zoom)

    def set_zoom(self, zoom):
        """Change the zoom, keeping the same world point in the middle of the view."""
        cx, cy = self.x + self.w / 2.0, self.y + self.h / 2.0
        self.zoom = max(1, int(zoom))
        # rounded up, so the scaled layer always covers the whole internal surface
        self.w = -(-gdev.INTERNAL_SIZE[0] // self.zoom)
        self.h = -(-gdev.INTERNAL_SIZE[1] // self.zoom)
        self.x, self.y = cx - self.w / 2.0, cy - self.h / 2.0
        self._clamp()

    @property
    def offset(self):
        return (int(round(self.x)), int(round(self.y)))

    @property
    def view_rect(self):
        ox, oy = self.offset
        return pygame.Rect(ox, oy, self.w, self.h)

    def snap(self, target):
        self.focus_x, self.focus_y = float(target[0]), float(target[1])
        self.x = self.focus_x - self.w / 2.0
        self.y = self.focus_y - self.h / 2.0
        self._clamp()

    def update(self, dt, target, deadzone=True, lerp=None):
        """deadzone=False aims straight at the target, e.g. while hacking; lerp overrides LERP."""
        tx, ty = float(target[0]), float(target[1])
        hw, hh = (self.DEADZONE[0] / 2.0, self.DEADZONE[1] / 2.0) if deadzone else (0.0, 0.0)
        if tx > self.focus_x + hw:
            self.focus_x = tx - hw
        elif tx < self.focus_x - hw:
            self.focus_x = tx + hw
        if ty > self.focus_y + hh:
            self.focus_y = ty - hh
        elif ty < self.focus_y - hh:
            self.focus_y = ty + hh
        k = 1.0 - (1.0 - (lerp or self.LERP)) ** (dt * 60.0)     # 0.15 per 60 Hz frame
        self.x += (self.focus_x - self.w / 2.0 - self.x) * k
        self.y += (self.focus_y - self.h / 2.0 - self.y) * k
        self._clamp()

    def _clamp(self):
        b = self.bounds
        if b.w <= self.w:
            self.x = b.centerx - self.w / 2.0
        else:
            self.x = max(b.left, min(self.x, b.right - self.w))
        if b.h <= self.h:
            self.y = b.centery - self.h / 2.0
        else:
            self.y = max(b.top, min(self.y, b.bottom - self.h))

    def world_to_screen(self, p):
        ox, oy = self.offset
        return (int(round(p[0])) - ox, int(round(p[1])) - oy)

    def screen_to_world(self, p):
        ox, oy = self.offset
        return pygame.math.Vector2(p[0] + ox, p[1] + oy)

    def world_to_internal(self, p):
        sx, sy = self.world_to_screen(p)
        return (sx * self.zoom, sy * self.zoom)

    def internal_to_world(self, p):
        return self.screen_to_world((p[0] / float(self.zoom), p[1] / float(self.zoom)))
