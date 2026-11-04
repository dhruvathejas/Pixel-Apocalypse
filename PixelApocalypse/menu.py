"""All menu screens, in the green-phosphor CRT style (doc 3).

Every screen is drawn on the 960x500 internal surface and works with the
keyboard (W/S or arrows, Enter/Space, Esc = back) and the mouse (hover
highlights, click selects). Mouse event positions are internal pixels.

Each screen has:
    handle_event(e) -> action string or None
    update(dt)
    draw(surface)
    frozen  -- optional Surface (the paused game frame) drawn dimmed behind it
Game turns actions into state changes.

game_start keeps the owner's meaning: True while a run is active.
"""
import math
import random

import pygame

import gdev
import hud
import settings as settings_mod
import shop

game_start = False

TITLE = "P1X3L /-\\P0C/-\\LYP53"
SUBTITLE = "T H R E A T   M O D E L"
VERSION = "v0.2.0-alpha THREAT MODEL"

UP_KEYS = (pygame.K_UP, pygame.K_w)
DOWN_KEYS = (pygame.K_DOWN, pygame.K_s)
LEFT_KEYS = (pygame.K_LEFT, pygame.K_a)
RIGHT_KEYS = (pygame.K_RIGHT, pygame.K_d)
OK_KEYS = (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE)

RAIN_GLYPHS = "0123456789ABCDEF|/\\-_<>()=@$#"
STATUS_LINES = [
    "KERNEL: ALL SYSTEMS NOMINAL",
    "KERNEL: LIFE SUPPORT B AT 61%. THIS IS FINE.",
    "KERNEL: INTRUDER SIGNATURE NOT FOUND. SEARCHING.",
    "K3RN3L: 4LL 5Y5T3M5 N0M1N4L",
    "KERNEL: PLEASE REPORT LEETSPEAK GRAFFITI TO MAINTENANCE.",
    "KERNEL: RACK A THERMALS ABOVE SPEC. SEND THE TECH.",
]


# ------------------------------------------------------------------ CRT look

class CRT(object):
    """Shared background (glyph rain) and overlay (scanlines, vignette, roll bar)."""

    def __init__(self, size=gdev.INTERNAL_SIZE):
        self.w, self.h = size
        self.t = 0.0
        rng = random.Random(7)
        self.columns = []
        for x in range(4, self.w, 14):
            self.columns.append([x, rng.uniform(-self.h, self.h), rng.uniform(14, 46),
                                 rng.randint(5, 16), rng.randint(0, 9999)])
        self.vignette = self._make_vignette()

    def _make_vignette(self):
        v = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        steps = 26
        for i in range(steps):
            a = int(110 * (1.0 - i / float(steps)) ** 2.2)
            pygame.draw.rect(v, (0, 0, 0, a), (i * 3, i * 2, self.w - i * 6, self.h - i * 4), 3)
        return v

    def update(self, dt):
        self.t += dt
        for c in self.columns:
            c[1] += c[2] * dt
            if c[1] - c[3] * 14 > self.h:
                c[1] = -random.uniform(0, 200)
                c[4] = random.randint(0, 9999)

    def background(self, surface, frozen=None, veil_alpha=236):
        if frozen is not None:
            surface.blit(frozen, (0, 0))
            veil = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            veil.fill((2, 14, 6, veil_alpha))
            surface.blit(veil, (0, 0))
            return
        surface.fill(gdev.BG)
        # faint circuit grid
        for x in range(0, self.w, 48):
            pygame.draw.line(surface, (7, 26, 14), (x, 0), (x, self.h))
        for y in range(0, self.h, 48):
            pygame.draw.line(surface, (7, 26, 14), (0, y), (self.w, y))
        # glyph rain
        for x, head, _speed, length, seed in self.columns:
            for k in range(length):
                y = int(head) - k * 14
                if y < -14 or y > self.h:
                    continue
                ch = RAIN_GLYPHS[(seed + k * 7 + int(self.t * 3) * (k % 3 == 0)) % len(RAIN_GLYPHS)]
                if k == 0:
                    col = (40, 120, 66)
                else:
                    fade = 1.0 - k / float(length)
                    col = (int(8 + 14 * fade), int(30 + 46 * fade), int(16 + 22 * fade))
                gdev.text(surface, ch, (x, y), 12, col)

    def overlay(self, surface):
        gdev.draw_scanlines(surface, 46)
        # slow rolling bright band
        y = int((self.t * 60) % (self.h + 80)) - 40
        band = pygame.Surface((self.w, 26), pygame.SRCALPHA)
        band.fill((120, 255, 160, 9))
        surface.blit(band, (0, y))
        surface.blit(self.vignette, (0, 0))


def shade(surface, rect, alpha=170):
    """Soft dark band (no border) so text stays readable over the glyph rain."""
    r = pygame.Rect(rect)
    band = pygame.Surface(r.size, pygame.SRCALPHA)
    band.fill((gdev.BG[0], gdev.BG[1], gdev.BG[2], alpha))
    surface.blit(band, r.topleft)
    edge = pygame.Surface((r.w, 6), pygame.SRCALPHA)
    for i in range(6):
        pygame.draw.line(edge, (gdev.BG[0], gdev.BG[1], gdev.BG[2], int(alpha * i / 6.0)), (0, i), (r.w, i))
    surface.blit(edge, (r.x, r.y - 6))
    surface.blit(pygame.transform.flip(edge, False, True), (r.x, r.bottom))


def draw_glow_text(surface, s, pos, size, color, align="center", bold=True, glow=(30, 120, 60)):
    """Phosphor text with a soft halo (offset copies underneath)."""
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        gdev.text(surface, s, (pos[0] + dx, pos[1] + dy), size, glow, align, bold, alpha=70)
    return gdev.text(surface, s, pos, size, color, align, bold)


def draw_title(surface, t, center, size=44):
    """The leetspeak logo, with an occasional RGB-split / slice glitch."""
    phase = t % 3.7
    glitch = phase < 0.18 or 1.9 < phase < 1.98
    if glitch:
        off = 3 if phase < 0.09 else -2
        gdev.text(surface, TITLE, (center[0] - off, center[1]), size, gdev.MAGENTA, "center", True, alpha=170)
        gdev.text(surface, TITLE, (center[0] + off, center[1]), size, gdev.CYAN, "center", True, alpha=170)
    r = draw_glow_text(surface, TITLE, center, size, gdev.GREEN)
    if glitch:
        rng = random.Random(int(t * 40))
        for _ in range(2):
            sy = rng.randint(r.top, r.bottom - 6)
            sh = rng.randint(3, 7)
            strip = surface.subsurface(pygame.Rect(r.left, sy, r.w, sh)).copy()
            surface.blit(strip, (r.left + rng.randint(-10, 10), sy))
    return r


def draw_footer(surface, hints, right=VERSION):
    w, h = surface.get_size()
    shade(surface, (0, h - 24, w, 24), 190)
    pygame.draw.line(surface, gdev.GREEN_DIM, (16, h - 26), (w - 16, h - 26))
    gdev.text(surface, hints, (18, h - 20), 12, gdev.GREEN_MID)
    gdev.text(surface, right, (w - 18, h - 20), 12, gdev.GREEN_DIM, "topright")


def draw_header(surface, title, sub=None):
    w = surface.get_width()
    shade(surface, (0, 8, w, 70), 150)
    gdev.text(surface, "P1X3L /-\\P0C/-\\LYP53", (18, 14), 12, gdev.GREEN_DIM)
    draw_glow_text(surface, title, (w // 2, 40), 28, gdev.GREEN)
    if sub:
        gdev.text(surface, sub, (w // 2, 62), 12, gdev.GREEN_MID, "midtop")
    pygame.draw.line(surface, gdev.GREEN_DIM, (16, 82), (w - 16, 82))


def typed(s, t, cps=90.0):
    """The first part of s, as if typed for t seconds (screen-open effect)."""
    n = int(t * cps)
    return s if n >= len(s) else s[:n]


def is_key(e, keys):
    return e.type == pygame.KEYDOWN and e.key in keys


def is_click(e, button=1):
    return e.type == pygame.MOUSEBUTTONDOWN and e.button == button


# ------------------------------------------------------------------ base screens

class Screen(object):
    def __init__(self, crt):
        self.crt = crt
        self.t = 0.0
        self.frozen = None

    def open(self):
        """Called by Game when the screen becomes active."""
        self.t = 0.0

    def handle_event(self, e):
        return None

    def update(self, dt):
        self.t += dt

    def draw(self, surface):
        pass


class ListMenu(Screen):
    """Vertical list of (label, action). Esc returns back_action."""
    back_action = "back"
    row_h = 30
    width = 300

    def __init__(self, crt, items, top=200):
        Screen.__init__(self, crt)
        self.items = items
        self.selected = 0
        self.top = top

    def item_rects(self):
        x = gdev.INTERNAL_SIZE[0] // 2 - self.width // 2
        return [pygame.Rect(x, self.top + i * (self.row_h + 4), self.width, self.row_h)
                for i in range(len(self.items))]

    def handle_event(self, e):
        n = len(self.items)
        if is_key(e, UP_KEYS):
            self.selected = (self.selected - 1) % n
        elif is_key(e, DOWN_KEYS):
            self.selected = (self.selected + 1) % n
        elif is_key(e, OK_KEYS):
            return self.items[self.selected][1]
        elif is_key(e, (pygame.K_ESCAPE,)):
            return self.back_action
        elif e.type == pygame.MOUSEMOTION:
            for i, r in enumerate(self.item_rects()):
                if r.collidepoint(e.pos):
                    self.selected = i
        elif is_click(e):
            for i, r in enumerate(self.item_rects()):
                if r.collidepoint(e.pos):
                    self.selected = i
                    return self.items[i][1]
        return None

    def draw_items(self, surface):
        rects = self.item_rects()
        box = rects[0].unionall(rects[1:]).inflate(36, 28)
        gdev.panel(surface, box, alpha=215, border=gdev.GREEN_DIM)
        # corner ticks, terminal style
        for cx, cy, sx, sy in ((box.left, box.top, 1, 1), (box.right - 1, box.top, -1, 1),
                               (box.left, box.bottom - 1, 1, -1), (box.right - 1, box.bottom - 1, -1, -1)):
            pygame.draw.line(surface, gdev.GREEN, (cx, cy), (cx + 10 * sx, cy))
            pygame.draw.line(surface, gdev.GREEN, (cx, cy), (cx, cy + 10 * sy))
        for i, (r, (label, _a)) in enumerate(zip(rects, self.items)):
            shown = typed(label, self.t - i * 0.05)
            if i == self.selected:
                pygame.draw.rect(surface, gdev.GREEN, r)
                gdev.text(surface, ">", (r.x + 12, r.centery), 18, gdev.BG, "midleft", True)
                tr = gdev.text(surface, shown, (r.x + 34, r.centery), 18, gdev.BG, "midleft", True)
                if int(self.t * 2.5) % 2 == 0:
                    pygame.draw.rect(surface, gdev.BG, (tr.right + 6, r.y + 7, 9, r.h - 14))
                gdev.text(surface, "<", (r.right - 12, r.centery), 18, gdev.BG, "midright", True)
            else:
                gdev.text(surface, shown, (r.x + 34, r.centery), 18, gdev.GREEN_MID, "midleft")
        return box


class MainMenu(ListMenu):
    """CONTINUE (only when a save exists), NEW GAME, HOW TO PLAY, SETTINGS, DECRYPT, QUIT.
    has_save: callable -> bool, asked every time the menu opens."""
    back_action = None

    def __init__(self, crt, has_save=None):
        self.has_save = has_save or (lambda: False)
        ListMenu.__init__(self, crt, [], top=210)
        self._build()

    def _build(self):
        items = [("CONTINUE", "continue")] if self.has_save() else []
        items += [("NEW GAME", "new_game"), ("HOW TO PLAY", "howto"), ("SETTINGS", "settings"),
                  ("DECRYPT LOG", "intel"), ("QUIT", "quit")]
        self.items = items
        self.selected = min(self.selected, len(items) - 1)

    def index_of(self, action):
        for i, (_label, a) in enumerate(self.items):
            if a == action:
                return i
        return None

    def open(self):
        ListMenu.open(self)
        self._build()
        self.selected = 0

    def handle_event(self, e):
        if is_key(e, (pygame.K_ESCAPE,)):
            self.selected = len(self.items) - 1     # Esc on the main menu points at QUIT
            return None
        return ListMenu.handle_event(self, e)

    def draw(self, surface):
        self.crt.background(surface)
        w = surface.get_width()
        shade(surface, (0, 66, w, 108), 205)
        draw_title(surface, self.t, (w // 2, 100), 46)
        gdev.text(surface, SUBTITLE, (w // 2, 132), 14, gdev.MAGENTA, "midtop", True)
        gdev.text(surface, typed("// DEEPSTACK MAINTENANCE TERMINAL :: ROOT ACCESS GRANTED", self.t, 70),
                  (w // 2, 158), 12, gdev.GREEN_DIM, "midtop")
        self.draw_items(surface)
        shade(surface, (0, surface.get_height() - 60, w, 20), 150)
        status = STATUS_LINES[int(self.t / 4.0) % len(STATUS_LINES)]
        if int(self.t * 1.3) % 9 == 0:
            status = status.replace("E", "3").replace("A", "4").replace("I", "1")
        gdev.text(surface, status, (w // 2, surface.get_height() - 46), 12, gdev.GREEN_MID, "midbottom")
        draw_footer(surface, "W/S  NAVIGATE    ENTER  SELECT    MOUSE  CLICK")
        self.crt.overlay(surface)


class PauseMenu(ListMenu):
    """Game sets .info (last-save line) before opening."""
    back_action = "resume"

    def __init__(self, crt):
        ListMenu.__init__(self, crt, [("RESUME", "resume"), ("DECRYPT LOG", "intel"), ("SETTINGS", "settings"),
                                      ("HOW TO PLAY", "howto"), ("QUIT TO MENU", "quit_to_menu")], top=190)
        self.info = ""

    def draw(self, surface):
        self.crt.background(surface, self.frozen, 212)
        w = surface.get_width()
        draw_title(surface, self.t + 1.0, (w // 2, 90), 30)
        draw_glow_text(surface, "// PAUSED", (w // 2, 138), 20, gdev.AMBER, glow=(90, 60, 10))
        gdev.text(surface, "the world is frozen. Kernel is not.", (w // 2, 156), 12, gdev.GREEN_DIM, "midtop")
        box = self.draw_items(surface)
        if self.info:
            gdev.text(surface, self.info, (w // 2, box.bottom + 10), 12, gdev.GREEN_MID, "midtop")
        draw_footer(surface, "W/S  NAVIGATE    ENTER  SELECT    ESC  RESUME")
        self.crt.overlay(surface)


# ------------------------------------------------------------------ Clean Node shop

class ShopScreen(Screen):
    """The Clean Node shop (doc 10). Game sets .run before opening; rows come from shop.items().
    W/S select, Enter / click buy, Esc / BACK return to PLAY. handle_event returns "back" or
    "buy:<item id>" (Game buys and sets .message)."""
    row_h = 22

    def __init__(self, crt):
        Screen.__init__(self, crt)
        self.run = None
        self.rows = []
        self.selected = 0
        self.message = ""
        self.message_ok = True

    def open(self):
        Screen.open(self)
        self.refresh()
        self.message = ""

    def refresh(self):
        self.rows = (shop.items(self.run) if self.run is not None else []) + [None]     # None = BACK
        self.selected = min(self.selected, len(self.rows) - 1)

    def row_rects(self):
        w = 700
        x = gdev.INTERNAL_SIZE[0] // 2 - w // 2
        return [pygame.Rect(x, 112 + i * (self.row_h + 3) + (8 if i == len(self.rows) - 1 else 0), w, self.row_h)
                for i in range(len(self.rows))]

    def _pick(self, i):
        row = self.rows[i]
        if row is None:
            return "back"
        return "buy:" + row["id"]

    def handle_event(self, e):
        n = len(self.rows)
        if is_key(e, UP_KEYS):
            self.selected = (self.selected - 1) % n
        elif is_key(e, DOWN_KEYS):
            self.selected = (self.selected + 1) % n
        elif is_key(e, OK_KEYS):
            return self._pick(self.selected)
        elif is_key(e, (pygame.K_ESCAPE, pygame.K_f)):
            return "back"
        elif e.type == pygame.MOUSEMOTION:
            for i, r in enumerate(self.row_rects()):
                if r.collidepoint(e.pos):
                    self.selected = i
        elif is_click(e):
            for i, r in enumerate(self.row_rects()):
                if r.collidepoint(e.pos):
                    self.selected = i
                    return self._pick(i)
        elif is_click(e, 3):
            return "back"
        return None

    def draw(self, surface):
        self.crt.background(surface, self.frozen)
        run = self.run
        bits = run.bits if run is not None else 0
        shards = run.progression.shards if run is not None else 0
        draw_header(surface, "CLEAN NODE :: SH0P", "saved and healed. BITS buy weapon upgrades, SHARDS buy tools")
        gdev.text(surface, "BITS %d" % bits, (gdev.INTERNAL_SIZE[0] // 2 - 20, 92), 16, gdev.AMBER, "topright", True)
        gdev.text(surface, "SHARDS %d" % shards, (gdev.INTERNAL_SIZE[0] // 2 + 20, 92), 16, gdev.CYAN, "topleft", True)
        for i, (row, r) in enumerate(zip(self.rows, self.row_rects())):
            sel = i == self.selected
            if row is None:
                r = pygame.Rect(r.centerx - 80, r.y, 160, r.h)
                if sel:
                    pygame.draw.rect(surface, gdev.GREEN, r)
                else:
                    pygame.draw.rect(surface, gdev.GREEN_DIM, r, 1)
                gdev.text(surface, "BACK", r.center, 14, gdev.BG if sel else gdev.GREEN_MID, "center", True)
                continue
            gdev.panel(surface, r, fill=(12, 48, 24) if sel else gdev.BG_PANEL, alpha=225,
                       border=gdev.GREEN if sel else gdev.GREEN_DIM)
            if sel:
                pygame.draw.rect(surface, gdev.GREEN, (r.x, r.y, 4, r.h))
            tool = row["kind"] == "tool"
            gdev.text(surface, row["weapon"], (r.x + 12, r.centery), 12, gdev.CYAN if tool else gdev.GREEN_DIM,
                      "midleft", True)
            col = gdev.GREEN if sel else gdev.GREEN_MID
            if row["owned"]:
                col = gdev.GREEN_DIM
            gdev.text(surface, typed(row["label"], self.t - i * 0.03), (r.x + 82, r.centery), 14, col, "midleft", sel)
            gdev.text(surface, row["desc"], (r.x + 236, r.centery), 12, gdev.GREEN_DIM if row["owned"] else
                      gdev.GREEN_MID, "midleft")
            if row["owned"]:
                tag, tcol = "OWNED", gdev.GREEN_DIM
            else:
                tag = "%d %s" % (row["price"], row["currency"])
                tcol = (gdev.CYAN if tool else gdev.AMBER) if row["affordable"] else gdev.RED
            gdev.text(surface, tag, (r.right - 12, r.centery), 12, tcol, "midright", True)
        if self.message:
            y = self.row_rects()[-1].bottom + 12
            gdev.text(surface, "> " + self.message, (gdev.INTERNAL_SIZE[0] // 2, y), 14,
                      gdev.CYAN if self.message_ok else gdev.RED, "midtop", True)
        draw_footer(surface, "W/S  SELECT    ENTER / CLICK  BUY    ESC / F  BACK")
        self.crt.overlay(surface)


# ------------------------------------------------------------------ settings

class SettingsMenu(Screen):
    """Rows built from settings.OPTIONS plus BACK. Left/Right/Enter/click cycle a value.
    on_change(key) is called after every change (Game applies + saves)."""
    row_h = 24

    def __init__(self, crt, settings, on_change=None):
        Screen.__init__(self, crt)
        self.settings = settings
        self.on_change = on_change
        self.selected = 0
        self.rows = [o[0] for o in settings_mod.OPTIONS] + ["back"]

    def row_rects(self):
        w = 560
        x = gdev.INTERNAL_SIZE[0] // 2 - w // 2
        return [pygame.Rect(x, 92 + i * (self.row_h + 3), w, self.row_h) for i in range(len(self.rows))]

    def _arrows(self, r):
        left = pygame.Rect(r.right - 190, r.y, 28, r.h)
        right = pygame.Rect(r.right - 34, r.y, 28, r.h)
        return left, right

    def _change(self, step):
        key = self.rows[self.selected]
        if key == "back":
            return
        settings_mod.cycle(self.settings, key, step)
        if self.on_change:
            self.on_change(key)

    def handle_event(self, e):
        n = len(self.rows)
        if is_key(e, UP_KEYS):
            self.selected = (self.selected - 1) % n
        elif is_key(e, DOWN_KEYS):
            self.selected = (self.selected + 1) % n
        elif is_key(e, LEFT_KEYS):
            self._change(-1)
        elif is_key(e, RIGHT_KEYS):
            self._change(1)
        elif is_key(e, OK_KEYS):
            if self.rows[self.selected] == "back":
                return "back"
            self._change(1)
        elif is_key(e, (pygame.K_ESCAPE,)):
            return "back"
        elif e.type == pygame.MOUSEMOTION:
            for i, r in enumerate(self.row_rects()):
                if r.collidepoint(e.pos):
                    self.selected = i
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
            for i, r in enumerate(self.row_rects()):
                if r.collidepoint(e.pos):
                    self.selected = i
                    if self.rows[i] == "back":
                        return "back"
                    left, _right = self._arrows(r)
                    self._change(-1 if (e.button == 3 or left.collidepoint(e.pos)) else 1)
        return None

    def draw(self, surface):
        self.crt.background(surface, self.frozen)
        draw_header(surface, "SETTINGS", "changes apply at once and are saved to save/settings.json")
        rects = self.row_rects()
        for i, (key, r) in enumerate(zip(self.rows, rects)):
            sel = i == self.selected
            if key == "back":
                r = pygame.Rect(r.centerx - 80, r.y + 4, 160, r.h)
                if sel:
                    pygame.draw.rect(surface, gdev.GREEN, r)
                else:
                    pygame.draw.rect(surface, gdev.GREEN_DIM, r, 1)
                gdev.text(surface, "BACK", r.center, 16, gdev.BG if sel else gdev.GREEN_MID, "center", True)
                continue
            label = [o[1] for o in settings_mod.OPTIONS if o[0] == key][0]
            value = settings_mod.value_label(self.settings[key])
            gdev.panel(surface, r, fill=(12, 48, 24) if sel else gdev.BG_PANEL, alpha=225,
                       border=gdev.GREEN if sel else gdev.GREEN_DIM)
            if sel:
                pygame.draw.rect(surface, gdev.GREEN, (r.x, r.y, 4, r.h))
            gdev.text(surface, typed(label, self.t - i * 0.04), (r.x + 16, r.centery), 16,
                      gdev.GREEN if sel else gdev.GREEN_MID, "midleft", sel)
            left, right = self._arrows(r)
            acol = gdev.GREEN if sel else gdev.GREEN_DIM
            gdev.text(surface, "<", left.center, 16, acol, "center", True)
            gdev.text(surface, ">", right.center, 16, acol, "center", True)
            vcol = gdev.CYAN if self.settings[key] not in (False, "NORMAL") else gdev.GREEN
            gdev.text(surface, value, ((left.right + right.left) // 2, r.centery), 16, vcol, "center", True)
        key = self.rows[self.selected]
        help_text = "Return to the previous screen."
        for o in settings_mod.OPTIONS:
            if o[0] == key:
                help_text = o[3]
        hr = pygame.Rect(rects[0].x, rects[-1].bottom + 10, rects[0].w, 24)
        gdev.panel(surface, hr, alpha=160, border=None)
        gdev.text(surface, "> " + help_text, (hr.x + 10, hr.centery), 12, gdev.AMBER, "midleft")
        draw_footer(surface, "W/S  SELECT    A/D  CHANGE    ENTER  CYCLE    ESC  BACK")
        self.crt.overlay(surface)


# ------------------------------------------------------------------ how to play

CONTROLS = [
    ("PLAY", [("WASD", "move (8 directions)"), ("MOUSE", "aim"), ("LMB", "fire / attack"),
              ("R", "reload pistol"), ("SPACE", "dash (i-frames)"), ("1 2 3", "bow / pistol / katana"),
              ("WHEEL", "switch weapon"), ("HOLD F", "jack in / breach port"),
              ("F", "terminals: save + shop, hatch"), ("TAB", "decrypt log"), ("ESC", "pause")]),
    ("HACK :: DECODE", [("1 2 3", "pick option by slot"), ("LMB", "press option under pointer"),
                        ("ESC / RMB", "abort (clean, no TRACE)"), ("--", "you are rooted"),
                        ("--", "world runs at 30%")]),
    ("HACK :: PAYLOAD", [("Z", "EXTRACT: delete it, bank the data"), ("X", "TURN: it fights for you 15s"),
                         ("C", "OVERLOAD: EMP exposes nearby"), ("--", "1.2s window, default Z")]),
    ("MENUS", [("W/S  ARROWS", "move"), ("ENTER SPACE", "select"), ("A/D", "change setting"),
               ("ESC", "back"), ("MOUSE", "hover + click")]),
]

TIER1 = [("3", "E"), ("0", "O"), ("1", "I"), ("4", "A"), ("5", "S"), ("7", "T"), ("@", "A"), ("$", "S"),
         ("|", "I")]
TIER2 = [("|-|", "H"), ("/-\\", "A"), ("|<", "K"), ("|_", "L"), ("|2", "R"), ("|>", "P"), ("()", "O"),
         ("\\/", "V"), ("|\\/|", "M"), ("|=", "F"), ("|_|", "U"), ("(", "C")]


class HowToScreen(Screen):
    PAGES = ["CONTROLS", "KILL OR HACK", "GLYPHS"]

    def __init__(self, crt):
        Screen.__init__(self, crt)
        self.page = 0
        self.focus_back = False

    def tab_rects(self):
        w = 180
        x0 = gdev.INTERNAL_SIZE[0] // 2 - (w * 3 + 16) // 2
        return [pygame.Rect(x0 + i * (w + 8), 90, w, 22) for i in range(3)]

    def back_rect(self):
        return pygame.Rect(gdev.INTERNAL_SIZE[0] - 120, gdev.INTERNAL_SIZE[1] - 58, 100, 24)

    def handle_event(self, e):
        if is_key(e, LEFT_KEYS):
            self.page = (self.page - 1) % 3
            self.t = 0.0
        elif is_key(e, RIGHT_KEYS) or is_key(e, (pygame.K_TAB,)):
            self.page = (self.page + 1) % 3
            self.t = 0.0
        elif is_key(e, (pygame.K_1, pygame.K_2, pygame.K_3)):
            self.page = e.key - pygame.K_1
            self.t = 0.0
        elif is_key(e, (pygame.K_ESCAPE,)):
            return "back"
        elif is_key(e, UP_KEYS + DOWN_KEYS):
            self.focus_back = not self.focus_back
        elif is_key(e, OK_KEYS):
            if self.focus_back:
                return "back"
            self.page = (self.page + 1) % 3
            self.t = 0.0
        elif e.type == pygame.MOUSEMOTION:
            self.focus_back = self.back_rect().collidepoint(e.pos)
        elif is_click(e):
            if self.back_rect().collidepoint(e.pos):
                return "back"
            for i, r in enumerate(self.tab_rects()):
                if r.collidepoint(e.pos):
                    self.page = i
                    self.t = 0.0
        return None

    def draw(self, surface):
        self.crt.background(surface, self.frozen)
        draw_header(surface, "HOW TO PLAY")
        for i, r in enumerate(self.tab_rects()):
            if i == self.page:
                pygame.draw.rect(surface, gdev.GREEN, r)
                gdev.text(surface, "%d %s" % (i + 1, self.PAGES[i]), r.center, 13, gdev.BG, "center", True)
            else:
                gdev.panel(surface, r, alpha=235, border=gdev.GREEN_DIM)
                gdev.text(surface, "%d %s" % (i + 1, self.PAGES[i]), r.center, 13, gdev.GREEN_MID, "center")
        [self._controls, self._kill_or_hack, self._glyphs][self.page](surface)
        br = self.back_rect()
        if self.focus_back:
            pygame.draw.rect(surface, gdev.GREEN, br)
        else:
            gdev.panel(surface, br, alpha=235, border=gdev.GREEN_DIM)
        gdev.text(surface, "BACK", br.center, 14, gdev.BG if self.focus_back else gdev.GREEN_MID, "center", True)
        draw_footer(surface, "A/D  PAGE    1-3  JUMP    ESC  BACK")
        self.crt.overlay(surface)

    def _controls(self, surface):
        cols = [(18, [CONTROLS[0]]), (330, [CONTROLS[1], CONTROLS[2]]), (642, [CONTROLS[3]])]
        for x, groups in cols:
            y = 124
            for title, rows in groups:
                box = pygame.Rect(x, y, 300, 26 + len(rows) * 18)
                gdev.panel(surface, box, alpha=210, border=gdev.GREEN_DIM)
                pygame.draw.rect(surface, gdev.GREEN_DARK, (box.x + 1, box.y + 1, box.w - 2, 18))
                gdev.text(surface, title, (box.x + 8, box.y + 3), 12, gdev.MAGENTA if "HACK" in title
                          else gdev.GREEN, bold=True)
                act_x = box.x + 26 + max(gdev.text_size(k, 12)[0] for k, _a in rows if k != "--")
                for i, (k, act) in enumerate(rows):
                    ry = box.y + 24 + i * 18
                    if k != "--":
                        kw = gdev.text_size(k, 12)[0] + 8
                        pygame.draw.rect(surface, gdev.GREEN_DIM, (box.x + 8, ry - 1, kw, 15), 1)
                        gdev.text(surface, k, (box.x + 12, ry), 12, gdev.WHITE)
                        gdev.text(surface, act, (act_x, ry), 12, gdev.GREEN_MID)
                    else:
                        gdev.text(surface, "* " + act, (box.x + 12, ry), 12, gdev.GREEN_MID)
                y = box.bottom + 10
        x, y = 642, 248
        box = pygame.Rect(x, y, 300, 128)
        gdev.panel(surface, box, alpha=210, border=gdev.CYAN)
        gdev.text(surface, "THE RUN", (box.x + 8, box.y + 6), 12, gdev.CYAN, bold=True)
        lines = gdev.wrap("14 KEYS decode the name bar. Hack Key carriers, beat the five Daemons with a "
                          "ROOT BREACH, open the blast doors (4 / 8 Keys) and the Deep Core (12). "
                          "Clean Nodes save. Death: back to the last Node, half your Bits lost.", 284, 12)
        for i, ln in enumerate(lines):
            gdev.text(surface, ln, (box.x + 8, box.y + 26 + i * 15), 12, gdev.GREEN)

    def _kill_or_hack(self, surface):
        y = 160
        steps = [("FIREWALL", "shoot it down", gdev.AMBER), ("EXPOSED", "4s window, glitching", gdev.CYAN)]
        x = 40
        for i, (name, sub, col) in enumerate(steps):
            r = pygame.Rect(x, y, 170, 48)
            gdev.panel(surface, r, alpha=220, border=col, width=2)
            gdev.text(surface, name, (r.centerx, r.y + 8), 16, col, "midtop", True)
            gdev.text(surface, sub, (r.centerx, r.y + 28), 12, gdev.GREEN_MID, "midtop")
            if i == 0:
                _arrow(surface, (r.right + 6, r.centery), (r.right + 54, r.centery), gdev.GREEN_MID)
            x = r.right + 60
        ex = pygame.Rect(270, y, 170, 48)      # the EXPOSED box drawn above
        # branches
        kill = pygame.Rect(560, 120, 360, 68)
        hackr = pygame.Rect(560, 196, 360, 68)
        _arrow(surface, (ex.right + 6, ex.centery - 6), (kill.left - 6, kill.centery), gdev.RED)
        _arrow(surface, (ex.right + 6, ex.centery + 6), (hackr.left - 6, hackr.centery), gdev.CYAN)
        gdev.panel(surface, kill, alpha=220, border=gdev.RED, width=2)
        gdev.text(surface, "KILL  :: any hit after 0.35s grace", (kill.x + 10, kill.y + 8), 13, gdev.RED, bold=True)
        gdev.text(surface, "fast and safe. Pays BITS. The data is destroyed.", (kill.x + 10, kill.y + 28), 12,
                  gdev.GREEN_MID)
        gdev.text(surface, "Strip-then-stop: a kill is always on purpose.", (kill.x + 10, kill.y + 46), 12,
                  gdev.GREEN_MID)
        gdev.panel(surface, hackr, alpha=220, border=gdev.CYAN, width=2)
        gdev.text(surface, "HACK  :: stand in the cyan ring, hold F", (hackr.x + 10, hackr.y + 8), 13,
                  gdev.CYAN, bold=True)
        gdev.text(surface, "slow, rooted, risky. Pays INTEL and KEYS.", (hackr.x + 10, hackr.y + 28), 12,
                  gdev.GREEN_MID)
        gdev.text(surface, "Only hacking moves the story forward.", (hackr.x + 10, hackr.y + 46), 12,
                  gdev.GREEN_MID)
        # the hack, step by step
        box = pygame.Rect(40, 276, 880, 150)
        gdev.panel(surface, box, alpha=215, border=gdev.GREEN_DIM)
        gdev.text(surface, "INSIDE THE HACK", (box.x + 12, box.y + 8), 13, gdev.MAGENTA, bold=True)
        lines = [
            ("DECODE", "The packet is leetspeak. Target words glow magenta, one at a time. Pick the right"),
            ("", "reading from 3 buttons (click or 1-3). Every wrong button is a real glyph misreading."),
            ("TRACE", "Wrong pick +35 (hasty pick +50). 100 = BACKTRACE: weapon jams, minion hardens."),
            ("TIMER", "Runs in real time while the world crawls at 30%. Other minions keep coming."),
            ("PAYLOAD", "Z EXTRACT  /  X TURN  /  C OVERLOAD, 1.2s to choose. Zero mistakes = CLEAN."),
        ]
        for i, (k, s) in enumerate(lines):
            gdev.text(surface, k, (box.x + 12, box.y + 32 + i * 22), 12, gdev.GREEN, bold=True)
            gdev.text(surface, s, (box.x + 90, box.y + 32 + i * 22), 12, gdev.GREEN_MID)

    def _glyphs(self, surface):
        gdev.text(surface, "TIER 1  single character", (40, 124), 13, gdev.GREEN, bold=True)
        self._glyph_row(surface, TIER1, 40, 144)
        gdev.text(surface, "TIER 2  multi-character clusters", (40, 206), 13, gdev.GREEN, bold=True)
        self._glyph_row(surface, TIER2[:6], 40, 226)
        self._glyph_row(surface, TIER2[6:], 40, 278)
        box = pygame.Rect(40, 340, 880, 82)
        gdev.panel(surface, box, alpha=215, border=gdev.GREEN_DIM)
        gdev.text(surface, "READING RULES", (box.x + 12, box.y + 8), 13, gdev.MAGENTA, bold=True)
        gdev.text(surface, "Each cluster is boxed on screen: never split a box. Letters with no glyph "
                  "(N, G, W, Y...) stay plain.", (box.x + 12, box.y + 30), 12, gdev.GREEN_MID)
        gdev.text(surface, "Decoys misread one glyph:  |2 read as IZ,  |_ read as U,  ( read as G.  "
                  "Read the glyph, not the sentence.", (box.x + 12, box.y + 50), 12, gdev.GREEN_MID)

    def _glyph_row(self, surface, pairs, x, y):
        for g, p in pairs:
            gw = max(30, gdev.text_size(g, 18)[0] + 12)
            cell = pygame.Rect(x, y, gw + 44, 40)
            gdev.panel(surface, cell, alpha=200, border=gdev.GREEN_DIM)
            box = pygame.Rect(x + 4, y + 5, gw, 30)
            pygame.draw.rect(surface, (40, 10, 34), box)
            pygame.draw.rect(surface, gdev.MAGENTA, box, 1)
            gdev.text(surface, g, box.center, 18, gdev.MAGENTA, "center", True)
            gdev.text(surface, p, (box.right + 20, box.centery), 18, gdev.CYAN, "center", True)
            x = cell.right + 8


def _arrow(surface, a, b, color):
    pygame.draw.line(surface, color, a, b, 2)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    for da in (2.6, -2.6):
        pygame.draw.line(surface, color, b, (b[0] + 8 * math.cos(ang + da), b[1] + 8 * math.sin(ang + da)), 2)


# ------------------------------------------------------------------ intel log

class IntelLogScreen(Screen):
    """The DECRYPT screen (doc 8.2): name bar, decoded fragments grouped by act (left list, right
    reader) and the glyph codex (bottom). Game sets .intel (hack.IntelLog or None), .codex
    (hack.Codex or None) and .run (Run or None, for the name bar) before opening."""
    ROWS = 8
    ACT_ORDER = {"P": 0, "I": 1, "II": 2, "III": 3, "": 4}

    def __init__(self, crt):
        Screen.__init__(self, crt)
        self.intel = None
        self.codex = None
        self.run = None
        self.selected = 0
        self.scroll = 0
        self.hover_back = False

    def entries(self):
        if self.intel is None:
            return []
        ents = list(self.intel.entries)
        ents.sort(key=lambda e: self.ACT_ORDER.get(str(e.get("act", "")), 4))     # grouped by act (stable)
        return ents

    def back_rect(self):
        return pygame.Rect(gdev.INTERNAL_SIZE[0] - 120, 22, 100, 24)

    def list_rects(self):
        out = []
        for i in range(min(self.ROWS, len(self.entries()) - self.scroll)):
            out.append((self.scroll + i, pygame.Rect(18, 118 + i * 28, 320, 24)))
        return out

    def handle_event(self, e):
        n = len(self.entries())
        if is_key(e, (pygame.K_ESCAPE, pygame.K_TAB)) or is_click(e, 3):
            return "back"
        if e.type == pygame.MOUSEMOTION:
            self.hover_back = self.back_rect().collidepoint(e.pos)
        if is_click(e) and self.back_rect().collidepoint(e.pos):
            return "back"
        if n == 0:
            if is_key(e, OK_KEYS) or is_click(e):
                return "back"
            return None
        if is_key(e, UP_KEYS):
            self.selected = (self.selected - 1) % n
        elif is_key(e, DOWN_KEYS):
            self.selected = (self.selected + 1) % n
        elif e.type == pygame.MOUSEWHEEL:
            self.selected = max(0, min(n - 1, self.selected - e.y))
        elif e.type == pygame.MOUSEMOTION:
            for i, r in self.list_rects():
                if r.collidepoint(e.pos):
                    self.selected = i
        elif is_click(e):
            for i, r in self.list_rects():
                if r.collidepoint(e.pos):
                    self.selected = i
        elif is_key(e, OK_KEYS):
            return "back"
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + self.ROWS:
            self.scroll = self.selected - self.ROWS + 1
        return None

    def draw(self, surface):
        self.crt.background(surface, self.frozen)
        ents = self.entries()
        n_red = len([e for e in ents if e.get("redacted")])
        draw_header(surface, "DECRYPT", "%d fragment%s decoded  ::  %d redacted" %
                    (len(ents), "" if len(ents) == 1 else "s", n_red))
        if self.run is not None:
            handle = self.run.handle if self.run.progression.key14_played else None
            hud.draw_name_bar(surface, self.run, (surface.get_width() // 2, 88), 12, key_anim={}, t=self.t,
                              handle=handle)
        if not ents:
            r = pygame.Rect(180, 130, 600, 130)
            gdev.panel(surface, r, alpha=215, border=gdev.GREEN_DIM)
            gdev.text(surface, "NO INTEL DECODED YET", (r.centerx, r.y + 30), 20, gdev.GREEN, "midtop", True)
            gdev.text(surface, "Strip a minion's firewall, then stand in the cyan ring and hold F.",
                      (r.centerx, r.y + 64), 12, gdev.GREEN_MID, "midtop")
            gdev.text(surface, "What you decode is saved here, word for word.", (r.centerx, r.y + 82), 12,
                      gdev.GREEN_DIM, "midtop")
        else:
            self.selected = min(self.selected, len(ents) - 1)
            for i, r in self.list_rects():
                e = ents[i]
                sel = i == self.selected
                if sel:
                    pygame.draw.rect(surface, gdev.GREEN, r)
                else:
                    gdev.panel(surface, r, alpha=190, border=gdev.GREEN_DIM)
                col = gdev.BG if sel else gdev.GREEN_MID
                act = str(e.get("act", "")) or "-"
                gdev.text(surface, "%-3s" % act, (r.x + 8, r.centery), 12, col, "midleft", True)
                title = str(e.get("title", e.get("id", "?")))
                if gdev.text_size(title, 12)[0] > 200:
                    while gdev.text_size(title + "..", 12)[0] > 200:
                        title = title[:-1]
                    title += ".."
                gdev.text(surface, title, (r.x + 34, r.centery), 12, col, "midleft")
                tag = "REDACTED" if e.get("redacted") else str(e.get("source", "")).upper()
                if e.get("redacted"):
                    tcol = (110, 0, 90) if sel else gdev.MAGENTA
                else:
                    tcol = gdev.BG if sel else gdev.CYAN
                gdev.text(surface, tag, (r.right - 8, r.centery), 12, tcol, "midright", True)
            if len(ents) > self.ROWS:
                gdev.text(surface, "%d-%d of %d" % (self.scroll + 1, min(len(ents), self.scroll + self.ROWS),
                                                    len(ents)), (178, 118 + self.ROWS * 28 - 4), 12,
                          gdev.GREEN_DIM, "midtop")
            self._reader(surface, ents[self.selected])
        self._codex(surface)
        br = self.back_rect()
        if self.hover_back:
            pygame.draw.rect(surface, gdev.GREEN, br)
        else:
            gdev.panel(surface, br, alpha=235, border=gdev.GREEN_DIM)
        gdev.text(surface, "BACK", br.center, 14, gdev.BG if self.hover_back else gdev.GREEN_MID, "center", True)
        draw_footer(surface, "W/S  SELECT    WHEEL  SCROLL    ESC / RMB  BACK")
        self.crt.overlay(surface)

    def _reader(self, surface, e):
        r = pygame.Rect(352, 118, 590, 226)
        gdev.panel(surface, r, alpha=225, border=gdev.CYAN if not e.get("redacted") else gdev.MAGENTA)
        pygame.draw.rect(surface, gdev.GREEN_DARK, (r.x + 1, r.y + 1, r.w - 2, 20))
        gdev.text(surface, "%s :: %s :: %s" % (e.get("id", "?"), e.get("source", "?"), e.get("tier", "")),
                  (r.x + 8, r.y + 4), 12, gdev.GREEN)
        gdev.text(surface, str(e.get("title", "")), (r.x + 12, r.y + 30), 16, gdev.WHITE, bold=True)
        y = r.y + 58
        for line in gdev.wrap(str(e.get("text", "")), r.w - 24, 14):
            self._draw_redacted_line(surface, line, (r.x + 12, y))
            y += 20
            if y > r.bottom - 20:
                break

    def _draw_redacted_line(self, surface, line, pos):
        # "████" runs are drawn as magenta bars; everything else in clean cyan.
        x, y = pos
        parts = line.split("█")
        for k, part in enumerate(parts):
            if part:
                rr = gdev.text(surface, part, (x, y), 14, gdev.CYAN)
                x = rr.right
            if k < len(parts) - 1:
                cw = gdev.text_size("M", 14)[0]
                pygame.draw.rect(surface, (120, 30, 100), (x, y + 3, cw, 13))
                x += cw

    def _codex(self, surface):
        r = pygame.Rect(18, 356, 924, 92)
        gdev.panel(surface, r, alpha=215, border=gdev.GREEN_DIM)
        gdev.text(surface, "GLYPH CODEX", (r.x + 10, r.y + 6), 12, gdev.MAGENTA, bold=True)
        rows = self.codex.rows() if self.codex is not None else []
        if not rows:
            gdev.text(surface, "no glyphs tested yet. Every pick you make is recorded here: right / wrong per glyph.",
                      (r.x + 10, r.y + 40), 12, gdev.GREEN_DIM)
            return
        head = "glyph  letter  right/wrong"
        worst = self.codex.worst() if hasattr(self.codex, "worst") else None
        top = self.codex.top_misread(worst) if worst and hasattr(self.codex, "top_misread") else None
        if top:
            head = "you read %s as %s (%dx)  ::  %s" % (worst, top[0], top[1], head)
        gdev.text(surface, head, (r.right - 10, r.y + 6), 12, gdev.GREEN_DIM, "topright")
        x, y = r.x + 10, r.y + 26
        for glyph, letter, hits, misses in rows:
            gw = gdev.text_size(glyph, 13, True)[0]
            w = gw + gdev.text_size(" %s  %d/%d" % (letter, hits, misses), 12)[0] + 22
            if x + w > r.right - 8:
                x = r.x + 10
                y += 30
                if y > r.bottom - 24:
                    break
            cell = pygame.Rect(x, y, w, 24)
            bad = misses > hits
            pygame.draw.rect(surface, (40, 10, 34) if bad else gdev.GREEN_DARK, cell)
            pygame.draw.rect(surface, gdev.MAGENTA if bad else gdev.GREEN_DIM, cell, 1)
            tr = gdev.text(surface, glyph, (cell.x + 6, cell.centery), 13, gdev.MAGENTA, "midleft", True)
            tr = gdev.text(surface, " " + letter, (tr.right + 2, cell.centery), 13, gdev.CYAN, "midleft", True)
            gdev.text(surface, "  %d/%d" % (hits, misses), (tr.right, cell.centery), 12,
                      gdev.RED if bad else gdev.GREEN_MID, "midleft")
            x = cell.right + 6


# ------------------------------------------------------------------ enter handle

HANDLE_MIN, HANDLE_MAX = 3, 12
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class HandleScreen(Screen):
    """ENTER HANDLE (doc 8.1): the game's only text field. Letters A-Z, 3-12 long. Typing,
    Backspace, Enter; or click the on-screen letter grid (DEL / OK). Esc goes back.
    Returns "handle_ok" (read .value) or "back"."""
    COLS = 13

    def __init__(self, crt):
        Screen.__init__(self, crt)
        self.value = ""
        self.focus = 0              # index into the grid: 0-25 letters, 26 DEL, 27 OK
        self.error_t = 0.0

    def reset(self):
        self.value = ""
        self.focus = 0
        self.error_t = 0.0

    def ok(self):
        return HANDLE_MIN <= len(self.value) <= HANDLE_MAX

    def cell_rects(self):
        w, h, gap = 40, 30, 6
        total = self.COLS * w + (self.COLS - 1) * gap
        x0 = gdev.INTERNAL_SIZE[0] // 2 - total // 2
        out = []
        for i in range(26):
            out.append(pygame.Rect(x0 + (i % self.COLS) * (w + gap), 268 + (i // self.COLS) * (h + gap), w, h))
        out.append(pygame.Rect(x0, 268 + 2 * (h + gap) + 6, 3 * w + 2 * gap, h))
        out.append(pygame.Rect(x0 + total - (3 * w + 2 * gap), 268 + 2 * (h + gap) + 6, 3 * w + 2 * gap, h))
        return out

    def _press(self, i):
        if i < 26:
            if len(self.value) < HANDLE_MAX:
                self.value += LETTERS[i]
        elif i == 26:
            self.value = self.value[:-1]
        elif self.ok():
            return "handle_ok"
        else:
            self.error_t = 1.2
        return None

    def handle_event(self, e):
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                return "back"
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                return self._press(27)
            if e.key == pygame.K_BACKSPACE:
                return self._press(26)
            if pygame.K_a <= e.key <= pygame.K_z:
                return self._press(e.key - pygame.K_a)
            if e.key == pygame.K_LEFT:
                self.focus = (self.focus - 1) % 28
            elif e.key == pygame.K_RIGHT:
                self.focus = (self.focus + 1) % 28
            elif e.key == pygame.K_UP:
                self.focus = max(0, self.focus - self.COLS)
            elif e.key == pygame.K_DOWN:
                self.focus = min(27, self.focus + self.COLS)
            elif e.key == pygame.K_SPACE:
                return self._press(self.focus)
        elif e.type == pygame.MOUSEMOTION:
            for i, r in enumerate(self.cell_rects()):
                if r.collidepoint(e.pos):
                    self.focus = i
        elif is_click(e):
            for i, r in enumerate(self.cell_rects()):
                if r.collidepoint(e.pos):
                    self.focus = i
                    return self._press(i)
        elif is_click(e, 3):
            return self._press(26)
        return None

    def update(self, dt):
        Screen.update(self, dt)
        self.error_t = max(0.0, self.error_t - dt)

    def draw(self, surface):
        self.crt.background(surface)
        w = surface.get_width()
        draw_header(surface, "MAINTENANCE LOG", "Kernel needs a name for the duty roster. It will only ask once.")
        box = pygame.Rect(w // 2 - 260, 112, 520, 132)
        gdev.panel(surface, box, alpha=225, border=gdev.GREEN_DIM)
        gdev.text(surface, typed("> ENTER HANDLE_", self.t, 40), (box.x + 16, box.y + 14), 16, gdev.GREEN, bold=True)
        field = pygame.Rect(box.x + 16, box.y + 44, box.w - 32, 44)
        pygame.draw.rect(surface, gdev.BG, field)
        pygame.draw.rect(surface, gdev.CYAN if self.ok() else gdev.GREEN_MID, field, 1)
        tr = gdev.text(surface, self.value, (field.x + 14, field.centery), 28, gdev.CYAN, "midleft", True)
        if int(self.t * 2.5) % 2 == 0:
            pygame.draw.rect(surface, gdev.CYAN, (tr.right + 4, field.y + 10, 12, field.h - 20))
        n = len(self.value)
        col = gdev.GREEN_MID if self.ok() else (gdev.RED if self.error_t > 0 else gdev.AMBER)
        gdev.text(surface, "%d/%d  ::  letters A-Z, %d to %d long" % (n, HANDLE_MAX, HANDLE_MIN, HANDLE_MAX),
                  (box.x + 16, box.y + 98), 12, col)
        gdev.text(surface, "ENTER to confirm", (box.right - 16, box.y + 98), 12,
                  gdev.CYAN if self.ok() else gdev.GREEN_DIM, "topright")
        for i, r in enumerate(self.cell_rects()):
            label = LETTERS[i] if i < 26 else ("DEL" if i == 26 else "OK")
            focus = i == self.focus
            enabled = i != 27 or self.ok()
            if focus:
                pygame.draw.rect(surface, gdev.GREEN if enabled else gdev.GREEN_DIM, r)
                gdev.text(surface, label, r.center, 16, gdev.BG, "center", True)
            else:
                gdev.panel(surface, r, alpha=225, border=gdev.GREEN_DIM if enabled else (20, 50, 30))
                gdev.text(surface, label, r.center, 16, gdev.GREEN_MID if enabled else gdev.GREEN_DIM, "center")
        draw_footer(surface, "TYPE A-Z    BACKSPACE  DELETE    ENTER  CONFIRM    ESC  BACK")
        self.crt.overlay(surface)


# ------------------------------------------------------------------ terminal story scenes

SCENE_COLORS = {"green": gdev.GREEN, "cyan": gdev.CYAN, "magenta": gdev.MAGENTA, "amber": gdev.AMBER,
                "red": gdev.RED, "white": gdev.WHITE}


class TerminalScene(Screen):
    """A paged terminal story scene over the frozen game frame: Core Dumps, Key 14, Spool's
    cache, the Deep Core descent. Game calls load(spec, run) and opens it (state SCENE).

    spec = {"kind": "core_dump"|"key14"|"cache"|"descent"|..., "title": str,
            "pages": [{"head": str, "lines": [str], "color": "green"|"cyan"|"magenta"|"amber"|"red",
                       "kind": "text"|"stats"|"key"|"exploit"|"patch"|"handle"}],
            "key_index": int|None (the "key" page animates this name-bar cluster),
            "on_close": "resume"|"enter_deep_core"}
    Enter / Space / LMB: finish typing, then next page; the last page returns "scene_done".
    Esc / RMB: jump to the last page (then close)."""
    INPUT_DELAY_S = 0.35
    CPS = 70.0

    def __init__(self, crt):
        Screen.__init__(self, crt)
        self.spec = {"title": "", "pages": []}
        self.run = None
        self.page = 0
        self.page_t = 0.0

    def load(self, spec, run):
        self.spec = spec or {"title": "", "pages": []}
        if not self.spec.get("pages"):
            self.spec["pages"] = [{"head": "", "lines": ["..."]}]
        self.run = run
        self.page = 0
        self.page_t = 0.0

    def open(self):
        Screen.open(self)
        self.page_t = 0.0

    def _page(self):
        return self.spec["pages"][self.page]

    def _text_len(self):
        p = self._page()
        return len(p.get("head", "")) + sum(len(str(x)) for x in p.get("lines", []))

    def _typing_done(self):
        return self.page_t * self.CPS >= self._text_len() + 2

    def _next(self):
        if not self._typing_done():
            self.page_t = (self._text_len() + 3) / self.CPS
            return None
        if self.page + 1 < len(self.spec["pages"]):
            self.page += 1
            self.page_t = 0.0
            return None
        return "scene_done"

    def handle_event(self, e):
        if self.t < self.INPUT_DELAY_S and e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            return None
        if is_key(e, OK_KEYS) or is_click(e):
            return self._next()
        if is_key(e, (pygame.K_ESCAPE,)) or is_click(e, 3):
            if self.page + 1 < len(self.spec["pages"]):
                self.page = len(self.spec["pages"]) - 1
                self.page_t = 0.0
                return None
            return "scene_done"
        return None

    def update(self, dt):
        Screen.update(self, dt)
        self.page_t += dt

    def draw(self, surface):
        self.crt.background(surface, self.frozen, 225)
        w, h = surface.get_size()
        win = pygame.Rect(w // 2 - 380, 74, 760, 340)
        gdev.panel(surface, win, alpha=235, border=gdev.CYAN)
        pygame.draw.rect(surface, gdev.GREEN_DARK, (win.x + 1, win.y + 1, win.w - 2, 22))
        gdev.text(surface, str(self.spec.get("title", "")), (win.x + 10, win.y + 4), 13, gdev.GREEN, bold=True)
        n = len(self.spec["pages"])
        gdev.text(surface, "%d/%d" % (self.page + 1, n), (win.right - 10, win.y + 4), 13, gdev.GREEN_MID,
                  "topright")
        p = self._page()
        col = SCENE_COLORS.get(p.get("color", "green"), gdev.GREEN)
        budget = int(self.page_t * self.CPS)
        head = str(p.get("head", ""))
        y = win.y + 40
        gdev.text(surface, typed(head, self.page_t, self.CPS), (win.x + 24, y), 20, col, bold=True)
        budget -= len(head)
        y += 36
        kind = p.get("kind", "text")
        if kind == "key" and self.run is not None:
            idx = self.spec.get("key_index")
            anim = {idx: max(0.0, self.page_t - 0.3)} if idx is not None else {}
            keys = set(self.run.keys)
            if idx is not None and self.page_t < 0.3:
                keys.discard(idx)
            hud.draw_name_bar(surface, self.run, (w // 2, y + 10), 16, keys=keys, key_anim=anim, t=self.t)
            y += 56
            if idx is not None:
                gdev.text(surface, "%s -> %s" % (hud.NAME_CLUSTERS[idx], hud.NAME_PLAIN[idx]), (w // 2, y), 18,
                          gdev.CYAN, "midtop", True)
                y += 30
        elif kind == "handle" and self.run is not None:
            hud.draw_name_bar(surface, self.run, (w // 2, y + 10), 18, handle=self.run.handle, t=self.t)
            y += 60
        elif kind == "patch":
            card = pygame.Rect(win.x + 24, y - 6, win.w - 48, 22 + 22 * max(1, len(p.get("lines", []))))
            gdev.panel(surface, card, fill=(30, 6, 26), alpha=220, border=gdev.MAGENTA)
            y += 4
        for line in p.get("lines", []):
            line = str(line)
            if budget <= 0:
                break
            shown = line[:max(0, budget)]
            budget -= len(line)
            for sub in gdev.wrap(shown, win.w - 60, 16):
                gdev.text(surface, sub, (win.x + 30, y), 16, gdev.WHITE if kind == "text" else col)
                y += 22
        if self._typing_done() and int(self.t * 2.5) % 2 == 0:
            gdev.text(surface, "> NEXT" if self.page + 1 < n else "> CLOSE", (win.right - 16, win.bottom - 24), 14,
                      gdev.CYAN, "topright", True)
        gdev.draw_scanlines(surface, 26, win)
        draw_footer(surface, "ENTER / CLICK  NEXT    ESC  SKIP TO END")
        self.crt.overlay(surface)


# ------------------------------------------------------------------ ending + credits

class EndingScreen(Screen):
    """The ending (doc 8.4) then the credits with real stats. Game calls load(spec, handle, keys).
    spec = {"ending": "PURGE"|"PATCH", "title", "lines": [str], "last_line": str,
            "credits": [(label, value)], "stat_line": str}
    Enter / click: finish typing, then ENDING -> CREDITS -> "ending_done" (main menu)."""
    INPUT_DELAY_S = 0.6
    CPS = 45.0

    def __init__(self, crt):
        Screen.__init__(self, crt)
        self.spec = {"ending": "PURGE", "title": "", "lines": [], "last_line": "", "credits": [], "stat_line": ""}
        self.handle = ""
        self.keys = set()
        self.page = 0           # 0 ending text, 1 credits
        self.page_t = 0.0

    def load(self, spec, handle, keys):
        self.spec = spec
        self.handle = handle
        self.keys = set(keys)
        self.page = 0
        self.page_t = 0.0

    def _text_len(self):
        return sum(len(x) for x in self.spec.get("lines", [])) + len(self.spec.get("last_line", ""))

    def handle_event(self, e):
        if self.t < self.INPUT_DELAY_S and e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            return None
        if is_key(e, OK_KEYS + (pygame.K_ESCAPE,)) or is_click(e):
            if self.page == 0 and self.page_t * self.CPS < self._text_len() + 4:
                self.page_t = (self._text_len() + 5) / self.CPS
                return None
            if self.page == 0:
                self.page = 1
                self.page_t = 0.0
                return None
            return "ending_done"
        return None

    def update(self, dt):
        Screen.update(self, dt)
        self.page_t += dt

    def draw(self, surface):
        self.crt.background(surface)
        w, h = surface.get_size()
        patch = self.spec.get("ending") == "PATCH"
        col = gdev.CYAN if patch else gdev.AMBER
        shade(surface, (0, 30, w, 96), 190)
        draw_glow_text(surface, "%s" % self.spec.get("title", ""), (w // 2, 62), 36, col,
                       glow=(20, 90, 110) if patch else (90, 60, 10))
        gdev.text(surface, "ENDING :: %s" % self.spec.get("ending", ""), (w // 2, 92), 13, gdev.GREEN_MID, "midtop")
        if self.page == 0:
            box = pygame.Rect(w // 2 - 360, 140, 720, 250)
            gdev.panel(surface, box, alpha=225, border=col)
            budget = int(self.page_t * self.CPS)
            y = box.y + 24
            for line in self.spec.get("lines", []):
                if budget <= 0:
                    break
                for sub in gdev.wrap(line[:budget], box.w - 48, 16):
                    gdev.text(surface, sub, (box.x + 24, y), 16, gdev.WHITE)
                    y += 24
                budget -= len(line)
                y += 6
            last = self.spec.get("last_line", "")
            if budget > 0 and last:
                gdev.text(surface, last[:budget], (w // 2, box.bottom - 40), 22, gdev.MAGENTA, "midtop", True)
            footer = "ENTER  CREDITS"
        else:
            hud.draw_name_bar(surface, None, (w // 2, 116), 16, keys=self.keys, key_anim={}, t=self.t,
                              handle=self.handle if patch or len(self.keys) >= 14 else None)
            box = pygame.Rect(w // 2 - 230, 152, 460, 30 + 24 * len(self.spec.get("credits", [])))
            gdev.panel(surface, box, alpha=225, border=gdev.GREEN_DIM)
            pygame.draw.rect(surface, gdev.GREEN_DARK, (box.x + 1, box.y + 1, box.w - 2, 18))
            gdev.text(surface, "CREDITS :: YOUR THREAT MODEL", (box.x + 10, box.y + 3), 12, gdev.GREEN, bold=True)
            for i, (k, v) in enumerate(self.spec.get("credits", [])):
                if self.page_t < 0.2 + i * 0.12:
                    break
                y = box.y + 28 + i * 24
                gdev.text(surface, k, (box.x + 16, y), 16, gdev.GREEN_MID)
                gdev.text(surface, str(v), (box.right - 16, y), 16, gdev.CYAN, "topright", True)
            gdev.text(surface, self.spec.get("stat_line", ""), (w // 2, box.bottom + 14), 13, gdev.MAGENTA, "midtop")
            footer = "ENTER  MAIN MENU"
        draw_footer(surface, footer)
        self.crt.overlay(surface)
