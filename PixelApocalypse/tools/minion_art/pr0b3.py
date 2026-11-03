"""PR0B3: non-damaging observer drone. 9x10 px floating eye (player is 8x11).

A round dark-metal sphere (grey shell, pink-white glint) whose facing side is
one big camera lens: a black housing with a red iris and a white catchlight.
A small rotor spins on a mast above it, a magenta recording light blinks on
its back, and a faint magenta hover glow sits under it. No weapon, no legs:
it reads as a mostly grey "eyeball" against the magenta P1NG pod and B1T bug.
It floats with BOTTOM = 17 (4px above the player's foot line) and bobs 1px.
The scan frame flares the iris white and throws a red/pink scan cone out
front: the "recording you" tell. Drawn facing right; mirrored for left.
"""

FRAMES = {
    # hover loop: rotor spins (wide blade / short blade), REC light blinks
    "right_0": [
        ".GGGGGGG.",
        "....g....",
        "..gGGGg..",
        ".gGWGKKK.",
        "MgGGKRRWK",
        "gGGGKRRRK",
        ".gGGGKKK.",
        "..ggggg..",
        ".........",
        "...pMp...",
    ],
    "right_1": [
        "...GGG...",
        "....g....",
        "..gGGGg..",
        ".gGWGKKK.",
        "dgGGKRRWK",
        "gGGGKRRRK",
        ".gGGGKKK.",
        "..ggggg..",
        ".........",
        "....M....",
    ],
    # scan: iris flares white, REC solid, scan cone sweeps out of the lens.
    # 4 extra columns on the right; OFFSETS keeps the body where it was.
    "right_scan": [
        "..GGGGG......",
        "....g......R.",
        "..gGGGg..R.p.",
        ".gGWGKKKRp.M.",
        "MgGGKWWWMpMp.",
        "gGGGKWRRMpMp.",
        ".gGGGKKKRp.M.",
        "..ggggg..R.p.",
        "...........R.",
        "...pMp.......",
    ],
}

BOTTOM = 17
# right_1 bobs up 1px; right_scan is 13 wide (centred 2px further left), so
# shift it +2 to keep the sphere on the same columns as the hover frames.
OFFSETS = {"right_1": (0, -1), "right_scan": (2, 0)}
