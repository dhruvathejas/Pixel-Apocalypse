"""W0RM: slow segmented crawler. 14x5 px, long and low (player is 8x11).

Four magenta carapace segments (plum belly shadow, pink glint) strung on
dark-metal link pins. The centre link is a brighter metal seam: that is
where it breaks into two B1Ts, and each half carries a bright magenta bit
core on its back like a B1T. The head borrows B1T's face: one red hostile
eye, a metal feeler and a black mandible. Tiny metal legs under each pair.
The loop is an inchworm undulation: the front pair arches up 1px while the
head tip and tail stay planted, so the footprint does not jitter.
Drawn facing right; the generator mirrors it for left.
"""

FRAMES = {
    # flat: every segment on the ground
    "right_0": [
        "..............",
        "...mmm.mmm.mmG",
        ".mgmpMGmpMgmRG",
        "dmgmmmGmmmgmmK",
        ".d.ddd.ddd.dd.",
        "..G..G..G...G.",
    ],
    # arch: the two middle segments hump up 1px, legs tuck
    "right_1": [
        "...mmm.mmm....",
        "..mmpMGmpMgmmG",
        ".mgmmmGmmmgmRG",
        "dmgdddGdddgmmK",
        ".d.........dd.",
        "..G.........G.",
    ],
}
