"""Pixel Apocalypse entry point: python3 init.py (from any folder).

    --dev         dev hotkeys in PLAY: F6 grant a Key, F7 teleport to the next zone,
                  F8 kill the current boss phase (Mirror: end the fight), F9 open the breach port,
                  F10 enter the Deep Core
    --frames N    quit after N fixed steps (headless checks; never leave the game running)
    --save-check  with --dev: write the settings and a fresh run save at start, read them back
                  and exit 1 if that fails (checks where a build saves; replaces the current save)

The macOS app (tools/make_app.py) runs this same file inside its launcher, with
PIXEL_APOCALYPSE_DATA_DIR set: saves and settings then go there (see gdev.DATA_DIR).
"""
import os
import sys

if os.environ.get("PIXEL_APOCALYPSE_DATA_DIR"):
    sys.dont_write_bytecode = True      # before the first import: never write __pycache__ into the app

import gdev  # noqa: F401  (puts the code folder, and the vendored pygame, on sys.path)
import game
import settings


def _arg_value(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


if __name__ == "__main__":
    frames = _arg_value("--frames")
    g = game.Game(dev="--dev" in sys.argv)
    if g.dev and "--save-check" in sys.argv:
        settings.save(g.settings)
        g.new_run(handle="CHECK")
        ok = (g.save_game("save check") and g.has_save() and settings.load() == g.settings
              and os.path.isfile(gdev.data_path(settings.SETTINGS_FILE)))
        print("[init] save check %s: %s, %s" % ("ok" if ok else "FAILED", gdev.data_path(game.SAVE_FILE),
                                               gdev.data_path(settings.SETTINGS_FILE)))
        if not ok:
            gdev.pygame.quit()
            sys.exit(1)
    g.main_loop(max_frames=int(frames) if frames else None)
    gdev.pygame.quit()
    sys.exit(0)
