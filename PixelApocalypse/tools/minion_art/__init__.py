"""Shared palette for |-|@c|<ing MINION sprites.

Each module in this package (b1t, p1ng, ...) defines FRAMES: a dict of
frame name -> list of equal-length strings using the characters below.
Frame names must contain "right"; the generator also writes a mirrored
"left" copy. Greens are reserved for the world, cyan for TURNED/hacked
(tinted in code), so minions use corrupted magenta/plum plus metal and
firewall orange.
"""

PALETTE = {
    "K": (0, 0, 0, 255),         # black: eyes, vents (same as player eyes)
    "W": (242, 242, 242, 255),   # glint
    "g": (58, 58, 68, 255),      # dark metal
    "G": (123, 123, 123, 255),   # metal (same as katana blade)
    "d": (74, 10, 62, 255),      # deep plum: body shadow
    "m": (160, 22, 127, 255),    # magenta: body
    "M": (255, 43, 214, 255),    # bright magenta: glow / core
    "p": (255, 138, 240, 255),   # pink: highlight
    "R": (255, 48, 48, 255),     # alert red: hostile eye, packet core
    "O": (255, 122, 26, 255),    # firewall orange
    "Y": (255, 210, 59, 255),    # firewall hot yellow
}

# Placement on the 32x32 canvas, matching player_assets: the player's feet
# sit on row 21 and its body is centred on column 16.
CANVAS = 32
BOTTOM = 21
CENTER_X = 16
