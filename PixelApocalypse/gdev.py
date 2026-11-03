"""gdev: small engine helpers shared by every module.

Paths are always resolved from the code folder (BASE_DIR), never from the
current working directory, so the game runs no matter where it is started.

Coordinate spaces used across the game:
  window   -- real window pixels (1920x1000 when windowed). Only gdev sees these.
  internal -- the 960x500 surface everything is drawn on. Mouse positions are
              converted to internal pixels by gdev.window_to_internal() (the
              Game does this for every mouse event before anyone else sees it).
  world    -- map pixels (0..10000). Convert with world.Camera.
"""
import json
import os
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    # The vendored pygame lives next to the code files.
    sys.path.insert(0, BASE_DIR)

# The vendored pygame is the official build for CPython 3.9 (macOS ships it as
# /usr/bin/python3). Started with another Python (e.g. python3 = 3.14, or the
# editor's Run button), relaunch the same script with the system 3.9 instead.
PYGAME_PYTHON = "/usr/bin/python3"
PYGAME_VERSION = (3, 9)
if sys.version_info[:2] != PYGAME_VERSION:
    script = sys.argv[0] if sys.argv else ""
    if os.path.isfile(script) and os.path.exists(PYGAME_PYTHON) and not os.environ.get("PIXEL_APOCALYPSE_REEXEC"):
        os.environ["PIXEL_APOCALYPSE_REEXEC"] = "1"
        os.execv(PYGAME_PYTHON, [PYGAME_PYTHON] + sys.argv)
    raise ImportError("Pixel Apocalypse's bundled pygame needs Python %d.%d; run it with %s (or play.sh), "
                      "not Python %d.%d." % (PYGAME_VERSION + (PYGAME_PYTHON,) + sys.version_info[:2]))

import pygame  # noqa: E402

pgmod = pygame          # the owner's old name for the module, kept working

WINDOW_SIZE = (1920, 1000)
INTERNAL_SIZE = (960, 500)
SCALE = 2

screen = None           # the real window surface (set by init)
clock = None            # pygame.time.Clock (set by init)
fps = 60
fullscreen = False
_present_rect = pygame.Rect(0, 0, WINDOW_SIZE[0], WINDOW_SIZE[1])  # where the internal surface lands

# Palette (doc 3: world greens, threats magenta/red, hacked things cyan)
BG = (4, 16, 9)
BG_PANEL = (7, 26, 14)
GREEN = (80, 255, 120)
GREEN_MID = (40, 170, 80)
GREEN_DIM = (22, 92, 46)
GREEN_DARK = (10, 44, 22)
CYAN = (90, 240, 255)
MAGENTA = (255, 60, 200)
RED = (255, 64, 72)
AMBER = (255, 190, 70)
WHITE = (235, 255, 240)
BLACK = (0, 0, 0)


def path(*parts):
    """Absolute path of a file inside the code folder."""
    return os.path.join(BASE_DIR, *parts)


# ---------------------------------------------------------------- display

def init(gfps, gname, start_fullscreen=False):
    """Start pygame and open the window. Sets gdev.screen, gdev.clock, gdev.fps."""
    global screen, clock, fps
    pygame.init()
    fps = int(gfps)
    clock = pygame.time.Clock()
    pygame.display.set_caption(str(gname))
    set_fullscreen(start_fullscreen)
    return screen


def _windowed_size():
    # 1920x1000 (exact 2x) unless the desktop is smaller; then the biggest
    # same-aspect window that fits, so the window is never cut off.
    if os.environ.get("SDL_VIDEODRIVER") == "dummy":
        return WINDOW_SIZE
    try:
        dw, dh = pygame.display.get_desktop_sizes()[0]
    except Exception:
        return WINDOW_SIZE
    if dw >= WINDOW_SIZE[0] and dh >= WINDOW_SIZE[1] + 60:
        return WINDOW_SIZE
    k = min(dw / float(INTERNAL_SIZE[0]), (dh - 80) / float(INTERNAL_SIZE[1]))
    k = max(1.0, k)
    return (int(INTERNAL_SIZE[0] * k), int(INTERNAL_SIZE[1] * k))


def set_fullscreen(on):
    """Switch between the window and fullscreen. Safe to call any time."""
    global screen, fullscreen, _present_rect
    fullscreen = bool(on)
    if fullscreen and os.environ.get("SDL_VIDEODRIVER") != "dummy":
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        screen = pygame.display.set_mode(_windowed_size())
    _present_rect = _fit_rect(screen.get_size())
    return screen


def _fit_rect(size):
    # Largest rect with the internal aspect ratio, centred (letterbox).
    w, h = size
    k = min(w / float(INTERNAL_SIZE[0]), h / float(INTERNAL_SIZE[1]))
    if k >= 1:
        ik = int(k)
        if ik >= 1 and (k - ik) < 0.02:
            k = ik            # prefer whole-number scaling for crisp pixels
    rw, rh = int(INTERNAL_SIZE[0] * k), int(INTERNAL_SIZE[1] * k)
    return pygame.Rect((w - rw) // 2, (h - rh) // 2, rw, rh)


def present(internal_surface):
    """Scale the internal surface (nearest neighbour) into the window and flip."""
    if screen is None:
        return
    r = _present_rect
    if r.size == screen.get_size() and r.size == (internal_surface.get_width() * 2,
                                                   internal_surface.get_height() * 2):
        pygame.transform.scale(internal_surface, r.size, screen)
    else:
        screen.fill(BLACK)
        screen.blit(pygame.transform.scale(internal_surface, r.size), r.topleft)
    pygame.display.flip()


def window_to_internal(pos):
    """Window pixel -> internal-surface pixel (handles 2x scale and letterbox)."""
    r = _present_rect
    x = (pos[0] - r.x) * INTERNAL_SIZE[0] / float(max(1, r.w))
    y = (pos[1] - r.y) * INTERNAL_SIZE[1] / float(max(1, r.h))
    return (int(x), int(y))


def internal_to_window(pos):
    """Internal-surface pixel -> window pixel (used by scripted tests)."""
    r = _present_rect
    return (int(r.x + pos[0] * r.w / float(INTERNAL_SIZE[0]) + 0.5),
            int(r.y + pos[1] * r.h / float(INTERNAL_SIZE[1]) + 0.5))


# ---------------------------------------------------------------- assets

class asset:
    """The owner's original API: gdev.asset.img.load("player_assets/player_back", "png")."""
    class img:
        @staticmethod
        def load(img, type):
            return pygame.image.load(path(str(img) + "." + type))


_image_cache = {}


def image(rel_path):
    """Load an image once (convert_alpha) and cache it. Path is relative to the code folder.
    Returns None (and warns once) if the file is missing."""
    if rel_path in _image_cache:
        return _image_cache[rel_path]
    surf = None
    try:
        surf = pygame.image.load(path(rel_path))
        if pygame.display.get_surface() is not None:
            surf = surf.convert_alpha()
    except (pygame.error, FileNotFoundError) as err:
        print("[gdev] missing image %s (%s)" % (rel_path, err))
    _image_cache[rel_path] = surf
    return surf


_font_cache = {}
FONT_NAMES = "menlo,sfnsmono,monaco,courier new,dejavusansmono,consolas,monospace"


def font(size, bold=False):
    """Monospace font at a pixel size (cached)."""
    key = (int(size), bool(bold))
    f = _font_cache.get(key)
    if f is None:
        if not pygame.font.get_init():
            pygame.font.init()
        try:
            f = pygame.font.SysFont(FONT_NAMES, int(size), bold=bold)
        except Exception:
            f = None
        if f is None:
            f = pygame.font.Font(None, int(size * 1.3))
        _font_cache[key] = f
    return f


_text_cache = {}


def render_text(s, size=16, color=GREEN, bold=False):
    """Rendered text surface (cached; the cache is cleared when it grows large)."""
    key = (s, size, tuple(color), bold)
    surf = _text_cache.get(key)
    if surf is None:
        if len(_text_cache) > 3000:
            _text_cache.clear()
        surf = font(size, bold).render(str(s), True, color)
        _text_cache[key] = surf
    return surf


def text(surface, s, pos, size=16, color=GREEN, align="topleft", bold=False, alpha=None):
    """Draw text. align is any pygame.Rect anchor ("topleft", "center", "midtop",
    "topright", "midleft", ...). Returns the Rect drawn."""
    surf = render_text(s, size, color, bold)
    r = surf.get_rect()
    setattr(r, align, (int(pos[0]), int(pos[1])))
    if alpha is not None:
        surf = surf.copy()
        surf.set_alpha(int(alpha))
    surface.blit(surf, r)
    return r


def text_size(s, size=16, bold=False):
    return font(size, bold).size(str(s))


def wrap(s, width_px, size=16):
    """Split text into lines that fit width_px."""
    lines = []
    for para in str(s).split("\n"):
        words = para.split(" ")
        line = ""
        for w in words:
            trial = w if not line else line + " " + w
            if text_size(trial, size)[0] <= width_px or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


_scan_cache = {}


def draw_scanlines(surface, alpha=40, rect=None):
    """CRT scanlines: a dark line on every second row."""
    r = pygame.Rect(rect) if rect else surface.get_rect()
    key = (r.size, alpha)
    over = _scan_cache.get(key)
    if over is None:
        over = pygame.Surface(r.size, pygame.SRCALPHA)
        for y in range(0, r.h, 2):
            pygame.draw.line(over, (0, 0, 0, alpha), (0, y), (r.w, y))
        _scan_cache[key] = over
    surface.blit(over, r.topleft)


def panel(surface, rect, fill=BG_PANEL, border=GREEN_MID, alpha=230, width=1):
    """Translucent rectangle with a 1px border (terminal windows, HUD boxes)."""
    r = pygame.Rect(rect)
    s = pygame.Surface(r.size, pygame.SRCALPHA)
    s.fill((fill[0], fill[1], fill[2], alpha))
    surface.blit(s, r.topleft)
    if border is not None:
        pygame.draw.rect(surface, border, r, width)
    return r


# ---------------------------------------------------------------- json

def load_json(rel_path, default=None):
    """Read JSON from a path relative to the code folder. Returns default on any error."""
    try:
        with open(path(rel_path), "r") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return default


def save_json(rel_path, data):
    """Write JSON (pretty) to a path relative to the code folder, creating folders."""
    full = path(rel_path)
    folder = os.path.dirname(full)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(full, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------- input

class InputState(object):
    """Everything the player needs from input for one frame (built by Game in PLAY).

    move           (mx, my), each -1/0/1 from WASD (not normalised)
    aim            pygame.math.Vector2 mouse position in WORLD pixels
    aim_screen     (x, y) mouse position in INTERNAL pixels
    fire_pressed / fire_held / fire_released   LMB edge / held / release this frame
    alt_pressed / alt_held                     RMB (katana parry)
    dash_pressed   Space pressed this frame
    reload_pressed R pressed this frame
    weapon_select  None or slot index 0/1/2 (keys 1/2/3)
    weapon_cycle   0, +1 or -1 (mouse wheel)
    interact_pressed / interact_held           F
    rooted         True while hacking: do not move, aim-fire or dash
    """

    def __init__(self):
        self.move = (0, 0)
        self.aim = pygame.math.Vector2(0, 0)
        self.aim_screen = (0, 0)
        self.fire_pressed = False
        self.fire_held = False
        self.fire_released = False
        self.alt_pressed = False
        self.alt_held = False
        self.dash_pressed = False
        self.reload_pressed = False
        self.weapon_select = None
        self.weapon_cycle = 0
        self.interact_pressed = False
        self.interact_held = False
        self.rooted = False


# Helpers for scripted (headless) sessions: build fake events.

def key_event(key, down=True, mod=0):
    t = pygame.KEYDOWN if down else pygame.KEYUP
    return pygame.event.Event(t, {"key": key, "mod": mod, "unicode": "", "scancode": 0})


def tap(key):
    """KEYDOWN + KEYUP for one key, as a list."""
    return [key_event(key, True), key_event(key, False)]


def mouse_move(internal_pos):
    return pygame.event.Event(pygame.MOUSEMOTION, {"pos": internal_to_window(internal_pos),
                                                   "rel": (0, 0), "buttons": (0, 0, 0)})


def mouse_button(internal_pos, button=1, down=True):
    t = pygame.MOUSEBUTTONDOWN if down else pygame.MOUSEBUTTONUP
    return pygame.event.Event(t, {"pos": internal_to_window(internal_pos), "button": button})


def click(internal_pos, button=1):
    """Motion + press + release at an internal-surface position, as a list."""
    return [mouse_move(internal_pos), mouse_button(internal_pos, button, True),
            mouse_button(internal_pos, button, False)]
