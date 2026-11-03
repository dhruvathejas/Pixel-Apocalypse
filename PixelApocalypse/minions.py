"""|-|@c|<ing minions and their spawner (doc sections 5.1 and 6).

One data-driven Minion class (KINDS below) runs every archetype:
  B1T   swarm rusher: packs of 4-6, seek + separation + wall avoidance, contact damage
  P1NG  ranged: holds 160-230px away, fires bursts of 3 slow packets, flees when EXPOSED

State machine: SPAWNING -> HUNTING -> EXPOSED -> (TURNED | DEAD | REBOOT | HARDENED)
  EXPOSED    firewall stripped. 4s window, the first 0.35s hits whiff (grace),
             after that ANY hit kills it (strip-then-stop). Timer out -> REBOOT.
  REBOOT     a short stun, then back to HUNTING with 50% firewall.
  HARDENED   after a BACKTRACE: red outline, +50% firewall, can never be hacked.
  TURNED     after a TURN payload: cyan, fights for the player, then dissolves.
  DEAD       plays a short dissolve, then the manager removes it.

MinionManager owns the minions, their packets and the Rack A spawner (a small
"Director-lite" budget). Positions are world pixels; `pos` is the centre of the
collision rect. All timers here run on WORLD dt (Game slows it while hacking).
"""
import glob
import itertools
import math
import os
import random

import pygame

import gdev

V = pygame.math.Vector2

SPAWNING = "SPAWNING"
HUNTING = "HUNTING"
EXPOSED = "EXPOSED"
DEAD = "DEAD"
REBOOT = "REBOOT"
HARDENED = "HARDENED"
TURNED = "TURNED"

EXPOSE_WINDOW_S = 4.0
EXPOSE_GRACE_S = 0.35
PIN_BONUS_S = 3.0           # a pinning shot (bow full charge) on an EXPOSED minion
REBOOT_S = 0.5
SPAWN_SLIDE_S = 0.4         # minion slides out of its port after the telegraph
DISSOLVE_S = 0.55
JACK_RING_PX = 64           # drawn jack-in range (Game uses the same 64px)

# Spawner (doc 6 + a trimmed Director, doc 9.2)
TELEGRAPH_S = 1.0           # port bar flickers this long before a minion comes out
PACK_STAGGER_S = 0.3
ZONE_CAP = 40               # live minions per zone
PORT_CAP = 6                # live minions per port
BUDGET_START = 6.0
BUDGET_MAX = 10.0
BUDGET_REGEN = 0.8          # per world second while the player is in the zone
FIRST_WAVE_S = 1.2          # delay after entering the zone
WAVE_GAP_S = (7.0, 10.0)    # calm between packs
SOFT_CAP = 8                # Director-lite target of live minions (+2 per hack, up to 14)
MAX_P1NG = 2
LEASH_PX = 560              # player out of the zone and this far: walk home and despawn

FIREWALL_ORANGE = (255, 140, 40)

# Archetype data. Speeds are px/s, times seconds, ranges px.
KINDS = {
    "b1t": {
        "name": "B1T", "initial": "B", "color": gdev.MAGENTA,
        "size": (8, 6), "top": 17,           # collision box, sprite's first opaque row
        "speed": 105.0, "firewall": 2, "core_hp": 1, "bits": 5, "cost": 1,
        "contact_damage": 1, "contact_cd": 1.0, "recoil_s": 0.25,
    },
    "p1ng": {
        "name": "P1NG", "initial": "7", "color": gdev.AMBER,
        "size": (8, 8), "top": 10,
        "speed": 80.0, "firewall": 3, "core_hp": 1, "bits": 8, "cost": 2,
        "hold": (160, 230), "fire_range": 300,
        "burst": 3, "burst_gap": 0.18, "windup": 0.35, "burst_cd": (2.2, 2.9),
        "packet_speed": 120.0, "packet_life": 2.6, "packet_damage": 1,
    },
}

_uids = itertools.count(1)


# ---------------------------------------------------------------- sprites

_frames_cache = {}
_fx_cache = {}


def load_minion_frames(name):
    """{frame_name: Surface} from minion_assets/<name>_*.png, e.g. "right_0", "packet_left"."""
    if name in _frames_cache:
        return _frames_cache[name]
    frames = {}
    pattern = gdev.path("minion_assets", "%s_*.png" % name)
    for f in sorted(glob.glob(pattern)):
        base = os.path.basename(f)[len(name) + 1:-4]
        img = gdev.image("minion_assets/" + os.path.basename(f))
        if img is not None:
            frames[base] = img
    if not frames:
        print("[minions] no sprites for %s, drawing boxes" % name)
    _frames_cache[name] = frames
    return frames


def _fallback_frame(kind):
    """Coloured 32x32 box with the archetype's leet initial (doc 6 fallback)."""
    key = ("box", kind)
    if key not in _fx_cache:
        info = KINDS.get(kind, KINDS["b1t"])
        s = pygame.Surface((32, 32), pygame.SRCALPHA)
        w, h = info["size"]
        r = pygame.Rect(0, 0, w + 4, 22 - info["top"])
        r.midbottom = (16, 22)
        pygame.draw.rect(s, info["color"], r)
        gdev.text(s, info["initial"], r.center, 8, gdev.BLACK, "center", True)
        _fx_cache[key] = s
    return _fx_cache[key]


def _silhouette(img, color):
    """The sprite's shape filled with one colour (keeps per-pixel alpha). Cached."""
    key = ("sil", id(img), tuple(color))
    s = _fx_cache.get(key)
    if s is None:
        s = img.copy()
        s.fill((0, 0, 0), special_flags=pygame.BLEND_RGB_MULT)
        s.fill(tuple(color)[:3], special_flags=pygame.BLEND_RGB_ADD)
        _fx_cache[key] = s
    return s


def _cyan_swap(img):
    """TURNED palette: the sprite in shades of cyan."""
    key = ("cyan", id(img))
    s = _fx_cache.get(key)
    if s is None:
        s = pygame.transform.grayscale(img)
        s.fill((110, 110, 110), special_flags=pygame.BLEND_RGB_ADD)
        s.fill(gdev.CYAN, special_flags=pygame.BLEND_RGB_MULT)
        _fx_cache[key] = s
    return s


def _rotated(img, degrees):
    """Rotation cached in 10 degree buckets (doc 2)."""
    b = int(round(degrees / 10.0)) * 10
    key = ("rot", id(img), b)
    s = _fx_cache.get(key)
    if s is None:
        s = pygame.transform.rotate(img, b)
        _fx_cache[key] = s
    return s


# ---------------------------------------------------------------- packets

class Packet(object):
    """A P1NG shot. Same fields as player.Projectile so code can treat them alike.
    team "enemy" hits the player; team "player" (a TURNED P1NG) hits enemy minions."""

    def __init__(self, pos, vel, damage=1, team="enemy", life=2.6):
        self.pos = V(pos)
        self.vel = V(vel)
        self.damage = damage
        self.kind = "packet"
        self.team = team
        self.pierce = False
        self.no_kill = True         # a turned minion's shot only strips firewalls
        self.hit_ids = set()
        self.alive = True
        self.life = life
        self.age = 0.0
        self.rect = pygame.Rect(0, 0, 4, 4)
        self.rect.center = (int(self.pos.x), int(self.pos.y))

    def update(self, dt, world):
        self.pos += self.vel * dt
        self.rect.center = (int(self.pos.x), int(self.pos.y))
        self.life -= dt
        self.age += dt
        if self.life <= 0 or world.projectile_hit(self.rect) is not None:
            self.alive = False


# ---------------------------------------------------------------- minion

class Minion(object):
    """Public surface:
      kind "b1t"|"p1ng", uid int, label str ("B1T 0x2F"), rack_id str|None, port (bar index)|None
      pos Vector2, rect Rect, state (constants above), team "enemy"|"player"
      firewall, firewall_max, core_hp, expose_t (seconds of EXPOSED left), bits (reward)
      hardened bool, dead_cause "weapon"|"extract"|"overload"|"turn_expired"|"despawn"|None
      jacked bool       -- set True by Game while a hack session targets it: hold EXPOSED,
                           do not move/attack, do not reboot on expose timeout.
      alive (property)  -- state != DEAD
      hackable (property) -- EXPOSED, not jacked, not hardened
      hit(damage, source=None) -> "firewall"|"exposed"|"grace"|"killed"|"pinned"|"ignored"
      expose(seconds=4.0), kill(cause="weapon"), reboot(), turn(seconds), harden()
      update(dt, player, manager), draw(surface, camera)

    Optional attributes read from the hit `source` (a projectile or a minion):
      expose=True   strips the whole firewall in one hit (katana parry, charged bow)
      pin=True or full_charge=True   on an EXPOSED minion: +3s to the window (once)
                    instead of a kill
      no_kill=True  never kills an EXPOSED minion (turned allies leave them for you)
    """

    def __init__(self, kind, pos, rack_id=None):
        if kind not in KINDS:
            kind = "b1t"
        info = KINDS[kind]
        self.kind = kind
        self.info = info
        self.uid = next(_uids)
        self.rng = random.Random(self.uid * 7919)   # per-minion, so runs repeat
        self.label = "%s 0x%02X" % (info["name"], (self.uid * 37) % 256)
        self.rack_id = rack_id
        self.port = None
        self.pos = V(pos)
        self.vel = V(0, 0)
        self.rect = pygame.Rect(0, 0, info["size"][0], info["size"][1])
        self.rect.center = (int(self.pos.x), int(self.pos.y))
        self.state = HUNTING
        self.team = "enemy"
        self.firewall_max = info["firewall"]
        self.firewall = self.firewall_max
        self.core_hp = info["core_hp"]
        self.hardened = False
        self.expose_t = 0.0
        self.expose_total = EXPOSE_WINDOW_S
        self.grace_t = 0.0
        self.pinned = False
        self.turn_t = 0.0
        self.turn_total = 0.0
        self.reboot_t = 0.0
        self.spawn_t = 0.0
        self.spawn_dir = V(0, 1)
        self.dissolve_t = 0.0
        self.bits = info["bits"]
        self.jacked = False
        self.dead_cause = None
        self.reported = False       # weapon kill already reported to the Run
        self.anim_t = self.rng.random()
        self.side = "right"
        self.flash_t = 0.0          # white flash after a firewall hit
        self.attack_t = 0.0         # attack frame held while > 0
        self.cooldown = 0.0         # contact damage / burst cooldown
        self.recoil_t = 0.0
        self.recoil_dir = V(0, 0)
        self.avoid_side = 1
        self.speed = info["speed"] * self.rng.uniform(0.9, 1.1)
        self.home = V(pos)
        self.homing = False
        self.home_t = 0.0
        # P1NG
        self.hold_px = self.rng.uniform(*info.get("hold", (0, 0))) if "hold" in info else 0
        self.strafe = self.rng.choice((-1, 1))
        self.strafe_t = self.rng.uniform(1.5, 3.0)
        self.shots_left = 0
        self.shot_t = 0.0
        if "burst_cd" in info:
            self.cooldown = self.rng.uniform(1.0, 2.0)

    # ------------------------------------------------------------ status

    @property
    def alive(self):
        return self.state != DEAD

    @property
    def hackable(self):
        return self.state == EXPOSED and not self.jacked and not self.hardened

    def hurtbox(self):
        """Hit area for shots: a little bigger than the tiny body."""
        return self.rect.inflate(4, 6)

    # ------------------------------------------------------------ state changes

    def hit(self, damage, source=None):
        if self.state in (DEAD, TURNED, SPAWNING) or self.jacked:
            return "ignored"
        no_kill = bool(getattr(source, "no_kill", False)) or isinstance(source, Minion)
        if self.state == EXPOSED:
            if self.grace_t > 0:
                return "grace"
            pin = getattr(source, "pin", False) or getattr(source, "full_charge", False)
            if pin and not self.pinned:
                self.pinned = True              # a full-charge arrow pins once per window
                self.expose_t += PIN_BONUS_S
                self.expose_total = max(self.expose_total, self.expose_t)
                return "pinned"
            if no_kill:
                return "ignored"
            self.kill("weapon")
            return "killed"
        if getattr(source, "expose", False):
            damage = self.firewall
        self.firewall -= max(1, int(damage))
        self.flash_t = 0.08
        vel = getattr(source, "vel", None)
        if vel is not None and vel.length_squared() > 0:
            self.recoil_dir = V(vel).normalize()
            self.recoil_t = 0.08
        if self.firewall <= 0:
            self.expose()
            return "exposed"
        return "firewall"

    def expose(self, seconds=EXPOSE_WINDOW_S):
        if self.state in (DEAD, TURNED):
            return
        self.state = EXPOSED
        self.firewall = 0
        self.expose_t = seconds
        self.expose_total = seconds
        self.grace_t = EXPOSE_GRACE_S
        self.pinned = False
        self.shots_left = 0
        self.attack_t = 0.0

    def kill(self, cause="weapon"):
        if self.state == DEAD:
            return
        self.state = DEAD
        self.dead_cause = cause
        self.dissolve_t = DISSOLVE_S
        self.vel = V(0, 0)

    def reboot(self):
        if self.state == DEAD:
            return
        self.state = REBOOT
        self.reboot_t = REBOOT_S
        self.firewall = max(1, int(math.ceil(self.firewall_max / 2.0)))
        self.expose_t = 0.0
        self.grace_t = 0.0
        self.jacked = False

    def turn(self, seconds):
        if self.state == DEAD:
            return
        self.state = TURNED
        self.team = "player"
        self.turn_t = float(seconds)
        self.turn_total = float(seconds)
        self.firewall = self.firewall_max
        self.expose_t = 0.0
        self.cooldown = 0.3

    def harden(self):
        if self.state == DEAD:
            return
        self.hardened = True
        self.state = HARDENED
        self.firewall_max = int(math.ceil(self.firewall_max * 1.5))
        self.firewall = self.firewall_max
        self.expose_t = 0.0
        self.grace_t = 0.0
        self.jacked = False

    # ------------------------------------------------------------ update

    def update(self, dt, player, manager):
        if dt <= 0:
            return                      # world frozen (Key flourish)
        self.anim_t += dt
        self.flash_t = max(0.0, self.flash_t - dt)
        self.attack_t = max(0.0, self.attack_t - dt)
        self.cooldown = max(0.0, self.cooldown - dt)

        if self.state == DEAD:
            self.dissolve_t -= dt
            return
        if self.state == SPAWNING:
            self.spawn_t -= dt
            self._move(manager.world, self.spawn_dir * 45.0 * dt)
            if self.spawn_t <= 0:
                self.state = HUNTING
        elif self.state == REBOOT:
            self.reboot_t -= dt
            if self.reboot_t <= 0:
                self.state = HARDENED if self.hardened else HUNTING
        elif self.state == EXPOSED:
            self.grace_t = max(0.0, self.grace_t - dt)
            if not self.jacked:
                self.expose_t -= dt
                if self.expose_t <= 0:
                    self.reboot()
                elif self.kind == "p1ng" and not player.dead:
                    away = self.pos - player.pos       # flee (the bow pin is the counter)
                    if away.length_squared() > 0 and away.length() < 320:
                        self._go(manager.world, away.normalize() * self.speed * 0.75, dt, manager)
        elif self.state == TURNED:
            self.turn_t -= dt
            if self.turn_t <= 0:
                self.kill("turn_expired")
            else:
                self._update_turned(dt, player, manager)
        else:                           # HUNTING / HARDENED
            if self.homing:
                self._update_homing(dt, manager)
            elif self.kind == "p1ng":
                self._update_p1ng(dt, player, manager)
            else:
                self._update_b1t(dt, player, manager)

        if self.recoil_t > 0 and self.state != DEAD and not self.jacked:
            self.recoil_t -= dt
            self._move(manager.world, self.recoil_dir * 90.0 * dt)
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))

    def _face(self, dx):
        if dx > 2:
            self.side = "right"
        elif dx < -2:
            self.side = "left"

    def _enemy_target(self, player, manager):
        """Who an enemy minion chases: the player, or a TURNED minion that is closer."""
        best = None if player.dead else player
        best_d = (player.pos - self.pos).length_squared() if best else 1e12
        for m in manager.minions:
            if m.state == TURNED:
                d = (m.pos - self.pos).length_squared()
                if d < 80 * 80 and d < best_d:
                    best, best_d = m, d
        return best

    def _update_b1t(self, dt, player, manager):
        target = self._enemy_target(player, manager)
        if self.recoil_t > 0:
            return
        if target is None:
            self._go(manager.world, V(0, 0), dt, manager)
            return
        to = target.pos - self.pos
        speed = self.speed
        if manager.hacking and target is player and to.length() < 320:
            speed *= 1.35               # +ADVANCE on a rooted, hacking player
        desire = to.normalize() * speed if to.length_squared() > 1 else V(0, 0)
        self._go(manager.world, desire, dt, manager)
        self._face(to.x)
        # contact damage
        if self.cooldown <= 0 and self.rect.inflate(4, 4).colliderect(target.rect):
            self.attack_t = 0.15
            if target is player:
                hurt = player.take_damage(self.info["contact_damage"], self.pos)
                self.cooldown = self.info["contact_cd"] if hurt else 0.3
                if not hurt and getattr(player, "parrying", False):
                    self.expose()               # katana parry EXPOSES melee attackers (doc 10)
                    if manager.run is not None:
                        manager.run.float_text((self.pos.x, self.pos.y - 14), "PARRIED", gdev.CYAN, 0.7)
                    return
            else:
                self.cooldown = self.info["contact_cd"]     # turned minions shrug it off
            self.recoil_t = self.info["recoil_s"]
            self.recoil_dir = (-to).normalize() if to.length_squared() > 0 else V(0, -1)

    def _update_p1ng(self, dt, player, manager):
        if player.dead:
            self._go(manager.world, V(0, 0), dt, manager)
            return
        to = player.pos - self.pos
        dist = to.length()
        self._face(to.x)
        dirn = to / dist if dist > 0 else V(1, 0)
        # firing: windup (attack frame), then a burst of 3 aimed packets
        if self.shots_left > 0:
            self.attack_t = max(self.attack_t, 0.1)
            self.shot_t -= dt
            if self.shot_t <= 0:
                self._fire(manager, player.pos, "enemy")
                self.shots_left -= 1
                self.shot_t = self.info["burst_gap"]
                if self.shots_left == 0:
                    self.cooldown = self.rng.uniform(*self.info["burst_cd"])
            self._go(manager.world, V(0, 0), dt, manager)
            return
        if self.cooldown <= 0 and dist < self.info["fire_range"]:
            self.shots_left = self.info["burst"]
            self.shot_t = self.info["windup"]
            return
        # hold 160-230px: close in, back off, or strafe
        self.strafe_t -= dt
        if self.strafe_t <= 0:
            self.strafe = -self.strafe
            self.strafe_t = self.rng.uniform(1.5, 3.0)
        if dist > self.hold_px + 25:
            desire = dirn * self.speed
        elif dist < self.hold_px - 25:
            desire = -dirn * self.speed
        else:
            desire = V(-dirn.y, dirn.x) * self.strafe * self.speed * 0.5
        self._go(manager.world, desire, dt, manager)

    def _fire(self, manager, at, team):
        aim = V(at) - self.pos
        if aim.length_squared() == 0:
            return
        aim = aim.normalize().rotate(self.rng.uniform(-6, 6))
        p = Packet(self.pos + aim * 6, aim * self.info["packet_speed"],
                   self.info["packet_damage"], team, self.info["packet_life"])
        manager.enemy_projectiles.append(p)

    def _update_turned(self, dt, player, manager):
        # Fight for the player: strip enemy firewalls, leave EXPOSED ones for the player to hack.
        target, best = None, 280.0 * 280.0
        for m in manager.minions:
            if m.team == "enemy" and m.state in (HUNTING, HARDENED, REBOOT):
                d = (m.pos - self.pos).length_squared()
                if d < best:
                    target, best = m, d
        if target is None:
            off = V(-24 if self.uid % 2 else 24, 18)          # follow the player
            to = player.pos + off - self.pos
            desire = to.normalize() * self.speed if to.length() > 10 else V(0, 0)
            self._go(manager.world, desire, dt, manager)
            self._face(to.x)
            return
        to = target.pos - self.pos
        self._face(to.x)
        if self.kind == "p1ng":
            dist = to.length()
            desire = V(0, 0)
            if dist > 130:
                desire = to.normalize() * self.speed
            elif dist < 90 and dist > 0:
                desire = -to.normalize() * self.speed
            self._go(manager.world, desire, dt, manager)
            if self.cooldown <= 0:
                self._fire(manager, target.pos, "player")
                self.attack_t = 0.15
                self.cooldown = 0.7
            return
        desire = to.normalize() * self.speed * 1.1 if to.length_squared() > 1 else V(0, 0)
        self._go(manager.world, desire, dt, manager)
        if self.cooldown <= 0 and self.rect.inflate(4, 4).colliderect(target.rect):
            target.hit(1, self)
            self.attack_t = 0.15
            self.cooldown = 0.6

    def _update_homing(self, dt, manager):
        to = self.home - self.pos
        self.home_t -= dt
        if to.length() < 12 or self.home_t <= 0:
            self.kill("despawn")
            return
        self._go(manager.world, to.normalize() * self.speed, dt, manager)
        self._face(to.x)

    # ------------------------------------------------------------ movement

    def _go(self, world, desire, dt, manager):
        """Steer toward `desire` (px/s) with separation and wall avoidance, then move."""
        sep = V(0, 0)
        for m in manager.minions:
            if m is self or m.state == DEAD:
                continue
            d = self.pos - m.pos
            dl = d.length_squared()
            if 0 < dl < 14 * 14:
                sep += d / dl * 14.0
            elif dl == 0:
                sep += V(1, 0).rotate(self.uid * 47)
        steer = V(desire) + sep * self.speed * 0.6
        if steer.length_squared() > 1:
            steer = self._avoid(world, steer)
            if steer.length() > self.speed * 1.4:
                steer.scale_to_length(self.speed * 1.4)
        self.vel = steer
        self._move(world, steer * dt)

    def _avoid(self, world, steer):
        """If the way ahead is solid, turn toward the first free direction (keeps a side)."""
        n = steer.normalize()
        ahead = self.pos + n * 14
        if not world.point_solid(ahead.x, ahead.y):
            return steer
        for ang in (45, 90, 135):
            for side in (self.avoid_side, -self.avoid_side):
                d = n.rotate(ang * side)
                p = self.pos + d * 14
                if not world.point_solid(p.x, p.y):
                    self.avoid_side = side
                    return d * steer.length()
        return -steer

    def _move(self, world, delta):
        """Float movement, X then Y; an axis that would enter a solid is blocked."""
        w, h = self.rect.size
        for axis in (0, 1):
            step = delta[axis]
            if step == 0:
                continue
            nx = self.pos.x + (step if axis == 0 else 0)
            ny = self.pos.y + (step if axis == 1 else 0)
            r = pygame.Rect(0, 0, w, h)
            r.center = (int(round(nx)), int(round(ny)))
            if world.art_bounds.contains(r) and r.collidelist(world.solids) == -1:
                self.pos.x, self.pos.y = nx, ny
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))

    # ------------------------------------------------------------ drawing

    def _frame(self):
        frames = load_minion_frames(self.kind)
        if self.attack_t > 0 and self.state not in (EXPOSED, DEAD):
            name = "%s_attack" % self.side
        else:
            moving = self.vel.length_squared() > 25 and self.state != EXPOSED
            rate = 6 if moving else 2
            name = "%s_%d" % (self.side, int(self.anim_t * rate) % 2)
        return frames.get(name) or frames.get(self.side + "_0") or _fallback_frame(self.kind)

    def draw(self, surface, camera):
        sx, sy = camera.world_to_screen(self.pos)
        img = self._frame()
        # 32x32 canvas, body centred on column 16, feet on row 21 = bottom of the box
        bx = sx - 16
        by = sy + self.rect.h // 2 - 22
        top = by + self.info["top"]

        if self.state == DEAD:
            self._draw_dissolve(surface, img, bx, by)
            return
        if self.state == SPAWNING:
            self._draw_spawning(surface, img, bx, by)
            return

        if self.hardened:
            red = _silhouette(img, gdev.RED)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                surface.blit(red, (bx + dx, by + dy))

        if self.state == EXPOSED:
            self._draw_exposed(surface, img, bx, by, sx, sy)
        elif self.state == TURNED:
            surface.blit(_cyan_swap(img), (bx, by))
        elif self.state == REBOOT:
            if int(self.anim_t * 20) % 2 == 0:
                surface.blit(img, (bx, by))
            else:
                surface.blit(_silhouette(img, gdev.GREEN_MID), (bx, by))
        else:
            surface.blit(img, (bx, by))
        if self.flash_t > 0:
            surface.blit(_silhouette(img, gdev.WHITE), (bx, by))

        # bars above the head
        if self.state in (HUNTING, HARDENED, REBOOT):
            self._draw_firewall(surface, sx, top - 4)
        elif self.state == TURNED:
            w = 14
            k = max(0.0, self.turn_t / max(0.01, self.turn_total))
            pygame.draw.rect(surface, gdev.GREEN_DARK, (sx - w // 2, top - 4, w, 2))
            pygame.draw.rect(surface, gdev.CYAN, (sx - w // 2, top - 4, int(w * k + 0.5), 2))

    def _draw_firewall(self, surface, cx, y):
        n = self.firewall_max
        seg = 3 if n <= 5 else 2
        w = n * (seg + 1) - 1
        x = cx - w // 2
        pygame.draw.rect(surface, (20, 8, 4), (x - 1, y - 1, w + 2, 4))
        col = gdev.RED if self.hardened else FIREWALL_ORANGE
        for i in range(n):
            r = (x + i * (seg + 1), y, seg, 2)
            if i < self.firewall:
                pygame.draw.rect(surface, col, r)
            else:
                pygame.draw.rect(surface, (70, 30, 10), r)

    def _draw_exposed(self, surface, img, bx, by, sx, sy):
        # Glitch (doc 5.1): RGB split + horizontal slice offsets, re-rolled 20x a second.
        rng = random.Random(int(self.anim_t * 20) * 131 + self.uid)
        split = 1 if rng.random() < 0.7 else 2
        surface.blit(_silhouette(img, (255, 40, 60)), (bx - split, by))
        surface.blit(_silhouette(img, gdev.CYAN), (bx + split, by))
        top = self.info["top"]
        y = top - 1
        while y < 23:
            hgt = rng.choice((1, 2, 2, 3))
            dx = rng.choice((0, 0, 0, 1, -1, 2, -2))
            surface.blit(img, (bx + dx, by + y), pygame.Rect(0, y, 32, hgt))
            y += hgt
        if rng.random() < 0.25:     # a stray tear line
            ty = by + rng.randrange(top, 22)
            pygame.draw.line(surface, gdev.MAGENTA, (bx + 10, ty), (bx + 22, ty))
        cy = by + (top + 22) // 2
        if self.hardened:
            gdev.text(surface, "LOCKED", (sx, by + top - 4), 8, gdev.RED, "midbottom", True)
        else:
            # cyan jack-in ring (64px range) + countdown arc
            pulse = 0.5 + 0.5 * math.sin(self.anim_t * 8)
            ring_col = (int(40 + 50 * pulse), int(150 + 90 * pulse), int(170 + 85 * pulse))
            _dashed_circle(surface, ring_col, (sx, cy), JACK_RING_PX, self.anim_t * 0.6)
        k = max(0.0, min(1.0, self.expose_t / max(0.01, self.expose_total)))
        arc = pygame.Rect(0, 0, 26, 26)
        arc.center = (sx, cy)
        pygame.draw.circle(surface, gdev.GREEN_DARK, arc.center, 13, 1)
        if k > 0:
            col = gdev.RED if self.hardened else (gdev.WHITE if self.jacked else gdev.CYAN)
            pygame.draw.arc(surface, col, arc, math.pi / 2, math.pi / 2 + k * 2 * math.pi, 2)
        if self.grace_t > 0:
            pygame.draw.circle(surface, gdev.WHITE, (sx, cy), 6 + int(20 * (1 - self.grace_t / EXPOSE_GRACE_S)), 1)

    def _draw_spawning(self, surface, img, bx, by):
        # materialise bottom-up as magenta scanlines
        k = 1.0 - max(0.0, self.spawn_t / SPAWN_SLIDE_S)
        top = self.info["top"]
        cut = int(22 - (22 - top + 1) * k)
        sil = _silhouette(img, gdev.MAGENTA)
        for y in range(top - 1, 23):
            src = img if y >= cut + 2 else sil
            if y >= cut:
                surface.blit(src, (bx, by + y), pygame.Rect(0, y, 32, 1))

    def _draw_dissolve(self, surface, img, bx, by):
        k = 1.0 - max(0.0, self.dissolve_t / DISSOLVE_S)
        if self.dead_cause in ("extract", "overload", "turn_expired"):
            col = gdev.CYAN
        elif self.dead_cause == "despawn":
            col = gdev.GREEN_MID
        else:
            col = gdev.WHITE
        sil = _silhouette(img, col)
        rng = random.Random(self.uid)
        top = self.info["top"]
        for y in range(top - 1, 23):
            if rng.random() < k:
                continue                            # this row is gone
            dx = int(rng.uniform(-6, 6) * k)
            dy = -int(rng.uniform(0, 10) * k)
            surface.blit(sil if k > 0.15 else img, (bx + dx, by + y + dy), pygame.Rect(0, y, 32, 1))
        for i in range(6):                          # rising pixels
            px = bx + 12 + rng.randrange(9) + int(rng.uniform(-8, 8) * k)
            py = by + top + rng.randrange(6) - int(18 * k * rng.uniform(0.5, 1.0))
            if rng.random() > k * 0.8:
                surface.fill(col, (px, py, 1, 1))


def _dashed_circle(surface, color, center, radius, phase):
    """A thin dashed circle (the jack-in range), slowly rotating."""
    segs = 24
    for i in range(0, segs, 2):
        a0 = phase + i * 2 * math.pi / segs
        a1 = a0 + 2 * math.pi / segs
        p0 = (center[0] + math.cos(a0) * radius, center[1] + math.sin(a0) * radius)
        p1 = (center[0] + math.cos(a1) * radius, center[1] + math.sin(a1) * radius)
        pygame.draw.line(surface, color, p0, p1)


# ---------------------------------------------------------------- manager

class MinionManager(object):
    """Public surface:
      minions [Minion]  (live and dying; DEAD ones are removed once their dissolve ends)
      enemy_projectiles [Packet]   every minion shot (team "enemy", or "player" when TURNED)
      update(dt_world, player, run)    AI, spawner (Rack A ports while the player is in its
                                       zone), contact damage via player.take_damage(n, pos);
                                       calls run.on_minion_killed(m) once per WEAPON kill
      resolve_projectiles(projectiles, player)   player shots vs minions (Minion.hit),
                                       minion packets vs the player / enemy minions
      nearest_exposed(pos, max_range) -> Minion|None   (hackable only)
      overload(center, radius, exclude=None) -> [Minion]  EXPOSED by an OVERLOAD EMP
      spawn(kind, pos, rack_id=None) -> Minion
      clear()                          remove every minion (respawn after death)
      live_count() -> int
      draw(surface, camera)
      draw_threats(surface, camera, exclude=None, near=None)   colour redraw of enemies while hacking
    """

    def __init__(self, world):
        self.world = world
        self.minions = []
        self.enemy_projectiles = []
        self.effects = []           # [x, y, radius, seconds_left, total]  (EMP rings)
        self.orders = []            # pending spawns: [bar, kind, seconds_left, rack]
        self.rng = None
        self.run = None
        self.hacking = False
        self.budget = BUDGET_START
        self.wave_t = FIRST_WAVE_S
        self.packs = 0
        self.time = 0.0
        self._dt = 1.0 / 60

    # ------------------------------------------------------------ basic API

    def spawn(self, kind, pos, rack_id=None):
        m = Minion(kind, pos, rack_id)
        self.minions.append(m)
        return m

    def clear(self):
        self.minions = []
        self.enemy_projectiles = []
        self.effects = []
        self.orders = []
        self.budget = BUDGET_START
        self.wave_t = FIRST_WAVE_S
        for rk in self.world.racks:
            for b in rk.bars:
                b.flicker = 0.0

    def live_count(self):
        return len([m for m in self.minions if m.alive])

    def nearest_exposed(self, pos, max_range):
        best, best_d = None, max_range * max_range
        p = V(pos)
        for m in self.minions:
            if m.hackable:
                d = (m.pos - p).length_squared()
                if d <= best_d:
                    best, best_d = m, d
        return best

    def overload(self, center, radius, exclude=None):
        """OVERLOAD EMP: every enemy minion within `radius` becomes EXPOSED (window refreshed).
        Returns them (hardened ones are exposed but never hackable)."""
        c = V(center)
        out = []
        for m in self.minions:
            if m is exclude or not m.alive or m.team != "enemy" or m.jacked:
                continue
            if (m.pos - c).length_squared() <= radius * radius:
                m.expose()
                out.append(m)
        self.effects.append([c.x, c.y, float(radius), 0.45, 0.45])
        return out

    # ------------------------------------------------------------ update

    def update(self, dt_world, player, run):
        self.run = run
        self._dt = dt_world
        self.hacking = getattr(run, "hack", None) is not None
        if self.rng is None:
            self.rng = random.Random(run.rng.random()) if hasattr(run, "rng") else random.Random()

        for m in self.minions:
            if m.state == DEAD and not m.reported:
                m.reported = True
                if m.dead_cause == "weapon":
                    run.on_minion_killed(m)     # Bits, kill stats, 20% health pip
        self.minions = [m for m in self.minions if m.state != DEAD or m.dissolve_t > 0]
        if dt_world <= 0:
            return
        self.time += dt_world

        self._update_spawner(dt_world, player, run)
        self._update_leash(player)
        for m in self.minions:
            m.update(dt_world, player, self)
        for p in self.enemy_projectiles:
            p.update(dt_world, self.world)
        self.enemy_projectiles = [p for p in self.enemy_projectiles if p.alive]
        for e in self.effects:
            e[3] -= dt_world
        self.effects = [e for e in self.effects if e[3] > 0]

    def _zone_rack(self):
        return self.world.rack("A")

    def _update_spawner(self, dt, player, run):
        rack = self._zone_rack()
        if rack is None or not rack.bars:
            return
        # pending, telegraphed spawns always land
        for o in self.orders:
            o[2] -= dt
            if o[2] <= 0:
                self._spawn_from(o[0], o[1], o[3])
        self.orders = [o for o in self.orders if o[2] > 0]
        for b in rack.bars:
            t = [o[2] for o in self.orders if o[0] is b and o[2] <= TELEGRAPH_S]
            b.flicker = max(t) if t else 0.0

        if player.dead or not rack.in_zone(player.pos):
            self.wave_t = max(self.wave_t, FIRST_WAVE_S)
            return
        self.budget = min(BUDGET_MAX, self.budget + BUDGET_REGEN * dt)
        self.wave_t -= dt
        if self.wave_t > 0 or self.orders:
            return

        hacks = len(getattr(run, "hacked_uids", ()))
        soft = min(SOFT_CAP + 2 * hacks, 14)
        live = len([m for m in self.minions if m.alive and m.rack_id == rack.id])
        room = min(ZONE_CAP - live, soft - live)
        size = min(self.rng.randint(4, 6), room, int(self.budget))
        if size < 2:
            self.wave_t = 1.0
            return
        # a port near the player that is under its cap
        ports = sorted(rack.bars, key=lambda b: (V(b.spawn_exit) - player.pos).length_squared())
        ports = [b for b in ports[:2] if self._port_count(rack, b) + size <= PORT_CAP] or \
                [b for b in rack.bars if self._port_count(rack, b) + size <= PORT_CAP]
        if not ports:
            self.wave_t = 1.0
            return
        bar = self.rng.choice(ports)
        kinds = ["b1t"] * size
        p1ngs = len([m for m in self.minions if m.alive and m.kind == "p1ng"])
        if self.packs >= 1 and size >= 3 and p1ngs < MAX_P1NG and self.rng.random() < 0.5:
            kinds = ["p1ng"] + ["b1t"] * (size - 2)
        for i, k in enumerate(kinds):
            self.orders.append([bar, k, TELEGRAPH_S + i * PACK_STAGGER_S, rack])
            self.budget -= KINDS[k]["cost"]
        self.packs += 1
        self.wave_t = self.rng.uniform(*WAVE_GAP_S) + len(kinds) * PACK_STAGGER_S

    def _port_count(self, rack, bar):
        n = len([m for m in self.minions if m.alive and m.rack_id == rack.id and m.port == bar.index])
        return n + len([o for o in self.orders if o[0] is bar])

    def _spawn_from(self, bar, kind, rack):
        ex, ey = bar.spawn_exit
        sh = rack.shell
        if ey >= sh.bottom:
            d = V(0, 1)
        elif ey <= sh.top:
            d = V(0, -1)
        elif ex <= sh.left:
            d = V(-1, 0)
        else:
            d = V(1, 0) if ex >= sh.right else V(0, 1)
        pos = V(ex, ey) + V(-d.y, d.x) * self.rng.uniform(-8, 8)
        for _ in range(8):                  # never start inside a wall
            if not self.world.point_solid(pos.x, pos.y):
                break
            pos += d * 8
        m = self.spawn(kind, pos, rack.id)
        m.port = bar.index
        m.home = V(pos)
        m.state = SPAWNING
        m.spawn_t = SPAWN_SLIDE_S
        m.spawn_dir = d
        return m

    def _update_leash(self, player):
        rack = self._zone_rack()
        if rack is None:
            return
        out = not rack.in_zone(player.pos)
        for m in self.minions:
            if m.team != "enemy" or m.state not in (HUNTING, HARDENED):
                continue
            far = (m.pos - player.pos).length_squared() > LEASH_PX * LEASH_PX
            if out and far and not m.homing:
                m.homing = True
                m.home_t = 10.0
            elif m.homing and not out:
                m.homing = False

    # ------------------------------------------------------------ projectiles

    def resolve_projectiles(self, projectiles, player):
        dt = self._dt
        for p in projectiles:
            if p.alive and getattr(p, "team", "player") == "player":
                self._shot_vs_minions(p, dt)
        for p in self.enemy_projectiles:
            if not p.alive:
                continue
            if p.team == "player":
                self._shot_vs_minions(p, dt)
            elif not player.dead and p.rect.colliderect(player.rect):
                if player.take_damage(p.damage, p.pos):
                    p.alive = False         # a dash (i-frames) lets it pass through
                elif getattr(player, "parrying", False):
                    # katana parry reflects packets: they now strip enemy firewalls
                    p.team = "player"
                    p.vel = -p.vel * 1.5
                    p.life = 2.0
                    p.hit_ids = set()
        self.enemy_projectiles = [p for p in self.enemy_projectiles if p.alive]

    def _shot_vs_minions(self, p, dt):
        """Swept test (previous -> current position) so fast shots cannot tunnel."""
        prev = p.pos - p.vel * dt
        a = (int(round(prev.x)), int(round(prev.y)))
        b = (int(round(p.pos.x)), int(round(p.pos.y)))
        hits = []
        for m in self.minions:
            if m.team != "enemy" or m.state == DEAD or m.uid in p.hit_ids:
                continue
            box = m.hurtbox().inflate(p.rect.w, p.rect.h)
            if box.collidepoint(b) or box.clipline(a, b):
                hits.append(((m.pos - prev).length_squared(), m))
        hits.sort(key=lambda h: h[0])
        for _d, m in hits:
            res = m.hit(getattr(p, "damage", 1), p)
            p.hit_ids.add(m.uid)
            if res in ("grace", "ignored"):
                continue                    # passes through a just-exposed / jacked minion
            if self.run is not None and p.team == "player" and not isinstance(p, Packet):
                if res == "exposed":
                    self.run.float_text((m.pos.x, m.pos.y - 14), "EXPOSED", gdev.CYAN, 0.7)
                elif res == "pinned":
                    self.run.float_text((m.pos.x, m.pos.y - 14), "PINNED +3s", gdev.CYAN, 0.8)
            if not p.pierce:
                p.alive = False
                break

    # ------------------------------------------------------------ drawing

    def draw(self, surface, camera):
        view = camera.view_rect.inflate(80, 80)
        self._draw_telegraphs(surface, camera, view)
        for m in sorted(self.minions, key=lambda m: m.pos.y):
            if view.collidepoint(m.pos):
                m.draw(surface, camera)
        self._draw_packets(surface, camera, view)
        for x, y, r, t, total in self.effects:
            k = 1.0 - t / total
            sx, sy = camera.world_to_screen((x, y))
            rad = max(2, int(r * (0.25 + 0.75 * k)))
            pygame.draw.circle(surface, gdev.CYAN, (sx, sy), rad, max(1, int(3 * (1 - k)) + 1))
            if k < 0.5:
                pygame.draw.circle(surface, gdev.WHITE, (sx, sy), max(2, rad // 2), 1)
        self._draw_edge_arrows(surface, camera)

    def draw_threats(self, surface, camera, exclude=None, near=None, near_px=160):
        """While hacking, Game greys out the world; this redraws the enemy minions and their
        packets in colour on top, so what is closing in on the rooted player stays visible.
        Hunters within near_px of `near` (the player) also get a red caret over their head."""
        view = camera.view_rect.inflate(40, 40)
        close = near_px * near_px
        for m in sorted(self.minions, key=lambda m: m.pos.y):
            if m is exclude or not m.alive or m.team != "enemy" or not view.collidepoint(m.pos):
                continue
            m.draw(surface, camera)
            if near is not None and m.state in (HUNTING, HARDENED) and \
               (m.pos - V(near)).length_squared() <= close:
                sx, sy = camera.world_to_screen(m.pos)
                y = sy + m.rect.h // 2 - 22 + m.info["top"] - 8     # just above the firewall pips
                pygame.draw.polygon(surface, gdev.RED, [(sx - 3, y - 4), (sx + 3, y - 4), (sx, y)])
        self._draw_packets(surface, camera, view)

    def _draw_telegraphs(self, surface, camera, view):
        # World draws the bar strobe and the exit marker (from bar.flicker); this adds
        # a small label so the player can read which port is about to open.
        seen = set()
        for bar, kind, t, rack in self.orders:
            if t > TELEGRAPH_S or id(bar) in seen:
                continue
            seen.add(id(bar))
            if not view.collidepoint(bar.spawn_exit):
                continue
            sx, sy = camera.world_to_screen(bar.spawn_exit)
            on = int(self.time * 16) % 2 == 0
            col = gdev.MAGENTA if on else (120, 20, 90)
            gdev.text(surface, "PORT %d ::0x%02X" % (bar.index + 1, int(self.time * 30) % 256),
                      (sx, sy + 16), 8, col, "midtop")

    def _draw_packets(self, surface, camera, view):
        frames = load_minion_frames("p1ng")
        for p in self.enemy_projectiles:
            if not view.collidepoint(p.pos):
                continue
            sx, sy = camera.world_to_screen(p.pos)
            left = p.vel.x < 0
            img = frames.get("packet_left" if left else "packet_right")
            if img is None:
                col = gdev.CYAN if p.team == "player" else gdev.MAGENTA
                pygame.draw.circle(surface, col, (sx, sy), 2)
                continue
            if p.team == "player":
                img = _cyan_swap(img)
            ang = -math.degrees(math.atan2(p.vel.y, p.vel.x))
            if left:
                ang -= 180
            rot = _rotated(img, ang)
            # sprite ball sits at (16, 16) of its canvas; a faint trail helps dodging
            trail = p.pos - p.vel.normalize() * 5 if p.vel.length_squared() else p.pos
            tx, ty = camera.world_to_screen(trail)
            pygame.draw.line(surface, (110, 30, 90), (tx, ty), (sx, sy))
            surface.blit(rot, rot.get_rect(center=(sx, sy)))

    def _draw_edge_arrows(self, surface, camera):
        """Off-screen but active ranged minions get an arrow at the screen edge (doc 2)."""
        vr = camera.view_rect
        sw, sh = surface.get_size()
        for m in self.minions:
            if m.kind != "p1ng" or m.team != "enemy" or m.state not in (HUNTING, HARDENED):
                continue
            if vr.collidepoint(m.pos):
                continue
            cx, cy = vr.center
            dx, dy = m.pos.x - cx, m.pos.y - cy
            d = math.hypot(dx, dy)
            if d < 1 or d > 900:
                continue
            ux, uy = dx / d, dy / d
            k = min((sw / 2.0 - 14) / max(abs(ux), 1e-6), (sh / 2.0 - 14) / max(abs(uy), 1e-6))
            ax, ay = sw / 2.0 + ux * k, sh / 2.0 + uy * k
            tip = (ax + ux * 6, ay + uy * 6)
            pygame.draw.polygon(surface, KINDS["p1ng"]["color"],
                                [tip, (ax - uy * 4, ay + ux * 4), (ax + uy * 4, ay - ux * 4)])
