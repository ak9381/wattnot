"""A small QR code encoder: byte mode, error correction level M, versions 1 to 10.

Enough for a URL plus a 64-character hash (up to 213 bytes). No dependencies. It returns the
module matrix, and `to_svg` draws it. Written from the ISO 18004 tables; the tests compare it
module by module with a reference encoder when one is installed.
"""

from __future__ import annotations

# (data codewords per block, ec codewords per block, blocks) per version, level M.
# Versions 8-10 have two block sizes: a list of (count, data_per_block).
_EC_PER_BLOCK = {1: 10, 2: 16, 3: 26, 4: 18, 5: 24, 6: 16, 7: 18, 8: 22, 9: 22, 10: 26}
_BLOCKS = {1: [(1, 16)], 2: [(1, 28)], 3: [(1, 44)], 4: [(2, 32)], 5: [(2, 43)],
           6: [(4, 27)], 7: [(4, 31)], 8: [(2, 38), (2, 39)], 9: [(3, 36), (2, 37)],
           10: [(4, 43), (1, 44)]}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
          8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
MAX_BYTES = {v: sum(n * d for n, d in b) - (2 if v < 10 else 3) for v, b in _BLOCKS.items()}

# GF(256) with the QR polynomial 0x11D
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _rs_generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            if c:
                nxt[j + 1] ^= _EXP[_LOG[c] + i]
        g = nxt
    return g


def _rs_remainder(data: list[int], n: int) -> list[int]:
    gen = _rs_generator(n)
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]
        rem = rem[1:] + [0]
        if f:
            for i in range(n):
                if gen[i + 1]:
                    rem[i] ^= _EXP[_LOG[gen[i + 1]] + _LOG[f]]
    return rem


def _bits(value: int, n: int) -> list[int]:
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def _pick_version(n: int) -> int:
    for v in range(1, 11):
        if n <= MAX_BYTES[v]:
            return v
    raise ValueError(f"text of {n} bytes is too long for this encoder (max {MAX_BYTES[10]})")


def _codewords(data: bytes, version: int) -> list[int]:
    cap = sum(n * d for n, d in _BLOCKS[version])
    bits = _bits(0b0100, 4) + _bits(len(data), 8 if version < 10 else 16)
    for b in data:
        bits += _bits(b, 8)
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(words) < cap:
        words.append(pad[i % 2])
        i += 1
    blocks: list[list[int]] = []
    pos = 0
    for count, size in _BLOCKS[version]:
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    ec = [_rs_remainder(b, _EC_PER_BLOCK[version]) for b in blocks]
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(_EC_PER_BLOCK[version]):
        for e in ec:
            out.append(e[i])
    return out


class _Matrix:
    def __init__(self, version: int) -> None:
        self.n = 17 + 4 * version
        self.v = version
        self.m = [[0] * self.n for _ in range(self.n)]
        self.fn = [[False] * self.n for _ in range(self.n)]  # function modules
        self._function_patterns()

    def _set(self, r: int, c: int, dark: int) -> None:
        self.m[r][c] = dark
        self.fn[r][c] = True

    def _finder(self, r0: int, c0: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if 0 <= r < self.n and 0 <= c < self.n:
                    inside = 0 <= dr <= 6 and 0 <= dc <= 6
                    dark = inside and (dr in (0, 6) or dc in (0, 6)
                                       or (2 <= dr <= 4 and 2 <= dc <= 4))
                    self._set(r, c, int(dark))

    def _function_patterns(self) -> None:
        n = self.n
        self._finder(0, 0)
        self._finder(0, n - 7)
        self._finder(n - 7, 0)
        for i in range(8, n - 8):
            self._set(6, i, int(i % 2 == 0))
            self._set(i, 6, int(i % 2 == 0))
        pos = _ALIGN[self.v]
        for r in pos:
            for c in pos:
                if (r, c) in ((6, 6), (6, pos[-1]), (pos[-1], 6)) and len(pos) > 0 \
                        and not (r == pos[-1] and c == pos[-1]):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self._set(r + dr, c + dc, int(max(abs(dr), abs(dc)) != 1))
        self._set(n - 8, 8, 1)  # the always-dark module
        self._format(0)  # reserve
        if self.v >= 7:
            self._version_info()

    def _format(self, mask: int) -> None:
        n = self.n
        data = (0b00 << 3) | mask  # level M = 00
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        b = [(bits >> i) & 1 for i in range(15)]
        for i in range(0, 6):
            self._set(i, 8, b[i])
        self._set(7, 8, b[6])
        self._set(8, 8, b[7])
        self._set(8, 7, b[8])
        for i in range(9, 15):
            self._set(8, 14 - i, b[i])
        for i in range(0, 8):
            self._set(8, n - 1 - i, b[i])
        for i in range(8, 15):
            self._set(n - 15 + i, 8, b[i])
        self._set(n - 8, 8, 1)

    def _version_info(self) -> None:
        rem = self.v
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (self.v << 12) | rem
        for i in range(18):
            bit = (bits >> i) & 1
            a, b = self.n - 11 + i % 3, i // 3
            self._set(a, b, bit)
            self._set(b, a, bit)

    def place(self, words: list[int]) -> None:
        bits = [b for w in words for b in _bits(w, 8)]
        i = 0
        c = self.n - 1
        up = True
        while c > 0:
            if c == 6:
                c -= 1
            rows = range(self.n - 1, -1, -1) if up else range(self.n)
            for r in rows:
                for cc in (c, c - 1):
                    if not self.fn[r][cc]:
                        self.m[r][cc] = bits[i] if i < len(bits) else 0
                        i += 1
            up = not up
            c -= 2

    def masked(self, mask: int) -> list[list[int]]:
        f = _MASKS[mask]
        out = [row[:] for row in self.m]
        for r in range(self.n):
            for c in range(self.n):
                if not self.fn[r][c] and f(r, c):
                    out[r][c] ^= 1
        return out


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _penalty(g: list[list[int]]) -> int:
    n = len(g)
    score = 0
    for lines in (g, [list(col) for col in zip(*g, strict=True)]):
        for line in lines:
            run = 1
            for i in range(1, n):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run = 1
            if run >= 5:
                score += run - 2
            s = "".join(map(str, line))
            score += 40 * (s.count("10111010000") + s.count("00001011101"))
    for r in range(n - 1):
        for c in range(n - 1):
            if g[r][c] == g[r + 1][c] == g[r][c + 1] == g[r + 1][c + 1]:
                score += 3
    dark = sum(map(sum, g))
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def encode(text: str, *, mask: int | None = None) -> list[list[int]]:
    """Return the QR module matrix (1 = dark) for `text`, level M."""

    data = text.encode("utf-8")
    version = _pick_version(len(data))
    mx = _Matrix(version)
    mx.place(_codewords(data, version))
    best = None
    for k in ([mask] if mask is not None else range(8)):
        mx._format(k)
        g = mx.masked(k)
        # the format bits were written into mx.m; copy them over the masked grid
        for r in range(mx.n):
            for c in range(mx.n):
                if mx.fn[r][c]:
                    g[r][c] = mx.m[r][c]
        s = _penalty(g)
        if best is None or s < best[0]:
            best = (s, g)
    return best[1]


def to_svg(matrix: list[list[int]], *, scale: int = 4, border: int = 4,
           title: str = "QR code") -> str:
    n = len(matrix)
    size = (n + 2 * border) * scale
    rects = "".join(f'<rect x="{(c + border) * scale}" y="{(r + border) * scale}" '
                    f'width="{scale}" height="{scale}"/>'
                    for r, row in enumerate(matrix) for c, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'width="{size}" height="{size}" role="img" aria-label="{title}">'
            f'<rect width="{size}" height="{size}" fill="#fff"/>'
            f'<g fill="#000" shape-rendering="crispEdges">{rects}</g></svg>')
