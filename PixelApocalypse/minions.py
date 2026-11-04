"""|-|@c|<ing minions and their ports (doc sections 5.1 and 6).

One data-driven Minion class (KINDS below) runs every archetype:
  B1T       swarm rusher: packs of 4-6, seek + separation + wall avoidance, contact damage
  P1NG      ranged: holds 160-230px away, fires bursts of 3 slow packets, flees when EXPOSED
  PR0B3     observer: circles the player (on-screen), no damage. 6s of line of sight while on
            screen -> it runs to its rack to upload (boss +1 Adaptation, TELEMETRY UPLOADED).
            Hears pistol fire within 600px (not the bow). A TURNED PR0B3 poisons the profiler.
  F1R3W4LL  slow warden: a 90 degree frontal shield (8 facings, code-drawn, authoritative) blocks
            projectiles; flank it, parry its bash, or arc over it. Links up to 3 allies within
            160px, whose firewalls regenerate while linked. Hacking it drops the links and
            EXPOSES the linked allies. Usually carries the Keys (progression tags it).
  W0RM      slow crawler: leaves a slowing trail; a weapon kill splits it into 2 B1Ts (a hack
            prevents the split).

State machine: SPAWNING -> HUNTING -> EXPOSED -> (TURNED | DEAD | REBOOT | HARDENED)
  EXPOSED    firewall stripped. 4s window, the first 0.35s hits whiff (grace),
             after that ANY hit kills it (strip-then-stop). Timer out -> REBOOT.
  REBOOT     a short stun, then back to HUNTING with 50% firewall.
  HARDENED   after a BACKTRACE: red outline, +50% firewall, can never be hacked.
  TURNED     after a TURN payload: cyan, fights for the player, then dissolves.
  DEAD       plays a short dissolve, then the manager removes it.

MinionManager owns the minions, their packets and the rack ports. It never decides WHEN to
spawn: bosses.py runs the zone waves (through the Director) and progression.py the Key
carriers; both call spawn_wave(). Positions are world pixels; `pos` is the centre of the
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

EXPOSE_WINDOW_S = 9.0
EXPOSE_GRACE_S = 0.35
PIN_BONUS_S = 3.0           # a pinning shot (bow full charge) on an EXPOSED minion
REBOOT_S = 0.5
SPAWN_SLIDE_S = 0.4         # minion slides out of its port after the telegraph
DISSOLVE_S = 0.55
JACK_RING_PX = 96           # drawn jack-in range (Game uses the same 96px)
JACK_RING_SMALL_PX = 16     # an EXPOSED minion the player is not near: a small ring, not the full range
JACK_RING_NEAR = 1.5        # the full range ring shows within this x range of the player (or the jack target)

# Ports (doc 6): bosses and progression decide when; these are the shared rules
TELEGRAPH_S = 1.5           # port bar flickers this long before a minion comes out
PACK_STAGGER_S = 0.3        # gap between minions leaving the same port
TRICKLE_S = 2.0             # "trickle" pattern: one minion every this many seconds
ZONE_CAP = 16               # live + queued minions per zone
PORT_CAP = 3                # live + queued minions per port
RING_FAN_S = 2.2            # "ring" pattern: seconds spent fanning out around the player
RING_FAN_PX = 110
LEASH_PX = 560              # player out of the zone and this far: walk home and despawn
ALARM_PX = 600              # BACKTRACE alarm pull (deliberately off-screen, doc 5.2)
NEAR_MISS_PX = 48           # a packet passing this close without hitting = a dodge (doc 9.1)
SPLIT_POP_S = 0.25          # W0RM split: the two B1Ts pop out this fast
SLIME_KNOCK_S = 0.02        # W0RM slime drag: one frame of counter-push (see _update_trail)

FIREWALL_ORANGE = (255, 140, 40)
PROBE_RED = (255, 80, 90)
WORM_PLUM = (170, 50, 150)
TRAIL_COL = (96, 22, 84)
TRAIL_HOT = (190, 60, 170)

# 8-direction tokens (same as profiler.dir_token): 0 = E, clockwise on screen
DIRVEC = [V(1, 0).rotate(45 * i) for i in range(8)]
SHIELD_HALF_DEG = 45
SHIELD_COS = math.cos(math.radians(SHIELD_HALF_DEG)) - 0.01

# Archetype data. Speeds are px/s, times seconds, ranges px.
KINDS = {
    "b1t": {
        "name": "B1T", "initial": "B", "color": gdev.MAGENTA,
        "size": (8, 6), "top": 17,           # collision box, sprite's first opaque row
        "speed": 80.0, "firewall": 1, "core_hp": 1, "bits": 5, "cost": 1,
        "contact_damage": 1, "contact_cd": 1.6, "recoil_s": 0.25,
    },
    "p1ng": {
        "name": "P1NG", "initial": "7", "color": gdev.AMBER,
        "size": (8, 8), "top": 10,
        "speed": 65.0, "firewall": 2, "core_hp": 1, "bits": 8, "cost": 2,
        "hold": (160, 230), "fire_range": 300,
        "burst": 2, "burst_gap": 0.25, "windup": 0.6, "burst_cd": (3.5, 4.5),
        "packet_speed": 90.0, "packet_life": 2.6, "packet_damage": 1,
    },
    "pr0b3": {
        "name": "PR0B3", "initial": "0", "color": PROBE_RED,
        "size": (8, 8), "top": 7, "hover": 2,  # floats: body drawn 2px higher, shadow under it
        "speed": 80.0, "firewall": 1, "core_hp": 1, "bits": 6, "cost": 1,
        "hold": (180, 230), "scan_s": 12.0, "hear_px": 600, "los_px": 360,
        "upload_speed": 1.7,
    },
    "f1r3w4ll": {
        "name": "F1R3W4LL", "initial": "#", "color": FIREWALL_ORANGE,
        "size": (10, 10), "top": 8,
        "speed": 36.0, "firewall": 3, "core_hp": 1, "bits": 12, "cost": 3,
        "contact_damage": 1, "recoil_s": 0.2,
        "bash_px": 26, "bash_windup": 0.7, "bash_cd": 2.6, "turn_s": 0.5,
        "link_px": 160, "link_keep_px": 210, "links": 3, "regen_s": 2.0,
    },
    "w0rm": {
        "name": "W0RM", "initial": "~", "color": WORM_PLUM,
        "size": (12, 5), "top": 16,
        "speed": 32.0, "firewall": 2, "core_hp": 1, "bits": 6, "cost": 2,
        "contact_damage": 1, "contact_cd": 1.6, "recoil_s": 0.25,
        "trail_s": 5.0, "trail_step": 6.0, "trail_px": 7, "slow": 0.25, "split": 2,
    },
}
SHIPPED = set(KINDS)
MELEE_KINDS = ("b1t", "w0rm", "f1r3w4ll")

_uids = itertools.count(1)


def dir_token(vx, vy):
    """8-direction token of a vector (the same as profiler.dir_token); None for a zero vector."""
    if abs(vx) < 1e-6 and abs(vy) < 1e-6:
        return None
    return int(round(math.atan2(vy, vx) / (math.pi / 4))) % 8


def counter_weights(table, effects):
    """Patch Notes counters that change WHAT spawns (doc 9.4), for whoever picks wave kinds:
    {kind: weight} -> {kind: weight} with FIREWALL_WEIGHT / MORE_PROBES / FAST_KITE applied
    (renormalised). Kinds that are missing from the table are not added."""
    out = dict(table or {})
    effects = effects or {}
    if "FIREWALL_WEIGHT" in effects and "f1r3w4ll" in out:
        out["f1r3w4ll"] *= float(effects["FIREWALL_WEIGHT"])
    if "MORE_PROBES" in effects and "pr0b3" in out:
        out["pr0b3"] *= float(effects["MORE_PROBES"])
    if "FAST_KITE" in effects and "p1ng" in out:
        out["p1ng"] *= 1.25                         # "more P1NGs"
    total = sum(out.values())
    if total <= 0:
        return out
    return dict((k, w / total) for k, w in out.items())


# ---------------------------------------------------------------- sprites

_frames_cache = {}
_fx_cache = {}
_warned = set()


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


def _scanlined(img):
    """Cached-copy look (R3C4LL): every other row dimmed and tinted toward cyan-green."""
    key = ("scan", id(img))
    s = _fx_cache.get(key)
    if s is None:
        s = img.copy()
        s.fill((140, 200, 190), special_flags=pygame.BLEND_RGB_MULT)
        w, h = s.get_size()
        for y in range(0, h, 2):
            s.fill((0, 70, 60), pygame.Rect(0, y, w, 1), special_flags=pygame.BLEND_RGB_ADD)
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


# A 3x5 pixel font for the head plates ("0x", "KEY", "UPL"). Drawn on the world layer it scales
# up with the zoom like the sprites, so a plate never dwarfs the 8px minion under it.
_MICRO = {
    "0": ("111", "101", "101", "101", "111"),
    "x": ("000", "000", "101", "010", "101"),
    "K": ("101", "101", "110", "101", "101"),
    "E": ("111", "100", "110", "100", "111"),
    "Y": ("101", "101", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "P": ("111", "101", "111", "100", "100"),
    "L": ("100", "100", "100", "100", "111"),
}


def _plate(text, fg, bg=(6, 20, 12), border=None):
    """A tiny pixel-font label plate (cached Surface)."""
    key = ("plate", text, tuple(fg), tuple(bg), tuple(border or fg))
    s = _fx_cache.get(key)
    if s is None:
        w = len(text) * 4 + 3
        s = pygame.Surface((w, 9), pygame.SRCALPHA)
        s.fill(tuple(bg) + (230,))
        pygame.draw.rect(s, tuple(border or fg), s.get_rect(), 1)
        x = 2
        for ch in text:
            rows = _MICRO.get(ch)
            if rows:
                for yy, row in enumerate(rows):
                    for xx, bit in enumerate(row):
                        if bit == "1":
                            s.set_at((x + xx, 2 + yy), fg)
            x += 4
        _fx_cache[key] = s
    return s


# ---------------------------------------------------------------- packets

class Packet(object):
    """A P1NG shot (attacks.py may append its own to MinionManager.enemy_projectiles too).
    Same fields as player.Projectile so code can treat them alike.
    team "enemy" hits the player; team "player" (a TURNED P1NG, or a parried packet) hits
    enemy minions. near / near_done: near-miss bookkeeping (MinionManager emits near_miss once
    per enemy packet that passes within NEAR_MISS_PX of the player without hitting; set
    near_done = True to opt a packet out)."""

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
        self.near = False
        self.near_done = False
        self.reflected = False
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
      kind "b1t"|"p1ng"|"pr0b3"|"f1r3w4ll"|"w0rm", uid int, label str ("B1T 0x2F"),
      rack_id str|None, port (bar index)|None
      pos Vector2, rect Rect, state (constants above), team "enemy"|"player"
      firewall, firewall_max, core_hp, expose_t (seconds of EXPOSED left), bits (reward)
      hardened bool, dead_cause "weapon"|"extract"|"overload"|"turn_expired"|"despawn"|
                 "flush"|"upload"|None
      jacked bool       -- set True by Game while a hack session targets it: hold EXPOSED,
                           do not move/attack, do not reboot on expose timeout.
      alive (property)  -- state != DEAD
      hackable (property) -- EXPOSED, not jacked, not hardened
      hit(damage, source=None) -> "firewall"|"exposed"|"grace"|"killed"|"pinned"|"ignored"
      expose(seconds=4.0), kill(cause="weapon"), reboot(), turn(seconds), harden()
      update(dt, player, manager), draw(surface, camera)

    Expansion tags (MinionManager.spawn_wave(tags=...) / spawn_at sets them as attributes):
      boss_id      parent Daemon id ("r3c4ll") or None
      wave_id      respawn_from_cache: which wave it belongs to
      original     respawn_from_cache: the wave's original (white 0x plate; gone once HARDENED)
      cached       respawn_from_cache: a cached copy (scanline tint; its packet is stale)
      key_source   progression: this minion carries a Key ("fw:A1", "prologue"); cyan KEY plate

    Per-archetype state other modules may read:
      PR0B3     probe_mode "orbit"|"investigate"|"upload", scan_t (s of line of sight), los bool
      F1R3W4LL  shield_dir (direction token 0..7), links [Minion], shield_up() -> bool
      any       linked_by (the F1R3W4LL whose link regenerates this minion) or None

    Optional attributes read from the hit `source` (a projectile or a minion):
      expose=True   strips the whole firewall in one hit (katana parry, charged bow)
      pin=True or full_charge=True   on an EXPOSED minion: +3s to the window (once)
                    instead of a kill
      no_kill=True  never kills an EXPOSED minion (turned allies leave them for you)
      arc=True / reflected=True   passes over / through a F1R3W4LL shield
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
        self.spawn_total = SPAWN_SLIDE_S
        self.spawn_dir = V(0, 1)
        self.dissolve_t = 0.0
        self.bits = info["bits"]
        self.jacked = False
        self.dead_cause = None
        # Expansion tags (set through MinionManager.spawn_wave(tags=...); see the contract)
        self.boss_id = None         # parent Daemon id ("r3c4ll"), None for untagged minions
        self.wave_id = None         # respawn_from_cache: which wave it belongs to
        self.original = False       # respawn_from_cache: the wave's 0x original
        self.cached = False         # respawn_from_cache: a cached copy (stale packets)
        self.key_source = None      # progression: this minion carries a Key ("fw:A1", "prologue")
        self.reported = False       # death already reported to the Run
        self.anim_t = self.rng.random()
        self.side = "right"
        self.flash_t = 0.0          # white flash after a firewall hit
        self.attack_t = 0.0         # attack frame held while > 0
        self.cooldown = 0.0         # contact damage / burst cooldown
        self.recoil_t = 0.0
        self.recoil_dir = V(0, 0)
        self.avoid_side = 1
        self.speed = info["speed"] * self.rng.uniform(0.9, 1.1)
        self.speed_mult = 1.0       # Patch Notes BIT_SPEED (set by the manager every frame)
        self.home = V(pos)
        self.homing = False
        self.home_t = 0.0
        self.fan_t = 0.0            # "ring" pattern: fanning out around the player
        self.fan_angle = 0.0
        self.linked_by = None       # the F1R3W4LL whose link regenerates this minion
        self.link_regen_t = 0.0
        self.regen_flash = 0.0
        # P1NG / PR0B3
        self.hold_px = self.rng.uniform(*info["hold"]) if "hold" in info else 0
        self.strafe = self.rng.choice((-1, 1))
        self.strafe_t = self.rng.uniform(1.5, 3.0)
        self.shots_left = 0
        self.shot_t = 0.0
        if "burst_cd" in info:
            self.cooldown = self.rng.uniform(1.0, 2.0)
        # PR0B3
        self.probe_mode = "orbit"
        self.scan_t = 0.0
        self.los = False
        self.los_check_t = self.rng.uniform(0.0, 0.2)
        self.orbit_angle = self.rng.uniform(0, 2 * math.pi)
        self.investigate_pos = None
        self.stuck_t = 0.0
        self.upload_t = 0.0
        # F1R3W4LL
        self.shield_dir = 2
        self.shield_turn_t = 0.0
        self.shield_flash = 0.0
        self.bash_t = 0.0
        self.links = []
        self.links_down = False     # hacked: never re-links
        self.block_text_t = 0.0
        # W0RM
        self.trail_from = V(pos)

    # ------------------------------------------------------------ status

    @property
    def alive(self):
        return self.state != DEAD

    @property
    def hackable(self):
        return self.state == EXPOSED and not self.jacked and not self.hardened

    def hurtbox(self):
        """Hit area for shots: a little bigger than the tiny body (raised for hovering PR0B3s)."""
        return self.rect.inflate(4, 6).move(0, -self.info.get("hover", 0))

    def shield_up(self):
        """F1R3W4LL: is the frontal shield blocking right now (not while EXPOSED or TURNED)?"""
        return self.kind == "f1r3w4ll" and self.team == "enemy" and \
            self.state in (HUNTING, HARDENED, REBOOT, SPAWNING)

    def shield_blocks(self, shot, player_pos=None):
        """True if the shield stops `shot`: it comes from within +-45 degrees of shield_dir.
        A moving shot is judged by where it came from (-vel), a katana slash (no velocity) by
        where the player stands. Arc shots and parried packets pass."""
        if not self.shield_up() or getattr(shot, "arc", False) or getattr(shot, "reflected", False):
            return False
        vel = getattr(shot, "vel", None)
        if vel is not None and vel.length_squared() > 1:
            come = -V(vel)
        elif player_pos is not None:
            come = V(player_pos) - self.pos
        else:
            return False
        if come.length_squared() < 1e-6:
            return False
        return come.normalize().dot(DIRVEC[self.shield_dir]) >= SHIELD_COS

    def _spd(self):
        return self.speed * self.speed_mult

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
        self.bash_t = 0.0

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
        self.probe_mode = "orbit"           # a turned PR0B3 never finishes its upload
        self.scan_t = 0.0
        self.bash_t = 0.0

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
        self.shield_flash = max(0.0, self.shield_flash - dt)
        self.regen_flash = max(0.0, self.regen_flash - dt)
        self.block_text_t = max(0.0, self.block_text_t - dt)
        self.speed_mult = manager.speed_mult(self)

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
                        self._go(manager.world, away.normalize() * self._spd() * 0.75, dt, manager)
        elif self.state == TURNED:
            self.turn_t -= dt
            if self.turn_t <= 0:
                self.kill("turn_expired")
            else:
                self._update_turned(dt, player, manager)
        else:                           # HUNTING / HARDENED
            if self.homing:
                self._update_homing(dt, manager)
            elif self.fan_t > 0 and self.kind in MELEE_KINDS:
                self._update_fan(dt, player, manager)
            elif self.kind == "p1ng":
                self._update_p1ng(dt, player, manager)
            elif self.kind == "pr0b3":
                self._update_pr0b3(dt, player, manager)
            elif self.kind == "f1r3w4ll":
                self._update_f1r3w4ll(dt, player, manager)
            else:
                self._update_b1t(dt, player, manager)       # B1T and W0RM

        if self.recoil_t > 0 and self.state != DEAD and not self.jacked:
            self.recoil_t -= dt
            self._move(manager.world, self.recoil_dir * 90.0 * dt)
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))
        if self.kind == "w0rm" and self.state != DEAD:
            manager.lay_trail(self)

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
        speed = self._spd()
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

    def _update_fan(self, dt, player, manager):
        """"ring" pattern: walk to an assigned spot around the player before closing in."""
        self.fan_t -= dt
        if player.dead:
            self.fan_t = 0.0
            return
        spot = player.pos + V(RING_FAN_PX, 0).rotate_rad(self.fan_angle)
        to = spot - self.pos
        if to.length() < 12:
            self.fan_t = 0.0
            return
        self._go(manager.world, to.normalize() * self._spd() * 1.1, dt, manager)
        self._face(to.x)
        if self.kind == "f1r3w4ll":
            self._turn_shield(player.pos - self.pos, dt)

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
                self._fire(manager, manager.aim_point(player), "enemy")
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
        # hold 160-230px (FAST_KITE: x1.2, at most 240): close in, back off, or strafe
        hold = manager.hold_px(self)
        self.strafe_t -= dt
        if self.strafe_t <= 0:
            self.strafe = -self.strafe
            self.strafe_t = self.rng.uniform(1.5, 3.0)
        spd = self._spd()
        if dist > hold + 25:
            desire = dirn * spd
        elif dist < hold - 25:
            desire = -dirn * spd
        else:
            desire = V(-dirn.y, dirn.x) * self.strafe * spd * 0.5
        self._go(manager.world, desire, dt, manager)

    def _fire(self, manager, at, team):
        aim = V(at) - self.pos
        if aim.length_squared() == 0:
            return
        aim = aim.normalize().rotate(self.rng.uniform(-6, 6))
        p = Packet(self.pos + aim * 6, aim * self.info["packet_speed"],
                   self.info["packet_damage"], team, self.info["packet_life"])
        manager.enemy_projectiles.append(p)

    # ---- PR0B3

    def _update_pr0b3(self, dt, player, manager):
        info = self.info
        if self.probe_mode == "upload":
            self._update_upload(dt, manager)
            return
        if player.dead:
            self.los = False
            self._go(manager.world, V(0, 0), dt, manager)
            return
        to = player.pos - self.pos
        dist = to.length()
        # line of sight while on screen (checked 5x a second)
        self.los_check_t -= dt
        if self.los_check_t <= 0:
            self.los_check_t = 0.2
            view = manager.view_rect()
            on_screen = view is None or view.collidepoint(self.pos)
            self.los = bool(on_screen and dist < info["los_px"] and
                            manager.world.segment_clear(self.pos, player.pos, 10))
        if self.los:
            self.scan_t += dt
            self._face(to.x)
            if self.scan_t >= info["scan_s"]:
                self._start_upload(manager)
                return
        else:
            self.scan_t = max(0.0, self.scan_t - 0.5 * dt)      # breaking sight buys time

        spd = self._spd()
        if self.probe_mode == "investigate" and self.investigate_pos is not None:
            go = self.investigate_pos - self.pos
            if go.length() < 24 or dist < self.hold_px:
                self.probe_mode = "orbit"
                self.investigate_pos = None
            else:
                self._go(manager.world, go.normalize() * spd * 1.3, dt, manager)
                self._face(go.x)
                return
        # orbit: an ellipse around the player sized to the visible view, so it stays on screen
        rx, ry = manager.orbit_radii(self.hold_px)
        if dist > 2.2 * max(rx, ry):
            desire = to.normalize() * spd                       # too far: come back first
        else:
            w = spd * 0.55 / max(40.0, (rx + ry) / 2.0)
            self.orbit_angle += self.strafe * w * dt
            spot = player.pos + V(math.cos(self.orbit_angle) * rx, math.sin(self.orbit_angle) * ry)
            go = spot - self.pos
            n = go.length()
            desire = go * (spd / n) if n > 6 else V(0, 0)
            if n > 6 and n < 40:
                desire *= max(0.35, n / 40.0)
        before = V(self.pos)
        self._go(manager.world, desire, dt, manager)
        if desire.length_squared() > 100 and (self.pos - before).length_squared() < (spd * dt * 0.2) ** 2:
            self.stuck_t += dt
            if self.stuck_t > 0.6:
                self.strafe = -self.strafe              # wall: circle the other way
                self.stuck_t = 0.0
        else:
            self.stuck_t = 0.0
        if not self.los:
            self._face(to.x)

    def _start_upload(self, manager):
        self.probe_mode = "upload"
        self.los = False
        self.upload_t = 0.0
        self.stuck_t = 0.0
        rack = manager.world.rack(self.rack_id) if self.rack_id else None
        if rack is not None and rack.bars:
            bar = min(rack.bars, key=lambda b: (V(b.spawn_exit) - self.pos).length_squared())
            self.home = V(bar.spawn_exit)
        if manager.run is not None:
            manager.run.float_text((self.pos.x, self.pos.y - 16), "UPL04D1NG", PROBE_RED, 1.0)

    def _update_upload(self, dt, manager):
        to = self.home - self.pos
        self.upload_t += dt
        if to.length() < 16:
            manager.probe_uploaded(self)
            return
        before = V(self.pos)
        self._go(manager.world, to.normalize() * self._spd() * self.info["upload_speed"], dt, manager)
        self._face(to.x)
        moved = (self.pos - before).length()
        self.stuck_t = self.stuck_t + dt if moved < 0.2 * self._spd() * dt else max(0.0, self.stuck_t - dt)
        if self.stuck_t > 4.0 or self.upload_t > 40.0:
            manager.probe_uploaded(self)            # cannot path home: it uploads from where it is

    def hear(self, pos):
        """PR0B3: a pistol shot within hear_px. Unaware or far ones come to look."""
        if self.kind != "pr0b3" or self.probe_mode == "upload" or self.state not in (HUNTING, HARDENED):
            return
        if (V(pos) - self.pos).length() > self.hold_px + 40:
            self.probe_mode = "investigate"
            self.investigate_pos = V(pos)
        self.homing = False

    # ---- F1R3W4LL

    def _turn_shield(self, to, dt):
        """Turn the shield toward `to`, one 45 degree step every turn_s (dashing past flanks it)."""
        want = dir_token(to.x, to.y)
        self.shield_turn_t -= dt
        if want is None or want == self.shield_dir or self.shield_turn_t > 0:
            return
        diff = (want - self.shield_dir) % 8
        self.shield_dir = (self.shield_dir + (1 if diff <= 4 else -1)) % 8
        self.shield_turn_t = self.info["turn_s"]
        self._face_shield()

    def _face_shield(self):
        x = DIRVEC[self.shield_dir].x
        self.side = "left" if x < -0.1 else "right"      # pure up / down use the right sprite

    def _update_f1r3w4ll(self, dt, player, manager):
        info = self.info
        target = self._enemy_target(player, manager)
        if target is None:
            self.bash_t = 0.0
            self._go(manager.world, V(0, 0), dt, manager)
            return
        to = target.pos - self.pos
        dist = to.length()
        if self.bash_t > 0:                         # wind-up: planted, shield raised
            self.attack_t = max(self.attack_t, 0.1)
            self.bash_t -= dt
            self._go(manager.world, V(0, 0), dt, manager)
            if self.bash_t <= 0:
                self._bash(target, player, manager)
            return
        self._turn_shield(to, dt)
        if self.recoil_t > 0:
            return
        if self.cooldown <= 0 and dist < info["bash_px"]:
            self.bash_t = info["bash_windup"]
            return
        desire = to.normalize() * self._spd() if dist > 16 else V(0, 0)
        self._go(manager.world, desire, dt, manager)

    def _bash(self, target, player, manager):
        info = self.info
        to = target.pos - self.pos
        self.cooldown = info["bash_cd"]
        self.attack_t = 0.2
        if to.length() > info["bash_px"] + 10:
            return                                  # it stepped away during the wind-up
        if target is player:
            hurt = player.take_damage(info["contact_damage"], self.pos)
            if not hurt and getattr(player, "parrying", False):
                self.expose()                       # katana parry EXPOSES the warden (doc 6)
                if manager.run is not None:
                    manager.run.float_text((self.pos.x, self.pos.y - 18), "PARRIED", gdev.CYAN, 0.7)
                return
        else:
            target.hit(1, self)
        self.recoil_t = info["recoil_s"]
        self.recoil_dir = (-to).normalize() if to.length_squared() > 0 else V(0, -1)

    # ---- TURNED / homing

    def _update_turned(self, dt, player, manager):
        # Fight for the player: strip enemy firewalls, leave EXPOSED ones for the player to hack.
        if self.kind == "pr0b3":                    # an observer: tags along, feeding the model lies
            off = V(-20 if self.uid % 2 else 20, -22)
            to = player.pos + off - self.pos
            self._go(manager.world, to.normalize() * self._spd() if to.length() > 8 else V(0, 0), dt, manager)
            self._face(to.x)
            return
        target, best = None, 280.0 * 280.0
        for m in manager.minions:
            if m.team == "enemy" and m.state in (HUNTING, HARDENED, REBOOT):
                d = (m.pos - self.pos).length_squared()
                if d < best:
                    target, best = m, d
        if target is None:
            off = V(-24 if self.uid % 2 else 24, 18)          # follow the player
            to = player.pos + off - self.pos
            desire = to.normalize() * self._spd() if to.length() > 10 else V(0, 0)
            self._go(manager.world, desire, dt, manager)
            self._face(to.x)
            return
        to = target.pos - self.pos
        self._face(to.x)
        if self.kind == "f1r3w4ll":
            self._turn_shield(to, dt)
        if self.kind == "p1ng":
            dist = to.length()
            desire = V(0, 0)
            if dist > 130:
                desire = to.normalize() * self._spd()
            elif dist < 90 and dist > 0:
                desire = -to.normalize() * self._spd()
            self._go(manager.world, desire, dt, manager)
            if self.cooldown <= 0:
                self._fire(manager, target.pos, "player")
                self.attack_t = 0.15
                self.cooldown = 0.7
            return
        desire = to.normalize() * self._spd() * 1.1 if to.length_squared() > 1 else V(0, 0)
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
        self._go(manager.world, to.normalize() * self._spd(), dt, manager)
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
        spd = self._spd()
        steer = V(desire) + sep * spd * 0.6
        if steer.length_squared() > 1:
            steer = self._avoid(world, steer)
            if steer.length() > spd * 1.4 and steer.length() > desire.length():
                steer.scale_to_length(max(spd * 1.4, desire.length()))
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
        elif self.kind == "pr0b3" and self.los and self.state in (HUNTING, HARDENED):
            name = "%s_scan" % self.side
        else:
            moving = self.vel.length_squared() > 25 and self.state != EXPOSED
            rate = 6 if moving else 2
            name = "%s_%d" % (self.side, int(self.anim_t * rate) % 2)
        img = frames.get(name) or frames.get(self.side + "_0")
        if img is None:
            if self.kind not in _warned:
                _warned.add(self.kind)
                print("[minions] missing frame %s_%s, drawing a box" % (self.kind, name))
            img = _fallback_frame(self.kind)
        return img

    def draw(self, surface, camera):
        sx, sy = camera.world_to_screen(self.pos)
        img = self._frame()
        # 32x32 canvas, body centred on column 16, feet on row 21 = bottom of the box
        hover = self.info.get("hover", 0)
        bx = sx - 16
        by = sy + self.rect.h // 2 - 22
        if hover:
            # a hovering drone: shadow on the floor, body bobbing above it
            pygame.draw.ellipse(surface, (6, 22, 12), (sx - 4, by + 19, 9, 3))
            by -= hover + int(round(math.sin(self.anim_t * 4.0)))
        top = by + self.info["top"]

        if self.state == DEAD:
            self._draw_dissolve(surface, img, bx, by)
            return
        if self.state == SPAWNING:
            self._draw_spawning(surface, img, bx, by)
            return

        shield_behind = self.kind == "f1r3w4ll" and DIRVEC[self.shield_dir].y < -0.1
        if shield_behind:
            self._draw_shield(surface, sx, by)

        if self.hardened:
            red = _silhouette(img, gdev.RED)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                surface.blit(red, (bx + dx, by + dy))
        elif self.linked_by is not None and self.state in (HUNTING, REBOOT):
            # linked: a thin orange outline (its firewall regenerates)
            col = gdev.WHITE if self.regen_flash > 0 else (150, 70, 20)
            sil = _silhouette(img, col)
            for dx, dy in ((1, 0), (-1, 0)):
                surface.blit(sil, (bx + dx, by + dy))

        if self.state == EXPOSED:
            self._draw_exposed(surface, img, bx, by, sx, sy)
        elif self.state == TURNED:
            surface.blit(_cyan_swap(img), (bx, by))
        elif self.state == REBOOT:
            if int(self.anim_t * 20) % 2 == 0:
                surface.blit(_scanlined(img) if self.cached else img, (bx, by))
            else:
                surface.blit(_silhouette(img, gdev.GREEN_MID), (bx, by))
        else:
            surface.blit(_scanlined(img) if self.cached else img, (bx, by))
        if self.flash_t > 0:
            surface.blit(_silhouette(img, gdev.WHITE), (bx, by))
        if self.kind == "f1r3w4ll" and not shield_behind and self.state != EXPOSED:
            self._draw_shield(surface, sx, by)

        # bars and plates above the head
        bar_y = top - 4
        if self.state in (HUNTING, HARDENED, REBOOT):
            self._draw_firewall(surface, sx, bar_y)
            if self.kind == "pr0b3" and self.scan_t > 0 and self.probe_mode != "upload":
                k = min(1.0, self.scan_t / self.info["scan_s"])
                pygame.draw.rect(surface, (40, 10, 14), (sx - 7, bar_y - 3, 14, 1))
                pygame.draw.rect(surface, PROBE_RED, (sx - 7, bar_y - 3, int(14 * k + 0.5), 1))
        elif self.state == TURNED:
            w = 14
            k = max(0.0, self.turn_t / max(0.01, self.turn_total))
            pygame.draw.rect(surface, gdev.GREEN_DARK, (sx - w // 2, bar_y, w, 2))
            pygame.draw.rect(surface, gdev.CYAN, (sx - w // 2, bar_y, int(w * k + 0.5), 2))
        self._draw_plates(surface, sx, bar_y - 4)

    def _draw_plates(self, surface, cx, y):
        """Stacked head plates: KEY (cyan), 0x original (white), UPL (uploading PR0B3)."""
        if self.state in (DEAD, SPAWNING):
            return
        plates = []
        if self.key_source and self.team == "enemy":
            plates.append(_plate("KEY", gdev.CYAN, (4, 30, 34)))
        if self.original and not self.hardened and self.team == "enemy":
            plates.append(_plate("0x", gdev.WHITE, (30, 30, 34)))
        if self.kind == "pr0b3" and self.probe_mode == "upload" and self.state != TURNED:
            on = int(self.anim_t * 8) % 2 == 0
            plates.append(_plate("UPL", PROBE_RED if on else gdev.WHITE, (40, 6, 12)))
        for p in plates:
            r = p.get_rect(midbottom=(cx, y))
            surface.blit(p, r)
            y = r.top - 1

    def _draw_shield(self, surface, sx, by):
        """F1R3W4LL: the authoritative 90 degree shield arc at its true 8-way facing."""
        if not self.shield_up() and self.state != TURNED:
            return
        cx, cy = sx, by + 15                        # body centre on the canvas
        ang = math.radians(45 * self.shield_dir)
        r = 11
        if self.shield_flash > 0:
            col = gdev.WHITE
        elif self.state == TURNED:
            col = gdev.CYAN
        elif self.bash_t > 0 and int(self.anim_t * 20) % 2 == 0:
            col = (255, 230, 120)                   # wind-up flash: the bash is coming
        else:
            col = gdev.RED if self.hardened else FIREWALL_ORANGE
        pts = []
        for i in range(7):
            a = ang - math.radians(SHIELD_HALF_DEG) + i * math.radians(2 * SHIELD_HALF_DEG) / 6
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r * 0.8))
        inner = [(cx + (x - cx) * 0.8, cy + (y - cy) * 0.8) for x, y in pts]
        pygame.draw.lines(surface, (120, 50, 10), False, inner, 1)
        pygame.draw.lines(surface, col, False, pts, 2)

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
            # cyan jack-in ring: the full 64px range only near the player (MinionManager.draw sets
            # ring_full), else a small ring, so a few EXPOSED minions never cover the view
            pulse = 0.5 + 0.5 * math.sin(self.anim_t * 8)
            ring_col = (int(40 + 50 * pulse), int(150 + 90 * pulse), int(170 + 85 * pulse))
            r = JACK_RING_PX if getattr(self, "ring_full", True) else JACK_RING_SMALL_PX
            _dashed_circle(surface, ring_col, (sx, cy), r, self.anim_t * 0.6)
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
        k = 1.0 - max(0.0, self.spawn_t / max(0.01, self.spawn_total))
        top = self.info["top"]
        cut = int(22 - (22 - top + 1) * k)
        sil = _silhouette(img, gdev.MAGENTA)
        for y in range(top - 1, 23):
            src = img if y >= cut + 2 else sil
            if y >= cut:
                surface.blit(src, (bx, by + y), pygame.Rect(0, y, 32, 1))

    def _draw_dissolve(self, surface, img, bx, by):
        k = 1.0 - max(0.0, self.dissolve_t / DISSOLVE_S)
        if self.dead_cause in ("extract", "overload", "turn_expired", "flush"):
            col = gdev.CYAN
        elif self.dead_cause == "upload":
            col = PROBE_RED
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
      enemy_projectiles [Packet]   every minion shot (team "enemy", or "player" when TURNED);
                                   attacks.py may append minions.Packet(pos, vel, dmg, "enemy", life)
      orders [[bar, kind, seconds_left, rack, tags]]   queued port spawns (spawn_at orders have
                                   bar None, rack None and a 6th item: the world position)
      trail [[x, y, seconds_left]]  W0RM slime (slows the player)
      update(dt_world, player, run)    AI, queued spawns, leash, links, trail, contact damage;
                                       calls run.on_minion_killed(m) once per WEAPON kill and
                                       run.on_minion_died(m) once per death of any cause
      resolve_projectiles(projectiles, player)   player shots vs minions (Minion.hit, F1R3W4LL
                                       shields), minion packets vs the player / enemy minions,
                                       near_miss events
      nearest_exposed(pos, max_range) -> Minion|None   (hackable only)
      overload(center, radius, exclude=None) -> [Minion]  EXPOSED by an OVERLOAD EMP
      spawn(kind, pos, rack_id=None) -> Minion        instant, untelegraphed (tests)
      spawn_wave(rack_id, kinds, pattern="vent_burst", ports=None, tags=None, params=None) -> int
      spawn_at(kind, pos, tags=None, telegraph=1.0) -> int
      respawn_copy(m, delay_s=3.0, tags=None) -> int    R3C4LL cached copy of m from its port
      live(rack_id=None, kind=None, team="enemy") -> [Minion]
      pending(rack_id=None) -> int        queued (telegraphing) spawns
      despawn_rack(rack_id, cause="flush")  dissolve every minion of that rack (ports go dark)
      set_world(world)                    the Deep Core swap
      clear()                          remove every minion (respawn after death)
      live_count() -> int
      on_event(name, data)             Run event listener
      draw(surface, camera)
      draw_threats(surface, camera, exclude=None, near=None)   colour redraw of enemies while hacking
    Events emitted: minion_spawned (every port / point / split spawn), probe_uploaded, near_miss.
    """

    def __init__(self, world):
        self.world = world
        self.minions = []
        self.enemy_projectiles = []
        self.effects = []           # [x, y, radius, seconds_left, total]  (EMP rings)
        self.orders = []            # pending spawns: [bar, kind, seconds_left, rack, tags(, pos)]
        self.trail = []             # W0RM slime: [x, y, seconds_left]
        self.rng = None
        self.run = None
        self.hacking = False
        self.time = 0.0
        self._dt = 1.0 / 60
        self.fx = {}                # Patch Notes effects this frame
        self._link_t = 0.0

    # ------------------------------------------------------------ spawning

    def spawn_wave(self, rack_id, kinds, pattern="vent_burst", ports=None, tags=None, params=None):
        """Queue telegraphed spawns at a rack's ports (doc 6 / 7.1). Each port bar flickers
        TELEGRAPH_S, then the minion slides out at that bar's spawn_exit with `tags` set as
        attributes. `kinds` items are kind strings, or {"kind": k, **tags} for per-minion tags
        (e.g. the one {"kind": "b1t", "original": True} of a R3C4LL wave).
          vent_burst  everything at once from 1-2 ports (the given ports, else nearest the player)
          trickle     one at a time, every params["trickle_s"] (default 1.2) from rotating ports
          ring        every port at once; then they fan out around the player
        params: trickle_s, delay_s (extra wait before the telegraph), stagger_s.
        Caps: ZONE_CAP live + queued per zone, PORT_CAP per port. Returns how many were queued."""
        rack = self.world.rack(rack_id)
        if rack is None or not rack.bars or not kinds:
            return 0
        params = params or {}
        rng = self._rng()
        room = ZONE_CAP - len(self.live(rack.id, team=None)) - self.pending(rack.id)
        if room <= 0:
            return 0
        ref = self.run.player.pos if self.run is not None else V(rack.zone_center)
        by_near = sorted(rack.bars, key=lambda b: (V(b.spawn_exit) - ref).length_squared())
        given = [rack.bars[i] for i in (ports or []) if 0 <= i < len(rack.bars)]
        if pattern == "ring":
            bars = list(rack.bars)
        elif given:
            bars = given
        elif pattern == "trickle":
            bars = by_near
        else:
            bars = by_near[:rng.choice((1, 2))]
        delay = float(params.get("delay_s", 0.0))
        stagger = float(params.get("stagger_s", PACK_STAGGER_S))
        trickle = float(params.get("trickle_s", TRICKLE_S))
        load = dict((id(b), self._port_count(rack, b)) for b in rack.bars)
        per_port = {}
        fan0 = rng.uniform(0, 2 * math.pi)
        entries = [k if isinstance(k, dict) else {"kind": k} for k in kinds]
        queued = 0
        for i, e in enumerate(entries):
            if queued >= room:
                break
            kind = e.get("kind") if e.get("kind") in KINDS else "b1t"
            bar = self._pick_port(bars, rack, load, i)
            if bar is None:
                break
            load[id(bar)] += 1
            n = per_port.get(id(bar), 0)
            per_port[id(bar)] = n + 1
            if pattern == "trickle":
                t = TELEGRAPH_S + delay + queued * trickle
            else:
                t = TELEGRAPH_S + delay + n * stagger
            mtags = dict(tags or {})
            mtags.update(dict((k, v) for k, v in e.items() if k != "kind"))
            if pattern == "ring":
                mtags.setdefault("fan_angle", fan0 + 2 * math.pi * i / max(1, len(entries)))
                mtags.setdefault("fan_t", RING_FAN_S)
            self.orders.append([bar, kind, t, rack, mtags])
            queued += 1
        return queued

    def _pick_port(self, bars, rack, load, i):
        """The i-th port of the pattern's list that is under PORT_CAP (else any port that is)."""
        for k in range(len(bars)):
            b = bars[(i + k) % len(bars)]
            if load[id(b)] < PORT_CAP:
                return b
        free = [b for b in rack.bars if load[id(b)] < PORT_CAP]
        return free[0] if free else None

    def respawn_copy(self, m, delay_s=3.0, tags=None):
        """R3C4LL's cache (respawn_from_cache): queue a cached copy of minion m from its own
        port (else the port nearest to where it died), arriving delay_s after this call
        (the 1s port telegraph included). Copies keep kind, rack, boss_id and wave_id and get
        cached=True, original=False; `tags` adds more. -> 1|0 (0: no rack or zone full)."""
        rack = self.world.rack(m.rack_id) if m.rack_id else None
        if rack is None or not rack.bars:
            return 0
        port = m.port if m.port is not None and 0 <= m.port < len(rack.bars) else \
            min(range(len(rack.bars)), key=lambda i: (V(rack.bars[i].spawn_exit) - m.pos).length_squared())
        t = {"boss_id": m.boss_id, "wave_id": m.wave_id, "cached": True, "original": False}
        t.update(tags or {})
        return self.spawn_wave(rack.id, [m.kind], "vent_burst", ports=[port], tags=t,
                               params={"delay_s": max(0.0, float(delay_s) - TELEGRAPH_S)})

    def spawn_at(self, kind, pos, tags=None, telegraph=1.0):
        """Telegraphed spawn at a world point (the Deep Core has no racks). A magenta glyph
        circle marks the spot for `telegraph` seconds. tags may carry rack_id / boss_id. -> 1|0"""
        live = len([m for m in self.minions if m.alive and m.rack_id == (tags or {}).get("rack_id")])
        if live + self.pending(None) >= ZONE_CAP:
            return 0
        kind = kind if kind in KINDS else "b1t"
        self.orders.append([None, kind, max(0.05, float(telegraph)), None, dict(tags or {}), V(pos)])
        return 1

    def spawn(self, kind, pos, rack_id=None):
        m = Minion(kind, pos, rack_id)
        self.minions.append(m)
        return m

    def _rng(self):
        if self.rng is None:
            self.rng = random.Random(self.run.rng.random()) if self.run is not None and \
                hasattr(self.run, "rng") else random.Random()
        return self.rng

    # ------------------------------------------------------------ queries

    def live(self, rack_id=None, kind=None, team="enemy"):
        return [m for m in self.minions if m.alive and (team is None or m.team == team) and
                (rack_id is None or m.rack_id == rack_id) and (kind is None or m.kind == kind)]

    def pending(self, rack_id=None):
        return len([o for o in self.orders if rack_id is None or
                    (o[3] is not None and o[3].id == rack_id)])

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

    def view_rect(self):
        cam = getattr(self.run, "camera", None) if self.run is not None else None
        return cam.view_rect if cam is not None else None

    def speed_mult(self, m):
        """Patch Notes BIT_SPEED: B1Ts move faster (doc 9.4)."""
        if m.kind == "b1t" and "BIT_SPEED" in self.fx:
            return float(self.fx["BIT_SPEED"])
        return 1.0

    def hold_px(self, m):
        """P1NG hold distance; FAST_KITE: x1.2, clamped to 240px (doc 9.4)."""
        if "FAST_KITE" in self.fx:
            return min(240.0, max(m.hold_px, m.hold_px * float(self.fx["FAST_KITE"])))
        return m.hold_px

    def orbit_radii(self, hold):
        """PR0B3 orbit: the doc's 180-230px, shrunk to an ellipse that fits the visible view
        (at 3X zoom the view is only ~320x167 world px)."""
        view = self.view_rect()
        if view is None:
            return hold, hold
        return min(hold, view.w * 0.40), min(hold, view.h * 0.36)

    def aim_point(self, player):
        """Where a P1NG aims. LEAD_SHOTS {"side": token, "lead": s}: lead the player's velocity,
        and a standing player gets the shot on the side it likes to dodge to (doc 9.4)."""
        lead = self.fx.get("LEAD_SHOTS")
        if not lead:
            return V(player.pos)
        import attacks                              # lazy: attacks.py imports minions
        s = float(lead.get("lead", 0.3)) if isinstance(lead, dict) else 0.3
        p = V(attacks.lead_point(player, s))
        side = lead.get("side") if isinstance(lead, dict) else None
        if side is not None and player.vel.length_squared() < 100:
            p += DIRVEC[int(side) % 8] * 18
        return p

    # ------------------------------------------------------------ commands

    def despawn_rack(self, rack_id, cause="flush"):
        for m in self.minions:
            if m.alive and m.rack_id == rack_id and m.team == "enemy" and not m.jacked:
                m.kill(cause)
        self.orders = [o for o in self.orders if o[3] is None or o[3].id != rack_id]

    def set_world(self, world):
        self.world = world
        self.clear()

    def clear(self):
        self.minions = []
        self.enemy_projectiles = []
        self.effects = []
        self.orders = []
        self.trail = []
        for rk in self.world.racks:
            for b in rk.bars:
                b.flicker = 0.0

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

    def probe_uploaded(self, m):
        """A PR0B3 reached its rack: +1 Adaptation for its boss (bosses listen), then it is gone."""
        if not m.alive:
            return
        m.probe_mode = "orbit"
        m.kill("upload")
        if self.run is not None and hasattr(self.run, "emit"):
            self.run.emit("probe_uploaded", minion=m)

    def lay_trail(self, m):
        """W0RM: drop a slime point every trail_step px it crawls."""
        step = m.info["trail_step"]
        if m.state in (HUNTING, HARDENED, TURNED) and (m.pos - m.trail_from).length_squared() >= step * step:
            m.trail_from = V(m.pos)
            self.trail.append([m.pos.x, m.pos.y + 2, m.info["trail_s"]])
            if len(self.trail) > 600:
                self.trail = self.trail[-600:]

    # ------------------------------------------------------------ events

    def on_event(self, name, data):
        """Run events: pistol fire -> PR0B3s hear it; a hacked F1R3W4LL drops its links and
        EXPOSES the linked allies; a hacked PR0B3 never uploads; BACKTRACE -> alarm pull."""
        if name == "player_attack":
            if data.get("weapon") == "PISTOL" and self.run is not None:
                p = self.run.player.pos
                for m in self.minions:
                    if m.kind == "pr0b3" and m.team == "enemy" and m.alive and \
                       (m.pos - p).length_squared() <= m.info["hear_px"] ** 2:
                        m.hear(p)
        elif name == "hack_finished" and data.get("provider") == "minion":
            m = data.get("target")
            res = data.get("result") or {}
            outcome = res.get("outcome")
            if not isinstance(m, Minion):
                return
            if outcome == "success":
                if m.kind == "f1r3w4ll":
                    exposed = self._drop_links(m, expose=True)
                    m.links_down = True
                    if exposed and self.run is not None:
                        self.run.float_text((m.pos.x, m.pos.y - 22), "L1NK5 D0WN", gdev.CYAN, 1.0)
                if m.kind == "pr0b3":
                    m.probe_mode = "orbit"          # the upload is cancelled
                    m.scan_t = 0.0
            elif outcome == "backtrace":
                self._alarm(m.pos)
        elif name in ("player_died", "deep_core_entered"):
            self.trail = []

    def _alarm(self, pos):
        """BACKTRACE alarm (doc 5.2): minions within 600px stop going home and come to you."""
        p = V(pos)
        for m in self.minions:
            if m.alive and m.team == "enemy" and (m.pos - p).length_squared() <= ALARM_PX * ALARM_PX:
                m.homing = False
                if m.kind == "pr0b3" and m.probe_mode != "upload":
                    m.probe_mode = "investigate"
                    m.investigate_pos = V(p)

    # ------------------------------------------------------------ update

    def update(self, dt_world, player, run):
        self.run = run
        self._dt = dt_world
        self.hacking = getattr(run, "hack", None) is not None
        self._rng()
        prof = getattr(run, "profiler", None)
        patches = getattr(prof, "patches", None) if prof is not None else None
        self.fx = patches.effects() if patches is not None else {}

        splits = []
        for m in self.minions:
            if m.state == DEAD and not m.reported:
                m.reported = True
                self._drop_links(m)
                if m.linked_by is not None:
                    self._unlink(m)
                if m.dead_cause == "weapon":
                    run.on_minion_killed(m)     # Bits, kill stats, 20% health pip
                    if m.kind == "w0rm" and m.team == "enemy":
                        splits.append(m)
                if hasattr(run, "on_minion_died"):
                    run.on_minion_died(m)       # every death, any cause
        self.minions = [m for m in self.minions if m.state != DEAD or m.dissolve_t > 0]
        for w in splits:
            self._split(w)
        if dt_world <= 0:
            return
        self.time += dt_world

        self._update_orders(dt_world)
        self._update_leash(player)
        for m in self.minions:
            m.update(dt_world, player, self)
        self._update_links(dt_world)
        self._update_trail(dt_world, player)
        for p in self.enemy_projectiles:
            p.update(dt_world, self.world)
            if not p.alive:
                self._near_miss_end(p, player)
        self.enemy_projectiles = [p for p in self.enemy_projectiles if p.alive]
        for e in self.effects:
            e[3] -= dt_world
        self.effects = [e for e in self.effects if e[3] > 0]

    def _update_orders(self, dt):
        # pending, telegraphed spawns always land (any rack, or a point in the arena)
        for o in self.orders:
            o[2] -= dt
            if o[2] <= 0:
                if o[0] is None:
                    self._spawn_point(o[5], o[1], o[4])
                else:
                    self._spawn_from(o[0], o[1], o[3], o[4] if len(o) > 4 else None)
        self.orders = [o for o in self.orders if o[2] > 0]
        for rk in self.world.racks:
            for b in rk.bars:
                t = [o[2] for o in self.orders if o[0] is b and o[2] <= TELEGRAPH_S]
                b.flicker = max(t) if t else 0.0

    def _port_count(self, rack, bar):
        n = len([m for m in self.minions if m.alive and m.rack_id == rack.id and m.port == bar.index])
        return n + len([o for o in self.orders if o[0] is bar])

    def _apply_tags(self, m, tags):
        for k, v in (tags or {}).items():
            setattr(m, k, v)

    def _emit_spawned(self, m):
        if self.run is not None and hasattr(self.run, "emit"):
            self.run.emit("minion_spawned", minion=m)

    def _spawn_from(self, bar, kind, rack, tags=None):
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
        pos = V(ex, ey) + V(-d.y, d.x) * self._rng().uniform(-8, 8)
        for _ in range(8):                  # never start inside a wall
            if not self.world.point_solid(pos.x, pos.y):
                break
            pos += d * 8
        m = self.spawn(kind, pos, rack.id)
        self._apply_tags(m, tags)
        m.port = bar.index
        m.home = V(pos)
        m.trail_from = V(pos)
        m.state = SPAWNING
        m.spawn_t = m.spawn_total = SPAWN_SLIDE_S
        m.spawn_dir = d
        if m.kind == "f1r3w4ll":
            m.shield_dir = dir_token(d.x, d.y)
            m._face_shield()
        self._emit_spawned(m)
        return m

    def _spawn_point(self, pos, kind, tags=None):
        p = V(pos)
        for r in range(0, 64, 8):           # nearest free floor around the spot
            found = False
            for a in range(0, 360, 45):
                q = p + V(r, 0).rotate(a)
                if not self.world.point_solid(q.x, q.y):
                    p, found = q, True
                    break
            if found:
                break
        m = self.spawn(kind, p, (tags or {}).get("rack_id"))
        self._apply_tags(m, tags)
        m.home = V(p)
        m.trail_from = V(p)
        m.state = SPAWNING
        m.spawn_t = m.spawn_total = SPAWN_SLIDE_S
        m.spawn_dir = V(0, 0)
        self._emit_spawned(m)
        return m

    def _split(self, w):
        """A W0RM killed by a weapon splits into 2 B1Ts (doc 6); a hack prevents it."""
        for i in range(w.info.get("split", 2)):
            off = V(7, 0).rotate(180 * i + w.uid * 37)
            pos = w.pos + off
            if self.world.point_solid(pos.x, pos.y):
                pos = V(w.pos)
            b = self.spawn("b1t", pos, w.rack_id)
            b.boss_id, b.wave_id, b.port = w.boss_id, w.wave_id, w.port
            b.home = V(w.home)
            b.state = SPAWNING
            b.spawn_t = b.spawn_total = SPLIT_POP_S
            b.spawn_dir = off.normalize()
            self._emit_spawned(b)
        if self.run is not None:
            self.run.float_text((w.pos.x, w.pos.y - 12), "5PL1T", WORM_PLUM, 0.8)

    def _update_leash(self, player):
        for m in self.minions:
            if m.team != "enemy" or m.state not in (HUNTING, HARDENED):
                continue
            if m.kind == "pr0b3" and m.probe_mode == "upload":
                continue
            rack = self.world.rack(m.rack_id) if m.rack_id else None
            if rack is None:
                continue
            out = not rack.in_zone(player.pos)
            far = (m.pos - player.pos).length_squared() > LEASH_PX * LEASH_PX
            if out and far and not m.homing:
                m.homing = True
                m.home_t = 10.0
            elif m.homing and not out:
                m.homing = False

    # ---- F1R3W4LL links

    def _unlink(self, a):
        w = a.linked_by
        a.linked_by = None
        if w is not None and a in w.links:
            w.links.remove(a)

    def _drop_links(self, w, expose=False):
        """Drop every link of warden w; with expose=True EXPOSE the allies. -> [exposed]"""
        out = []
        for a in list(w.links):
            a.linked_by = None
            if expose and a.alive and a.team == "enemy" and not a.jacked and a.state != SPAWNING:
                a.expose()
                out.append(a)
                if self.run is not None:
                    self.run.float_text((a.pos.x, a.pos.y - 14), "EXPOSED", gdev.CYAN, 0.7)
        w.links = []
        return out

    def _update_links(self, dt):
        wardens = [m for m in self.minions if m.kind == "f1r3w4ll"]
        if not wardens:
            return
        for w in wardens:
            if not w.links:
                continue
            if not w.alive or w.team != "enemy" or w.links_down:
                self._drop_links(w)
                continue
            keep = w.info["link_keep_px"] ** 2
            for a in list(w.links):
                if not a.alive or a.team != "enemy" or (a.pos - w.pos).length_squared() > keep:
                    self._unlink(a)
        # re-link twice a second
        self._link_t -= dt
        if self._link_t <= 0:
            self._link_t = 0.5
            for w in wardens:
                if not w.alive or w.team != "enemy" or w.links_down or w.state == SPAWNING:
                    continue
                room = w.info["links"] - len(w.links)
                if room <= 0:
                    continue
                reach = w.info["link_px"] ** 2
                cands = []
                for a in self.minions:
                    if a is w or a.linked_by is not None or not a.alive or a.team != "enemy" or \
                       a.state == SPAWNING or a in w.links or a.links:
                        continue
                    d = (a.pos - w.pos).length_squared()
                    if d <= reach:
                        cands.append((d, a))
                cands.sort(key=lambda c: c[0])
                for _d, a in cands[:room]:
                    a.linked_by = w
                    a.link_regen_t = 0.0
                    w.links.append(a)
        # linked allies regenerate 1 firewall point every regen_s
        for w in wardens:
            for a in w.links:
                a.link_regen_t += dt
                if a.link_regen_t >= w.info["regen_s"]:
                    a.link_regen_t = 0.0
                    if a.state in (HUNTING, HARDENED) and a.firewall < a.firewall_max and not a.jacked:
                        a.firewall += 1
                        a.regen_flash = 0.2

    # ---- W0RM trail

    def _update_trail(self, dt, player):
        if not self.trail:
            return
        for t in self.trail:
            t[2] -= dt
        self.trail = [t for t in self.trail if t[2] > 0]
        if player.dead or getattr(player, "dash_t", 0) > 0 or getattr(player, "rooted", False):
            return
        if getattr(player, "knock_t", 0) > SLIME_KNOCK_S or player.vel.length_squared() < 1:
            return                                  # a real knockback is playing: leave it
        r2 = KINDS["w0rm"]["trail_px"] ** 2
        px, py = player.pos.x, player.pos.y + 3          # feet
        for x, y, _t in self.trail:
            if (x - px) * (x - px) + (y - py) * (y - py) <= r2:
                # slow through the knockback channel (the dome uses it too): next frame's
                # step loses `slow` of the player's speed. Lasts one frame, re-applied per frame.
                player.knock = -V(player.vel) * KINDS["w0rm"]["slow"]
                player.knock_t = SLIME_KNOCK_S
                break

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
                    p.near_done = True
                elif getattr(player, "parrying", False):
                    # katana parry reflects packets: they now strip enemy firewalls
                    p.team = "player"
                    p.vel = -p.vel * 1.5
                    p.life = 2.0
                    p.hit_ids = set()
                    p.reflected = True
                    p.near_done = True
            if p.alive and p.team == "enemy":
                self._near_miss_track(p, player)
        self.enemy_projectiles = [p for p in self.enemy_projectiles if p.alive]

    def _near_miss_track(self, p, player):
        if getattr(p, "near_done", True) or player.dead:
            return
        d2 = (p.pos - player.pos).length_squared()
        if d2 <= NEAR_MISS_PX * NEAR_MISS_PX:
            p.near = True
        elif p.near:
            self._emit_near_miss(p, player)          # it passed by and is moving away

    def _near_miss_end(self, p, player):
        if getattr(p, "near", False) and not getattr(p, "near_done", True) and p.team == "enemy":
            self._emit_near_miss(p, player)

    def _emit_near_miss(self, p, player):
        p.near_done = True
        if self.run is not None and hasattr(self.run, "emit") and not player.dead:
            self.run.emit("near_miss", pos=(p.pos.x, p.pos.y), dir=dir_token(player.vel.x, player.vel.y))

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
        ppos = self.run.player.pos if self.run is not None else None
        for _d, m in hits:
            if m.kind == "f1r3w4ll" and m.shield_blocks(p, ppos):
                p.hit_ids.add(m.uid)
                m.shield_flash = 0.12
                if self.run is not None and p.team == "player" and m.block_text_t <= 0 and \
                   not isinstance(p, Packet):
                    m.block_text_t = 0.8
                    self.run.float_text((m.pos.x, m.pos.y - 16), "BLOCKED", FIREWALL_ORANGE, 0.6)
                if getattr(p, "kind", "") == "slash":
                    continue                # the arc still reaches whoever stands beside it
                p.alive = False
                break
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
        self._draw_trail(surface, camera, view)
        self._draw_telegraphs(surface, camera, view)
        self._draw_links(surface, camera, view)
        run = self.run
        p = run.player.pos if run is not None else None
        target = getattr(run, "jack_target", None) if run is not None else None
        near = (JACK_RING_PX * JACK_RING_NEAR) ** 2
        for m in sorted(self.minions, key=lambda m: m.pos.y):
            if view.collidepoint(m.pos):
                m.ring_full = p is None or m is target or (m.pos - p).length_squared() <= near
                m.draw(surface, camera)
        self._draw_probe_beams(surface, camera, view)
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
        self._draw_links(surface, camera, view)
        for m in sorted(self.minions, key=lambda m: m.pos.y):
            if m is exclude or not m.alive or m.team != "enemy" or not view.collidepoint(m.pos):
                continue
            m.draw(surface, camera)
            if near is not None and m.state in (HUNTING, HARDENED) and m.kind != "pr0b3" and \
               (m.pos - V(near)).length_squared() <= close:
                sx, sy = camera.world_to_screen(m.pos)
                y = sy + m.rect.h // 2 - 22 + m.info["top"] - 8     # just above the firewall pips
                pygame.draw.polygon(surface, gdev.RED, [(sx - 3, y - 4), (sx + 3, y - 4), (sx, y)])
        self._draw_packets(surface, camera, view)

    def _draw_trail(self, surface, camera, view):
        if not self.trail:
            return
        total = KINDS["w0rm"]["trail_s"]
        for x, y, t in self.trail:
            if not view.collidepoint(x, y):
                continue
            sx, sy = camera.world_to_screen((x, y))
            k = t / total
            col = TRAIL_HOT if k > 0.85 else (TRAIL_COL if k > 0.3 else (52, 16, 46))
            surface.fill(col, (sx - 2, sy - 1, 4, 2))
            if (int(x) + int(y)) % 3 == 0:
                surface.fill(col, (sx - 1 + (int(x) % 3) - 1, sy + 1, 1, 1))

    def _draw_links(self, surface, camera, view):
        """F1R3W4LL shield links: flowing orange lines to each linked ally."""
        for w in self.minions:
            if w.kind != "f1r3w4ll" or not w.links or not w.alive:
                continue
            if not view.collidepoint(w.pos):
                continue
            wx, wy = camera.world_to_screen(w.pos)
            wy -= 4
            for a in w.links:
                ax, ay = camera.world_to_screen(a.pos)
                ay -= 2 + a.info.get("hover", 0)
                col = (150, 70, 20) if w.state == EXPOSED else FIREWALL_ORANGE
                pygame.draw.line(surface, (70, 30, 8), (wx, wy), (ax, ay), 1)
                # a bright packet running along the link
                n = max(1.0, math.hypot(ax - wx, ay - wy))
                k = (self.time * 60.0 / n + (a.uid % 7) / 7.0) % 1.0
                px, py = wx + (ax - wx) * k, wy + (ay - wy) * k
                surface.fill(col, (int(px), int(py), 2, 2))
                surface.fill(col, (ax - 1, ay - 1, 3, 3))

    def _draw_probe_beams(self, surface, camera, view):
        """A faint dotted scan line from a watching PR0B3 to the player."""
        if self.run is None or self.run.player.dead:
            return
        ppos = self.run.player.pos
        for m in self.minions:
            if m.kind != "pr0b3" or not m.los or m.state not in (HUNTING, HARDENED) or \
               m.probe_mode == "upload" or not view.collidepoint(m.pos):
                continue
            sx, sy = camera.world_to_screen(m.pos)
            sy -= m.info.get("hover", 0) + 1
            tx, ty = camera.world_to_screen(ppos)
            n = max(1, int(math.hypot(tx - sx, ty - sy) / 5))
            phase = int(self.time * 12) % 2
            col = (150, 40, 50) if m.scan_t < m.info["scan_s"] * 0.66 else PROBE_RED
            for i in range(phase, n, 2):
                surface.fill(col, (int(sx + (tx - sx) * i / n), int(sy + (ty - sy) * i / n), 1, 1))

    def _draw_telegraphs(self, surface, camera, view):
        # World draws the bar strobe and the exit marker (from bar.flicker); this adds
        # a small label so the player can read which port is about to open, and the glyph
        # circle of a point spawn (spawn_at).
        seen = set()
        on = int(self.time * 16) % 2 == 0
        col = gdev.MAGENTA if on else (120, 20, 90)
        for o in self.orders:
            bar, kind, t, rack = o[:4]
            if bar is None:
                if t > 1.5 or not view.collidepoint(o[5]):
                    continue
                sx, sy = camera.world_to_screen(o[5])
                r = 4 + int(8 * min(1.0, t))
                pygame.draw.circle(surface, col, (sx, sy), r, 1)
                pygame.draw.line(surface, col, (sx - r, sy), (sx + r, sy))
                pygame.draw.line(surface, col, (sx, sy - r), (sx, sy + r))
                continue
            if t > TELEGRAPH_S or id(bar) in seen:
                continue
            seen.add(id(bar))
            if not view.collidepoint(bar.spawn_exit):
                continue
            sx, sy = camera.world_to_screen(bar.spawn_exit)
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
        """Off-screen but active ranged minions and PR0B3s get an arrow at the screen edge in
        their archetype colour (doc 2); an uploading PR0B3 blinks."""
        import hud                                  # lazy: hud -> hack -> ... at import time
        vr = camera.view_rect
        z = float(getattr(camera, "zoom", 1))
        safe = hud.SAFE_RECT                        # keep arrows off the HUD panels
        sx0, sy0 = safe.left / z, safe.top / z
        hw, hh = safe.w / z / 2.0, safe.h / z / 2.0
        scx, scy = sx0 + hw, sy0 + hh
        for m in self.minions:
            if m.kind not in ("p1ng", "pr0b3") or m.team != "enemy" or m.state not in (HUNTING, HARDENED):
                continue
            if vr.collidepoint(m.pos):
                continue
            uploading = m.kind == "pr0b3" and m.probe_mode == "upload"
            cx, cy = vr.left + scx, vr.top + scy
            dx, dy = m.pos.x - cx, m.pos.y - cy
            d = math.hypot(dx, dy)
            if d < 1 or (d > 900 and not uploading):
                continue
            ux, uy = dx / d, dy / d
            k = min((hw - 6) / max(abs(ux), 1e-6), (hh - 6) / max(abs(uy), 1e-6))
            ax, ay = scx + ux * k, scy + uy * k
            tip = (ax + ux * 6, ay + uy * 6)
            col = m.info["color"]
            if uploading and int(self.time * 6) % 2:
                col = gdev.WHITE
            pygame.draw.polygon(surface, col, [tip, (ax - uy * 4, ay + ux * 4), (ax + uy * 4, ay - ux * 4)])
            if uploading:
                pl = _plate("UPL", PROBE_RED, (40, 6, 12))
                surface.blit(pl, pl.get_rect(center=(int(ax - ux * 10), int(ay - uy * 10))))
