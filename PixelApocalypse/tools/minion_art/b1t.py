"""B1T: swarm rusher. A scuttling corrupted bit-bug, 7x5 px (player is 8x11).

Low dome carapace (plum belly shadow, magenta shell, pink glint, a bright
magenta "bit" core on its back), one red hostile eye, a metal feeler and a
black mandible at the front, and three metal legs that alternate.
Drawn facing right; the generator mirrors it for left.
"""

FRAMES = {
    # scuttle loop: legs alternate, body stays put
    "right_0": [
        "..mmm.G",
        ".mpMmRG",
        "dmmmmmK",
        ".ddddd.",
        "G.G.G..",
    ],
    "right_1": [
        "..mmm.G",
        ".mpMmRG",
        "dmmmmmK",
        ".ddddd.",
        ".G.G.G.",
    ],
    # lunge/bite: jaws snap open, eye flares, legs splay; shifted 1px forward
    "right_attack": [
        "..mmm.K",
        ".mpMmM.",
        "dmmmmRK",
        ".ddddd.",
        "G...G.G",
    ],
}

OFFSETS = {"right_attack": (1, 0)}
