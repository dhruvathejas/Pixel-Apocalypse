"""
slice_map.py - measure bg/map.png once and write data/map_data.json.

The map art is a handful of flat-colour rectangles, so the game never loads
the 10000x10000 PNG. This tool is the ONLY place that image is opened.

Run from anywhere (paths are resolved from the code folder):

    /usr/bin/python3 tools/slice_map.py
    /usr/bin/python3 tools/slice_map.py --overlay /some/dir/map_overlay.png

How it works:
  1. Colour masks at full resolution (pygame.mask.from_threshold) - so every
     rect is pixel exact, no refinement guesswork.
  2. Connected regions of the "shell" colour (mid green):
       - region whose bounding box is fully solid and holds light bars -> rack
       - fully solid, no bars                                      -> node/block
       - not a rectangle (the L of the top/left border)            -> walls,
         split into rectangles
  3. Light-green regions inside a rack are its bars (data ports).
  4. Labels (A-E, N1, N2, ...) come from the design doc's section 3.1 table,
     matched by nearest centre.
  5. Spawn exits, zones, player start, then sanity checks.

The output is a draft: a human may hand-correct data/map_data.json.
"""

import os
import sys
import json
import re
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)  # vendored pygame lives in the code folder

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import gdev  # noqa: E402,F401  (first: relaunches with the Python the vendored pygame needs)
import pygame  # noqa: E402

MAP_PATH = "bg/map.png"
OUT_PATH = "data/map_data.json"

ZONE_RADIUS = 1200
SPAWN_MARGIN = 24      # how far outside the shell edge a spawn exit sits
CLEAR_BOX = 32         # spawn exits / player start need this much free floor

# Design doc 3.1 (approximate rects, x0, x1, y0, y1). Used only for naming.
DOC_RACKS = {
    "A": ((1100, 3000, 860, 1860), 4),
    "B": ((5400, 7000, 750, 2040), 3),
    "C": ((2330, 3800, 4550, 6460), 3),
    "E": ((470, 1740, 7250, 8190), 3),
    "D": ((6110, 7550, 6460, 8190), 2),
}
DOC_NODES = {
    "N1": ((1100, 1630, 2990, 3560), "clean_node", "Clean Node 1: start, save, shop, terminal"),
    "N2": ((4970, 5550, 3350, 3790), "clean_node", "Clean Node 2: mid-game hub"),
}
DOC_BLOCKS = {
    "deep_core_hatch": ((3670, 4960, 8140, 8190), "Deep Core hatch: entrance to the finale arena (partial block on bottom edge)"),
    "spool_cache": ((8160, 8190, 4970, 5980), "Spool's cache: sealed Act II Key terminal (partial block on right edge)"),
}


def path(*parts):
    return os.path.join(BASE_DIR, *parts)


def rect_list(r):
    return [int(r.x), int(r.y), int(r.w), int(r.h)]


def colour_mask(surf, colour):
    # threshold (1,1,1,1) means "exactly this RGBA"
    return pygame.mask.from_threshold(surf, colour, (1, 1, 1, 1))


def find_colours(img, step=16):
    """Sample a coarse grid to find the palette, then count each colour
    exactly at full resolution."""
    w, h = img.get_size()
    seen = set()
    for y in range(0, h, step):
        for x in range(0, w, step):
            seen.add(tuple(img.get_at((x, y))))
    counts = {}
    for c in seen:
        counts[c] = colour_mask(img, c).count()
    return counts


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def solid_area(mask, rect):
    box = pygame.Mask(rect.size, fill=True)
    return mask.overlap_area(box, rect.topleft)


def mask_to_rects(comp):
    """Split one hole-free orthogonal region into rectangles.
    Corners of its outline give every x/y where an edge can be; cells of
    that grid are uniform, so we test one pixel per cell and merge."""
    pts = comp.outline()
    xs, ys = set(), set()
    n = len(pts)
    for i in range(n):
        px, py = pts[i - 1]
        x, y = pts[i]
        nx, ny = pts[(i + 1) % n]
        d1 = (x - px, y - py)
        d2 = (nx - x, ny - y)
        if d1 != d2:
            xs.update((x, x + 1))
            ys.update((y, y + 1))
    bb = comp.get_bounding_rects()[0]
    xs.update((bb.left, bb.right))
    ys.update((bb.top, bb.bottom))
    xs = sorted(v for v in xs if bb.left <= v <= bb.right)
    ys = sorted(v for v in ys if bb.top <= v <= bb.bottom)

    bands = []  # [y0, y1, runs]
    for yi in range(len(ys) - 1):
        y0, y1 = ys[yi], ys[yi + 1]
        runs = []
        for xi in range(len(xs) - 1):
            x0, x1 = xs[xi], xs[xi + 1]
            if comp.get_at((x0, y0)):
                if runs and runs[-1][1] == x0:
                    runs[-1] = (runs[-1][0], x1)
                else:
                    runs.append((x0, x1))
        if bands and bands[-1][2] == runs and bands[-1][1] == y0:
            bands[-1][1] = y1
        else:
            bands.append([y0, y1, runs])

    rects = []
    for y0, y1, runs in bands:
        for x0, x1 in runs:
            rects.append(pygame.Rect(x0, y0, x1 - x0, y1 - y0))
    area = sum(r.w * r.h for r in rects)
    assert area == comp.count(), "wall split failed (%d vs %d)" % (area, comp.count())
    return rects


def nearest_label(rect, table):
    """Pick the doc label whose approximate rect centre is closest."""
    cx, cy = rect.center
    best, best_d = None, None
    for key, row in table.items():
        x0, x1, y0, y1 = row[0]
        d = ((x0 + x1) / 2.0 - cx) ** 2 + ((y0 + y1) / 2.0 - cy) ** 2
        if best_d is None or d < best_d:
            best, best_d = key, d
    return best, best_d ** 0.5


def is_clear(box, solids, art):
    if not art.contains(box):
        return False
    return box.collidelist(solids) == -1


def spawn_exit_for(bar, shell, solids, art):
    """Point just outside the shell edge nearest to the bar, on open floor.
    Ties prefer below, then beside, then above."""
    options = [
        (shell.bottom - bar.bottom, 0, "bottom", (bar.centerx, shell.bottom + SPAWN_MARGIN)),
        (bar.left - shell.left, 1, "left", (shell.left - SPAWN_MARGIN, bar.centery)),
        (shell.right - bar.right, 1, "right", (shell.right + SPAWN_MARGIN, bar.centery)),
        (bar.top - shell.top, 2, "top", (bar.centerx, shell.top - SPAWN_MARGIN)),
    ]
    options.sort(key=lambda o: (o[0], o[1]))
    for _dist, _pref, side, pt in options:
        box = pygame.Rect(0, 0, CLEAR_BOX, CLEAR_BOX)
        box.center = pt
        if is_clear(box, solids, art):
            return side, [int(pt[0]), int(pt[1])]
    raise RuntimeError("no walkable spawn exit for bar %s" % (bar,))


def slice_map(img):
    notes = []
    w, h = img.get_size()

    # --- art bounds and palette -----------------------------------------
    opaque = pygame.mask.from_surface(img, 0)
    art = opaque.get_bounding_rects()[0]
    for r in opaque.get_bounding_rects()[1:]:
        art.union_ip(r)

    counts = find_colours(img)
    assert sum(counts.values()) == w * h, "palette scan missed colours"
    colours = [c for c in counts if c[3] == 255]
    floor = max(colours, key=lambda c: counts[c])
    others = sorted((c for c in colours if c != floor), key=lum)
    if len(others) != 2:
        notes.append("expected 2 non-floor colours, found %d: %s" % (len(others), others))
    shell_c, bar_c = others[0], others[-1]

    shell_m = colour_mask(img, shell_c)
    bar_m = colour_mask(img, bar_c)

    # transparent pixels inside the art = holes in the drawing
    holes_m = pygame.Mask(img.get_size())
    holes_m.draw(colour_mask(img.subsurface(art), (0, 0, 0, 0)), art.topleft)
    holes = holes_m.get_bounding_rects()

    solid_m = shell_m.copy()
    solid_m.draw(bar_m, (0, 0))
    solid_m.draw(holes_m, (0, 0))

    bars_all = bar_m.get_bounding_rects()

    walls, racks, plain = [], [], []
    for bb in shell_m.get_bounding_rects():
        if solid_area(solid_m, bb) == bb.w * bb.h:
            inside = [b for b in bars_all if bb.contains(b)]
            if inside:
                racks.append((bb, inside))
            else:
                plain.append(bb)
        else:
            # find a pixel of this region and split it into rects
            seed = None
            for x in range(bb.left, bb.right):
                if shell_m.get_at((x, bb.top)):
                    seed = (x, bb.top)
                    break
            comp = shell_m.connected_component(seed)
            for r in mask_to_rects(comp):
                walls.append(r)

    # --- holes: fold a hole that runs alongside a bar into that bar -------
    for hole in holes:
        merged = False
        for _shell, bars in racks:
            for i, b in enumerate(bars):
                side_by_side = (hole.top == b.top and hole.bottom == b.bottom and
                                (hole.left == b.right or hole.right == b.left))
                if side_by_side:
                    notes.append("transparent strip %s beside bar %s folded into the bar"
                                 % (rect_list(hole), rect_list(b)))
                    bars[i] = b.union(hole)
                    merged = True
        if not merged:
            notes.append("unhandled transparent hole inside art: %s" % rect_list(hole))

    solids = [r for r in walls] + [s for s, _ in racks] + plain

    # --- racks ------------------------------------------------------------
    rack_out = []
    used = set()
    for shell, bars in racks:
        rid, dist = nearest_label(shell, DOC_RACKS)
        if rid in used or dist > 600:
            notes.append("rack at %s did not match the doc table (best %s, %.0fpx)"
                         % (rect_list(shell), rid, dist))
            rid = "R%d" % len(rack_out)
        used.add(rid)
        bars.sort(key=lambda b: (b.x, b.y))
        want = DOC_RACKS.get(rid, (None, None))[1]
        if want is not None and want != len(bars):
            notes.append("rack %s has %d bars, doc says %d" % (rid, len(bars), want))
        bar_out = []
        for i, b in enumerate(bars):
            side, exit_pt = spawn_exit_for(b, shell, solids, art)
            bar_out.append({
                "id": "%s%d" % (rid, i + 1),
                "rect": rect_list(b),
                "color": list(bar_c[:3]),
                "spawn_exit": exit_pt,
                "exit_side": side,
                "hittable": False,
            })
        cut = not (shell.right < art.right and shell.bottom < art.bottom)
        rack_out.append({
            "id": rid,
            "shell": rect_list(shell),
            "color": list(shell_c[:3]),
            "bars": bar_out,
            "zone_center": [shell.centerx, shell.centery],
            "zone_radius": ZONE_RADIUS,
            "cut_by_void": cut,
        })
    rack_out.sort(key=lambda r: r["id"])

    # --- nodes and blocks -------------------------------------------------
    node_out, block_out = [], []
    for r in plain:
        nid, nd = nearest_label(r, DOC_NODES)
        bid, bd = nearest_label(r, DOC_BLOCKS)
        if nd <= bd and nd < 600:
            _approx, role, note = DOC_NODES[nid]
            node_out.append({"id": nid, "role": role, "rect": rect_list(r),
                             "color": list(shell_c[:3]), "note": note})
        else:
            if bd > 600:
                notes.append("block at %s did not match the doc table" % rect_list(r))
                bid = "block%d" % len(block_out)
                note = "unlabelled block"
            else:
                note = DOC_BLOCKS[bid][1]
            block_out.append({"id": bid, "rect": rect_list(r),
                              "color": list(shell_c[:3]), "note": note})
    node_out.sort(key=lambda n: n["id"])
    block_out.sort(key=lambda b: b["id"])
    for key in DOC_NODES:
        if key not in [n["id"] for n in node_out]:
            notes.append("doc node %s not found in the image" % key)
    for key in DOC_BLOCKS:
        if key not in [b["id"] for b in block_out]:
            notes.append("doc block %s not found in the image" % key)
    for key in DOC_RACKS:
        if key not in used:
            notes.append("doc rack %s not found in the image" % key)

    # --- player start: open floor just above Clean Node 1, toward Rack A ---
    n1 = [pygame.Rect(n["rect"]) for n in node_out if n["id"] == "N1"]
    if n1:
        n1 = n1[0]
        start = [n1.centerx, n1.top - 80]
    else:
        start = [art.centerx, art.centery]
        notes.append("no N1 found; player_start set to art centre")

    if art.right != art.bottom or art.right != 8192:
        notes.append("art bounds are %s" % rect_list(art))

    data = {
        "source": MAP_PATH,
        "size": [w, h],
        "art_bounds": rect_list(art),
        "floor_color": list(floor[:3]),
        "walls": [{"rect": rect_list(r), "color": list(shell_c[:3])} for r in walls],
        "racks": rack_out,
        "nodes": node_out,
        "blocks": block_out,
        "player_start": start,
        "notes": notes,
    }
    return data


def validate(data):
    """Raise AssertionError if the extracted map does not hang together."""
    art = pygame.Rect(data["art_bounds"])
    walls = [pygame.Rect(wl["rect"]) for wl in data["walls"]]
    shells = [pygame.Rect(r["shell"]) for r in data["racks"]]
    others = [pygame.Rect(n["rect"]) for n in data["nodes"]] + \
             [pygame.Rect(b["rect"]) for b in data["blocks"]]
    solids = walls + shells + others

    for i, a in enumerate(solids):
        assert art.contains(a), "solid %s outside art" % a
        for b in solids[i + 1:]:
            assert not a.colliderect(b), "solids overlap: %s %s" % (a, b)

    for rack in data["racks"]:
        shell = pygame.Rect(rack["shell"])
        bars = [pygame.Rect(b["rect"]) for b in rack["bars"]]
        for i, b in enumerate(bars):
            assert shell.contains(b), "bar %s not inside shell %s" % (b, shell)
            for other in bars[i + 1:]:
                assert not b.colliderect(other), "bars overlap %s %s" % (b, other)
        xs = [b.x for b in bars]
        assert xs == sorted(xs), "bars not sorted left to right"
        for bar in rack["bars"]:
            box = pygame.Rect(0, 0, CLEAR_BOX, CLEAR_BOX)
            box.center = bar["spawn_exit"]
            assert is_clear(box, solids, art), "spawn exit %s blocked" % bar["spawn_exit"]
            d = ((box.centerx - shell.centerx) ** 2 + (box.centery - shell.centery) ** 2) ** 0.5
            assert d < rack["zone_radius"], "spawn exit outside its zone"

    box = pygame.Rect(0, 0, CLEAR_BOX, CLEAR_BOX)
    box.center = data["player_start"]
    assert is_clear(box, solids, art), "player_start is inside a solid"


def compact_json(data):
    """indent=2, but short number lists stay on one line (easier to hand-edit)."""
    text = json.dumps(data, indent=2)
    return re.sub(r"\[\s+([-\d.,\s]+?)\s+\]",
                  lambda m: "[" + ", ".join(v.strip() for v in m.group(1).split(",")) + "]",
                  text)


def draw_overlay(img, data, out_file, scale=0.1):
    size = (int(img.get_width() * scale), int(img.get_height() * scale))
    small = pygame.transform.scale(img, size)
    view = pygame.Surface(size)
    view.fill((60, 0, 60))            # void shows as dark purple
    view.blit(small, (0, 0))
    pygame.font.init()
    font = pygame.font.Font(None, 20)

    def sr(r):
        x, y, w, h = r
        return pygame.Rect(int(x * scale), int(y * scale),
                           max(1, int(round(w * scale))), max(1, int(round(h * scale))))

    def sp(p):
        return int(p[0] * scale), int(p[1] * scale)

    def label(text, pos, colour):
        view.blit(font.render(text, True, colour), pos)

    pygame.draw.rect(view, (255, 255, 255), sr(data["art_bounds"]), 1)
    for wl in data["walls"]:
        pygame.draw.rect(view, (60, 140, 255), sr(wl["rect"]), 2)
    for rack in data["racks"]:
        shell = sr(rack["shell"])
        pygame.draw.circle(view, (120, 120, 40), sp(rack["zone_center"]),
                           int(rack["zone_radius"] * scale), 1)
        pygame.draw.rect(view, (255, 0, 200), shell, 2)
        label("RACK " + rack["id"], (shell.x + 3, shell.y + 3), (255, 120, 230))
        for bar in rack["bars"]:
            pygame.draw.rect(view, (255, 230, 0), sr(bar["rect"]), 1)
            ex = sp(bar["spawn_exit"])
            pygame.draw.circle(view, (255, 40, 40), ex, 4)
            label(bar["id"], (ex[0] + 4, ex[1] - 6), (255, 90, 90))
    for node in data["nodes"]:
        r = sr(node["rect"])
        pygame.draw.rect(view, (0, 255, 255), r, 2)
        label(node["id"], (r.x + 3, r.y + 3), (0, 255, 255))
    for blk in data["blocks"]:
        r = sr(blk["rect"])
        pygame.draw.rect(view, (255, 140, 0), r.inflate(6, 6), 2)
        label(blk["id"], (r.x - 70 if r.x > 700 else r.x, r.y - 16), (255, 160, 40))
    px, py = sp(data["player_start"])
    pygame.draw.line(view, (255, 255, 255), (px - 6, py), (px + 6, py), 2)
    pygame.draw.line(view, (255, 255, 255), (px, py - 6), (px, py + 6), 2)
    label("START", (px + 8, py - 8), (255, 255, 255))

    folder = os.path.dirname(os.path.abspath(out_file))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    pygame.image.save(view, out_file)


def main():
    parser = argparse.ArgumentParser(description="Extract data/map_data.json from bg/map.png")
    parser.add_argument("--overlay", help="also save a downscaled check image to this PNG path")
    parser.add_argument("--out", default=path(OUT_PATH), help="output json path")
    args = parser.parse_args()

    img = pygame.image.load(path(MAP_PATH))
    data = slice_map(img)
    validate(data)

    folder = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(args.out, "w") as f:
        f.write(compact_json(data) + "\n")

    print("art bounds:", data["art_bounds"])
    print("walls:", [wl["rect"] for wl in data["walls"]])
    for rack in data["racks"]:
        print("rack %s shell %s bars %d %s" % (
            rack["id"], rack["shell"], len(rack["bars"]),
            [b["exit_side"] for b in rack["bars"]]))
    for node in data["nodes"]:
        print("node %s %s" % (node["id"], node["rect"]))
    for blk in data["blocks"]:
        print("block %s %s" % (blk["id"], blk["rect"]))
    print("player_start:", data["player_start"])
    for n in data["notes"]:
        print("NOTE:", n)
    print("wrote", args.out)

    if args.overlay:
        draw_overlay(img, data, args.overlay)
        print("overlay", args.overlay)


if __name__ == "__main__":
    main()
