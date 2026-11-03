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


def _hash(a, b, c=0):
    """Cheap deterministic hash of three ints -> 0..2**32-1 (void noise)."""
    h = (a * 73856093) ^ (b * 19349663) ^ (c * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    return (h ^ (h >> 16)) & 0xFFFFFFFF


def _scale(c, k):
    return (min(255, int(c[0] * k)), min(255, int(c[1] * k)), min(255, int(c[2] * k)))


def _mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


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
        self.solids = [r for r, _c in self.walls] + [r.shell for r in self.racks] + \
                      [n.rect for n in self.nodes] + [r for r, _c in self.blocks]
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

    def respawn_point(self):
        """Free floor right next to Clean Node 1 (above it first, like player_start)."""
        n = self.node("N1")
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
        if r.inflate(240, 240).collidepoint(sx, sy) and self.rect_free(pygame.Rect(sx - 8, sy - 8, 16, 16)):
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
                if b.hittable and b.rect.colliderect(rect):
                    return ("bar", rk, b)
        for rk in self.racks:
            if rk.shell.colliderect(rect):
                return ("shell", rk, None)
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

    # -------------------------------------------------------------- drawing

    def draw(self, surface, camera):
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
            if r.colliderect(view):
                self._draw_block(surface, r, c, ox, oy, view)
                bid = self.block_ids[i] if i < len(self.block_ids) else ""
                if bid == "deep_core_hatch":
                    self._label(surface, "D33P C0R3 // S34L3D", (r.centerx - ox, r.top - oy - 4),
                                _scale(c, 1.6), "midbottom")
        for n in self.nodes:
            if n.rect.colliderect(view):
                self._draw_node(surface, n, ox, oy, view)
        for rk in self.racks:
            if rk.shell.colliderect(view.inflate(64, 64)):
                self._draw_rack(surface, rk, ox, oy, view)
        self.draw_void(surface, camera)

    def _label(self, surface, s, pos, color, align="topleft", size=10):
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
            self._label(surface, "CLEAN NODE %s" % num, (sr.centerx, sr.top + 14), col, "midtop", 12)
            self._label(surface, "save // shop // terminal", (sr.centerx, sr.top + 30), _scale(c, 1.5),
                        "midtop", 10)
            scr = pygame.Rect(0, 0, 60, 36)
            scr.center = (sr.centerx, sr.top + 72)
            pygame.draw.rect(surface, (4, 22, 10), scr)
            pygame.draw.rect(surface, col, scr, 1)
            if int(self.time * 2) % 2 == 0:
                pygame.draw.rect(surface, col, (scr.x + 6, scr.y + 24, 6, 2))
            gdev.text(surface, ">_", (scr.x + 6, scr.y + 6), 10, col)

    def _draw_rack(self, surface, rk, ox, oy, view):
        c = rk.color
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
        for rk in self.racks:
            if not rk.zone_rect.colliderect(view):
                continue
            cx, cy = rk.zone_center
            rr = rk.zone_radius * rk.zone_radius
            corners = (view.topleft, view.topright, view.bottomleft, view.bottomright)
            if all((p[0] - cx) ** 2 + (p[1] - cy) ** 2 <= rr for p in corners):
                surface.fill(self.zone_floor, fr)
                continue
            old = surface.get_clip()
            surface.set_clip(fr.clip(old))
            pygame.draw.circle(surface, self.zone_floor, (int(cx - ox), int(cy - oy)), int(rk.zone_radius))
            surface.set_clip(old)
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
        if b.right < view.right and b.right >= view.left:
            for y in range(view.top - view.top % step, view.bottom, step):
                n = er.randrange(0, 14) if er.random() < 0.6 else 0
                if n and b.top <= y < b.bottom:
                    surface.fill(self.floor_color, (b.right - ox, y - oy, n, step - er.randrange(0, 4)))
                if er.random() < 0.15:
                    surface.fill((60, 12, 50), (b.right - ox + n + er.randrange(0, 20), y - oy, 2, 2))
        if b.bottom < view.bottom and b.bottom >= view.top:
            for x in range(view.left - view.left % step, view.right, step):
                n = er.randrange(0, 14) if er.random() < 0.6 else 0
                if n and b.left <= x < b.right:
                    surface.fill(self.floor_color, (x - ox, b.bottom - oy, step - er.randrange(0, 4), n))
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
