"""In-game HUD, drawn on the 960x500 internal surface after the world.

draw_hud(surface, run, settings) reads (never writes) the Run object. Everything here is in
INTERNAL pixels; world points are converted with run.camera.world_to_internal.

Layout (960x500):
    top-left      vitals: HP pips, BITS, INTEL, KEYS x/14, SHARDS
    top-centre    the 14-cluster name bar (after Key 14: your handle in leetspeak, glitching)
    y 34          objective line (run.objective_text(), i.e. progression / finale)
    top-right     act // zone label (progression.zone_label()), FPS under it
    right edge    visible_queue entries of the active boss (hud_info()["queue"])
    bottom-left   weapon bar;  bottom-centre: the boss bar (run.boss_hud());  toasts above it
    y ~200        the Key banner (run.key_banner)
    edges         objective arrow (magenta), Key carrier arrows (cyan)
"""
import math
import random

import pygame

import gdev
import hack

# The name bar (doc 8.2): 14 clusters in their heavier encoding -> ULTIMATEHACKER
NAME_CLUSTERS = ["|_|", "|_", "7", "|", "|\\/|", "/-\\", "7", "3", "|-|", "/-\\", "(", "|<", "3", "|2"]
NAME_PLAIN = "ULTIMATEHACKER"
KEY_ANIM_S = 2.4            # length of the de-leet flourish for one cluster
SCRAMBLE = "#%&@$!?/\\|<>=+*01"

# Where floating windows (the hack terminal) may go without covering the HUD:
# below the name bar + objective line, above the weapon bar.
SAFE_RECT = pygame.Rect(8, 56, 944, 384)
BOSS_BAR = pygame.Rect(300, 448, 360, 44)
QUEUE_X = 952                # right edge of the visible_queue column
KEY_BANNER_S = 3.6           # keep in step with game.KEY_BANNER_S
VITALS_RIGHT = 176           # right edge of the HP / Bits panel (the objective line stays clear of it)
STATE_LABELS = {"DORMANT": "D0RM4NT", "ZONE": "Z0N3", "SIEGE": "513G3", "EXPOSURE": "3XP0SUR3",
                "BREACH_READY": "R00T P0RT 0P3N", "BREACH": "BR34CH", "CORE_DUMP": "C0R3 DUMP",
                "CLEARED": "CL34N"}


def draw_hud(surface, run, settings):
    draw_vitals(surface, run)
    handle = run.handle if run.progression.key14_played else None
    draw_name_bar(surface, run, (surface.get_width() // 2, 6), handle=handle,
                  glitch=not settings.get("reduced_glitch"))
    draw_objective(surface, run)
    draw_zone_label(surface, run, settings)
    draw_weapon(surface, run)
    info = run.boss_hud()
    if info is not None:
        draw_boss_bar(surface, run, info)
        if run.hack is None:               # while hacking, Game draws it beside the terminal
            draw_queue(surface, run, info.get("queue"))
    draw_arrows(surface, run)
    draw_toasts(surface, run, raised=info is not None)
    draw_key_banner(surface, run)
    draw_world_prompts(surface, run)       # last: the jack-in prompt is never under HUD text
    if run.player.dead:
        draw_death(surface, run)


# ------------------------------------------------------------------ pieces

def draw_vitals(surface, run):
    p = run.player
    gdev.panel(surface, (6, 6, 170, 52), alpha=170, border=gdev.GREEN_DIM)
    gdev.text(surface, "HP", (12, 11), 12, gdev.GREEN_MID)
    step = min(19, 132 // max(1, p.max_hp))      # pips shrink so 10 HP still fits the panel
    for i in range(p.max_hp):
        r = pygame.Rect(34 + i * step, 11, step - 4, 12)
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
    gdev.text(surface, "SHARDS %d" % run.progression.shards, (94, 28), 12, gdev.GREEN_MID)


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


def draw_name_bar(surface, run, center_top, size=12, keys=None, key_anim=None, t=None, handle=None,
                  glitch=True):
    """The 14-cluster name bar. Decoded clusters are clean cyan letters; a cluster
    whose Key just arrived scrambles, then settles with a flash (run.key_anim).
    handle: after Key 14 the bar re-encodes into the player's handle in leetspeak (doc 8.3)."""
    t = run.time if t is None else t
    if handle:
        return draw_handle_bar(surface, handle, center_top, size, t, glitch)
    keys = run.keys if keys is None else keys
    key_anim = run.key_anim if key_anim is None else key_anim
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


def draw_handle_bar(surface, handle, center_top, size, t, glitch=True):
    """Key 14: the name is YOU. Your handle, leetified with the extended set, glitching."""
    clusters = hack.leetify_handle(handle)
    widths = [max(gdev.text_size(c, size)[0], gdev.text_size("M", size)[0]) + 8 for c in clusters]
    total = sum(widths) + 3 * (len(widths) - 1)
    x = center_top[0] - total // 2
    rects = []
    for w in widths:
        rects.append(pygame.Rect(x, center_top[1], w, size + 8))
        x += w + 3
    bar = rects[0].unionall(rects[1:]).inflate(10, 6)
    gdev.panel(surface, bar, fill=(30, 6, 26), alpha=200, border=gdev.MAGENTA)
    rng = random.Random(int(t * 12))
    for i, (c, r) in enumerate(zip(clusters, rects)):
        dx = rng.choice((0, 0, 0, 0, 1, -1)) if glitch else 0
        pygame.draw.rect(surface, (50, 10, 45), r.move(dx, 0))
        pygame.draw.rect(surface, gdev.MAGENTA, r.move(dx, 0), 1)
        if glitch and rng.random() < 0.06:
            gdev.text(surface, handle[i] if i < len(handle) else c, r.center, size, gdev.CYAN, "center", True)
        else:
            gdev.text(surface, c, r.move(dx, 0).center, size, gdev.MAGENTA, "center", True)
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
    # centred between the vitals panel and the right edge: shrink, then drop trailing parts
    room = surface.get_width() - 2 * (VITALS_RIGHT + 6)
    size = 12
    while size > 10 and gdev.text_size(text, size)[0] > room:
        size -= 1
    while gdev.text_size(text, size)[0] > room and "  ::  " in text:
        text = text.rsplit("  ::  ", 1)[0]
    w, h = gdev.text_size(text, size)
    gdev.panel(surface, (cx - w // 2 - 6, 34, w + 12, h + 4), alpha=110, border=None)
    gdev.text(surface, text, (cx, 36), size, gdev.GREEN, "midtop")


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
    """Jack-in prompt and hold-F progress ring around the jack target (any provider), or the
    interact hint of a nearby terminal / hatch / cache."""
    m = run.jack_target
    if run.hack is not None or run.player.dead:
        return
    if m is None:
        if run.interact_hint is not None:
            pt, label = run.interact_hint
            sx, sy = run.camera.world_to_internal(pt)
            r = gdev.text(surface, label, (sx, sy - 30), 12, gdev.GREEN, "midbottom", bold=True)
            pygame.draw.line(surface, gdev.GREEN, (r.left, r.bottom), (r.right, r.bottom))
        return
    sx, sy = run.camera.world_to_internal(m.pos)
    if run.jack_progress > 0:
        k = min(1.0, run.jack_progress)
        rect = pygame.Rect(sx - 20, sy - 20, 40, 40)
        pygame.draw.arc(surface, gdev.CYAN, rect, math.pi / 2, math.pi / 2 + k * 2 * math.pi, 3)
    label = getattr(m, "prompt", None)
    if not label:
        label = "[F] TAP TO JACK IN" if run.player.weapon_name() == "KATANA" else "HOLD [F] JACK IN"
        if getattr(m, "key_source", None):
            label += " :: KEY"
    r = gdev.text(surface, label, (sx, sy - 44), 12, gdev.CYAN, "midbottom", bold=True)
    pygame.draw.line(surface, gdev.CYAN, (r.left, r.bottom), (r.right, r.bottom))


def zone_label_fit(screen_w, forms):
    """(label, size, room): the longest label form that fits right of the name bar, shrunk to
    size 9 at most if even the shortest does not."""
    room = screen_w - 8 - (name_bar_layout((screen_w // 2, 6))[-1].right + 12)
    label = forms[-1]
    for f in forms:
        if gdev.text_size(f, 12)[0] <= room:
            label = f
            break
    size = 12
    while size > 9 and gdev.text_size(label, size)[0] > room:
        size -= 1
    return label, size, room


def draw_zone_label(surface, run, settings):
    """Top-right zone label, never over the name bar."""
    w = surface.get_width()
    label, size, _room = zone_label_fit(w, run.progression.zone_label_forms())
    gdev.text(surface, label, (w - 8, 6), size, gdev.GREEN_MID, "topright")
    if settings.get("show_fps"):
        f = gdev.clock.get_fps() if gdev.clock else 0
        fps = "%3d FPS" % int(f + 0.5) if f > 0 else " -- FPS"     # 0 until the clock has ticked
        gdev.text(surface, fps, (w - 8, 20), 12, gdev.GREEN_DIM, "topright")


def draw_arrows(surface, run):
    """Edge-of-screen arrows: the objective point (magenta: rack, door, hatch, breach port, cache)
    and live Key carriers (cyan). Hidden while hacking, dead, or when the point is on screen."""
    if run.player.dead or run.hack is not None:
        return
    p = run.player.pos
    view = run.camera.view_rect.inflate(-40, -60)
    for pt, label in run.progression.carrier_points()[:2]:
        if not view.collidepoint(pt):
            dist = int(math.hypot(pt[0] - p.x, pt[1] - p.y))
            _edge_arrow(surface, run, pt, "%s  %dm" % (label, dist // 32), gdev.CYAN)
    obj = None if run.in_deep_core else run.progression.objective_point()
    if obj is not None:
        pt, label = obj
        if not view.collidepoint(pt):
            dist = int(math.hypot(pt[0] - p.x, pt[1] - p.y))
            _edge_arrow(surface, run, pt, "%s  %dm" % (label, dist // 32))


def draw_boss_bar(surface, run, info):
    """Firewall points, core HP segments, phase and the EXPOSURE window (doc 7.2)."""
    r = BOSS_BAR
    gdev.panel(surface, r, fill=(24, 6, 22), alpha=205, border=gdev.MAGENTA)
    gdev.text(surface, info.get("name", ""), (r.x + 8, r.y + 4), 13, gdev.MAGENTA, bold=True)
    state = str(info.get("state", ""))
    sub = "%s  ::  %s" % (info.get("subtitle", ""), STATE_LABELS.get(state, state.replace("_", " ")))
    gdev.text(surface, sub, (r.right - 8, r.y + 5), 12, (200, 120, 190), "topright")
    # firewall pips
    fw, fwm = int(info.get("firewall", 0)), int(info.get("firewall_max", 0))
    x = r.x + 8
    y = r.y + 22
    if fwm > 0:
        gdev.text(surface, "FW", (x, y - 1), 12, gdev.AMBER)
        x += 22
        for i in range(fwm):
            cell = pygame.Rect(x + i * 10, y + 1, 8, 8)
            if i < fw:
                pygame.draw.rect(surface, (255, 140, 40), cell)
            else:
                pygame.draw.rect(surface, (90, 40, 12), cell, 1)
        x += fwm * 10 + 10
    # cores
    cores = info.get("cores", [])
    if cores:
        gdev.text(surface, "CORES", (x, y - 1), 12, gdev.MAGENTA)
        x += 44
        cw = max(20, (r.right - 8 - x - 6 * (len(cores) - 1)) // max(1, len(cores)))
        for i, c in enumerate(cores):
            cell = pygame.Rect(x + i * (cw + 6), y + 1, cw, 8)
            pygame.draw.rect(surface, (60, 14, 50), cell)
            if not c.get("broken"):
                k = max(0.0, min(1.0, c.get("hp", 0) / max(0.01, c.get("max", 1))))
                col = gdev.WHITE if i == info.get("phase") and info.get("window") is not None else gdev.MAGENTA
                pygame.draw.rect(surface, col, (cell.x, cell.y, int(cell.w * k), cell.h))
            pygame.draw.rect(surface, (160, 40, 130), cell, 1)
    win = info.get("window")
    if win is not None and info.get("window_total"):
        k = max(0.0, min(1.0, win / float(info["window_total"])))
        pygame.draw.rect(surface, gdev.CYAN, (r.x + 2, r.bottom - 5, int((r.w - 4) * k), 3))
    if info.get("prediction"):
        gdev.text(surface, info["prediction"], (r.centerx, r.y - 4), 12, gdev.CYAN, "midbottom", True)


def _queue_boxes(queue, x, y):
    out = []
    for e in queue[:6]:
        label = "%s  %.1fs" % (e.get("label", "?"), max(0.0, e.get("t", 0.0)))
        w, h = gdev.text_size(label, 12, True)
        out.append((pygame.Rect(x - w - 12, y, w + 12, h + 6), label, tuple(e.get("color") or gdev.MAGENTA)))
        y += h + 10
    return out


def draw_queue(surface, run, queue, avoid=None):
    """visible_queue (doc 7.3): upcoming boss attacks, honest countdowns. Entries are dicts
    {"label", "t" (seconds left), "color" (rgb, optional), "real_time" (bool)}.
    avoid: a rect (the hack terminal) the column must not cover; it moves beside or under it."""
    if not queue:
        return
    spots = [(QUEUE_X, 120)]
    if avoid is not None:
        spots += [(QUEUE_X, avoid.bottom + 22), (avoid.left - 8, 120), (avoid.left - 8, avoid.bottom + 22)]
    boxes = None
    for x, y in spots:
        boxes = _queue_boxes(queue, x, y)
        bound = boxes[0][0].unionall([b[0] for b in boxes]).inflate(0, 20)
        if (avoid is None or not bound.colliderect(avoid)) and bound.left >= 0 and \
                bound.bottom <= BOSS_BAR.top - 4:
            break
    head = boxes[0][0]
    gdev.text(surface, "QU3U3", (head.right, head.y - 16), 12, gdev.MAGENTA, "topright", True)
    for box, label, col in boxes:
        gdev.panel(surface, box, fill=(24, 6, 22), alpha=200, border=col)
        gdev.text(surface, label, (box.right - 6, box.centery), 12, col, "midright", True)


def draw_key_banner(surface, run):
    """KEY nn RECOVERED band with the Key's reveal line (doc 8.3), KEY_BANNER_S long."""
    kb = run.key_banner
    if kb is None:
        return
    t = kb["t"]
    k = min(1.0, t / 0.4)
    fade = 1.0 if t < KEY_BANNER_S - 0.5 else max(0.0, (KEY_BANNER_S - t) / 0.5)
    w = surface.get_width()
    h = int(64 * k)
    band = pygame.Rect(0, 200 - h // 2, w, h)
    gdev.panel(surface, band, fill=(4, 30, 36), alpha=int(220 * fade), border=gdev.CYAN if fade > 0.5 else None)
    if k >= 1.0:
        letter = NAME_PLAIN[kb["index"]] if 0 <= kb["index"] < len(NAME_PLAIN) else "?"
        gdev.text(surface, "KEY %02d/14 RECOVERED  ::  %s -> %s" % (kb["count"], NAME_CLUSTERS[kb["index"]], letter),
                  (w // 2, band.y + 10), 24, gdev.CYAN, "midtop", True, alpha=int(255 * fade))
        if kb.get("reveal"):
            gdev.text(surface, kb["reveal"], (w // 2, band.y + 40), 13, gdev.WHITE, "midtop", alpha=int(255 * fade))


def _edge_arrow(surface, run, world_pt, label, color=gdev.MAGENTA):
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
    pygame.draw.polygon(surface, color, [tip, left, right])
    # the label sits behind the arrow by half its own size, so the triangle never covers it,
    # and stays fully on screen
    tw, th = gdev.text_size(label, 12)
    back = 14 + 0.5 * abs(ux) * tw + 0.5 * abs(uy) * th
    r = pygame.Rect(0, 0, tw, th)
    r.center = (int(ax - ux * back), int(ay - uy * back))
    r.clamp_ip(surface.get_rect().inflate(-8, -8))
    gdev.text(surface, label, r.topleft, 12, color)


def draw_toasts(surface, run, raised=False):
    y = surface.get_height() - (100 if raised else 92)
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
    gdev.text(surface, "REBOOTING AT %s :: INTEL AND KEYS KEPT :: A QUARTER OF YOUR BITS LOST" %
              run.progression.respawn_label(),
              (sw // 2, sh // 2 + 14), 14, gdev.WHITE, "center")
