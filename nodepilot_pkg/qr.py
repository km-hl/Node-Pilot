"""Pure-Python ISO 18004 QR Code encoder and terminal renderer (Byte mode, ECC L)."""

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _EXP[_i + 255] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D

# (version, ec_per_block, blocks_g1, data_g1, blocks_g2, data_g2) for ECC Level L.
_LEVEL_L = (
    (1, 7, 1, 19, 0, 0),
    (2, 10, 1, 34, 0, 0),
    (3, 15, 1, 55, 0, 0),
    (4, 20, 1, 80, 0, 0),
    (5, 26, 1, 108, 0, 0),
    (6, 18, 2, 68, 0, 0),
    (7, 20, 2, 78, 0, 0),
    (8, 24, 2, 97, 0, 0),
    (9, 30, 2, 116, 0, 0),
    (10, 18, 2, 68, 2, 69),
    (11, 20, 4, 81, 0, 0),
    (12, 24, 2, 92, 2, 93),
    (13, 26, 4, 107, 0, 0),
    (14, 30, 3, 115, 1, 116),
    (15, 22, 5, 87, 1, 88),
    (16, 24, 5, 98, 1, 99),
)

_ALIGN = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
    11: (6, 30, 54),
    12: (6, 32, 58),
    13: (6, 34, 62),
    14: (6, 26, 46, 66),
    15: (6, 26, 48, 70),
    16: (6, 26, 50, 74),
}

_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0, 12: 0, 13: 0, 14: 3, 15: 3, 16: 3}

_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_gen(degree):
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        root = _EXP[i]
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= _gf_mul(coeff, root)
        poly = nxt
    return poly


def _rs_encode(data, ec_len):
    gen = _rs_gen(ec_len)
    rem = list(data) + [0] * ec_len
    for i in range(len(data)):
        factor = rem[i]
        if factor:
            for j in range(1, len(gen)):
                rem[i + j] ^= _gf_mul(gen[j], factor)
    return bytes(rem[len(data):])


def _format_bits(mask):
    data = (0b01 << 3) | mask
    rem = data << 10
    for shift in range(4, -1, -1):
        if rem & (1 << (shift + 10)):
            rem ^= 0x537 << shift
    return ((data << 10) | rem) ^ 0x5412


def _version_bits(version):
    rem = version << 12
    for shift in range(5, -1, -1):
        if rem & (1 << (shift + 12)):
            rem ^= 0x1F25 << shift
    return (version << 12) | rem


def _encode_data(raw):
    for ver, ec_len, b1, d1, b2, d2 in _LEVEL_L:
        cap = b1 * d1 + b2 * d2
        count_bits = 8 if ver < 10 else 16
        if 4 + count_bits + len(raw) * 8 <= cap * 8:
            break
    else:
        raise ValueError("内容过长，超出终端二维码版本 16 上限。")
    bits = []

    def push(val, width):
        for s in range(width - 1, -1, -1):
            bits.append((val >> s) & 1)

    push(0b0100, 4)
    push(len(raw), count_bits)
    for byte in raw:
        push(byte, 8)
    total_bits = cap * 8
    term = min(4, total_bits - len(bits))
    bits.extend([0] * term)
    while len(bits) % 8:
        bits.append(0)
    out = bytearray()
    for i in range(0, len(bits), 8):
        b = 0
        for j in range(8):
            b = (b << 1) | bits[i + j]
        out.append(b)
    pad = (0xEC, 0x11)
    idx = 0
    while len(out) < cap:
        out.append(pad[idx & 1])
        idx += 1

    data_blocks, ec_blocks = [], []
    pos = 0
    for count, length in ((b1, d1), (b2, d2)):
        for _ in range(count):
            blk = bytes(out[pos:pos + length])
            pos += length
            data_blocks.append(blk)
            ec_blocks.append(_rs_encode(blk, ec_len))

    stream = bytearray()
    max_d = max(d1, d2)
    for i in range(max_d):
        for blk in data_blocks:
            if i < len(blk):
                stream.append(blk[i])
    for i in range(ec_len):
        for blk in ec_blocks:
            stream.append(blk[i])

    bitstream = []
    for byte in stream:
        for s in range(7, -1, -1):
            bitstream.append((byte >> s) & 1)
    bitstream.extend([0] * _REMAINDER_BITS[ver])
    return ver, bitstream


def _build_base(ver):
    size = ver * 4 + 17
    grid = [[False] * size for _ in range(size)]
    func = [[False] * size for _ in range(size)]

    def put(r, c, val):
        if 0 <= r < size and 0 <= c < size:
            grid[r][c] = bool(val)
            func[r][c] = True

    def finder(top, left):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top + dr, left + dc
                if 0 <= r < size and 0 <= c < size:
                    dark = (0 <= dr <= 6 and 0 <= dc <= 6 and (
                        dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)
                    ))
                    put(r, c, dark)

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):
        val = i % 2 == 0
        put(6, i, val)
        put(i, 6, val)

    centers = _ALIGN[ver]
    for r in centers:
        for c in centers:
            if func[r][c]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)

    for i in range(9):
        if i != 6:
            func[8][i] = True
            func[i][8] = True
    for i in range(8):
        func[8][size - 1 - i] = True
        if i < 7:
            func[size - 1 - i][8] = True
    put(size - 8, 8, True)

    if ver >= 7:
        vbits = _version_bits(ver)
        for i in range(18):
            bit = bool((vbits >> i) & 1)
            r, c = i // 3, size - 11 + (i % 3)
            put(r, c, bit)
            put(c, r, bit)
    return size, grid, func


def _apply_format(grid, size, mask):
    bits = _format_bits(mask)
    for i in range(15):
        bit = bool((bits >> i) & 1)
        if i < 6:
            grid[8][i] = bit
        elif i == 6:
            grid[8][7] = bit
        elif i == 7:
            grid[8][8] = bit
        elif i == 8:
            grid[7][8] = bit
        else:
            grid[14 - i][8] = bit
        if i < 7:
            grid[size - 1 - i][8] = bit
        else:
            grid[8][size - 15 + i] = bit


def _penalty(grid):
    n = len(grid)
    score = 0
    for r in range(n):
        run_len = 1
        for c in range(1, n):
            if grid[r][c] == grid[r][c - 1]:
                run_len += 1
            else:
                if run_len >= 5:
                    score += run_len - 2
                run_len = 1
        if run_len >= 5:
            score += run_len - 2
    for c in range(n):
        run_len = 1
        for r in range(1, n):
            if grid[r][c] == grid[r - 1][c]:
                run_len += 1
            else:
                if run_len >= 5:
                    score += run_len - 2
                run_len = 1
        if run_len >= 5:
            score += run_len - 2
    for r in range(n - 1):
        for c in range(n - 1):
            v = grid[r][c]
            if v == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]:
                score += 3
    p1 = (True, False, True, True, True, False, True, False, False, False, False)
    p2 = (False, False, False, False, True, False, True, True, True, False, True)
    for row in grid:
        for i in range(n - 10):
            seg = tuple(row[i:i + 11])
            if seg == p1 or seg == p2:
                score += 40
    for c in range(n):
        col = [grid[r][c] for r in range(n)]
        for i in range(n - 10):
            seg = tuple(col[i:i + 11])
            if seg == p1 or seg == p2:
                score += 40
    dark = sum(1 for r in grid for cell in r if cell)
    k = abs((dark * 100 // (n * n)) - 50) // 5
    score += k * 10
    return score


def matrix(text):
    """Return a square boolean matrix (True = dark module) for UTF-8 text."""
    ver, bitstream = _encode_data(text.encode("utf-8"))
    size, base, func = _build_base(ver)
    idx = 0
    up = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not func[r][c]:
                    base[r][c] = bool(bitstream[idx]) if idx < len(bitstream) else False
                    idx += 1
        up = not up
        col -= 2

    best_score, best_grid = None, None
    for mask_id, fn in enumerate(_MASKS):
        cand = [row[:] for row in base]
        for r in range(size):
            for c in range(size):
                if not func[r][c] and fn(r, c):
                    cand[r][c] = not cand[r][c]
        _apply_format(cand, size, mask_id)
        sc = _penalty(cand)
        if best_score is None or sc < best_score:
            best_score, best_grid = sc, cand
    return best_grid


def render(text, compact=True, border=2):
    """Render QR code for terminal display (UTF-8 half-blocks or plain ASCII)."""
    grid = matrix(text)
    size = len(grid)
    padded = size + border * 2

    def cell(r, c):
        rr, cc = r - border, c - border
        return grid[rr][cc] if 0 <= rr < size and 0 <= cc < size else False

    if compact:
        lines = []
        # Light-module background uses space, dark uses black modules for standard white-on-black / black-on-white contrast:
        # In dark SSH terminals, invert=True makes quiet-zone/light white (█) and dark modules black (' '),
        # which phone cameras scan reliably on dark terminals.
        for r in range(0, padded, 2):
            row_chars = []
            for c in range(padded):
                top = not cell(r, c)
                bot = not cell(r + 1, c) if r + 1 < padded else True
                if top and bot:
                    row_chars.append("█")
                elif top:
                    row_chars.append("▀")
                elif bot:
                    row_chars.append("▄")
                else:
                    row_chars.append(" ")
            lines.append("".join(row_chars))
        return "\n".join(lines)
    lines = []
    for r in range(padded):
        lines.append("".join("##" if cell(r, c) else "  " for c in range(padded)))
    return "\n".join(lines)
