"""F1R3W4LL: slow armoured sentry/warden. 13x14 px, the biggest minion (player is 8x11).

A squat, heavy warden: a dark-metal dome helmet with a red visor eye, broad
magenta shoulders over a plum belly, and two thick metal stump legs. Its whole
facing (right) side is a tall firewall slab: orange bricks with hot-yellow
mortar and a flickering yellow/orange flame crest, gripped by a metal arm.
The brick wall is taller than the body, so "shield in front, flank it" reads
at a glance; the back is plain dark metal/magenta. Drawn facing right; the
generator mirrors it for left.
"""

FRAMES = {
    # heavy step loop: legs trade places, body sinks 1px on the planted foot,
    # flame crest flickers. The shield slab never moves; the grip arm sinks
    # with the body so it stays attached at the shoulder.
    "right_0": [
        "..........Y..",
        ".........YOY.",
        "..ggggg..OOYO",
        ".gGWGGGg.OOYO",
        ".gGGGKRK.YYYY",
        ".gggggggGYOOO",
        "gmmmmmmmGYOOO",
        "mmpMmmmmGYYYY",
        "mmmMmmmmGOOYO",
        "dmmmmmmm.OOYO",
        ".dddddd..YYYY",
        ".gGg.gGg.YOOO",
        ".gGg.gGg.YOOO",
        "ggGg.ggGg....",
    ],
    "right_1": [
        "...........Y.",
        "..........YOY",
        ".........OOYO",
        "..ggggg..OOYO",
        ".gGWGGGg.YYYY",
        ".gGGGKRK.YOOO",
        ".gggggggGYOOO",
        "gmmmmmmmGYYYY",
        "mmpMmmmmGOOYO",
        "mmmMmmmmGOOYO",
        "dmmmmmmm.YYYY",
        ".dddddd..YOOO",
        "ggGg.gGg.YOOO",
        ".....ggGg....",
    ],
    # shield bash: the wall is shoved 2px forward on a straightened arm, its
    # leading edge flashes white-hot with sparks, the eye flares, and the
    # warden leans in. 2 extra columns on the right; OFFSETS keeps the body
    # on the same columns as the step frames.
    "right_attack": [
        "............Y.Y",
        "...........YOYO",
        "...ggggg...OOYW",
        "..gGWGGGg..OOYW",
        "..gGGGKMR..YYYW",
        "..ggggggg..YOOW",
        ".gmmmmmmmGGYOOW",
        "mmpMmmmmmmGYYYW",
        "mmmMmmmmm..OOYW",
        "dmmmmmmm...OOYW",
        ".dddddd....YYYW",
        ".gGg.gGg...YOOY",
        "gGg..gGg...YOOO",
        "gg...ggGg......",
    ],
}

OFFSETS = {"right_attack": (1, 0)}
