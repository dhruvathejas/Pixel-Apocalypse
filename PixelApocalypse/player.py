"""Player: movement, dash, weapons, projectiles, HP.

game.py, hud.py and minions.py use the names documented on Player and Projectile.
Positions are world pixels. `pos` is the centre of the collision rect, which is
also the centre of the 32x32 sprite canvas (the body is ~8x11px around it).

Weapons (doc 10, simplified):
  BOW     hold LMB to charge 0.2-1.0s, release to shoot; a full charge pierces
  PISTOL  6-round magazine, 1s reload (R, or automatic when empty)
  KATANA  3-hit arc combo at short range; RMB parry (0.15s window)

Weapon upgrades (doc 10, bought with Bits at a Clean Node shop; Player.upgrades holds the ids):
  SPLIT_NOCK  a full-charge bow shot looses 3 arrows      ARC_SHOT   arrows lob over F1R3W4LL shields
  MAG_PLUS    pistol magazine 6 -> 8                       RICOCHET   bullets bounce once off walls
  LONG_EDGE   wider, longer katana arc                     RIPOSTE    a parried hit answers with a slash
"""
import math

import pygame

import gdev

SPEED = 180.0
MAX_HP = 10
BODY_SIZE = (8, 10)             # collision box, centred on pos

DASH_DIST = 96.0
DASH_TIME = 0.15
DASH_COOLDOWN = 0.6
HURT_IFRAMES = 1.4
RESPAWN_IFRAMES = 3.0
KNOCKBACK = 200.0               # px/s push away from whatever hit us
KNOCK_TIME = 0.12

PISTOL_MAG = 10
PISTOL_RELOAD = 0.6
PISTOL_INTERVAL = 0.12          # fastest click rate
PISTOL_AUTO = 0.2               # repeat rate while LMB is held
BULLET_SPEED = 520.0
BULLET_LIFE = 1.2               # ~620px, just past the screen edge

BOW_MIN = 0.2                   # hold at least this long or the arrow is not loosed
BOW_FULL = 0.6
BOW_COOLDOWN = 0.3
BOW_DRAW_SLOW = 0.75            # walking speed while drawing the bow
ARROW_SPEED = (380.0, 720.0)    # min / full charge
ARROW_LIFE = 1.0
CORE_ARROW_MULT = 2             # an arrow hits a Daemon's exposed core twice as hard (bow DPS ~ pistol)
SPLIT_NOCK_DEG = 10.0           # side arrows of a Split Nock shot
MAG_PLUS = 2
RICOCHET_BOUNCES = 1
LONG_EDGE_BOX = 12              # extra slash box size / reach with Long Edge
RIPOSTE_DAMAGE = 2

KATANA_DAMAGE = (2, 2, 3)       # combo hits 1, 2, 3
KATANA_RANGE = 16               # slash box centre, px in front of the body
KATANA_BOX = 30
KATANA_INTERVAL = 0.2
KATANA_RESET = 0.45             # combo drops if the next press is later than this
KATANA_FINISH = 0.4             # pause after the third hit
KATANA_BUFFER = 0.15
PARRY_WINDOW = 0.3
PARRY_COOLDOWN = 0.5
HITSTOP = 0.06                  # Game may freeze the world while player.hitstop > 0

WEAPONS = ["BOW", "PISTOL", "KATANA"]
FX_LIFE = {"spark": 0.12, "ping": 0.3, "swap": 0.6}

# The hand the weapon sprites hang from, in 32x32 canvas pixels.
PIVOT = {"right": (19.5, 17.0), "left": (12.5, 17.0)}

# Projectile sprites: (file, crop rect of the visible pixels) for each side.
PROJ_ART = {
    "bullet": {"right": ("weapon_assets/bullet_right.png", (21, 15, 3, 3)),
               "left": ("weapon_assets/bullet_left.png", (8, 15, 3, 3))},
    "arrow": {"right": ("weapon_assets/arrow_right.png", (18, 15, 5, 3)),
              "left": ("weapon_assets/arrow_left.png", (9, 15, 5, 3))},
    "packet": {"right": ("minion_assets/p1ng_packet_right.png", None),
               "left": ("minion_assets/p1ng_packet_left.png", None)},
}


# ---------------------------------------------------------------- sprite helpers

_rot_cache = {}
_crop_cache = {}
_tint_cache = {}


def _bucket(angle):
    """Round an angle (degrees) to the nearest 10-degree bucket."""
    return int(round(angle / 10.0)) * 10


def _side_angle(dx, dy):
    """Which art side to use for a direction, and the pygame rotation for it.
    The *_left art is used when pointing left, so nothing is drawn upside down."""
    ang = -math.degrees(math.atan2(dy, dx))      # pygame rotates counter-clockwise
    if dx < 0:
        ang = (ang - 180.0 + 540.0) % 360.0 - 180.0
        return "left", ang
    return "right", ang


def _weapon_angle(side, angle_rad):
    """pygame rotation for a held weapon: the *_right art for the right hand,
    *_left for the left, each limited to +-90 degrees so it is never upside down."""
    ang = -math.degrees(angle_rad)
    if side == "left":
        ang = (ang - 180.0 + 540.0) % 360.0 - 180.0
    return max(-90.0, min(90.0, ang))


def _cropped(rel, crop):
    key = (rel, crop)
    surf = _crop_cache.get(key)
    if surf is None:
        img = gdev.image(rel)
        if img is None:
            return None
        if crop is None:
            crop = img.get_bounding_rect()
        surf = img.subsurface(pygame.Rect(crop)).copy()
        _crop_cache[key] = surf
    return surf


def _rotated(rel, crop, angle):
    """Cropped sprite rotated to a 10-degree bucket (cached)."""
    b = _bucket(angle)
    key = (rel, crop, b)
    surf = _rot_cache.get(key)
    if surf is None:
        base = _cropped(rel, crop)
        if base is None:
            return None
        surf = pygame.transform.rotate(base, b) if b else base
        _rot_cache[key] = surf
    return surf


def _rotated_canvas(rel, angle, tint=None):
    """Whole 32x32 weapon canvas rotated to a 10-degree bucket (cached)."""
    b = _bucket(angle)
    key = (rel, b, tint)
    surf = _rot_cache.get(key)
    if surf is None:
        img = _tinted(rel, tint) if tint else gdev.image(rel)
        if img is None:
            return None
        surf = pygame.transform.rotate(img, b) if b else img
        _rot_cache[key] = surf
    return surf


def _tinted(rel, color, alpha=255):
    """Silhouette of a sprite in one colour (hurt flash, dash ghosts, jammed gun)."""
    key = (rel, color, alpha)
    surf = _tint_cache.get(key)
    if surf is None:
        img = gdev.image(rel)
        if img is None:
            return None
        surf = img.copy()
        surf.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
        surf.fill((color[0], color[1], color[2], 0), special_flags=pygame.BLEND_RGBA_ADD)
        if alpha < 255:
            surf.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
        _tint_cache[key] = surf
    return surf


def blit_pivot(surface, img, angle, pivot_canvas, pivot_screen):
    """Rotate a 32x32 canvas image around a canvas point and draw it so that
    point lands on pivot_screen."""
    off = pygame.math.Vector2(16 - pivot_canvas[0], 16 - pivot_canvas[1]).rotate(-_bucket(angle))
    r = img.get_rect(center=(int(round(pivot_screen[0] + off.x)), int(round(pivot_screen[1] + off.y))))
    surface.blit(img, r)


# ---------------------------------------------------------------- projectiles

class Projectile(object):
    """A player (or minion) shot. minions.py reads and kills these.

    pos Vector2, vel Vector2 (px/s), rect Rect (hit box, kept on pos),
    damage int, team "player"|"enemy", kind "bullet"|"arrow"|"slash"|"packet",
    pierce bool, hit_ids set (minion uids already hit, for piercing shots),
    alive bool (set False to remove), life float seconds left.
    world_hit   what stopped it: None or the tuple from World.projectile_hit
    draw(surface, camera)   sprite rotated toward its velocity (any kind)
    """
    def __init__(self, pos, vel, damage=1, kind="bullet", team="player", size=4, life=1.2, pierce=False):
        self.pos = pygame.math.Vector2(pos)
        self.vel = pygame.math.Vector2(vel)
        self.damage = damage
        self.kind = kind
        self.team = team
        self.pierce = pierce
        self.hit_ids = set()
        self.alive = True
        self.life = life
        self.max_life = life
        self.world_hit = None
        self.core_mult = 1          # x damage on a Daemon's exposed core (arrows: CORE_ARROW_MULT)
        self.bounces = 0            # Ricochet: wall bounces left
        self.rect = pygame.Rect(0, 0, size, size)
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))
        self.angle = 0.0            # slash only: aim angle (radians) and swing direction
        self.swing = 1
        self.combo = 0
        self.hitstop_done = False

    def update(self, dt, world):
        self.life -= dt
        if self.kind == "slash":
            # melee arcs do not travel and are not stopped by walls
            if self.life <= 0:
                self.alive = False
            return
        # move in small steps so fast shots cannot skip through thin solids
        move = self.vel * dt
        steps = max(1, int(move.length() / 6.0) + 1)
        step = move / steps
        for _ in range(steps):
            self.pos += step
            self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))
            hit = world.projectile_hit(self.rect)
            if hit is not None and hit[0] == "wall" and self.bounces > 0:
                self.bounces -= 1
                self._bounce(world, step)
                break
            if hit is not None:
                self.world_hit = hit
                self.alive = False
                if hit[0] == "bar" and hit[2] is not None and self.team == "player":
                    hit[2].flash = 0.12
                    hit[2].hits += self.damage * self.core_mult
                break
        if self.life <= 0:
            self.alive = False

    def _bounce(self, world, step):
        """Ricochet: step back out of the wall and mirror the velocity on the axis that hit."""
        self.pos -= step
        probe = self.rect.copy()
        probe.center = (int(round(self.pos.x + step.x)), int(round(self.pos.y)))
        if world.projectile_hit(probe) is not None:
            self.vel.x = -self.vel.x
        probe.center = (int(round(self.pos.x)), int(round(self.pos.y + step.y)))
        if world.projectile_hit(probe) is not None:
            self.vel.y = -self.vel.y
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))
        self.hit_ids = set()

    def draw(self, surface, camera):
        if self.kind == "slash":
            return
        sx, sy = camera.world_to_screen(self.pos)
        art = PROJ_ART.get(self.kind)
        img = None
        if art is not None and self.vel.length_squared() > 0:
            side, ang = _side_angle(self.vel.x, self.vel.y)
            rel, crop = art[side]
            img = _rotated(rel, crop, ang)
        if img is None:
            col = gdev.MAGENTA if self.team == "enemy" else gdev.AMBER
            pygame.draw.circle(surface, col, (sx, sy), 2)
            return
        surface.blit(img, img.get_rect(center=(sx, sy)))


# ---------------------------------------------------------------- player

class Player(object):
    """Public surface:
      pos Vector2, vel Vector2 (px/s), rect Rect, hp, max_hp, dead, just_hit, invuln,
      facing "left"|"right"|"back", weapon_index, weapon_slots [name|None x3], projectiles [Projectile]
      update(dt, inp, world)        dt is WORLD dt; inp is gdev.InputState
      draw(surface, camera, mouse_world)
      take_damage(n, from_pos) -> bool   (False during i-frames/dash/parry/dead)
      heal(n), respawn(pos), jam(seconds)
      weapon_name() -> "PISTOL"|"BOW"|"KATANA"
      weapon_hud() -> {"name", "ammo", "mag", "reload", "charge", "jammed"}
      upgrades set of weapon upgrade ids (doc 10 shop), add_upgrade(uid)
    Extras other modules may read:
      dashing (bool), parrying (bool), parried_from (pos of the last parried hit or None),
      hitstop (seconds; set when a katana hit lands -- Game may freeze the world for it)
    """

    def __init__(self, pos):
        self.pos = pygame.math.Vector2(pos)
        self.vel = pygame.math.Vector2(0, 0)
        self.rect = pygame.Rect(0, 0, BODY_SIZE[0], BODY_SIZE[1])
        self.max_hp = MAX_HP
        self.weapon_slots = list(WEAPONS)
        self.weapon_index = 1                   # start with the pistol
        self.projectiles = []
        self.fx = []                            # [kind, x, y, t, extra] short visual effects
        self.upgrades = set()                   # weapon upgrades bought at a Clean Node (kept on death)
        self.reset_state()
        self._sync_rect()

    def reset_state(self):
        self.hp = self.max_hp
        self.dead = False
        self.death_t = 0.0
        self.just_hit = False       # set by take_damage; Game reads and clears it every frame
        self.invuln = 0.0           # any i-frames (hurt, dash, respawn)
        self.blink = 0.0            # hurt/respawn blink (drawn), separate from dash i-frames
        self.hurt_flash = 0.0
        self.knock = pygame.math.Vector2(0, 0)
        self.knock_t = 0.0
        self.facing = "right"
        self.side = "right"         # which side of the body the weapon is on (mouse side)
        self.rooted = False
        self.move_dir = pygame.math.Vector2(0, 0)
        self.dash_t = 0.0
        self.dash_cd = 0.0
        self.dash_dir = pygame.math.Vector2(1, 0)
        self.trail = []             # dash after-images [(x, y, sprite, age)]
        self.mag = self.mag_size()
        self.ammo = self.mag
        self.reload_t = 0.0
        self.fire_cd = 0.0
        self.muzzle_t = 0.0
        self.jam_t = 0.0
        self.charge_t = None        # bow: seconds held, None when not drawing
        self.combo = 0              # katana: hits done in the current combo
        self.combo_t = 0.0          # time since the last slash
        self.attack_buffer = 0.0
        self.slash_anim = 0.0
        self.slash_angle = 0.0
        self.slash_swing = 1
        self.parry_t = 0.0
        self.parry_cd = 0.0
        self.parried_from = None
        self.parry_flash = 0.0
        self.hitstop = 0.0
        self.aim_angle = 0.0        # radians, from the hand toward the mouse

    def _sync_rect(self):
        self.rect.center = (int(round(self.pos.x)), int(round(self.pos.y)))

    # ------------------------------------------------------------ queries

    def mag_size(self):
        return PISTOL_MAG + (MAG_PLUS if "MAG_PLUS" in self.upgrades else 0)

    def add_upgrade(self, uid):
        self.upgrades.add(uid)
        if uid == "MAG_PLUS":
            self.mag = self.mag_size()
            self.ammo = self.mag

    def weapon_name(self):
        return self.weapon_slots[self.weapon_index]

    @property
    def dashing(self):
        return self.dash_t > 0

    @property
    def parrying(self):
        return self.parry_t > 0 and not self.dead

    def weapon_hud(self):
        name = self.weapon_name()
        info = {"name": name, "ammo": 0, "mag": None, "reload": None, "charge": None,
                "jammed": self.jam_t > 0}
        if name == "PISTOL":
            info["ammo"] = self.ammo
            info["mag"] = self.mag
            if self.reload_t > 0:
                info["reload"] = 1.0 - self.reload_t / PISTOL_RELOAD
        elif name == "BOW" and self.charge_t is not None:
            info["charge"] = min(1.0, self.charge_t / BOW_FULL)
        return info

    def hand_pos(self):
        """World position of the weapon hand."""
        px, py = PIVOT[self.side]
        return pygame.math.Vector2(self.pos.x + px - 16, self.pos.y + py - 16)

    # ------------------------------------------------------------ update

    def update(self, dt, inp, world):
        self._tick_timers(dt)
        self._update_fx(dt)
        if self.dead:
            self.death_t += dt
            self._update_projectiles(dt, world)
            return

        self.rooted = inp.rooted
        mx, my = (0, 0) if inp.rooted else inp.move
        d = pygame.math.Vector2(mx, my)
        if d.length_squared() > 0:
            d = d.normalize()
        self.move_dir = d

        # aim: which side the weapon is on, and the angle from the hand
        if not inp.rooted:
            self.side = "left" if inp.aim.x < self.pos.x else "right"
        hand = self.hand_pos()
        aim = pygame.math.Vector2(inp.aim) - hand
        if aim.length_squared() > 1:
            self.aim_angle = math.atan2(aim.y, aim.x)

        # facing (doc 2): straight up shows the back; otherwise the mouse side
        if my < 0 and mx == 0:
            self.facing = "back"
        else:
            self.facing = self.side

        if not inp.rooted:
            self._handle_weapon_switch(inp)
            if inp.dash_pressed and self.dash_cd <= 0 and self.dash_t <= 0:
                self._start_dash(d)
        else:
            # jacked in: drop anything in progress and stand still
            self.charge_t = None
            self.attack_buffer = 0.0
            self.dash_t = 0.0
            self.knock_t = 0.0

        self._move(dt, d, world)
        if not inp.rooted:
            self._handle_attack(dt, inp)
        self._update_projectiles(dt, world)

    def _tick_timers(self, dt):
        self.invuln = max(0.0, self.invuln - dt)
        self.blink = max(0.0, self.blink - dt)
        self.hurt_flash = max(0.0, self.hurt_flash - dt)
        self.jam_t = max(0.0, self.jam_t - dt)
        self.dash_cd = max(0.0, self.dash_cd - dt)
        self.fire_cd = max(0.0, self.fire_cd - dt)
        self.muzzle_t = max(0.0, self.muzzle_t - dt)
        self.parry_t = max(0.0, self.parry_t - dt)
        self.parry_cd = max(0.0, self.parry_cd - dt)
        self.parry_flash = max(0.0, self.parry_flash - dt)
        self.slash_anim = max(0.0, self.slash_anim - dt)
        self.attack_buffer = max(0.0, self.attack_buffer - dt)
        self.combo_t += dt
        self.knock_t = max(0.0, self.knock_t - dt)
        if self.reload_t > 0:
            self.reload_t -= dt
            if self.reload_t <= 0:
                self.reload_t = 0.0
                self.ammo = self.mag
        for t in self.trail:
            t[3] += dt
        self.trail = [t for t in self.trail if t[3] < 0.18]

    def _start_dash(self, d):
        if d.length_squared() == 0:
            d = pygame.math.Vector2(math.cos(self.aim_angle), math.sin(self.aim_angle))
        self.dash_dir = pygame.math.Vector2(d)
        self.dash_t = DASH_TIME
        self.dash_cd = DASH_COOLDOWN
        self.invuln = max(self.invuln, DASH_TIME + 0.03)

    def _move(self, dt, d, world):
        if self.dash_t > 0:
            step = min(dt, self.dash_t)
            self.dash_t -= dt
            self.vel = self.dash_dir * (DASH_DIST / DASH_TIME)
            move = self.dash_dir * (DASH_DIST / DASH_TIME) * step
            if dt > 0 and (not self.trail or self.trail[-1][3] > 0.03):
                self.trail.append([self.pos.x, self.pos.y, self._body_sprite(), 0.0])
        else:
            speed = SPEED
            if self.charge_t is not None:
                speed *= BOW_DRAW_SLOW
            if hasattr(world, "on_trunk") and world.on_trunk(self.pos):
                speed *= world.TRUNK_SPEED
            self.vel = d * speed
            move = self.vel * dt
        if self.knock_t > 0:
            move += self.knock * dt
        if move.x or move.y:
            if hasattr(world, "move_pos"):
                x, y = world.move_pos(self.pos.x, self.pos.y, BODY_SIZE[0], BODY_SIZE[1], move.x, move.y)
            else:
                self._sync_rect()
                r = world.move_and_collide(self.rect, move.x, move.y)
                x, y = r.centerx, r.centery
            self.pos.update(x, y)
        self._sync_rect()

    def _handle_weapon_switch(self, inp):
        new = self.weapon_index
        if inp.weapon_select is not None and 0 <= inp.weapon_select < len(self.weapon_slots):
            if self.weapon_slots[inp.weapon_select]:
                new = inp.weapon_select
        elif inp.weapon_cycle:
            n = len(self.weapon_slots)
            i = self.weapon_index
            for _ in range(n):
                i = (i + inp.weapon_cycle) % n
                if self.weapon_slots[i]:
                    new = i
                    break
        if new != self.weapon_index:
            self.weapon_index = new
            self.charge_t = None
            self.combo = 0
            self.attack_buffer = 0.0
            self.fx.append(["swap", self.pos.x, self.pos.y - 18, 0.0, self.weapon_name()])

    def _aim_vec(self):
        return pygame.math.Vector2(math.cos(self.aim_angle), math.sin(self.aim_angle))

    def _handle_attack(self, dt, inp):
        name = self.weapon_name()
        jammed = self.jam_t > 0
        if name == "PISTOL":
            if inp.reload_pressed and self.ammo < self.mag and self.reload_t <= 0:
                self.reload_t = PISTOL_RELOAD
            want = inp.fire_pressed or (inp.fire_held and self.fire_cd <= 0)
            if want and not jammed and self.fire_cd <= 0:
                if self.reload_t > 0:
                    pass
                elif self.ammo <= 0:
                    self.reload_t = PISTOL_RELOAD
                else:
                    self._fire_pistol(inp.fire_pressed)
        elif name == "BOW":
            if inp.fire_pressed and not jammed and self.fire_cd <= 0:
                self.charge_t = 0.0
            if self.charge_t is not None:
                if inp.fire_released or (not inp.fire_held and not inp.fire_pressed):
                    # released (or the button state was lost): loose if drawn far enough
                    if inp.fire_released and self.charge_t >= BOW_MIN and not jammed:
                        self._fire_arrow()
                    self.charge_t = None
                else:
                    before = self.charge_t
                    self.charge_t += dt
                    if before < BOW_FULL <= self.charge_t:
                        self.fx.append(["ping", self.pos.x, self.pos.y, 0.0, None])
        elif name == "KATANA":
            if inp.fire_pressed:
                self.attack_buffer = KATANA_BUFFER
            if self.attack_buffer > 0 and self.fire_cd <= 0 and not jammed:
                self.attack_buffer = 0.0
                self._slash()
            if inp.alt_pressed and self.parry_cd <= 0:
                self.parry_t = PARRY_WINDOW
                self.parry_cd = PARRY_COOLDOWN

    def _fire_pistol(self, clicked):
        v = self._aim_vec()
        muzzle = self.hand_pos() + v * 5
        b = Projectile(muzzle, v * BULLET_SPEED, 1, "bullet", "player", 4, BULLET_LIFE)
        if "RICOCHET" in self.upgrades:
            b.bounces = RICOCHET_BOUNCES
        self.projectiles.append(b)
        self.ammo -= 1
        self.fire_cd = PISTOL_INTERVAL if clicked else PISTOL_AUTO
        self.muzzle_t = 0.05
        if self.ammo == 0:
            self.reload_t = PISTOL_RELOAD

    def _fire_arrow(self):
        k = min(1.0, (self.charge_t - BOW_MIN) / (BOW_FULL - BOW_MIN))
        full = self.charge_t >= BOW_FULL
        v = self._aim_vec()
        speed = ARROW_SPEED[0] + (ARROW_SPEED[1] - ARROW_SPEED[0]) * k
        spread = (-SPLIT_NOCK_DEG, 0.0, SPLIT_NOCK_DEG) if full and "SPLIT_NOCK" in self.upgrades else (0.0,)
        for deg in spread:
            u = v.rotate(deg)
            p = Projectile(self.hand_pos() + u * 4, u * speed, 2 if full else 1, "arrow", "player", 4,
                           ARROW_LIFE, pierce=full)
            p.full_charge = full and deg == 0.0      # only the centre arrow pins (doc 10)
            p.core_mult = CORE_ARROW_MULT
            p.arc = "ARC_SHOT" in self.upgrades      # lobs over F1R3W4LL shields
            self.projectiles.append(p)
        self.fire_cd = BOW_COOLDOWN

    def _slash(self, toward=None, damage=None):
        if self.combo_t > KATANA_RESET or self.combo >= 3:
            self.combo = 0
        dmg = KATANA_DAMAGE[self.combo] if damage is None else damage
        self.combo += 1
        self.combo_t = 0.0
        self.fire_cd = KATANA_FINISH if self.combo >= 3 else KATANA_INTERVAL
        v = self._aim_vec()
        if toward is not None:
            d = pygame.math.Vector2(toward) - self.pos
            if d.length_squared() > 1:
                v = d.normalize()
                self.aim_angle = math.atan2(v.y, v.x)
        size = KATANA_BOX + (6 if self.combo == 3 else 0)
        reach = KATANA_RANGE
        if "LONG_EDGE" in self.upgrades:
            size += LONG_EDGE_BOX
            reach += LONG_EDGE_BOX // 2
        centre = self.pos + v * reach
        p = Projectile(centre, (0, 0), dmg, "slash", "player", size, 0.08, pierce=True)
        p.angle = self.aim_angle
        p.swing = 1 if self.combo % 2 else -1
        p.combo = self.combo
        self.projectiles.append(p)
        self.slash_anim = 0.14
        self.slash_angle = self.aim_angle
        self.slash_swing = p.swing

    def _update_projectiles(self, dt, world):
        keep = []
        for p in self.projectiles:
            if p.alive:
                p.update(dt, world)
            if p.kind == "slash" and p.hit_ids and not p.hitstop_done:
                p.hitstop_done = True
                self.hitstop = HITSTOP
            if p.alive:
                keep.append(p)
            elif p.kind != "slash" and p.life > 0:
                # stopped early (wall, shell, port or a minion): spark there
                col = gdev.MAGENTA if p.world_hit and p.world_hit[0] == "bar" else gdev.AMBER
                self.fx.append(["spark", p.pos.x, p.pos.y, 0.0, col])
        self.projectiles = keep

    def _update_fx(self, dt):
        for f in self.fx:
            f[3] += dt
        self.fx = [f for f in self.fx if f[3] < FX_LIFE.get(f[0], 0.3)]

    # ------------------------------------------------------------ damage / life

    def take_damage(self, n, from_pos=None):
        if self.dead or self.invuln > 0 or self.dash_t > 0:
            return False
        if self.parrying:
            self.parried_from = tuple(from_pos) if from_pos is not None else None
            self.parry_flash = 0.15
            if "RIPOSTE" in self.upgrades and self.weapon_name() == "KATANA" and not self.rooted:
                self._slash(toward=from_pos, damage=RIPOSTE_DAMAGE)     # the counter-cut
            return False
        self.hp = max(0, self.hp - int(n))
        self.just_hit = True
        self.invuln = HURT_IFRAMES
        self.blink = HURT_IFRAMES
        self.hurt_flash = 0.1
        self.charge_t = None
        if from_pos is not None:
            away = self.pos - pygame.math.Vector2(from_pos)
            if away.length_squared() > 0:
                self.knock = away.normalize() * KNOCKBACK
                self.knock_t = KNOCK_TIME
        if self.hp <= 0:
            self.dead = True
            self.death_t = 0.0
            self.vel = pygame.math.Vector2(0, 0)
        return True

    def heal(self, n):
        if not self.dead:
            self.hp = min(self.max_hp, self.hp + int(n))

    def respawn(self, pos):
        self.pos = pygame.math.Vector2(pos)
        self.vel = pygame.math.Vector2(0, 0)
        self.projectiles = []
        self.fx = []
        self.reset_state()
        self.invuln = RESPAWN_IFRAMES
        self.blink = RESPAWN_IFRAMES
        self._sync_rect()

    def jam(self, seconds):
        self.jam_t = max(self.jam_t, seconds)
        self.charge_t = None

    # ------------------------------------------------------------ drawing

    def _body_sprite(self):
        """Which player sprite to show (doc 2 precedence)."""
        if self.facing == "back":
            return "player_assets/player_back.png"
        if self.rooted or self.dead:
            # hands on the jack cable, weapon away
            return "player_assets/player_%s_asset.png" % self.side
        return "player_assets/player_%s_asset_weapon.png" % self.side

    def draw(self, surface, camera, mouse_world):
        sx, sy = camera.world_to_screen(self.pos)
        top_left = (sx - 16, sy - 16)

        # dash after-images
        for x, y, rel, age in self.trail:
            ghost = _tinted(rel, gdev.CYAN, int(110 * (1.0 - age / 0.18)))
            if ghost is not None:
                gx, gy = camera.world_to_screen((x, y))
                surface.blit(ghost, (gx - 16, gy - 16))

        if self.dead:
            self._draw_death(surface, sx, sy)
            self._draw_projectiles(surface, camera)
            self._draw_fx(surface, camera)
            return

        # shadow under the feet
        pygame.draw.ellipse(surface, (0, 14, 0), (sx - 5, sy + 4, 10, 3))

        rel = self._body_sprite()
        visible = not (self.blink > 0 and int(self.blink * 20) % 2 == 0)
        show_weapon = not self.rooted
        if visible:
            if self.facing == "back" and show_weapon:
                self._draw_weapon(surface, sx, sy)          # weapon behind the body
            body = _tinted(rel, gdev.WHITE) if self.hurt_flash > 0 else gdev.image(rel)
            if body is not None:
                surface.blit(body, top_left)
            if self.facing != "back" and show_weapon:
                self._draw_weapon(surface, sx, sy)
        if self.parry_t > 0 or self.parry_flash > 0:
            col = gdev.WHITE if self.parry_flash > 0 else gdev.CYAN
            pygame.draw.circle(surface, col, (sx, sy), 13, 1)
        if self.reload_t > 0 and self.weapon_name() == "PISTOL":
            k = 1.0 - self.reload_t / PISTOL_RELOAD
            r = pygame.Rect(sx - 5, sy - 22, 10, 10)
            pygame.draw.circle(surface, gdev.GREEN_DARK, r.center, 5, 1)
            if k > 0.02:
                pygame.draw.arc(surface, gdev.AMBER, r, math.pi / 2, math.pi / 2 + k * 2 * math.pi, 2)
        self._draw_projectiles(surface, camera)
        self._draw_fx(surface, camera)

    def _draw_weapon(self, surface, sx, sy):
        name = self.weapon_name()
        if not name:
            return
        side = self.side
        ang = _weapon_angle(side, self.aim_angle)
        # direction the weapon actually points (after the +-90 limit)
        rad = math.radians(ang)
        dx = math.cos(rad) if side == "right" else -math.cos(rad)
        dy = -math.sin(rad) if side == "right" else math.sin(rad)
        px, py = PIVOT[side]
        pivot_screen = (sx - 16 + px, sy - 16 + py)

        if name == "KATANA" and self.slash_anim > 0:
            self._draw_slash(surface, sx, sy)
            # the blade sweeps across the arc
            k = 1.0 - self.slash_anim / 0.14
            sweep = (-70 + 140 * k) * self.slash_swing
            ang += sweep if side == "right" else -sweep
        tint = gdev.RED if self.jam_t > 0 and int(self.jam_t * 8) % 2 == 0 else None
        rel = "weapon_assets/%s_%s.png" % (name.lower(), side)
        img = _rotated_canvas(rel, ang, tint)
        if img is not None:
            blit_pivot(surface, img, ang, (px, py), pivot_screen)

        if name == "BOW" and self.charge_t is not None:
            # the nocked arrow is pulled back as the charge builds
            k = min(1.0, self.charge_t / BOW_FULL)
            back = 4.0 * k
            ax = pivot_screen[0] + dx * (3 - back)
            ay = pivot_screen[1] + dy * (3 - back)
            art = PROJ_ART["arrow"][side]
            arrow = _rotated(art[0], art[1], ang)
            if arrow is not None:
                surface.blit(arrow, arrow.get_rect(center=(int(ax), int(ay))))
            if k >= 1.0 and int(self.charge_t * 12) % 2 == 0:
                pygame.draw.circle(surface, gdev.CYAN, (int(ax), int(ay)), 6, 1)
        if name == "PISTOL" and self.muzzle_t > 0:
            mx = int(pivot_screen[0] + dx * 7)
            my = int(pivot_screen[1] + dy * 7)
            pygame.draw.circle(surface, gdev.AMBER, (mx, my), 3)
            pygame.draw.circle(surface, gdev.WHITE, (mx, my), 1)

    def _draw_slash(self, surface, sx, sy):
        """Code-drawn crescent for the katana arc (bigger on the third hit)."""
        k = 1.0 - self.slash_anim / 0.14
        big = self.combo >= 3
        r_out = 24 if big else 21
        r_in = r_out - (7 if big else 5)
        span = math.radians(130)
        a0 = self.slash_angle - span / 2
        n = 9
        upto = max(2, int(n * min(1.0, k * 1.6)) + 1)
        outer, inner = [], []
        for i in range(upto):
            t = i / float(n - 1)
            a = a0 + span * (t if self.slash_swing > 0 else 1.0 - t)
            outer.append((sx + math.cos(a) * r_out, sy + math.sin(a) * r_out))
            inner.append((sx + math.cos(a) * r_in, sy + math.sin(a) * r_in))
        pts = outer + inner[::-1]
        if len(pts) >= 3:
            col = gdev.CYAN if big else gdev.WHITE
            if k > 0.6:
                col = gdev.GREEN_MID
            pygame.draw.polygon(surface, col, pts)
            pygame.draw.lines(surface, gdev.GREEN, False, outer, 1)

    def _draw_projectiles(self, surface, camera):
        view = surface.get_rect().inflate(16, 16)
        for p in self.projectiles:
            if view.collidepoint(camera.world_to_screen(p.pos)):
                p.draw(surface, camera)

    def _draw_fx(self, surface, camera):
        for kind, x, y, t, extra in self.fx:
            sx, sy = camera.world_to_screen((x, y))
            if kind == "spark":
                n = int(2 + t * 20)
                for i in range(4):
                    a = i * math.pi / 2 + 0.6
                    pygame.draw.line(surface, extra, (sx + math.cos(a) * (n - 2), sy + math.sin(a) * (n - 2)),
                                     (sx + math.cos(a) * n, sy + math.sin(a) * n))
            elif kind == "ping":
                pygame.draw.circle(surface, gdev.CYAN, (sx, sy), int(6 + t * 60), 1)
            elif kind == "swap":
                gdev.text(surface, extra, (sx, sy - int(t * 20)), 10, gdev.GREEN, "midbottom", True)

    def _draw_death(self, surface, sx, sy):
        """Dissolve: the sprite tears into horizontal slices and scatters."""
        t = self.death_t
        img = gdev.image("player_assets/player_%s_asset.png" % self.side)
        if img is None or t > 1.0:
            return
        k = min(1.0, t / 0.8)
        for row in range(10, 23):
            off = int(math.sin(row * 2.3 + t * 30) * k * 10)
            lift = int(k * k * (row % 3) * 6)
            strip = img.subsurface((0, row, 32, 1))
            if (row * 7 + int(t * 30)) % 5 < int(k * 5):
                continue
            surface.blit(strip, (sx - 16 + off, sy - 16 + row - lift))
        for i in range(8):
            a = i * 0.785 + 0.3
            d = 4 + k * 26
            col = gdev.MAGENTA if i % 2 else gdev.GREEN
            pygame.draw.rect(surface, col, (int(sx + math.cos(a) * d), int(sy + math.sin(a) * d - k * 8), 2, 2))
