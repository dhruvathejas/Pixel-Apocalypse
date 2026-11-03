"""Player settings, saved to save/settings.json (the only thing the prototype saves).

Settings are a plain dict. Read them with s["key"]; the helpers below turn the
menu values into numbers other modules use.
"""
import gdev

SETTINGS_FILE = "save/settings.json"

DEFAULTS = {
    "hack_difficulty": "NORMAL",        # EASY / NORMAL / HARD -> Breach Level 1 / 2 / 3 (B1T: -1)
    "hack_timer": "NORMAL",             # NORMAL / LONG (LONG = x1.5 on hack timers)
    "fixed_button_positions": False,    # True: no reshuffle after a wrong pick
    "zoom": "3X",                       # world camera zoom (the player is only 8px wide)
    "screen_shake": True,
    "fullscreen": False,
    "show_fps": False,
}

# Menu rows: (key, label, allowed values, one-line help). The settings menu is built from this.
OPTIONS = [
    ("hack_difficulty", "HACK DIFFICULTY", ["EASY", "NORMAL", "HARD"],
     "EASY = L1 (1-word packets), NORMAL = L2, HARD = L3. B1T runs 1 lower."),
    ("hack_timer", "HACK TIMER", ["NORMAL", "LONG"],
     "LONG gives x1.5 time on every decode timer."),
    ("fixed_button_positions", "KEEP OPTION POSITIONS", [False, True],
     "Option buttons stay in place after a wrong pick (no reshuffle)."),
    ("zoom", "ZOOM", ["1X", "2X", "3X", "4X"],
     "How close the camera is. Higher = bigger player and minions, less map on screen."),
    ("screen_shake", "SCREEN SHAKE", [True, False],
     "Camera jolts on hits, backtraces and access granted."),
    ("fullscreen", "FULLSCREEN", [False, True],
     "Switch between the 1920x1000 window and fullscreen."),
    ("show_fps", "SHOW FPS", [False, True],
     "Frame counter in the top-right corner."),
]


def load():
    """Settings dict: saved values over DEFAULTS. Unknown or bad values fall back to defaults."""
    s = dict(DEFAULTS)
    saved = gdev.load_json(SETTINGS_FILE, {})
    if isinstance(saved, dict):
        for key, _label, values, _help in OPTIONS:
            if key in saved and saved[key] in values:
                s[key] = saved[key]
    return s


def save(s):
    try:
        gdev.save_json(SETTINGS_FILE, dict((k, s[k]) for k in DEFAULTS))
    except (IOError, OSError) as err:
        print("[settings] could not save: %s" % err)


def cycle(s, key, step=1):
    """Move a setting to its next (step=1) or previous (step=-1) value. Returns the new value."""
    for k, _label, values, _help in OPTIONS:
        if k == key:
            i = values.index(s[key]) if s[key] in values else 0
            s[key] = values[(i + step) % len(values)]
            return s[key]
    return s.get(key)


def value_label(value):
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    return str(value)


def breach_level(s):
    """Base Breach Level 1/2/3 from hack_difficulty (archetype offsets are applied by hack.py)."""
    return {"EASY": 1, "NORMAL": 2, "HARD": 3}.get(s.get("hack_difficulty"), 2)


def zoom(s):
    """World camera zoom as an int (1-4)."""
    return {"1X": 1, "2X": 2, "3X": 3, "4X": 4}.get(s.get("zoom"), 3)


def timer_mult(s):
    """Multiplier for all hack timers (1.0 or 1.5)."""
    return 1.5 if s.get("hack_timer") == "LONG" else 1.0
