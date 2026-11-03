"""Tiny stdlib-only pixel-art helpers (no PIL / pygame needed).

Images are lists of rows; each row is a list of (r, g, b, a) tuples.

    python3 tools/pixelart.py dump player_assets/player_right_asset.png
    python3 tools/pixelart.py sheet out.png 8 a.png b.png ...
"""
import struct
import sys
import zlib

TRANSPARENT = (0, 0, 0, 0)


def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path + " is not a PNG"
    pos, idat, palette, trns = 8, b"", None, None
    width = height = depth = ctype = None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            assert depth == 8 and interlace == 0, "only 8-bit non-interlaced PNGs"
        elif kind == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    bpp = {6: 4, 2: 3, 3: 1, 4: 2, 0: 1}[ctype]
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(height):
        ftype = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ftype == 1:
                line[x] = (line[x] + a) & 255
            elif ftype == 2:
                line[x] = (line[x] + b) & 255
            elif ftype == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        prev = line
        row = []
        for x in range(width):
            px = line[x * bpp:(x + 1) * bpp]
            if ctype == 6:
                row.append(tuple(px))
            elif ctype == 2:
                row.append(tuple(px) + (255,))
            elif ctype == 3:
                alpha = trns[px[0]] if trns and px[0] < len(trns) else 255
                row.append(palette[px[0]] + (alpha,))
            elif ctype == 4:
                row.append((px[0], px[0], px[0], px[1]))
            else:
                row.append((px[0], px[0], px[0], 255))
        rows.append(row)
    return rows


def write_png(path, rows):
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def from_ascii(grid, palette):
    """grid: list of equal-length strings; palette: char -> (r, g, b, a). '.' is transparent."""
    return [[TRANSPARENT if ch == "." else palette[ch] for ch in line] for line in grid]


def mirror(rows):
    return [list(reversed(row)) for row in rows]


def scale(rows, factor):
    out = []
    for row in rows:
        big = [px for px in row for _ in range(factor)]
        out.extend([list(big) for _ in range(factor)])
    return out


def sheet(images, factor=8, gap=2, bg=(40, 40, 48, 255)):
    """Side-by-side scaled preview on a dark background (alpha composited)."""
    scaled = [scale(img, factor) for img in images]
    h = max(len(s) for s in scaled) + gap * 2
    w = sum(len(s[0]) for s in scaled) + gap * (len(scaled) + 1)
    out = [[bg] * w for _ in range(h)]
    x0 = gap
    for s in scaled:
        for y, row in enumerate(s):
            for x, (r, g, b, a) in enumerate(row):
                if a:
                    br, bgc, bb, _ = out[gap + y][x0 + x]
                    k = a / 255.0
                    out[gap + y][x0 + x] = (int(r * k + br * (1 - k)), int(g * k + bgc * (1 - k)),
                                            int(b * k + bb * (1 - k)), 255)
        x0 += len(s[0]) + gap
    return out


def dump(rows):
    """Print an image as ASCII with a generated palette legend."""
    keys, legend = {}, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    for row in rows:
        line = ""
        for px in row:
            if px[3] == 0:
                line += "."
                continue
            if px not in keys:
                keys[px] = legend[len(keys) % len(legend)]
            line += keys[px]
        print(line)
    for px, k in keys.items():
        print(k, "#%02x%02x%02x a=%d" % px)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dump":
        for p in sys.argv[2:]:
            print("==", p)
            dump(read_png(p))
    elif cmd == "sheet":
        write_png(sys.argv[2], sheet([read_png(p) for p in sys.argv[4:]], int(sys.argv[3])))
