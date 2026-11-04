"""Build the macOS app and disk image (stdlib only, run with Apple's Python 3.9):

    /usr/bin/python3 tools/make_app.py              # dist/Pixel Apocalypse.app + the .dmg
    /usr/bin/python3 tools/make_app.py --no-dmg     # just the .app
    /usr/bin/python3 tools/make_app.py --out DIR    # somewhere other than <repo>/dist

Steps: compile tools/macapp/launcher.c (x86_64 only, see the comment there) -> copy the
game files into Contents/Resources/game -> draw the icon from the player sprite and pack
it with iconutil -> Info.plist (version from menu.VERSION) -> ad-hoc codesign + verify
-> hdiutil UDZO image with an Applications link. Scratch files go to <repo>/build/macapp.
Needs Apple's command line tools: clang, iconutil, codesign, hdiutil (all in the CLT).
See docs/macos-app.md.
"""
import argparse
import math
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pixelart  # noqa: E402

CODE = os.path.dirname(HERE)                 # PixelApocalypse/ (the game files)
REPO = os.path.dirname(CODE)
APP_NAME = "Pixel Apocalypse"
BUNDLE_ID = "com.dhruvathejas.pixelapocalypse"
AUTHOR = "Dhruva Thejas"
DEPLOYMENT_TARGET = "10.13"   # what the launcher is compiled for (clang -mmacosx-version-min)
# What the app really needs: the Python 3.9 of Command Line Tools / Xcode 14+, which need
# macOS 12.5. On anything older macOS itself says the app needs a newer macOS.
MIN_MACOS = "12.5"
LAUNCHER_SRC = os.path.join(HERE, "macapp", "launcher.c")
ICON_SPRITE = os.path.join(CODE, "player_assets", "player_right_asset.png")

# Not copied into the app: dev-only material, the player's saves, build tools (the game
# imports nothing from tools/ at run time), and pygame's docs / examples / tests (~12 MB).
EXCLUDE_TOP = {"prompts", "chat.md", "save", "tools", "docs", "play.sh"}
EXCLUDE_PYGAME = {"docs", "examples", "tests"}
EXCLUDE_ANYWHERE = {"__pycache__", ".DS_Store", ".git", ".gitignore"}

# Icon palette (gdev's: world greens, threats magenta)
TOP = (12, 42, 23)
BOTTOM = (3, 12, 7)
BORDER = (40, 170, 80)
OUTLINE = (80, 255, 120)
MAGENTA = (255, 60, 200)


def log(msg):
    print("[make_app] " + msg)
    sys.stdout.flush()


def run(cmd, **kw):
    log("$ " + " ".join('"%s"' % c if " " in c else c for c in cmd))
    subprocess.check_call(cmd, **kw)


# ---------------------------------------------------------------- version

def read_version():
    """('0.2.0', 'alpha') from menu.VERSION = "v0.2.0-alpha THREAT MODEL" (no pygame import)."""
    with open(os.path.join(CODE, "menu.py"), "r") as f:
        src = f.read()
    m = re.search(r'^VERSION\s*=\s*"v?(\d+\.\d+\.\d+)(?:-([A-Za-z0-9.]+))?', src, re.M)
    if not m:
        raise SystemExit("make_app: could not find VERSION = \"vX.Y.Z...\" in menu.py")
    return m.group(1), m.group(2) or ""


# ---------------------------------------------------------------- icon

def _sprite_cells():
    """The player sprite cropped to its opaque pixels: list of rows of (r, g, b, a)."""
    rows = pixelart.read_png(ICON_SPRITE)
    ys = [y for y, row in enumerate(rows) if any(px[3] for px in row)]
    xs = [x for x in range(len(rows[0])) if any(row[x][3] for row in rows)]
    return [row[min(xs):max(xs) + 1] for row in rows[min(ys):max(ys) + 1]]


def _outlined(cells):
    """Grow the sprite by one cell of bright green (8-neighbour) so it reads on the dark tile."""
    h, w = len(cells) + 2, len(cells[0]) + 2
    grid = [[None] * w for _ in range(h)]
    for y, row in enumerate(cells):
        for x, px in enumerate(row):
            if px[3]:
                grid[y + 1][x + 1] = px[:3]
    out = [list(r) for r in grid]
    for y in range(h):
        for x in range(w):
            if grid[y][x] is None and any(0 <= y + dy < h and 0 <= x + dx < w and grid[y + dy][x + dx]
                                          for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                out[y][x] = OUTLINE
    return out


def _round_rect_dist(px, py, cx, cy, half, radius):
    # Signed distance to a rounded square (negative inside), in pixels.
    qx = abs(px - cx) - (half - radius)
    qy = abs(py - cy) - (half - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _mix(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def draw_icon(size, cells):
    """One icon PNG's rows: macOS rounded-square tile (824/1024 grid), green border, CRT
    scanlines, a magenta glow, and the player sprite scaled nearest-neighbour on top."""
    s = float(size)
    half, radius = s * 412 / 1024, s * 185 / 1024
    cx = cy = s / 2
    border = max(1.0, s * 18 / 1024)
    shadow_dy, shadow_blur = s * 12 / 1024, max(1.0, s * 24 / 1024)
    scan = max(1, size // 128)                 # scanline period (none on the tiny sizes)

    # Sprite: 1x / 2x without outline at 16 / 32 px, outlined and ~2/3 of the tile above.
    sprite = [[px[:3] if px[3] else None for px in row] for row in cells] if size <= 32 else _outlined(cells)
    k = max(1, size // 16) if size <= 32 else max(1, int(s * 0.5 / len(sprite) + 0.5))
    sw, sh = len(sprite[0]) * k, len(sprite) * k
    sx0, sy0 = (size - sw) // 2, (size - sh) // 2
    glow_r = s * 0.42

    rows = []
    for y in range(size):
        py = y + 0.5
        base = _mix(TOP, BOTTOM, y / s)
        if size >= 64 and (y // scan) % 2:
            base = (base[0] * 0.78, base[1] * 0.78, base[2] * 0.78)
        row = []
        for x in range(size):
            px = x + 0.5
            d = _round_rect_dist(px, py, cx, cy, half, radius)
            cover = min(1.0, max(0.0, 0.5 - d))
            sd = _round_rect_dist(px, py - shadow_dy, cx, cy, half, radius)
            shadow = 0.45 * min(1.0, max(0.0, 1.0 - sd / shadow_blur)) if sd > -1 else 0.45
            alpha = cover + shadow * (1.0 - cover)
            if alpha <= 0.0:
                row.append(pixelart.TRANSPARENT)
                continue
            if cover <= 0.0:
                row.append((0, 0, 0, int(alpha * 255 + 0.5)))
                continue
            col = base
            g = math.hypot(px - cx, py - (sy0 + sh * 0.45)) / glow_r
            if g < 1.0:
                col = _mix(col, MAGENTA, 0.55 * (1.0 - g) ** 1.6)
            col = _mix(col, BORDER, min(1.0, max(0.0, d + border + 0.5)))
            gx, gy = (x - sx0) // k, (y - sy0) // k
            if 0 <= x - sx0 < sw and 0 <= y - sy0 < sh and sprite[gy][gx] is not None:
                col = sprite[gy][gx]
            # Premultiplied over the (black) shadow, then back to straight alpha.
            k_rgb = cover / alpha
            row.append((int(col[0] * k_rgb + 0.5), int(col[1] * k_rgb + 0.5), int(col[2] * k_rgb + 0.5),
                        int(alpha * 255 + 0.5)))
        rows.append(row)
    return rows


ICONSET = [("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
           ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256),
           ("icon_256x256@2x", 512), ("icon_512x512", 512), ("icon_512x512@2x", 1024)]


def build_icon(build_dir, icns_path):
    iconset = os.path.join(build_dir, "AppIcon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    cells = _sprite_cells()
    drawn = {}
    for name, size in ICONSET:
        if size not in drawn:
            log("icon %dpx" % size)
            drawn[size] = os.path.join(build_dir, "icon_%d.png" % size)
            pixelart.write_png(drawn[size], draw_icon(size, cells))
        shutil.copyfile(drawn[size], os.path.join(iconset, name + ".png"))
    run(["iconutil", "-c", "icns", "-o", icns_path, iconset])
    # The game hands this to pygame.display.set_icon (gdev.app_icon); otherwise SDL swaps
    # the Dock icon for pygame's logo as soon as the window opens.
    shutil.copyfile(drawn[512], os.path.join(os.path.dirname(icns_path), "AppIcon.png"))


# ---------------------------------------------------------------- bundle

def _ignore(folder, names):
    rel = os.path.relpath(folder, CODE)
    skip = {n for n in names if n in EXCLUDE_ANYWHERE or n.endswith((".pyc", ".pyo"))}
    if rel == ".":
        skip |= EXCLUDE_TOP & set(names)
    elif rel == "pygame":
        skip |= EXCLUDE_PYGAME & set(names)
    return skip


def compile_launcher(out_path):
    run(["clang", "-arch", "x86_64", "-mmacosx-version-min=" + DEPLOYMENT_TARGET, "-O2", "-Wall",
         "-o", out_path, LAUNCHER_SRC, "-framework", "CoreFoundation"])


def write_info_plist(contents, version, stage):
    long_version = version + ("-" + stage if stage else "")
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundlePackageType": "APPL",
        "CFBundleSignature": "????",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleGetInfoString": "%s %s" % (APP_NAME, long_version),
        "CFBundleIconFile": "AppIcon",
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.action-games",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "%s %s. Copyright (c) %s %s." % (APP_NAME, long_version,
                                                                       time.strftime("%Y"), AUTHOR),
    }
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
    with open(os.path.join(contents, "PkgInfo"), "w") as f:
        f.write("APPL????")
    run(["plutil", "-lint", os.path.join(contents, "Info.plist")])


def _is_macho(path):
    with open(path, "rb") as f:
        magic = f.read(4)
    return magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def sign(app):
    # Clear Finder info / quarantine detritus (codesign --strict rejects it), sign the
    # pygame .so / .dylib files one by one (--deep only follows nested bundles, not loose
    # binaries in Resources), then seal the whole bundle.
    run(["xattr", "-cr", app])
    inner = []
    for folder, _dirs, files in os.walk(os.path.join(app, "Contents", "Resources")):
        for name in files:
            p = os.path.join(folder, name)
            if not os.path.islink(p) and name.endswith((".so", ".dylib")) and _is_macho(p):
                inner.append(p)
    inner.sort()
    log("signing %d pygame libraries" % len(inner))
    for i in range(0, len(inner), 40):
        # Quiet unless it fails (every file says "replacing existing signature").
        p = subprocess.run(["codesign", "--force", "--sign", "-"] + inner[i:i + 40],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if p.returncode:
            raise SystemExit("make_app: codesign failed:\n" + p.stdout)
    run(["codesign", "--force", "--deep", "--sign", "-", app])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])


def build_app(out_dir, build_dir, do_sign=True):
    version, stage = read_version()
    app = os.path.join(out_dir, APP_NAME + ".app")
    contents = os.path.join(app, "Contents")
    log("building %s %s%s" % (app, version, "-" + stage if stage else ""))
    shutil.rmtree(app, ignore_errors=True)
    os.makedirs(os.path.join(contents, "MacOS"))
    os.makedirs(os.path.join(contents, "Resources"))
    os.makedirs(build_dir, exist_ok=True)

    compile_launcher(os.path.join(contents, "MacOS", APP_NAME))
    log("copying game files")
    shutil.copytree(CODE, os.path.join(contents, "Resources", "game"), ignore=_ignore, symlinks=True)
    build_icon(build_dir, os.path.join(contents, "Resources", "AppIcon.icns"))
    write_info_plist(contents, version, stage)
    if do_sign:
        sign(app)
    return app, version, stage


def build_dmg(app, out_dir, build_dir, version, stage):
    name = "PixelApocalypse-%s%s-macOS.dmg" % (version, "-" + stage if stage else "")
    dmg = os.path.join(out_dir, name)
    staging = os.path.join(build_dir, "dmg")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    run(["ditto", app, os.path.join(staging, os.path.basename(app))])   # keeps the signature intact
    os.symlink("/Applications", os.path.join(staging, "Applications"))
    if os.path.exists(dmg):
        os.remove(dmg)
    run(["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", staging, "-ov",
         "-format", "UDZO", "-imagekey", "zlib-level=9", dmg])
    shutil.rmtree(staging, ignore_errors=True)
    return dmg


def main():
    ap = argparse.ArgumentParser(description="Build %s.app and its .dmg." % APP_NAME)
    ap.add_argument("--out", default=os.path.join(REPO, "dist"), help="output folder (default: <repo>/dist)")
    ap.add_argument("--build", default=os.path.join(REPO, "build", "macapp"),
                    help="scratch folder (default: <repo>/build/macapp)")
    ap.add_argument("--no-dmg", action="store_true", help="build only the .app")
    ap.add_argument("--no-sign", action="store_true", help="skip codesign (the .dmg is then skipped too)")
    args = ap.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("make_app: macOS only")
    for tool in ("clang", "iconutil", "codesign", "hdiutil", "plutil", "ditto", "xattr"):
        if not shutil.which(tool):
            raise SystemExit("make_app: %s not found (install Apple's Command Line Tools: "
                             "xcode-select --install)" % tool)
    out_dir, build_dir = os.path.abspath(args.out), os.path.abspath(args.build)
    os.makedirs(out_dir, exist_ok=True)
    app, version, stage = build_app(out_dir, build_dir, do_sign=not args.no_sign)
    log("app: " + app)
    if not args.no_dmg and not args.no_sign:
        log("dmg: " + build_dmg(app, out_dir, build_dir, version, stage))
    log("done")


if __name__ == "__main__":
    main()
