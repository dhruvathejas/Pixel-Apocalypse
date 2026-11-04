"""Boss attack library (doc 7.1): radial_burst, port_beam, port_overload, packet_rain, blackout,
firewall_dome, with telegraphs, aim player|lead, terminal-overlay telegraphs and damage to the player.

Every Daemon and the Mirror fire attacks through AttackSystem.spawn(entry, origin, params, owner).
An entry is a library name or {"id": name, ...params} as written in bosses/*.json.

Time: telegraphs and attack lifetimes count in WORLD time (update gets world dt), so a 1s telegraph
lasts 3.3s of real time while the player is jacked in. An attack shows its telegraph for
telegraph_s, then stays armed (pulsing, full) for delay_s more (the attack_delay_s modifier), then
lands. Damage only goes through player.take_damage(n, from_pos): dash i-frames dodge everything,
a katana parry reflects bolts, and Game turns a hit on a rooted player into +35 TRACE.

Weapon variants (params["weapon"], set by the copy_player_weapon modifier, doc 7.3):
  BOW     slow piercing charged lines with a long telegraph
  PISTOL  fast 6-shot bursts with a 1s reload gap, a laser sight before each burst
  KATANA  short arcs next to the player, and the firewall_dome comes in close

Coordinates: Attack shapes are in world pixels. draw() draws on the WORLD LAYER with
camera.world_to_screen; draw_overlay() and draw_terminal_overlay() draw on the full-size internal
surface with camera.world_to_internal (and the jack-in zoom while hacking).
"""
import math
import random

import pygame

import gdev
import minions as minions_mod

V = pygame.math.Vector2

LIBRARY = ("radial_burst", "port_beam", "port_overload", "packet_rain", "blackout", "firewall_dome")
WEAPONS = ("BOW", "PISTOL", "KATANA")
DEFAULT_TELEGRAPH_S = 1.2

# telegraph seconds (world time) per attack, when the entry gives no telegraph_s
TELEGRAPH_S = {"radial_burst": 0.9, "port_beam": 1.0, "port_overload": 0.6, "packet_rain": 1.0,
               "blackout": 0.8, "firewall_dome": 0.9}
BOW_CHARGE_S = 1.0              # BOW variants: extra telegraph (the charge-up)
KATANA_TELEGRAPH_S = 0.9       # KATANA arcs are quick but close
PISTOL_SIGHT_S = 1.0            # laser sight before each 6-shot burst

DIRVEC = [V(math.cos(i * math.pi / 4), math.sin(i * math.pi / 4)) for i in range(8)]
PLAYER_R = 5.0                  # about half the player's 8x10 body, for overlap tests
NEAR_MISS_PX = 48.0
PREDICT_PX = 64.0               # predict: aim this far along the predicted dodge (x probability)
LEAD_SPEED_CAP = 190.0          # aim: lead uses at most this speed (walking, not a dash burst)

# radial_burst
BURST_COUNT = 10
BURST_SPEED = 85.0
BURST_WAVE_GAP_S = 0.9
# port_beam
BEAM_WIDTH = 10.0
BEAM_ACTIVE_S = 0.45
BEAM_SWEEP_S = 0.8
# packet_rain
RAIN_COUNT = 3
RAIN_RADIUS = 24.0
RAIN_STAGGER_S = 0.18
RAIN_FLASH_S = 0.25
# blackout
BLACKOUT_S = 3.5
BLACKOUT_FADE_S = 0.6
BLACKOUT_ALPHA = 200
BLACKOUT_LIGHT = 100.0           # light pool radius around the player, world px
# firewall_dome
DOME_SPEED = 190.0
DOME_THICK = 8.0
DOME_KNOCK = 360.0
DOME_KNOCK_T = 0.12
# weapon variants
ARROW_SPEED = 190.0
BULLET_SPEED = 220.0
PISTOL_SHOTS = 4
PISTOL_INTERVAL = 0.14
PISTOL_RELOAD_S = 1.0
PISTOL_BURSTS = 1
KATANA_ARCS = 2
KATANA_RADIUS = 34.0
KATANA_HALF = math.radians(70)
KATANA_ACTIVE_S = 0.16

THREAT = gdev.MAGENTA
HOT = gdev.RED


def lead_point(player, delay_s):
    """Where the player will be after delay_s at the current velocity (aim: lead, doc 7.1/9.4)."""
    return pygame.math.Vector2(player.pos) + pygame.math.Vector2(player.vel) * float(delay_s)


def _seg_dist(p, a, b):
    """Distance from point p to the segment a-b (all Vector2)."""
    ab = b - a
    n = ab.length_squared()
    if n <= 1e-9:
        return (p - a).length()
    k = max(0.0, min(1.0, (p - a).dot(ab) / n))
    return (p - (a + ab * k)).length()


def _ang_diff(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def _owner_pos(owner, default):
    """The Mirror has a pos; a Daemon is its rack, so its attacks come from the given origin."""
    p = getattr(owner, "pos", None) if owner is not None else None
    return V(p) if p is not None else V(default)


def _blink(t, hz=8.0):
    return int(t * hz) % 2 == 0


def _dashed(surface, color, a, b, width=1, dash=4, gap=3):
    seg = surface.get_clip().inflate(2 * width + 2, 2 * width + 2).clipline(a, b)
    if not seg:
        return                      # nothing of it on this surface
    a0 = V(a)
    a, b = V(seg[0]), V(seg[1])
    if (a - a0).length_squared() > 0 and (b - a0).length_squared() < (a - a0).length_squared():
        a, b = b, a                 # keep the dash pattern running from the original start
    # start on the original pattern so the dashes do not crawl as the clip moves
    off = (a - a0).length() % (dash + gap)
    d = b - a
    n = d.length()
    if n < 1:
        return
    u = d / n
    s = -off
    while s < n:
        e = min(n, s + dash)
        if e > 0:
            pygame.draw.line(surface, color, a + u * max(0.0, s), a + u * e, width)
        s = e + gap


def _dashed_circle(surface, color, c, r, phase=0.0, dashes=16, width=1):
    """A ring of `dashes` arcs (pygame.draw.arc does the work in C; big domes stay cheap)."""
    clip = surface.get_clip()
    rect = pygame.Rect(0, 0, int(2 * r), int(2 * r))
    rect.center = (int(c[0]), int(c[1]))
    if rect.w < 4 or not clip.colliderect(rect):
        return
    step = 2 * math.pi / dashes
    for i in range(dashes):
        a = phase + i * step
        pygame.draw.arc(surface, color, rect, a, a + step * 0.55, width)


def _sector_points(c, r, facing, half, steps=10):
    pts = [(c.x, c.y)]
    for i in range(steps + 1):
        a = facing - half + 2 * half * i / float(steps)
        pts.append((c.x + math.cos(a) * r, c.y + math.sin(a) * r))
    return pts


_disc_cache = {}


def _alpha_disc(surface, color, alpha, center, r):
    """Translucent filled circle (world layer or internal), cached by size and colour."""
    r = max(1, int(r))
    key = (r, color, int(alpha) // 8)
    img = _disc_cache.get(key)
    if img is None:
        if len(_disc_cache) > 256:
            _disc_cache.clear()
        img = pygame.Surface((2 * r + 1, 2 * r + 1), pygame.SRCALPHA)
        pygame.draw.circle(img, color + (int(alpha) // 8 * 8,), (r, r), r)
        _disc_cache[key] = img
    surface.blit(img, (int(center[0]) - r, int(center[1]) - r))


class _Mapper(object):
    """World -> drawing-surface points. scale: surface px per world px.
    world layer: camera.world_to_screen; internal: world_to_internal, then the jack-in zoom."""

    def __init__(self, camera, internal=False, zoom_map=None):
        self.cam = camera
        self.internal = internal
        self.zoom_map = zoom_map
        z = float(getattr(camera, "zoom", 1)) if internal else 1.0
        self.scale = z * (zoom_map[2] if zoom_map else 1.0)

    def pt(self, p):
        if not self.internal:
            return self.cam.world_to_screen(p)
        x, y = self.cam.world_to_internal(p)
        if self.zoom_map:
            x0, y0, kx, ky = self.zoom_map
            return (int((x - x0) * kx), int((y - y0) * ky))
        return (x, y)


# ---------------------------------------------------------------- bolts (own projectiles)

class Shot(object):
    """A BOW arrow or PISTOL bullet fired by an attack. Kept by its Attack (not by minions.py) so
    it can look like the weapon it copies. A katana parry turns it into a reflected
    minions.Packet (team "player") that strips enemy firewalls like a parried P1NG packet."""

    def __init__(self, pos, vel, damage=1, kind="bullet", life=2.0, pierce=False):
        self.pos = V(pos)
        self.vel = V(vel)
        self.damage = damage
        self.kind = kind
        self.life = life
        self.pierce = pierce
        self.alive = True
        self.hit = False
        self.near = False
        self.near_done = False
        size = 6 if kind == "arrow" else 4
        self.rect = pygame.Rect(0, 0, size, size)
        self.rect.center = (int(self.pos.x), int(self.pos.y))

    def update(self, dt, system):
        run = system.run
        player = run.player
        world = run.world
        self.pos += self.vel * dt
        self.rect.center = (int(self.pos.x), int(self.pos.y))
        self.life -= dt
        if self.life <= 0:
            self.alive = False
        elif self.pierce:
            if not world.art_bounds.colliderect(self.rect):
                self.alive = False
        elif world.projectile_hit(self.rect) is not None:
            self.alive = False
        if self.alive and not self.hit and not player.dead and self.rect.colliderect(player.rect):
            if player.take_damage(self.damage, self.pos - self.vel * 0.05):
                self.hit = True
                self.near_done = True
                if not self.pierce:
                    self.alive = False
            elif getattr(player, "parrying", False):
                self._reflect(run)
        if not self.near_done and not player.dead:
            d2 = (self.pos - player.pos).length_squared()
            if d2 <= NEAR_MISS_PX * NEAR_MISS_PX:
                self.near = True
            elif self.near:
                system.near_miss(self.pos)
                self.near_done = True
        if not self.alive and self.near and not self.near_done:
            system.near_miss(self.pos)
            self.near_done = True

    def _reflect(self, run):
        p = minions_mod.Packet(self.pos, -self.vel * 1.5, self.damage, "player", 2.0)
        p.reflected = True
        p.near_done = True
        run.minions.enemy_projectiles.append(p)
        self.alive = False
        self.near_done = True

    def draw(self, surface, mapper, view):
        if not view.collidepoint(self.pos):
            return
        if self.vel.length_squared() < 1e-6:
            return
        u = self.vel.normalize()
        head = V(mapper.pt(self.pos))
        if self.kind == "arrow":
            tail = head - u * 8
            pygame.draw.line(surface, (120, 30, 100), tail - u * 6, tail, 1)
            pygame.draw.line(surface, THREAT, tail, head, 2)
            pygame.draw.line(surface, gdev.WHITE, head - u * 2, head, 2)
        else:
            pygame.draw.line(surface, (130, 30, 40), head - u * 5, head, 1)
            pygame.draw.circle(surface, HOT, (int(head.x), int(head.y)), 1)
            surface.set_at((int(head.x), int(head.y)), gdev.WHITE)


# ---------------------------------------------------------------- attack base

class Attack(object):
    """One live attack. Public fields (contract):
      id (library name), owner (boss/mirror object or None), params dict, origin Vector2
      state "telegraph"|"active"|"done", t (seconds left in the current state, world time)
      telegraph_total, aim Vector2 (the locked aim point)
      shape: ("line", a, b, width) | ("circle", centre, radius) | ("dome", centre, radius) |
             ("screen",) | ("burst", centre, [angles], reach) | ("arc", centre, radius, facing,
             half_angle) | ("ports", [points])
      lands_on(pos) -> bool   True if the pos is inside what this attack will hit (terminal overlay)
    Extra: variant (the copied weapon or None), aim_mode "player"|"lead", shots [Shot]."""

    default_telegraph = DEFAULT_TELEGRAPH_S
    active_s = 0.2

    def __init__(self, system, attack_id, origin, params, owner, aim):
        self.system = system
        self.run = system.run
        self.id = attack_id
        self.owner = owner
        self.params = params
        self.origin = V(origin)
        w = params.get("weapon")
        self.variant = w if w in WEAPONS else None
        self.aim_mode = params.get("aim", "player")
        self.aim = V(aim)
        self.damage = int(params.get("damage", 1))
        self.telegraph_s = max(0.0, float(params.get("telegraph_s", self.default_telegraph)))
        self.delay_s = max(0.0, float(params.get("delay_s", 0.0)))
        self.telegraph_total = self.telegraph_s + self.delay_s
        self.t = self.telegraph_total
        self.state = "telegraph"
        self.age = 0.0
        self.shots = []
        self.hit_player = False
        self.shape = ("screen",)

    # -- life cycle

    def progress(self):
        """0..1: how far the telegraph has filled (1 while armed during delay_s)."""
        if self.state != "telegraph":
            return 1.0
        if self.telegraph_s <= 0:
            return 1.0
        return max(0.0, min(1.0, (self.telegraph_total - self.t) / self.telegraph_s))

    def update(self, wdt):
        if wdt <= 0:
            return
        self.age += wdt
        if self.state == "telegraph":
            self.t -= wdt
            if self.t <= 0:
                self.state = "active"
                self.t = self.active_s
                self.land()
        elif self.state == "active":
            self.t -= wdt
            self.tick(wdt)
        for s in self.shots:
            s.update(wdt, self.system)
        self.shots = [s for s in self.shots if s.alive]
        if self.state == "active" and self.t <= 0 and not self.shots and self.finished():
            self.state = "done"

    def land(self):
        pass

    def tick(self, wdt):
        pass

    def finished(self):
        return True

    def lands_on(self, pos):
        return False

    def eta(self):
        """World seconds until this attack hits whatever lands_on() covers (None: not pending)."""
        return self.t if self.state == "telegraph" else None

    # -- helpers

    def strike(self, from_pos):
        """Damage the player once per attack. True if it hurt."""
        p = self.run.player
        if self.hit_player or p.dead:
            return False
        if p.take_damage(self.damage, from_pos):
            self.hit_player = True
            return True
        return False

    def missed_by(self, dist_out):
        """Area attack landed without a hit: near_miss when the player was within 48px of it."""
        p = self.run.player
        if not self.hit_player and not p.dead and dist_out <= NEAR_MISS_PX:
            self.system.near_miss(p.pos)

    def edge_point(self):
        """The world point an off-screen telegraph marker points at."""
        return self.origin

    def visible_in(self, view):
        return view.collidepoint(self.edge_point())

    # -- drawing (world layer); draw_outline is shared with the overlays

    def draw(self, surface, mapper, view):
        self.draw_outline(surface, mapper, THREAT, 1)

    def draw_outline(self, surface, mapper, color, width):
        pass


# ---------------------------------------------------------------- radial_burst

class RadialBurst(Attack):
    """A ring of P1NG packets from the port (or the Mirror). One spoke aims at the aim point;
    count packets per wave, `waves` waves BURST_WAVE_GAP_S apart, each turned half a step."""
    default_telegraph = TELEGRAPH_S["radial_burst"]

    def __init__(self, *a):
        Attack.__init__(self, *a)
        self.count = max(3, int(self.params.get("count", BURST_COUNT)))
        self.waves = max(1, int(self.params.get("waves", 1)))
        self.speed = float(self.params.get("speed", BURST_SPEED))
        d = self.aim - self.origin
        self.base = math.atan2(d.y, d.x) if d.length_squared() > 1 else 0.0
        self.reach = max(320.0, d.length() + 260.0)
        self.fired = 0
        self.wave_t = 0.0
        step = 2 * math.pi / self.count
        self.shape = ("burst", self.origin, [self.base + i * step for i in range(self.count)], self.reach)

    def land(self):
        self._fire_wave()

    def _fire_wave(self):
        step = 2 * math.pi / self.count
        off = self.base + (step / 2.0 if self.fired % 2 else 0.0)
        life = min(6.0, self.reach / self.speed)
        out = self.run.minions.enemy_projectiles
        for i in range(self.count):
            a = off + i * step
            u = V(math.cos(a), math.sin(a))
            out.append(minions_mod.Packet(self.origin + u * 6, u * self.speed, self.damage, "enemy", life))
        self.fired += 1
        self.wave_t = BURST_WAVE_GAP_S
        self.t = max(self.t, BURST_WAVE_GAP_S if self.fired < self.waves else 0.0)

    def tick(self, wdt):
        if self.fired < self.waves:
            self.wave_t -= wdt
            if self.wave_t <= 0:
                self._fire_wave()

    def finished(self):
        return self.fired >= self.waves

    def lands_on(self, pos):
        d = V(pos) - self.origin
        n = d.length()
        if n < 1 or n > self.reach:
            return n < 1
        a = math.atan2(d.y, d.x)
        tol = math.atan2(PLAYER_R + 3, n)
        return any(abs(_ang_diff(a, s)) <= tol for s in self.shape[2])

    def draw_outline(self, surface, mapper, color, width):
        if self.state != "telegraph":
            return
        c = mapper.pt(self.origin)
        k = self.progress()
        sc = mapper.scale
        r0 = 6 * sc
        r1 = (6 + 22 * k) * sc
        for a in self.shape[2]:
            u = (math.cos(a), math.sin(a))
            pygame.draw.line(surface, color, (c[0] + u[0] * r0, c[1] + u[1] * r0),
                             (c[0] + u[0] * r1, c[1] + u[1] * r1), width)
        pygame.draw.circle(surface, color, c, max(2, int((4 + 10 * (1 - k)) * sc)), width)

    def draw(self, surface, mapper, view):
        if self.state != "telegraph":
            return
        c = mapper.pt(self.origin)
        k = self.progress()
        _alpha_disc(surface, THREAT, 40 + 90 * k, c, 5 + 6 * k)
        col = gdev.WHITE if k >= 1 and _blink(self.age, 10) else THREAT
        self.draw_outline(surface, mapper, col, 1)
        # the aimed spoke is drawn longer, so you see which way the ring is turned
        u = V(math.cos(self.base), math.sin(self.base))
        _dashed(surface, (150, 40, 120), V(c) + u * 30, V(c) + u * (30 + 50 * k), 1, 3, 3)


# ---------------------------------------------------------------- port_beam

class PortBeam(Attack):
    """A telegraphed beam from a bar's spawn_exit through the aim point. sweep_deg > 0 makes it
    sweep across that many degrees (centred on the aim) over sweep_s instead of a straight shot."""
    default_telegraph = TELEGRAPH_S["port_beam"]
    active_s = BEAM_ACTIVE_S

    def __init__(self, *a):
        Attack.__init__(self, *a)
        d = self.aim - self.origin
        n = d.length()
        self.dir = d / n if n > 1 else V(1, 0)
        self.length = max(220.0, min(900.0, float(self.params.get("length", n + 220.0))))
        self.width = float(self.params.get("width", BEAM_WIDTH))
        self.sweep = math.radians(float(self.params.get("sweep_deg", 0.0)))
        self.sweep_s = float(self.params.get("sweep_s", BEAM_SWEEP_S))
        self.mid = math.atan2(self.dir.y, self.dir.x)
        if self.sweep > 0:
            self.active_s = self.sweep_s
            self.shape = ("arc", self.origin, self.length, self.mid, self.sweep / 2.0)
        else:
            self.shape = ("line", self.origin, self.origin + self.dir * self.length, self.width)

    def beam_angle(self):
        if self.sweep <= 0:
            return self.mid
        k = 0.0 if self.state == "telegraph" else 1.0 - max(0.0, self.t) / max(0.01, self.active_s)
        return self.mid - self.sweep / 2.0 + self.sweep * k

    def beam_end(self):
        a = self.beam_angle()
        return self.origin + V(math.cos(a), math.sin(a)) * self.length

    def land(self):
        self.run.shake(2, 0.12)
        p = self.run.player
        self.out_at_land = _seg_dist(p.pos, self.origin, self.beam_end()) - self.width / 2.0
        self._check()

    def tick(self, wdt):
        self._check()
        if self.t <= 0:
            p = self.run.player
            out = _seg_dist(p.pos, self.origin, self.beam_end()) - self.width / 2.0
            # dashed out of it after it fired: still a near miss (the dodge is what we learn from)
            self.missed_by(min(out, getattr(self, "out_at_land", out)))

    def _check(self):
        p = self.run.player
        if _seg_dist(p.pos, self.origin, self.beam_end()) <= self.width / 2.0 + PLAYER_R:
            self.strike(self.origin)

    def lands_on(self, pos):
        pos = V(pos)
        if self.sweep > 0:
            d = pos - self.origin
            if d.length() > self.length + PLAYER_R:
                return False
            return abs(_ang_diff(math.atan2(d.y, d.x), self.mid)) <= self.sweep / 2.0 + 0.05
        return _seg_dist(pos, self.shape[1], self.shape[2]) <= self.width / 2.0 + PLAYER_R

    def eta(self):
        if self.state == "telegraph":
            return self.t
        if self.sweep > 0 and self.state == "active" and not self.hit_player:
            return max(0.0, self.t)
        return None

    def edge_point(self):
        return self.origin

    def visible_in(self, view):
        return view.clipline(self.origin, self.origin + self.dir * self.length) != ()

    def draw_outline(self, surface, mapper, color, width):
        if self.state != "telegraph":
            return
        a = mapper.pt(self.origin)
        if self.sweep > 0:
            for ang in (self.mid - self.sweep / 2.0, self.mid + self.sweep / 2.0):
                b = mapper.pt(self.origin + V(math.cos(ang), math.sin(ang)) * self.length)
                _dashed(surface, color, a, b, width, 5, 4)
        else:
            _dashed(surface, color, a, mapper.pt(self.shape[2]), width, 5, 4)

    def draw(self, surface, mapper, view):
        a = mapper.pt(self.origin)
        if self.state == "telegraph":
            k = self.progress()
            armed = k >= 1
            col = gdev.WHITE if armed and _blink(self.age, 10) else THREAT
            if self.sweep > 0:
                self.draw_outline(surface, mapper, col, 1)
                b = mapper.pt(self.beam_end())
                pygame.draw.line(surface, col, a, b, 1)
            else:
                b = mapper.pt(self.shape[2])
                # the beam's width closes in as it charges
                half = self.width / 2.0 * (1.6 - 0.6 * k)
                n = V(-self.dir.y, self.dir.x) * half
                for s in (1, -1):
                    _dashed(surface, (130, 30, 105), V(a) + n * s, V(b) + n * s, 1, 3, 4)
                _dashed(surface, col, a, b, 1, 6, 3)
            _alpha_disc(surface, THREAT, 60 + 120 * k, a, 3 + 4 * k)
        elif self.state == "active":
            b = mapper.pt(self.beam_end())
            w = max(2, int(self.width))
            pygame.draw.line(surface, THREAT, a, b, w)
            pygame.draw.line(surface, (255, 170, 235), a, b, max(1, w // 2))
            pygame.draw.line(surface, gdev.WHITE, a, b, 1)
            pygame.draw.circle(surface, gdev.WHITE, a, w // 2 + 2)


# ---------------------------------------------------------------- port_overload

class PortOverload(Attack):
    """Every port of the owner's rack spawns at once (within the boss's max_alive and the
    minions caps). The ports strobe for the telegraph, then minions.py's own 1s port flicker
    plays. In the Deep Core (no rack) it spawns with minions.spawn_at at the arena ports."""
    default_telegraph = TELEGRAPH_S["port_overload"]

    def __init__(self, *a):
        Attack.__init__(self, *a)
        self.rack = self.params.get("rack") or getattr(self.owner, "rack", None)
        if self.rack is not None and self.rack.bars:
            pts = [V(b.spawn_exit) for b in self.rack.bars]
        else:
            pts = [V(p) for p in (self.params.get("points") or self._arena_ports())]
        self.points = pts
        self.shape = ("ports", pts)

    def _arena_ports(self):
        pts = getattr(self.owner, "ports", None)
        if pts:
            return pts
        arena = getattr(self.run.world, "arena", None) or {}
        return arena.get("ports", []) if isinstance(arena, dict) else []

    def edge_point(self):
        if not self.points:
            return self.origin
        p = self.run.player.pos
        return min(self.points, key=lambda q: (q - p).length_squared())

    def land(self):
        run = self.run
        n = len(self.points) * max(1, int(self.params.get("count", 1)))
        mx = getattr(self.owner, "max_alive", None)
        rid = self.rack.id if self.rack is not None else None
        if mx is not None:
            n = min(n, int(mx) - len(run.minions.live(rid)) - run.minions.pending(rid))
        if n <= 0:
            return
        kinds = list(self.params.get("kinds") or [])
        if len(kinds) < n:
            roll = getattr(self.owner, "roll_kinds", None)
            kinds += roll(n - len(kinds)) if roll is not None else ["b1t"] * (n - len(kinds))
        kinds = kinds[:n]
        tags = dict(self.params.get("tags") or {})
        if self.rack is not None and self.rack.bars:
            ports = list(range(len(self.rack.bars)))
            if hasattr(self.owner, "spawn_wave"):
                # through the Daemon: boss_id + wave_id tags, and its modifiers (R3C4LL's original)
                self.owner.spawn_wave(kinds, "vent_burst", ports=ports)
            else:
                run.minions.spawn_wave(self.rack.id, kinds, "vent_burst", ports=ports, tags=tags)
        else:
            for i, k in enumerate(kinds):
                if self.points:
                    run.minions.spawn_at(k, self.points[i % len(self.points)], tags=tags)
        run.shake(2, 0.2)

    def draw_outline(self, surface, mapper, color, width):
        if self.state != "telegraph":
            return
        k = self.progress()
        for p in self.points:
            c = mapper.pt(p)
            pygame.draw.circle(surface, color, c, max(2, int((14 - 8 * k) * mapper.scale)), width)

    def draw(self, surface, mapper, view):
        if self.state != "telegraph":
            return
        col = gdev.WHITE if _blink(self.age, 12) else THREAT
        self.draw_outline(surface, mapper, col, 1)
        for p in self.points:
            if view.collidepoint(p):
                c = mapper.pt(p)
                gdev.text(surface, "0V3RL04D", (c[0], c[1] - 18), 8, col, "midbottom")


# ---------------------------------------------------------------- packet_rain

class RainCircle(Attack):
    """One packet_rain circle: a shrinking ring and a filling disc, then a flash that hurts
    anyone inside. spawn() makes `count` of them around the aim point, staggered."""
    default_telegraph = TELEGRAPH_S["packet_rain"]
    active_s = RAIN_FLASH_S

    def __init__(self, system, attack_id, origin, params, owner, aim, centre=None):
        Attack.__init__(self, system, attack_id, origin, params, owner, aim)
        self.radius = float(params.get("radius", RAIN_RADIUS))
        self.centre = V(centre) if centre is not None else V(aim)
        self.shape = ("circle", self.centre, self.radius)

    def edge_point(self):
        return self.centre

    def land(self):
        p = self.run.player
        d = (p.pos - self.centre).length()
        if d <= self.radius + PLAYER_R - 1:
            self.strike(self.centre)
        else:
            self.missed_by(d - self.radius)

    def lands_on(self, pos):
        return (V(pos) - self.centre).length() <= self.radius + PLAYER_R - 1

    def draw_outline(self, surface, mapper, color, width):
        if self.state != "telegraph":
            return
        pygame.draw.circle(surface, color, mapper.pt(self.centre), max(2, int(self.radius * mapper.scale)), width)

    def draw(self, surface, mapper, view):
        if not view.inflate(80, 80).collidepoint(self.centre):
            return
        c = mapper.pt(self.centre)
        r = int(self.radius)
        if self.state == "telegraph":
            k = self.progress()
            armed = k >= 1
            _alpha_disc(surface, THREAT, 30 + 70 * k, c, max(1, int(r * k)))
            col = gdev.WHITE if armed and _blink(self.age, 10) else THREAT
            pygame.draw.circle(surface, col, c, r, 1)
            pygame.draw.circle(surface, (150, 40, 120), c, max(2, int(r + 10 * (1 - k))), 1)
            pygame.draw.line(surface, col, (c[0] - 3, c[1]), (c[0] + 3, c[1]))
            pygame.draw.line(surface, col, (c[0], c[1] - 3), (c[0], c[1] + 3))
        elif self.state == "active":
            k = max(0.0, self.t) / RAIN_FLASH_S
            _alpha_disc(surface, (255, 150, 230), 60 + 160 * k, c, r)
            pygame.draw.circle(surface, gdev.WHITE, c, max(2, int(r * (1.2 - 0.4 * k))), 1)


# ---------------------------------------------------------------- blackout

class Blackout(Attack):
    """The lights go out around a pool on the player for duration_s (world time). No damage of
    its own; minions and packets hide in the dark. Attack telegraphs stay drawn over it."""
    default_telegraph = TELEGRAPH_S["blackout"]

    def __init__(self, *a):
        Attack.__init__(self, *a)
        self.duration = max(0.5, float(self.params.get("duration_s", BLACKOUT_S)))
        self.active_s = self.duration
        self.light = float(self.params.get("radius", BLACKOUT_LIGHT))
        self.shape = ("screen",)

    def eta(self):
        return None

    def visible_in(self, view):
        return True

    def darkness(self):
        """0..1 of BLACKOUT_ALPHA right now."""
        if self.state == "telegraph":
            return self.progress() * 0.85
        if self.state == "active":
            return min(1.0, max(0.0, self.t) / BLACKOUT_FADE_S)
        return 0.0

    def draw(self, surface, mapper, view):
        pass


# ---------------------------------------------------------------- firewall_dome

class FirewallDome(Attack):
    """An expanding firewall ring from the port (or the Mirror). Its front hurts once and knocks
    the player back; dash through it (i-frames) or stay outside its dotted max radius."""
    default_telegraph = TELEGRAPH_S["firewall_dome"]

    def __init__(self, *a):
        Attack.__init__(self, *a)
        d = (self.aim - self.origin).length()
        self.speed = float(self.params.get("speed", DOME_SPEED))
        self.thick = float(self.params.get("thickness", DOME_THICK))
        self.r_max = float(self.params.get("radius", max(140.0, min(420.0, d + 90.0))))
        self.r = 6.0
        self.passed = False
        self.shape = ("dome", self.origin, self.r_max)

    def land(self):
        self.run.shake(2, 0.15)
        self.t = (self.r_max - self.r) / max(1.0, self.speed)

    def tick(self, wdt):
        p = self.run.player
        prev = self.r
        self.r = min(self.r_max, self.r + self.speed * wdt)
        d = (p.pos - self.origin).length()
        if not self.passed and d <= self.r + self.thick / 2.0 + PLAYER_R and \
                d >= prev - self.thick / 2.0 - PLAYER_R:
            if self.strike(self.origin):
                away = p.pos - self.origin
                if away.length_squared() > 0:
                    p.knock = away.normalize() * DOME_KNOCK
                    p.knock_t = DOME_KNOCK_T
            else:
                self.system.near_miss(p.pos)    # dashed through it
            self.passed = True
        if self.r >= self.r_max and not self.passed:
            self.passed = True
            self.missed_by(d - self.r_max)
        if self.r >= self.r_max:
            self.t = 0.0

    def lands_on(self, pos):
        d = (V(pos) - self.origin).length()
        if d > self.r_max + PLAYER_R:
            return False
        return self.state == "telegraph" or (not self.passed and d >= self.r - self.thick)

    def eta(self):
        p = self.run.player
        d = (p.pos - self.origin).length()
        if self.state == "telegraph":
            return self.t + max(0.0, d - self.r) / max(1.0, self.speed)
        if self.state == "active" and not self.passed:
            return max(0.0, d - self.r) / max(1.0, self.speed)
        return None

    def visible_in(self, view):
        return view.collidepoint(self.origin)       # else an edge marker points at its source

    def draw_outline(self, surface, mapper, color, width):
        c = mapper.pt(self.origin)
        if self.state == "telegraph":
            pygame.draw.circle(surface, color, c, max(2, int(self.r_max * mapper.scale)), width)
        elif self.state == "active":
            pygame.draw.circle(surface, color, c, max(2, int(self.r * mapper.scale)), max(width, 2))

    def draw(self, surface, mapper, view):
        c = mapper.pt(self.origin)
        if self.state == "telegraph":
            k = self.progress()
            col = gdev.WHITE if k >= 1 and _blink(self.age, 10) else THREAT
            # dashed max radius (turning slowly): how far the dome will reach
            _dashed_circle(surface, col if k >= 1 else (170, 45, 140), c, self.r_max, self.age * 0.4,
                           max(16, int(self.r_max / 6)))
            _alpha_disc(surface, THREAT, 40 + 120 * k, c, 4 + 8 * k)
            pygame.draw.circle(surface, col, c, max(2, int(4 + 10 * k)), 1)
        elif self.state == "active":
            r = int(self.r)
            w = max(2, int(self.thick))
            pygame.draw.circle(surface, THREAT, c, r, min(w, r))
            pygame.draw.circle(surface, gdev.WHITE, c, r, 1)
            if r > w + 2:
                pygame.draw.circle(surface, (255, 150, 230), c, r - w, 1)


# ---------------------------------------------------------------- weapon variants

class ChargedLine(Attack):
    """BOW variant: a long charge-up along a line, then a slow piercing arrow down it."""
    default_telegraph = TELEGRAPH_S["port_beam"] + BOW_CHARGE_S

    def __init__(self, system, attack_id, origin, params, owner, aim, angle_off=0.0):
        Attack.__init__(self, system, attack_id, origin, params, owner, aim)
        d = self.aim - self.origin
        a = (math.atan2(d.y, d.x) if d.length_squared() > 1 else 0.0) + angle_off
        self.dir = V(math.cos(a), math.sin(a))
        self.length = max(260.0, min(900.0, d.length() + 240.0))
        self.speed = float(self.params.get("speed", ARROW_SPEED))
        self.width = 6.0
        self.shape = ("line", self.origin, self.origin + self.dir * self.length, self.width)

    def land(self):
        life = self.length / self.speed
        self.shots.append(Shot(self.origin + self.dir * 4, self.dir * self.speed, self.damage, "arrow",
                               life, pierce=True))

    def lands_on(self, pos):
        return _seg_dist(V(pos), self.shape[1], self.shape[2]) <= self.width / 2.0 + PLAYER_R

    def eta(self):
        if self.state != "telegraph":
            return None
        d = (self.run.player.pos - self.origin).length()
        return self.t + d / self.speed

    def visible_in(self, view):
        return view.clipline(self.shape[1], self.shape[2]) != ()

    def draw_outline(self, surface, mapper, color, width):
        if self.state == "telegraph":
            _dashed(surface, color, mapper.pt(self.shape[1]), mapper.pt(self.shape[2]), width, 7, 4)

    def draw(self, surface, mapper, view):
        for s in self.shots:
            s.draw(surface, mapper, view)
        if self.state != "telegraph":
            return
        k = self.progress()
        a = V(mapper.pt(self.shape[1]))
        b = V(mapper.pt(self.shape[2]))
        col = gdev.WHITE if k >= 1 and _blink(self.age, 10) else THREAT
        _dashed(surface, (110, 28, 90), a, b, 1, 7, 4)
        # the charged part of the line grows toward the target
        _dashed(surface, col, a, a + (b - a) * k, 1, 7, 2)
        pygame.draw.circle(surface, col, (int(a.x), int(a.y)), max(2, int(2 + 4 * k)), 1)
        if view.collidepoint(self.origin):
            gdev.text(surface, "CH4RG3", (int(a.x), int(a.y) - 8), 8, col, "midbottom")


class PistolBurst(Attack):
    """PISTOL variant: a laser sight, then 6 fast shots; `bursts` times with a 1s reload gap.
    Each burst re-aims at the player (or the lead point)."""
    default_telegraph = PISTOL_SIGHT_S

    def __init__(self, *a):
        Attack.__init__(self, *a)
        self.bursts = max(1, int(self.params.get("bursts", PISTOL_BURSTS)))
        self.shots_per = max(1, int(self.params.get("count", PISTOL_SHOTS)))
        self.speed = float(self.params.get("speed", BULLET_SPEED))
        self.done_bursts = 0
        self.fired = 0              # shots of the current burst
        self.shot_t = 0.0
        self.mode = "sight"         # sight -> fire -> reload -> sight ...
        self.mode_t = 0.0
        self._set_line()

    def _set_line(self):
        d = self.aim - self.origin
        self.dir = d.normalize() if d.length_squared() > 1 else V(1, 0)
        self.length = max(260.0, min(800.0, d.length() + 200.0))
        self.shape = ("line", self.origin, self.origin + self.dir * self.length, 4.0)

    def land(self):
        self.mode = "fire"
        self.fired = 0
        self.shot_t = 0.0
        self.t = 1.0

    def tick(self, wdt):
        self.t = 1.0                 # stays active until the last burst is out
        if self.mode == "fire":
            self.shot_t -= wdt
            while self.shot_t <= 0 and self.fired < self.shots_per:
                spread = math.radians(self.system.rng.uniform(-3.0, 3.0))
                u = self.dir.rotate_rad(spread) if hasattr(self.dir, "rotate_rad") else self.dir
                life = self.length / self.speed
                self.shots.append(Shot(self.origin + u * 5, u * self.speed, self.damage, "bullet", life))
                self.fired += 1
                self.shot_t += PISTOL_INTERVAL
            if self.fired >= self.shots_per:
                self.done_bursts += 1
                self.mode = "reload" if self.done_bursts < self.bursts else "out"
                self.mode_t = PISTOL_RELOAD_S
        elif self.mode == "reload":
            self.mode_t -= wdt
            if self.mode_t <= PISTOL_SIGHT_S:
                self.aim = self.system.aim_point(self.params, self.mode_t)
                self._set_line()
                self.mode = "sight"
                self.mode_t = PISTOL_SIGHT_S
        elif self.mode == "sight":
            self.mode_t -= wdt
            if self.mode_t <= 0:
                self.mode = "fire"
                self.fired = 0
                self.shot_t = 0.0
        else:
            self.t = 0.0

    def finished(self):
        return self.mode == "out"

    def lands_on(self, pos):
        if self.state == "telegraph" or self.mode in ("sight", "fire"):
            return _seg_dist(V(pos), self.shape[1], self.shape[2]) <= 4.0 + PLAYER_R
        return False

    def eta(self):
        if self.state == "telegraph":
            return self.t
        if self.state == "active" and self.mode == "sight":
            return self.mode_t
        if self.state == "active" and self.mode == "reload":
            return self.mode_t
        return None

    def visible_in(self, view):
        return view.clipline(self.shape[1], self.shape[2]) != ()

    def draw_outline(self, surface, mapper, color, width):
        if self.state == "telegraph" or self.mode == "sight":
            _dashed(surface, color, mapper.pt(self.shape[1]), mapper.pt(self.shape[2]), width, 3, 3)

    def draw(self, surface, mapper, view):
        for s in self.shots:
            s.draw(surface, mapper, view)
        a = mapper.pt(self.origin)
        if self.state == "telegraph" or (self.state == "active" and self.mode == "sight"):
            col = gdev.WHITE if _blink(self.age, 14) else HOT
            pygame.draw.line(surface, (120, 24, 30), a, mapper.pt(self.shape[2]), 1)
            _dashed(surface, col, a, mapper.pt(self.shape[2]), 1, 3, 5)
            pygame.draw.circle(surface, HOT, a, 3, 1)
        elif self.state == "active" and self.mode == "reload" and view.collidepoint(self.origin):
            gdev.text(surface, "R3L04D", (a[0], a[1] - 8), 8, gdev.AMBER, "midbottom")


class KatanaArc(Attack):
    """KATANA variant: a short sword arc right next to the player (a sector on the boss's side
    of you that covers your spot). `arcs` of them in a combo, each re-placed on the player."""
    default_telegraph = KATANA_TELEGRAPH_S
    active_s = KATANA_ACTIVE_S

    def __init__(self, system, attack_id, origin, params, owner, aim, left=1):
        Attack.__init__(self, system, attack_id, origin, params, owner, aim)
        self.left = left            # arcs still to come in this combo, this one included
        d = self.aim - _owner_pos(owner, origin)
        u = d.normalize() if d.length_squared() > 1 else V(1, 0)
        self.radius = float(params.get("radius", KATANA_RADIUS))
        self.centre = self.aim - u * (self.radius * 0.45)
        self.facing = math.atan2(u.y, u.x)
        self.half = KATANA_HALF
        self.shape = ("arc", self.centre, self.radius, self.facing, self.half)

    def edge_point(self):
        return self.centre

    def land(self):
        p = self.run.player
        if self.lands_on(p.pos):
            self.strike(self.centre)
        else:
            self.missed_by((p.pos - self.centre).length() - self.radius)
        if self.left > 1 and not p.dead:
            params = dict(self.params)
            self.system._add(KatanaArc(self.system, self.id, self.origin, params, self.owner,
                                       self.system.aim_point(params, self.telegraph_total), self.left - 1))

    def lands_on(self, pos):
        d = V(pos) - self.centre
        n = d.length()
        if n > self.radius + PLAYER_R:
            return False
        if n < PLAYER_R + 2:
            return True
        return abs(_ang_diff(math.atan2(d.y, d.x), self.facing)) <= self.half + 0.1

    def draw_outline(self, surface, mapper, color, width):
        if self.state != "telegraph":
            return
        c = V(mapper.pt(self.centre))
        pts = _sector_points(c, self.radius * mapper.scale, self.facing, self.half)
        pygame.draw.polygon(surface, color, pts, width)

    def draw(self, surface, mapper, view):
        c = V(mapper.pt(self.centre))
        if self.state == "telegraph":
            k = self.progress()
            col = gdev.WHITE if k >= 1 and _blink(self.age, 12) else THREAT
            pygame.draw.polygon(surface, (120, 28, 95), _sector_points(c, self.radius * k, self.facing, self.half), 0)
            pygame.draw.polygon(surface, col, _sector_points(c, self.radius, self.facing, self.half), 1)
        elif self.state == "active":
            k = 1.0 - max(0.0, self.t) / KATANA_ACTIVE_S
            a0 = self.facing - self.half
            sweep = a0 + 2 * self.half * k
            pts = [(c.x + math.cos(a0 + (sweep - a0) * i / 8.0) * self.radius,
                    c.y + math.sin(a0 + (sweep - a0) * i / 8.0) * self.radius) for i in range(9)]
            if len(pts) > 1:
                pygame.draw.lines(surface, gdev.WHITE, False, pts, 3)
                pygame.draw.lines(surface, THREAT, False, [(c.x + (x - c.x) * 0.8, c.y + (y - c.y) * 0.8)
                                                          for x, y in pts], 1)


# ---------------------------------------------------------------- the system

class AttackSystem(object):
    """Public surface (contract):
      active [Attack]
      spawn(attack_id, origin, params=None, owner=None) -> Attack|None
          attack_id: one of LIBRARY, or {"id": ..., **params} as written in bosses/*.json
          params (all optional): aim "player"|"lead", delay_s (attack_delay_s modifier: extra
          telegraph), weapon "BOW"|"PISTOL"|"KATANA" (copy_player_weapon variant), rack (world.Rack),
          bar (world.Bar), damage (1), count, radius, telegraph_s, predict (bool: aim at the dodge
          predictor's guess at Director PEAK)
          more: aim_point (lock the aim), waves / speed (radial_burst), sweep_deg / sweep_s /
          width / length (port_beam), duration_s (blackout; radius = its light pool),
          kinds / tags / points (port_overload), bursts (PISTOL), thickness / speed (dome)
      update(wdt, real_dt)          advance telegraphs / hits (world time); damage via player.take_damage
      clear(owner=None)             remove all (or one owner's) attacks -- player death, phase change
      busy(owner=None) -> bool      any attack of this owner still telegraphing or active
      draw(world_surface, camera)   telegraph shapes + attack visuals on the world layer
      draw_overlay(surface, camera) full-screen effects (blackout darkness with a player light pool)
      draw_terminal_overlay(surface, window_rect, camera)
                                    while HACK: telegraphs that cross the terminal window are drawn
                                    over it too; an attack that will land on the rooted player traces
                                    the window's border (doc 7.1)
      on_event(name, data)
    spawn() returns the attack aimed at the player when one call makes several (packet_rain
    circles, BOW lines, KATANA combos).
    """

    def __init__(self, run):
        self.run = run
        self.active = []
        self.rng = random.Random(run.rng.random()) if hasattr(run, "rng") else random.Random()
        self.time = 0.0
        self._dark = None           # blackout darkness surface (internal size)
        self._holes = {}            # light pool gradients by radius

    # ------------------------------------------------------------ spawning

    def spawn(self, attack_id, origin, params=None, owner=None):
        if isinstance(attack_id, dict):
            params = dict(attack_id, **(params or {}))
            attack_id = params.pop("id", None)
        if attack_id not in LIBRARY:
            print("[attacks] unknown attack %r" % (attack_id,))
            return None
        params = dict(params or {})
        origin = V(origin)
        weapon = params.get("weapon") if params.get("weapon") in WEAPONS else None
        p = self.run.player

        if attack_id == "radial_burst":
            if weapon == "BOW":
                return self._bow_lines(attack_id, origin, params, owner, int(params.get("count", 3)))
            if weapon == "PISTOL":
                return self._add(PistolBurst(self, attack_id, origin, params, owner,
                                             self.aim_point(params, self._total(params, PISTOL_SIGHT_S))))
            if weapon == "KATANA":
                return self._katana(attack_id, origin, params, owner)
            tele = TELEGRAPH_S[attack_id]
            return self._add(RadialBurst(self, attack_id, origin, params, owner,
                                         self.aim_point(params, self._total(params, tele))))

        if attack_id == "port_beam":
            if weapon == "BOW":
                return self._bow_lines(attack_id, origin, params, owner, 1)
            if weapon == "PISTOL":
                params.setdefault("bursts", 1)
                return self._add(PistolBurst(self, attack_id, origin, params, owner,
                                             self.aim_point(params, self._total(params, PISTOL_SIGHT_S))))
            if weapon == "KATANA":
                # a lunge: a short, wide beam that starts right next to you
                params.setdefault("telegraph_s", KATANA_TELEGRAPH_S + 0.15)
                aim = self.aim_point(params, self._total(params, 0.7))
                d = aim - origin
                u = d.normalize() if d.length_squared() > 1 else V(1, 0)
                params.setdefault("length", 130.0)
                params.setdefault("width", 14.0)
                return self._add(PortBeam(self, attack_id, aim - u * 90.0, params, owner, aim))
            tele = TELEGRAPH_S[attack_id]
            return self._add(PortBeam(self, attack_id, origin, params, owner,
                                      self.aim_point(params, self._total(params, tele))))

        if attack_id == "port_overload":
            return self._add(PortOverload(self, attack_id, origin, params, owner, origin))

        if attack_id == "packet_rain":
            return self._rain(attack_id, origin, params, owner, weapon)

        if attack_id == "blackout":
            for a in self.active:
                if a.id == "blackout" and a.state != "done":
                    return a                # one darkness at a time
            return self._add(Blackout(self, attack_id, origin, params, owner, p.pos))

        # firewall_dome
        if weapon == "KATANA":
            # it comes in close: a small fast dome centred between you and the boss
            aim = self.aim_point(params, 0.0)
            d = _owner_pos(owner, origin) - aim
            u = d.normalize() if d.length_squared() > 1 else V(0, -1)
            params.setdefault("telegraph_s", 0.7)
            params.setdefault("radius", 120.0)
            params.setdefault("speed", 340.0)
            return self._add(FirewallDome(self, attack_id, aim + u * 60.0, params, owner, aim))
        if weapon == "BOW":
            params.setdefault("telegraph_s", TELEGRAPH_S[attack_id] + BOW_CHARGE_S * 0.7)
            params.setdefault("speed", 170.0)
            params.setdefault("thickness", 6.0)
        elif weapon == "PISTOL":
            params.setdefault("speed", 300.0)
        return self._add(FirewallDome(self, attack_id, origin, params, owner, self.aim_point(params, 0.0)))

    def _add(self, a):
        self.active.append(a)
        return a

    def _total(self, params, default_tele):
        return float(params.get("telegraph_s", default_tele)) + float(params.get("delay_s", 0.0))

    def aim_point(self, params, delay_s):
        """The locked aim (doc 7.1 / 9.5): aim_point if given, else the player or the lead point
        (where the player will be when the attack lands: velocity x the whole telegraph + delay,
        doc 7.3 |-|34D1NG), plus the dodge predictor's guess while the Director is at PEAK and
        params["predict"] is set. The lead uses walking speed at most, so a dash in progress does
        not throw it across the map; it may land off screen (the edge markers point at it)."""
        if params.get("aim_point") is not None:
            return V(params["aim_point"])
        p = self.run.player
        pt = V(p.pos)
        if params.get("aim") == "lead":
            vel = V(p.vel)
            if vel.length() > LEAD_SPEED_CAP:
                vel.scale_to_length(LEAD_SPEED_CAP)
            pt += vel * float(delay_s)
        if params.get("predict"):
            director = getattr(self.run, "director", None)
            prof = getattr(self.run, "profiler", None)
            pred = getattr(prof, "predictor", None) if prof is not None else None
            if director is not None and getattr(director, "state", None) == "PEAK" and pred is not None:
                guess = pred.predict()
                if guess and guess[0] is not None:
                    tok, prob = guess
                    pt += DIRVEC[int(tok) % 8] * PREDICT_PX * float(prob)
        return pt

    def _bow_lines(self, attack_id, origin, params, owner, n):
        tele = TELEGRAPH_S["port_beam"] + BOW_CHARGE_S
        aim = self.aim_point(params, self._total(params, tele))
        n = max(1, n)
        first = None
        for i in range(n):
            off = math.radians(18.0) * (i - (n - 1) / 2.0)
            a = self._add(ChargedLine(self, attack_id, origin, params, owner, aim, off))
            if first is None or abs(off) < 1e-6:
                first = a
        return first

    def _katana(self, attack_id, origin, params, owner):
        n = max(1, int(params.get("count", KATANA_ARCS)))
        tele = float(params.get("telegraph_s", KATANA_TELEGRAPH_S))
        aim = self.aim_point(params, tele + float(params.get("delay_s", 0.0)))
        return self._add(KatanaArc(self, attack_id, origin, params, owner, aim, n))

    def _rain(self, attack_id, origin, params, owner, weapon):
        instant = float(params.get("telegraph_s", 1.0)) <= 0
        if weapon == "PISTOL":
            count, radius, stagger = 6, 16.0, 0.12
        elif weapon == "BOW":
            count, radius, stagger = 2, 34.0, 0.35
            params.setdefault("telegraph_s", TELEGRAPH_S[attack_id] + BOW_CHARGE_S)
        elif weapon == "KATANA":
            count, radius, stagger = 3, 22.0, 0.1
            params.setdefault("telegraph_s", 0.75)
        else:
            count, radius, stagger = RAIN_COUNT, RAIN_RADIUS, RAIN_STAGGER_S
        count = int(params.get("count", 1 if instant else count))
        radius = float(params.get("radius", radius))
        params["radius"] = radius
        tele = float(params.get("telegraph_s", TELEGRAPH_S[attack_id]))
        aim = self.aim_point(params, tele + float(params.get("delay_s", 0.0)))
        centres = [V(aim)]
        tries = 0
        while len(centres) < count and tries < 60:
            tries += 1
            a = self.rng.uniform(0, 2 * math.pi)
            d = self.rng.uniform(radius * 1.6, radius * 1.6 + 60.0)
            c = aim + V(math.cos(a), math.sin(a)) * d
            if all((c - o).length() >= radius * 1.5 for o in centres):
                centres.append(c)
        first = None
        for i, c in enumerate(centres):
            pp = dict(params)
            pp["telegraph_s"] = tele + (0.0 if instant else stagger * i)
            a = self._add(RainCircle(self, attack_id, origin, pp, owner, aim, c))
            first = first or a
        return first

    # ------------------------------------------------------------ update / queries

    def update(self, wdt, real_dt):
        self.time += real_dt
        for a in list(self.active):
            a.update(wdt)
        self.active = [a for a in self.active if a.state != "done"]

    def clear(self, owner=None):
        self.active = [a for a in self.active if owner is not None and a.owner is not owner]

    def busy(self, owner=None):
        return any(owner is None or a.owner is owner for a in self.active)

    def near_miss(self, pos):
        run = self.run
        p = run.player
        if p.dead:
            return
        import profiler                    # lazy: profiler imports hack, keep import order simple
        run.emit("near_miss", pos=(pos[0], pos[1]), dir=profiler.dir_token(p.vel.x, p.vel.y))

    def on_event(self, name, data):
        if name in ("player_died", "deep_core_entered"):
            self.clear()

    def incoming(self):
        """(attack, real seconds) of the soonest attack that will land on the player, or None.
        Real seconds = world seconds / time_scale (a hack stretches them)."""
        p = self.run.player
        best = None
        for a in self.active:
            e = a.eta()
            if e is None or not a.lands_on(p.pos):
                continue
            if best is None or e < best[1]:
                best = (a, e)
        if best is None:
            return None
        ts = float(getattr(self.run, "time_scale", 1.0) or 0.0)
        return best[0], (best[1] / ts if ts > 0.01 else best[1])

    # ------------------------------------------------------------ drawing

    def draw(self, surface, camera):
        if not self.active:
            return
        view = camera.view_rect
        mapper = _Mapper(camera)
        for a in self.active:
            a.draw(surface, mapper, view)
        self._draw_edge_markers(surface, camera, view)

    def _draw_edge_markers(self, surface, camera, view):
        """Telegraphs outside the view get a blinking wedge at the screen edge (like the minion
        edge arrows), so an off-screen port is never a surprise."""
        import hud                                  # lazy: hud -> hack at import time
        p = self.run.player
        z = float(getattr(camera, "zoom", 1))
        safe = hud.SAFE_RECT
        hw, hh = safe.w / z / 2.0, safe.h / z / 2.0
        scx, scy = safe.left / z + hw, safe.top / z + hh
        for a in self.active:
            if a.state != "telegraph" or a.visible_in(view):
                continue
            q = V(a.edge_point())
            if (q - p.pos).length() > 700:
                continue
            cx, cy = view.left + scx, view.top + scy
            dx, dy = q.x - cx, q.y - cy
            d = math.hypot(dx, dy)
            if d < 1:
                continue
            ux, uy = dx / d, dy / d
            k = min((hw - 8) / max(abs(ux), 1e-6), (hh - 8) / max(abs(uy), 1e-6))
            ax, ay = scx + ux * k, scy + uy * k
            # a magenta chevron pair (">>" toward the threat), so it never reads as the cursor
            col = gdev.WHITE if a.progress() >= 1 and _blink(a.age, 10) else THREAT
            for back in (0, 4):
                bx, by = ax - ux * back, ay - uy * back
                pygame.draw.lines(surface, col, False, [(bx - ux * 3 - uy * 4, by - uy * 3 + ux * 4),
                                                        (bx + ux * 2, by + uy * 2),
                                                        (bx - ux * 3 + uy * 4, by - uy * 3 - ux * 4)], 2)

    def draw_overlay(self, surface, camera):
        dark = [a for a in self.active if a.id == "blackout" and a.state != "done"]
        if not dark:
            return
        k = max(a.darkness() for a in dark)
        if k <= 0.01:
            return
        w, h = surface.get_size()
        if self._dark is None or self._dark.get_size() != (w, h):
            self._dark = pygame.Surface((w, h), pygame.SRCALPHA)
        alpha = int(BLACKOUT_ALPHA * k)
        self._dark.fill((0, 5, 2, alpha))
        p = self.run.player
        z = float(getattr(camera, "zoom", 1))
        # the pool breathes a little; quantised so the gradient cache stays small
        light = max(a.light for a in dark)
        r = int((light + 3 * math.sin(self.time * 3.0)) * z) // 4 * 4
        hole = self._hole(max(8, r))
        cx, cy = camera.world_to_internal(p.pos)
        self._dark.blit(hole, (cx - hole.get_width() // 2, cy - hole.get_height() // 2),
                        special_flags=pygame.BLEND_RGBA_MULT)
        surface.blit(self._dark, (0, 0))
        # telegraphs stay readable in the dark (fair warning), in thin outlines
        mapper = _Mapper(camera, internal=True)
        for a in self.active:
            if a.id != "blackout":
                a.draw_outline(surface, mapper, THREAT, 2)
        if dark[0].state == "telegraph":
            gdev.text(surface, "BL4CK0UT", (w // 2, 64), 12, THREAT, "midtop", True)

    def _hole(self, r):
        img = self._holes.get(r)
        if img is None:
            if len(self._holes) > 24:
                self._holes.clear()
            # white with an alpha ramp: a MULT blit keeps the darkness outside the ramp and
            # scales its alpha down to 0 in the middle of the pool
            img = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
            img.fill((255, 255, 255, 255))
            inner = 0.45 * r
            steps = 24
            for i in range(steps + 1):
                rr = r - (r - inner) * i / float(steps)
                al = int(255 * (rr - inner) / float(r - inner)) if r > inner else 0
                pygame.draw.circle(img, (255, 255, 255, max(0, al)), (r, r), max(1, int(rr)))
            pygame.draw.circle(img, (255, 255, 255, 0), (r, r), max(1, int(inner)))
            self._holes[r] = img
        return img

    def draw_terminal_overlay(self, surface, window_rect, camera):
        if window_rect is None or (not self.active and not self.run.minions.enemy_projectiles):
            return
        win = pygame.Rect(window_rect)
        mapper = _Mapper(camera, internal=True, zoom_map=self._zoom_map(camera))
        old_clip = surface.get_clip()
        surface.set_clip(win)
        for a in self.active:
            if a.id == "blackout" or a.eta() is None:
                continue
            col = THREAT if not _blink(a.age, 6) else (200, 40, 160)
            if a.shape[0] == "line":
                pa, pb = mapper.pt(a.shape[1]), mapper.pt(a.shape[2])
                seg = win.clipline(pa, pb)
                if seg:
                    _dashed(surface, col, seg[0], seg[1], 2, 8, 5)
            else:
                a.draw_outline(surface, mapper, col, 2)
        self._draw_bolts_in(surface, win, mapper)
        surface.set_clip(old_clip)
        hit = self.incoming() if self.run.player.rooted or self.run.hack is not None else None
        if hit is not None:
            self._trace_border(surface, win, hit[0], hit[1])

    def _draw_bolts_in(self, surface, win, mapper):
        """Enemy packets and bolts flying under the terminal window show through it as small
        magenta streaks, so a shot heading for the rooted player is never hidden."""
        bolts = [s for a in self.active for s in a.shots]
        bolts += [p for p in self.run.minions.enemy_projectiles if p.team == "enemy"]
        for b in bolts:
            x, y = mapper.pt(b.pos)
            if not win.collidepoint(x, y):
                continue
            v = b.vel
            if v.length_squared() > 1e-6:
                u = v.normalize()
                pygame.draw.line(surface, (150, 40, 120), (x - u.x * 10, y - u.y * 10), (x, y), 2)
            pygame.draw.circle(surface, THREAT, (x, y), 3)
            surface.set_at((x, y), gdev.WHITE)

    def _trace_border(self, surface, win, a, real_s):
        """A ring tracing the terminal window's border: it closes as the attack gets closer to
        landing on the rooted player, with the impact time in REAL seconds."""
        k = 1.0 - (a.eta() or 0.0) / max(0.01, a.telegraph_total + 0.5)
        k = max(0.05, min(1.0, k))
        r = win.inflate(6, 6)
        per = 2 * (r.w + r.h)
        n = per * k
        pts = [(r.left, r.top), (r.right, r.top), (r.right, r.bottom), (r.left, r.bottom), (r.left, r.top)]
        col = HOT if _blink(self.time, 8 if real_s > 1.0 else 16) else gdev.WHITE
        pygame.draw.rect(surface, (110, 20, 30), r, 1)
        left = n
        for i in range(4):
            p0, p1 = V(pts[i]), V(pts[i + 1])
            seg = (p1 - p0).length()
            if left <= 0:
                break
            e = p0 + (p1 - p0) * min(1.0, left / seg)
            pygame.draw.line(surface, col, p0, e, 3)
            left -= seg
        label = "!! 1NC0M1NG %.1fs" % real_s
        tw, th = gdev.text_size(label, 10, True)
        box = pygame.Rect(0, 0, tw + 8, th + 2)
        box.midbottom = (r.centerx, r.top - 1)
        pygame.draw.rect(surface, (40, 6, 12), box)
        pygame.draw.rect(surface, col, box, 1)
        gdev.text(surface, label, box.center, 10, col, "center", True)

    def _zoom_map(self, camera):
        """The jack-in zoom (Game._apply_hack_zoom) this frame: camera.zoom_map if the integrator
        publishes it there, else Game's own _zoom_map (Game is a Run listener)."""
        zm = getattr(camera, "zoom_map", None)
        if zm is not None:
            return zm
        for l in getattr(self.run, "listeners", []):
            if hasattr(l, "_zoom_map") and hasattr(l, "_zoomed"):
                return l._zoom_map
        return None
