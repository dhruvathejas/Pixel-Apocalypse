#!/bin/sh
# Pixel Apocalypse launcher: runs the game with the system Python 3.9 (the vendored pygame needs it).
cd "$(dirname "$0")" || exit 1
exec /usr/bin/python3 init.py "$@"
