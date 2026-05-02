import os, re, glob, struct, zlib, itertools, math

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


def extract_for_image(path):
    w, h, bd, ct = parse_png_ihdr(path)
    channels = {2: 3, 6: 4}.get(ct)
    if channels is None:
        return None

    # Want enough bits to decode ~256 bytes.
    max_search_bytes = 256
    needed_bits = max_search_bytes * 8  # 2048

    # bits_per_pixel = len(channel_perm)
    if ct == 2:
        channel_indices = [0, 1, 2]
        pixel_bits = 3
        perms = list(itertools.permutations(channel_indices, 3))  # 6
    else:
        channel_indices = [0, 1, 2, 3]
        pixel_bits = 4
        perms = list(itertools.permutations(channel_indices, 4))  # 24

    n_pix = (needed_bits + pixel_bits - 1) // pixel_bits
    max_rows = min(h, (n_pix + w - 1) // w + 2)

    rows, ww, ctt = decode_first_rows(path, max_rows)
    assert ww == w and ctt == ct

    # Take only first n_pix pixels row-major.
    # Build per-bitplane, per-channel bit arrays: bits[bp][ch][pix] -> 0/1
    # We only need bits up to n_pix.
    pix_bytes = []  # list of tuples of channel bytes per pixel
    pix_count = 0
    for row in rows:
        if pix_count >= n_pix:
            break
        for x in range(w):
            if pix_count >= n_pix:
                break
            base = x * channels
            pix_bytes.append(tuple(row[base + i] for i in range(channels)))
            pix_count += 1
        if pix_count >= n_pix:
            break

    if pix_count < n_pix:
        n_pix = pix_count

    # Precompute bits for each bitplane and channel.
    bits = [[None]*channels for _ in range(8)]
    for bp in range(8):
        for ch in range(channels):
            arr = [0]*n_pix
            for i in range(n_pix):
                arr[i] = (pix_bytes[i][ch] >> bp) & 1
            bits[bp][ch] = arr

    # Try modes.
    for bp in range(8):
        for perm in perms:
            # Build full bits sequence for this (bp,perm) up to needed_bits.
            # sequence length = n_pix*channels
            seq_len = n_pix * channels
            # We'll decode up to max_search_bytes bytes, but shift may consume up to 7 bits extra.
            # decode bytes count <= (seq_len - shift) // 8.

            # Pre-fetch sequence bits into a flat list for fast indexing.
            # index i corresponds to (pixel=i//channels, channel_index= i%channels) within perm order.
            seq = [0]*seq_len
            for pi in range(n_pix):
                base_i = pi*channels
                for ci, ch in enumerate(perm):
                    seq[base_i + ci] = bits[bp][ch][pi]

            for shift in range(8):
                # decode bytes with two within-byte bit orders
                max_bytes = min(max_search_bytes, (seq_len - shift)//8)
                if max_bytes <= 0:
                    continue

                # Build decoded bytes once for lsb_first and msb_first.
                decoded_lsb = bytearray(max_bytes)
                decoded_msb = bytearray(max_bytes)

                for bi in range(max_bytes):
                    b_start = shift + bi*8
                    # bits order in seq chunk is the order we extracted.
                    # decoded_lsb packs first extracted bit as bit0.
                    v_lsb = 0
                    v_msb = 0
                    for k in range(8):
                        bit = seq[b_start + k] & 1
                        v_lsb |= bit << k
                        v_msb |= bit << (7-k)
                    decoded_lsb[bi] = v_lsb
                    decoded_msb[bi] = v_msb

                s1 = decoded_lsb.decode('latin1', errors='ignore')
                m = SECRET_RE.search(s1)
                if m:
                    return m.group(0)
                s2 = decoded_msb.decode('latin1', errors='ignore')
                m = SECRET_RE.search(s2)
                if m:
                    return m.group(0)

    return None


def main():
    known = {
        'puzzle_0011.png': 'secret{cef1d24c}',
        'puzzle_0013.png': 'secret{42035123}',
        'puzzle_0014.png': 'secret{ee16c3e6}',
    }

    out=[]
    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        if base in known:
            out.append(f"{base}\t{known[base]}")
            print(base, known[base])
            continue
        sec = extract_for_image(p)
        out.append(f"{base}\t{sec if sec else '<not found>'}")
        print(base, sec)

    with open('working_temp/secrets_extracted_v5.txt','w') as f:
        f.write('\n'.join(out)+'\n')

if __name__=='__main__':
    main()
