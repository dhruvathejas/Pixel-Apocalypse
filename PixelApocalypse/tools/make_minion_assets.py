"""Build minion_assets/*.png from the ASCII art in tools/minion_art/.

    python3 tools/make_minion_assets.py                  # all minions
    python3 tools/make_minion_assets.py b1t p1ng         # just these
    python3 tools/make_minion_assets.py --preview DIR    # also write 8x preview sheets

Each art module defines FRAMES (name -> rows) and may override BOTTOM (row of
the grid's last line on the canvas) or OFFSETS (name -> (dx, dy)).
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pixelart
from minion_art import BOTTOM, CANVAS, CENTER_X, PALETTE

MINIONS = ["b1t", "p1ng", "pr0b3", "f1r3w4ll", "w0rm"]
OUT_DIR = os.path.join(os.path.dirname(HERE), "minion_assets")
FLOOR = (2, 34, 0, 255)  # map floor colour, for previews


def place(name, frame, grid, bottom, offset):
    width = len(grid[0])
    for i, line in enumerate(grid):
        assert len(line) == width, "%s/%s row %d is %d wide, expected %d" % (name, frame, i, len(line), width)
        for ch in line:
            assert ch == "." or ch in PALETTE, "%s/%s uses unknown colour %r" % (name, frame, ch)
    left = CENTER_X - width // 2 + offset[0]
    top = bottom - len(grid) + 1 + offset[1]
    assert left >= 0 and top >= 0 and left + width <= CANVAS and top + len(grid) <= CANVAS, \
        "%s/%s does not fit the %dx%d canvas" % (name, frame, CANVAS, CANVAS)
    canvas = [[pixelart.TRANSPARENT] * CANVAS for _ in range(CANVAS)]
    for y, row in enumerate(pixelart.from_ascii(grid, PALETTE)):
        for x, px in enumerate(row):
            canvas[top + y][left + x] = px
    return canvas


def build(name, preview_dir=None):
    art = importlib.import_module("minion_art." + name)
    bottom = getattr(art, "BOTTOM", BOTTOM)
    offsets = getattr(art, "OFFSETS", {})
    written = []
    for frame, grid in art.FRAMES.items():
        assert "right" in frame, "%s frame %r must contain 'right'" % (name, frame)
        img = place(name, frame, grid, bottom, offsets.get(frame, (0, 0)))
        for key, out in ((frame, img), (frame.replace("right", "left"), pixelart.mirror(img))):
            path = os.path.join(OUT_DIR, "%s_%s.png" % (name, key))
            pixelart.write_png(path, out)
            written.append(path)
    if preview_dir:
        imgs = [pixelart.read_png(p) for p in written]
        pixelart.write_png(os.path.join(preview_dir, name + "_preview.png"), pixelart.sheet(imgs, 8, bg=FLOOR))
    return written


if __name__ == "__main__":
    args, preview = sys.argv[1:], None
    if "--preview" in args:
        i = args.index("--preview")
        preview = args[i + 1]
        del args[i:i + 2]
    os.makedirs(OUT_DIR, exist_ok=True)
    for name in args or MINIONS:
        for path in build(name, preview):
            print(os.path.relpath(path, os.path.dirname(HERE)))
