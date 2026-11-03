"""Pixel Apocalypse entry point: python3 init.py (from any folder)."""
import sys

import gdev  # noqa: F401  (puts the code folder, and the vendored pygame, on sys.path)
import game

if __name__ == "__main__":
    g = game.Game()
    g.main_loop()
    gdev.pygame.quit()
    sys.exit(0)
