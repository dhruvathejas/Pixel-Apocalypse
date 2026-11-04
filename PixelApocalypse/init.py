"""Pixel Apocalypse entry point: python3 init.py (from any folder).

    --dev         dev hotkeys in PLAY: F6 grant a Key, F7 teleport to the next zone,
                  F8 kill the current boss phase (Mirror: end the fight), F9 open the breach port,
                  F10 enter the Deep Core
    --frames N    quit after N fixed steps (headless checks; never leave the game running)
"""
import sys

import gdev  # noqa: F401  (puts the code folder, and the vendored pygame, on sys.path)
import game


def _arg_value(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


if __name__ == "__main__":
    frames = _arg_value("--frames")
    g = game.Game(dev="--dev" in sys.argv)
    g.main_loop(max_frames=int(frames) if frames else None)
    gdev.pygame.quit()
    sys.exit(0)
