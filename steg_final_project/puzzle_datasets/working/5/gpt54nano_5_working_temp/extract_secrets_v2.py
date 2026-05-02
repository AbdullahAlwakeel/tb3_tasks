import os, re, glob, struct, zlib, itertools

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
                return w, h, bit_depth, color_type
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
    w, h, bd, ct = parse_png_ihdr(path)
    channels = {2: 3, 6: 4}.get(ct)
    if channels is None:
        raise ValueError(f"unsupported color_type {ct}")
    if bd != 8:
        raise ValueError('unexpected bit depth')

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
    row_count = min(max_rows, h)
    for _ in range(row_count):
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


def channel_orders_for_ct(ct):
    # Stored channel order in these PNGs is byte order: RGB for ct=2, RGBA for ct=6.
    if ct == 2:
        canonical = [0,1,2]
    else:
        canonical = [0,1,2,3]

    orders = []
    n = len(canonical)
    for mask in range(1, 1<<n):
        idxs = [canonical[i] for i in range(n) if (mask>>i)&1]
        if len(idxs) == 1:
            orders.append(tuple(idxs))
        else:
            # try relative order (canonical) and its reverse
            orders.append(tuple(idxs))
            orders.append(tuple(reversed(idxs)))
    # de-dup while keeping order
    seen=set(); out=[]
    for o in orders:
        if o not in seen:
            seen.add(o); out.append(o)
    return out


def try_lsb_modes(rows, w, ct, max_bits=40000):
    orders = channel_orders_for_ct(ct)
    channels = {2: 3, 6: 4}[ct]
    bytes_per_pixel = channels

    # We'll scan pixels row-major across the decoded rows.
    # For each mode, we stop after producing enough bits to cover search windows.
    # We decode bytes using both bit orderings.

    for bitplane in range(8):
        for ch_idxs in orders:
            # precompute per pixel which byte positions are used within a row
            # pixel x => row base = x*channels
            if not ch_idxs:
                continue
            for sig_name, pack in [('lsb_first',0), ('msb_first',1)]:
                bits = []
                # collect bits until max_bits
                for row in rows:
                    n_pix = w
                    for x in range(n_pix):
                        base = x * channels
                        for ch in ch_idxs:
                            v = row[base + ch]
                            bits.append((v >> bitplane) & 1)
                            if len(bits) >= max_bits:
                                break
                        if len(bits) >= max_bits:
                            break
                    if len(bits) >= max_bits:
                        break

                if len(bits) < 128:
                    continue

                # Now search for secret within decoded bytes for each bit shift 0..7.
                for shift in range(8):
                    decoded = bytearray()
                    bit_i = shift
                    # We'll decode up to 4096 bytes max, bounded by available bits.
                    max_dec_bytes = min(4096, (len(bits)-shift)//8)
                    for j in range(max_dec_bytes):
                        b = bits[bit_i:bit_i+8]
                        if sig_name == 'lsb_first':
                            val = sum((b[k] & 1) << k for k in range(8))
                        else:
                            val = sum((b[k] & 1) << (7 - k) for k in range(8))
                        decoded.append(val)
                        bit_i += 8
                    # Quick string search
                    s = decoded.decode('latin1', errors='ignore')
                    m = SECRET_RE.search(s)
                    if m:
                        return m.group(0)

    return None


def main():
    known = {
        'puzzle_0011.png': 'secret{cef1d24c}',
        'puzzle_0013.png': 'secret{42035123}',
        'puzzle_0014.png': 'secret{ee16c3e6}',
    }

    out_lines=[]
    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        if base in known:
            out_lines.append(f"{base}\t{known[base]}")
            continue
        w,h,bd,ct = parse_png_ihdr(p)
        # Decode enough pixels for early secret. We may still miss if secret is far, but this is a big jump vs v1.
        pixel_budget = 20000
        max_rows = (pixel_budget + w - 1)//w + 3
        max_rows = min(h, max(3, max_rows))
        rows, ww, ctt = decode_first_rows(p, max_rows)
        assert ww==w and ctt==ct
        sec = try_lsb_modes(rows, w, ct, max_bits=60000)
        out_lines.append(f"{base}\t{sec if sec else '<not found>'}")
        print(base, sec)

    with open('working_temp/secrets_extracted_v2.txt','w') as f:
        f.write('\n'.join(out_lines)+'\n')

if __name__=='__main__':
    main()
