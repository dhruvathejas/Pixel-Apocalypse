"""In-game HUD, drawn on the 960x500 internal surface after the world.

draw_hud(surface, run, settings) reads (never writes) the Run object.
"""
import math
import random

import pygame

import gdev

# The name bar (doc 8.2): 14 clusters in their heavier encoding -> ULTIMATEHACKER
NAME_CLUSTERS = ["|_|", "|_", "7", "|", "|\\/|", "/-\\", "7", "3", "|-|", "/-\\", "(", "|<", "3", "|2"]
NAME_PLAIN = "ULTIMATEHACKER"
KEY_ANIM_S = 2.4            # length of the de-leet flourish for one cluster
SCRAMBLE = "#%&@$!?/\\|<>=+*01"

# Where floating windows (the hack terminal) may go without covering the HUD:
# below the name bar + objective line, above the weapon bar.
SAFE_RECT = pygame.Rect(8, 56, 944, 384)


def draw_hud(surface, run, settings):
    draw_vitals(surface, run)
    draw_name_bar(surface, run, (surface.get_width() // 2, 6))
    draw_objective(surface, run)
    draw_weapon(surface, run)
    draw_zone_arrow(surface, run)
    draw_toasts(surface, run)
    draw_world_prompts(surface, run)       # last: the jack-in prompt is never under HUD text
    if settings.get("show_fps"):
        f = gdev.clock.get_fps() if gdev.clock else 0
        label = "%3d FPS" % int(f + 0.5) if f > 0 else " -- FPS"     # 0 until the clock has ticked
        gdev.text(surface, label, (surface.get_width() - 8, 6), 12, gdev.GREEN_MID, "topright")
    if run.player.dead:
        draw_death(surface, run)


# ------------------------------------------------------------------ pieces

def draw_vitals(surface, run):
    p = run.player
    gdev.panel(surface, (6, 6, 150, 52), alpha=170, border=gdev.GREEN_DIM)
    gdev.text(surface, "HP", (12, 11), 12, gdev.GREEN_MID)
    for i in range(p.max_hp):
        r = pygame.Rect(34 + i * 19, 11, 15, 12)
        if i < p.hp:
            col = gdev.RED if p.hp <= 1 and int(run.time * 4) % 2 == 0 else gdev.GREEN
            pygame.draw.rect(surface, col, r)
            pygame.draw.line(surface, gdev.WHITE, (r.x + 1, r.y + 1), (r.right - 3, r.y + 1))
        else:
            pygame.draw.rect(surface, gdev.GREEN_DIM, r, 1)
    gdev.text(surface, "BITS", (12, 28), 12, gdev.GREEN_MID)
    gdev.text(surface, "%04d" % run.bits, (52, 28), 12, gdev.AMBER)
    gdev.text(surface, "INTEL", (12, 42), 12, gdev.GREEN_MID)
    gdev.text(surface, "%02d" % run.intel.count(), (52, 42), 12, gdev.CYAN)
    gdev.text(surface, "KEYS %d/14" % len(run.keys), (78, 42), 12,
              gdev.CYAN if run.keys else gdev.GREEN_DIM)


def name_bar_layout(center_top, size=12):
    """Rects of the 14 name-bar boxes, centred on center_top (x, y)."""
    widths = [max(gdev.text_size(c, size)[0], gdev.text_size("M", size)[0]) + 8 for c in NAME_CLUSTERS]
    gap = 3
    total = sum(widths) + gap * (len(widths) - 1)
    x = center_top[0] - total // 2
    rects = []
    for w in widths:
        rects.append(pygame.Rect(x, center_top[1], w, size + 8))
        x += w + gap
    return rects


def draw_name_bar(surface, run, center_top, size=12, keys=None, key_anim=None, t=None):
    """The 14-cluster name bar. Decoded clusters are clean cyan letters; a cluster
    whose Key just arrived scrambles, then settles with a flash (run.key_anim)."""
    keys = run.keys if keys is None else keys
    key_anim = run.key_anim if key_anim is None else key_anim
    t = run.time if t is None else t
    rects = name_bar_layout(center_top, size)
    bar = rects[0].unionall(rects[1:]).inflate(10, 6)
    gdev.panel(surface, bar, alpha=170, border=gdev.GREEN_DIM)
    for i, r in enumerate(rects):
        anim = key_anim.get(i)
        if anim is not None and anim < KEY_ANIM_S:
            _draw_key_cluster(surface, r, i, anim, size)
            continue
        if i in keys:
            pygame.draw.rect(surface, (10, 60, 70), r)
            pygame.draw.rect(surface, gdev.CYAN, r, 1)
            gdev.text(surface, NAME_PLAIN[i], r.center, size, gdev.CYAN, "center", bold=True)
        else:
            pygame.draw.rect(surface, gdev.GREEN_DARK, r)
            pygame.draw.rect(surface, gdev.GREEN_DIM, r, 1)
            # locked clusters flicker very slightly, like a failing display
            col = gdev.GREEN_MID if (int(t * 3 + i * 7) % 23) else gdev.MAGENTA
            gdev.text(surface, NAME_CLUSTERS[i], r.center, size, col, "center")
    return bar


def _draw_key_cluster(surface, r, i, anim, size):
    settle = KEY_ANIM_S * 0.55
    if anim < settle:
        rng = random.Random(int(anim * 30) + i)
        ch = rng.choice(SCRAMBLE)
        pygame.draw.rect(surface, (50, 10, 45), r)
        pygame.draw.rect(surface, gdev.MAGENTA, r, 1)
        gdev.text(surface, ch, r.center, size, gdev.MAGENTA, "center", bold=True)
    else:
        k = (anim - settle) / (KEY_ANIM_S - settle)
        # expanding cyan ring that fades out, then the clean letter
        ring = r.inflate(int(4 + 40 * k), int(4 + 24 * k))
        g = pygame.Surface(ring.size, pygame.SRCALPHA)
        pygame.draw.rect(g, (90, 240, 255, int(230 * (1.0 - k))), g.get_rect(), 2)
        surface.blit(g, ring.topleft)
        flash = k < 0.25
        pygame.draw.rect(surface, gdev.WHITE if flash else (10, 60, 70), r)
        pygame.draw.rect(surface, gdev.CYAN, r, 1)
        gdev.text(surface, NAME_PLAIN[i], r.center, size, gdev.BG if flash else gdev.CYAN, "center", bold=True)


def draw_objective(surface, run):
    # During a hack the terminal's title bar carries TRACE; the band stays clear.
    if run.hack is not None:
        return
    cx = surface.get_width() // 2
    text = run.objective_text()
    w, h = gdev.text_size(text, 12)
    gdev.panel(surface, (cx - w // 2 - 6, 34, w + 12, h + 4), alpha=110, border=None)
    gdev.text(surface, text, (cx, 36), 12, gdev.GREEN, "midtop")


def draw_weapon(surface, run):
    p = run.player
    h = surface.get_height()
    info = p.weapon_hud()
    x = 8
    for i, name in enumerate(p.weapon_slots):
        label = "%d %s" % (i + 1, name if name else "----")
        w = gdev.text_size(label, 12)[0] + 10
        r = pygame.Rect(x, h - 44, w, 16)
        if i == p.weapon_index and name:
            pygame.draw.rect(surface, gdev.GREEN, r)
            gdev.text(surface, label, r.center, 12, gdev.BG, "center", bold=True)
        else:
            gdev.panel(surface, r, alpha=150, border=gdev.GREEN_DIM)
            gdev.text(surface, label, r.center, 12, gdev.GREEN_MID if name else gdev.GREEN_DIM, "center")
        x += w + 4
    y = h - 24
    if info.get("jammed"):
        gdev.text(surface, "WEAPON JAMMED", (8, y), 12, gdev.RED, bold=True)
    elif info.get("reload") is not None:
        gdev.text(surface, "RELOAD", (8, y), 12, gdev.AMBER)
        r = pygame.Rect(62, y + 3, 80, 8)
        pygame.draw.rect(surface, gdev.GREEN_DIM, r, 1)
        pygame.draw.rect(surface, gdev.AMBER, (r.x + 1, r.y + 1, int((r.w - 2) * info["reload"]), r.h - 2))
    elif info.get("mag"):
        for i in range(info["mag"]):
            r = pygame.Rect(8 + i * 9, y, 6, 12)
            if i < info.get("ammo", 0):
                pygame.draw.rect(surface, gdev.AMBER, r)
            else:
                pygame.draw.rect(surface, gdev.GREEN_DIM, r, 1)
        gdev.text(surface, "%d/%d" % (info.get("ammo", 0), info["mag"]), (14 + info["mag"] * 9, y), 12,
                  gdev.AMBER)
    if info.get("charge") is not None:
        r = pygame.Rect(8, y - 4, 80, 3)
        pygame.draw.rect(surface, gdev.CYAN, (r.x, r.y, int(r.w * info["charge"]), r.h))


def draw_world_prompts(surface, run):
    """Jack-in prompt and hold-F progress ring around the targeted EXPOSED minion."""
    m = run.jack_target
    if m is None or run.hack is not None or run.player.dead:
        return
    sx, sy = run.camera.world_to_internal(m.pos)
    if run.jack_progress > 0:
        k = min(1.0, run.jack_progress)
        rect = pygame.Rect(sx - 20, sy - 20, 40, 40)
        pygame.draw.arc(surface, gdev.CYAN, rect, math.pi / 2, math.pi / 2 + k * 2 * math.pi, 3)
    label = "[F] TAP TO JACK IN" if run.player.weapon_name() == "KATANA" else "HOLD [F] JACK IN"
    r = gdev.text(surface, label, (sx, sy - 44), 12, gdev.CYAN, "midbottom", bold=True)
    pygame.draw.line(surface, gdev.CYAN, (r.left, r.bottom), (r.right, r.bottom))


def draw_zone_arrow(surface, run):
    """Edge-of-screen arrow toward Rack A while the player is outside its zone. Inside the
    zone it points at the nearest port until a hostile minion is on screen (or the port is)."""
    rack = run.world.rack("A")
    if rack is None or run.player.dead or run.hack is not None or run.victory_t is not None:
        return
    p = run.player.pos
    if rack.in_zone(p):
        view = run.camera.view_rect
        if not rack.bars or any(m.alive and m.team == "enemy" and view.collidepoint(m.pos)
                                for m in run.minions.minions):
            return
        exit_pt = min((b.spawn_exit for b in rack.bars),
                      key=lambda e: (e[0] - p.x) ** 2 + (e[1] - p.y) ** 2)
        if view.inflate(-80, -140).collidepoint(exit_pt):
            return                             # the port itself is in plain view
        dist = int(math.hypot(exit_pt[0] - p.x, exit_pt[1] - p.y))
        _edge_arrow(surface, run, exit_pt, "RACK A PORTS  %dm" % (dist // 32))
        return
    dist = int(math.hypot(rack.zone_center[0] - p.x, rack.zone_center[1] - p.y) - rack.zone_radius)
    _edge_arrow(surface, run, rack.zone_center, "RACK A  %dm" % max(0, dist // 32))


def _edge_arrow(surface, run, world_pt, label):
    sw, sh = surface.get_size()
    tx, ty = run.camera.world_to_internal(world_pt)
    cx, cy = sw / 2.0, sh / 2.0
    dx, dy = tx - cx, ty - cy
    d = math.hypot(dx, dy)
    if d < 1:
        return
    ux, uy = dx / d, dy / d
    k = min((sw / 2.0 - 40) / max(abs(ux), 1e-6), (sh / 2.0 - 70) / max(abs(uy), 1e-6))
    ax, ay = cx + ux * k, cy + uy * k
    pulse = 4 * math.sin(run.time * 5)
    tip = (ax + ux * (10 + pulse), ay + uy * (10 + pulse))
    left = (ax - uy * 7, ay + ux * 7)
    right = (ax + uy * 7, ay - ux * 7)
    pygame.draw.polygon(surface, gdev.MAGENTA, [tip, left, right])
    gdev.text(surface, label, (ax - ux * 18, ay - uy * 18), 12, gdev.MAGENTA, "center")


def draw_toasts(surface, run):
    y = surface.get_height() - 92
    for text, color, t in reversed(run.toasts[-3:]):
        a = 255 if t > 0.4 else int(255 * t / 0.4)
        w, h = gdev.text_size(text, 16, True)
        # dark backing so world detail (e.g. Clean Node 1's terminal) never cuts through the text
        gdev.panel(surface, (surface.get_width() // 2 - w // 2 - 8, y - h - 2, w + 16, h + 4),
                   alpha=int(235 * a / 255), border=None)
        gdev.text(surface, text, (surface.get_width() // 2, y), 16, color, "midbottom", bold=True, alpha=a)
        y -= 22


def draw_death(surface, run):
    sw, sh = surface.get_size()
    veil = pygame.Surface((sw, sh), pygame.SRCALPHA)
    veil.fill((30, 0, 8, 150))
    surface.blit(veil, (0, 0))
    rng = random.Random(int(run.time * 12))
    for _ in range(10):
        y = rng.randrange(sh)
        pygame.draw.line(surface, (120, 20, 40), (0, y), (sw, y))
    gdev.text(surface, "SIGNAL LOST", (sw // 2, sh // 2 - 30), 40, gdev.RED, "center", bold=True)
    gdev.text(surface, "REBOOTING AT CLEAN NODE 1 :: INTEL KEPT :: HALF YOUR BITS LOST",
              (sw // 2, sh // 2 + 14), 14, gdev.WHITE, "center")
