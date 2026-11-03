"""P1NG: ranged emitter drone. 9x11 px hovering sentinel (player is 8x11).

A rounded magenta pod (plum shadow, pink glint) with one red eye, a short
antenna with a blinking magenta tip, and a metal dish on its facing side:
a "(" shaped reflector with a bright feed at its focus. It hovers on a
small nozzle with a flickering thruster glow. The firing frame flares the
feed white, lights the dish rim and throws a ping arc out front.
Also a 3x3 projectile, packet_right, centred at row 16 like bullet_right.
Drawn facing right; the generator mirrors it for left.
"""

FRAMES = {
    # hover loop: tip blinks, thruster flickers, pod bobs 1px (OFFSETS)
    "right_0": [
        "..M......",
        "..G....GG",
        ".mmmm.Gg.",
        "mpmmmmGg.",
        "mmmmRgGgM",
        "dmmmmmGg.",
        ".dddd.Gg.",
        "..ggg..GG",
        "...G.....",
        "..gGg....",
        "...M.....",
    ],
    "right_1": [
        "..m......",
        "..G....GG",
        ".mmmm.Gg.",
        "mpmmmmGg.",
        "mmmmRgGgM",
        "dmmmmmGg.",
        ".dddd.Gg.",
        "..ggg..GG",
        "...G.....",
        "..gGg....",
        "..pMp....",
    ],
    # fire: feed flashes white, rim glows, ping arc leaves the dish
    "right_attack": [
        "..M......",
        "..G....Gp",
        ".mmmm.GgM",
        "mpmmmmGMp",
        "mmmmRgGWW",
        "dmmmmmGMp",
        ".dddd.GgM",
        "..ggg..Gp",
        "...G.....",
        "..gGg....",
        "..MpM....",
    ],
    # projectile: glowing packet, red core
    "packet_right": [
        ".M.",
        "MRp",
        ".M.",
    ],
}

# packet grid's last row defaults to 21; lift it so its centre sits on row 16
OFFSETS = {"right_1": (0, -1), "packet_right": (0, -4)}
