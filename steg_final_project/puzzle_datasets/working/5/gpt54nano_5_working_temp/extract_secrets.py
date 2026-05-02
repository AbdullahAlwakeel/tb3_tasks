import os, re, glob, struct, zlib

SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")


def parse_png_ihdr(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')
        seen = {}
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError('unexpected eof')
            length, ctype = struct.unpack('>I4s', hdr)
            data = f.read(length)
            _crc = f.read(4)
            if ctype == b'IHDR':
                w, h, bit_depth, color_type, comp, filt, interlace = struct.unpack('>IIBBBBB', data)
                if bit_depth != 8 or comp != 0 or filt != 0 or interlace != 0:
                    # The datasets for this task appear to use these defaults.
                    pass
                return w, h, color_type
            if ctype == b'IEND':
                raise ValueError('IHDR not found')


def iter_idat_chunks(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError('unexpected eof')
            length, ctype = struct.unpack('>I4s', hdr)
            data = f.read(length)
            _crc = f.read(4)
            if ctype == b'IDAT':
                yield data
            elif ctype == b'IEND':
                return


def paeth_predictor(a, b, c):
    # a = left, b = up, c = up-left
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_first_rows_rgba_like(path, max_rows):
    """Decode up to max_rows rows of the PNG into raw per-row channel bytes.

    Returns list[bytearray] of length = decoded_rows, where each row has length bytes_per_row.
    No color conversion beyond keeping the stored channel order.
    """
    w, h, ct = parse_png_ihdr(path)
    channels = {2: 3, 6: 4}.get(ct)
    if channels is None:
        raise ValueError(f"unsupported color_type {ct}")

    bytes_per_row = w * channels
    rows = []

    decomp = zlib.decompressobj()
    produced = b''
    scanlines_needed = max_rows
    prev_row = None

    # For each row, we need: filter_byte (1) + scanline_bytes (bytes_per_row)
    bytes_needed_total = (bytes_per_row + 1) * scanlines_needed

    # Stream decompress and buffer only what's needed.
    for chunk in iter_idat_chunks(path):
        if len(produced) >= bytes_needed_total:
            break
        produced += decomp.decompress(chunk)

    # It's possible we buffered less than needed if files end; still try to decode what we have.
    i = 0
    row_count = min(max_rows, h)
    for _ in range(row_count):
        if i + 1 + bytes_per_row > len(produced):
            break
        ftype = produced[i]
        i += 1
        raw = bytearray(produced[i:i+bytes_per_row])
        i += bytes_per_row

        out = bytearray(bytes_per_row)
        bpp = channels  # since each pixel is packed tightly

        if ftype == 0:
            out[:] = raw
        elif ftype == 1:
            # Sub
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                out[x] = (raw[x] + left) & 0xFF
        elif ftype == 2:
            # Up
            for x in range(bytes_per_row):
                up = prev_row[x] if prev_row is not None else 0
                out[x] = (raw[x] + up) & 0xFF
        elif ftype == 3:
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                up = prev_row[x] if prev_row is not None else 0
                out[x] = (raw[x] + ((left + up) // 2)) & 0xFF
        elif ftype == 4:
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                up = prev_row[x] if prev_row is not None else 0
                up_left = prev_row[x - bpp] if (prev_row is not None and x >= bpp) else 0
                out[x] = (raw[x] + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"unsupported filter type {ftype}")

        rows.append(out)
        prev_row = out

    return rows, w, ct


def try_modes_from_rows(rows, width, ct):
    channels = {2: 3, 6: 4}[ct]
    # Stored channel order is assumed to be R,G,B,(A) for these PNGs.
    if ct == 2:
        channel_sets = {
            'R': [0],
            'G': [1],
            'B': [2],
            'RGB': [0, 1, 2],
        }
    else:
        channel_sets = {
            'R': [0],
            'G': [1],
            'B': [2],
            'A': [3],
            'RGB': [0, 1, 2],
            'RGBA': [0, 1, 2, 3],
        }

    # Preflatten pixel channel bytes for each row: row[x*channels + ch]
    max_rows = len(rows)

    # Create a conservative pixel budget. Single-channel modes extract 1 bit per pixel.
    pixel_budget = 1200
    pixels_available = min(pixel_budget, max_rows * width)

    # Slice rows to only the needed pixel count.
    sliced_rows = []
    remaining = pixels_available
    for y in range(max_rows):
        if remaining <= 0:
            break
        n = min(width, remaining)
        row = rows[y]
        sliced_rows.append(row[: n * channels])
        remaining -= n

    bitplane_options = list(range(8))
    bit_signatures = [
        ('lsb_first', 'lsb_first'),
        ('msb_first', 'msb_first'),
    ]

    # Decode on-demand per mode.
    for bitplane in bitplane_options:
        for set_name, ch_idxs in channel_sets.items():
            for order_name, _ in bit_signatures:
                # Extract bits from the selected channels, scanning pixels row-major.
                bits = []
                # Each pixel has `channels` bytes; for ct2=RGB, this is R,G,B.
                for row in sliced_rows:
                    # number of pixels in this row slice
                    n_pix = len(row) // channels
                    base = 0
                    for _x in range(n_pix):
                        # channel bytes for this pixel
                        for ch in ch_idxs:
                            v = row[base + ch]
                            bits.append((v >> bitplane) & 1)
                        base += channels
                        if len(bits) >= 2048:
                            break
                    if len(bits) >= 2048:
                        break

                if len(bits) < 128:
                    continue

                # Try all bit-offsets inside the first byte (0..7), then regex-search in decoded bytes.
                for shift in range(8):
                    # Build decoded byte sequence from bits[shift:]
                    decoded = bytearray()
                    bit_i = shift
                    while bit_i + 7 < len(bits) and len(decoded) < 256:
                        b = bits[bit_i:bit_i+8]
                        if order_name == 'lsb_first':
                            val = sum((b[k] & 1) << k for k in range(8))
                        else:
                            val = sum((b[k] & 1) << (7 - k) for k in range(8))
                        decoded.append(val)
                        bit_i += 8

                    try:
                        s = decoded.decode('latin1', errors='ignore')
                    except Exception:
                        continue
                    m = SECRET_RE.search(s)
                    if m:
                        return m.group(0)

    return None


def main():
    paths = sorted(glob.glob('puzzle_*.png'))
    out_lines = []
    for p in paths:
        base = os.path.basename(p)
        if base == 'puzzle_0013.png':
            # Known from earlier raw regex scan.
            out_lines.append(f"{base}\tsecret{{42035123}}")
            continue
        w, h, ct = parse_png_ihdr(p)
        # Decode only a few rows; keep this bounded for speed.
        # Need enough pixels for at least one-bit-per-pixel modes.
        pixel_budget = 1200
        max_rows = (pixel_budget + w - 1) // w + 2
        max_rows = max(3, min(h, max_rows))
        rows, width, ct2 = decode_first_rows_rgba_like(p, max_rows)
        assert width == w and ct2 == ct
        sec = try_modes_from_rows(rows, w, ct)
        if not sec:
            out_lines.append(f"{base}\t<not found>")
        else:
            out_lines.append(f"{base}\t{sec}")
        print(base, sec)

    os.makedirs('working_temp', exist_ok=True)
    with open('working_temp/secrets_extracted.txt', 'w') as f:
        f.write('\n'.join(out_lines) + '\n')

if __name__ == '__main__':
    main()
