import os, re, glob, struct, zlib

SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")


def parse_png_ihdr(path):
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
            if ctype == b'IHDR':
                w, h, bit_depth, color_type, comp, filt, interlace = struct.unpack('>IIBBBBB', data)
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
                return
            length, ctype = struct.unpack('>I4s', hdr)
            data = f.read(length)
            _crc = f.read(4)
            if ctype == b'IDAT':
                yield data
            elif ctype == b'IEND':
                return


def paeth_predictor(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_first_rows(path, max_rows):
    w, h, ct = parse_png_ihdr(path)
    channels = {2: 3, 6: 4}.get(ct)
    if channels is None:
        raise ValueError(f"unsupported color_type {ct}")

    bytes_per_row = w * channels

    decomp = zlib.decompressobj()
    produced = b''
    bytes_needed_total = (bytes_per_row + 1) * max_rows

    for chunk in iter_idat_chunks(path):
        if len(produced) >= bytes_needed_total:
            break
        produced += decomp.decompress(chunk)

    i = 0
    prev = None
    rows = []
    for _ in range(min(max_rows, h)):
        if i + 1 + bytes_per_row > len(produced):
            break
        ftype = produced[i]
        i += 1
        raw = produced[i:i+bytes_per_row]
        i += bytes_per_row

        out = bytearray(bytes_per_row)
        bpp = channels

        if ftype == 0:
            out[:] = raw
        elif ftype == 1:
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                out[x] = (raw[x] + left) & 0xFF
        elif ftype == 2:
            for x in range(bytes_per_row):
                up = prev[x] if prev is not None else 0
                out[x] = (raw[x] + up) & 0xFF
        elif ftype == 3:
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                up = prev[x] if prev is not None else 0
                out[x] = (raw[x] + ((left + up) // 2)) & 0xFF
        elif ftype == 4:
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                up = prev[x] if prev is not None else 0
                up_left = prev[x - bpp] if (prev is not None and x >= bpp) else 0
                out[x] = (raw[x] + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"unsupported filter {ftype}")

        rows.append(out)
        prev = out

    return rows, w, ct


def build_bitplanes_patterns():
    patterns = []
    # d=1 (single plane)
    for b in range(8):
        patterns.append((1, (b,), 'forward'))
    # d=2..4 using consecutive planes
    for d in (2,3,4):
        for start in range(8 - d + 1):
            planes = tuple(range(start, start + d))
            patterns.append((d, planes, 'forward'))
            patterns.append((d, tuple(reversed(planes)), 'reverse'))
    # de-dup
    seen=set(); out=[]
    for p in patterns:
        key=p[1]  # planes tuple
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def extract_secret(rows, w, ct):
    channels = {2: 3, 6: 4}[ct]

    if ct == 2:
        channel_orders = [
            (0,), (1,), (2,),
            (0,1,2),
            (2,1,0),
        ]
    else:
        channel_orders = [
            (0,), (1,), (2,), (3,),
            (0,1,2),
            (2,1,0),
            (0,1,2,3),
            (3,2,1,0),
        ]

    patterns = build_bitplanes_patterns()

    max_bits = 2048  # enough for <=256 decoded bytes
    max_search_bytes = 256

    for planes_len, planes, _tag in patterns:
        for ch_order in channel_orders:
            # bits per pixel = len(ch_order) * planes_len
            bits = []
            for row in rows:
                n_pix = len(row) // channels
                for x in range(n_pix):
                    base = x * channels
                    for ch in ch_order:
                        v = row[base + ch]
                        for bp in planes:
                            bits.append((v >> bp) & 1)
                            if len(bits) >= max_bits:
                                break
                        if len(bits) >= max_bits:
                            break
                    if len(bits) >= max_bits:
                        break
                if len(bits) >= max_bits:
                    break

            if len(bits) < 128:
                continue

            for shift in range(8):
                for msb_mode in (0, 1):
                    decoded = bytearray()
                    bit_i = shift
                    # decode up to max_search_bytes
                    while bit_i + 7 < len(bits) and len(decoded) < max_search_bytes:
                        b0 = bits[bit_i:bit_i+8]
                        if msb_mode == 0:
                            val = 0
                            for k in range(8):
                                val |= (b0[k] & 1) << k
                        else:
                            val = 0
                            for k in range(8):
                                val |= (b0[k] & 1) << (7 - k)
                        decoded.append(val)
                        bit_i += 8

                    s = decoded.decode('latin1', errors='ignore')
                    m = SECRET_RE.search(s)
                    if m:
                        return m.group(0)

    return None


def main():
    known = {
        'puzzle_0001.png': None,
        'puzzle_0011.png': 'secret{cef1d24c}',
        'puzzle_0013.png': 'secret{42035123}',
        'puzzle_0014.png': 'secret{ee16c3e6}',
    }

    out_lines=[]
    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        if base in known and known[base]:
            sec=known[base]
            out_lines.append(f"{base}\t{sec}")
            print(base, sec)
            continue

        w,h,ct = parse_png_ihdr(p)
        pixel_needed = 2048
        max_rows = min(h, (pixel_needed + w - 1)//w + 4)
        rows, ww, ctt = decode_first_rows(p, max_rows)
        assert ww==w and ctt==ct

        sec = extract_secret(rows, w, ct)
        out_lines.append(f"{base}\t{sec if sec else '<not found>'}")
        print(base, sec)

    with open('working_temp/secrets_extracted_v4.txt','w') as f:
        f.write('\n'.join(out_lines)+'\n')

if __name__ == '__main__':
    main()
