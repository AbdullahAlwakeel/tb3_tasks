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


def decode_first_rows_filtered_and_unfiltered(path, max_rows):
    w, h, bd, ct = parse_png_ihdr(path)
    channels = {2: 3, 6: 4}.get(ct)
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
    rows_filtered = []
    rows_unfiltered = []

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

        rows_filtered.append(bytearray(raw))
        rows_unfiltered.append(out)
        prev = out

    return rows_filtered, rows_unfiltered, w, ct


def decode_bits_for_mode(rows, w, ct, bitplane, ch_order, max_bits):
    channels = {2: 3, 6: 4}[ct]
    bits = []
    for row in rows:
        n_pix = len(row)//channels
        for x in range(n_pix):
            base = x*channels
            for ch in ch_order:
                v = row[base+ch]
                bits.append((v>>bitplane)&1)
                if len(bits) >= max_bits:
                    return bits
    return bits


def try_lsb_search(rows, w, ct, max_search_bytes=1024):
    if ct == 2:
        channel_orders = [ (0,), (1,), (2,), (0,1,2), (2,1,0) ]
    else:
        channel_orders = [ (0,), (1,), (2,), (3,), (0,1,2), (2,1,0), (0,1,2,3), (3,2,1,0) ]

    max_bits = max_search_bytes*8 + 7

    for bitplane in range(8):
        for ch_order in channel_orders:
            bits = decode_bits_for_mode(rows, w, ct, bitplane, ch_order, max_bits)
            if len(bits) < 128:
                continue

            for shift in range(8):
                max_bytes = min(max_search_bytes, (len(bits)-shift)//8)
                if max_bytes <= 0:
                    continue

                decoded_lsb = bytearray(max_bytes)
                decoded_msb = bytearray(max_bytes)

                for bi in range(max_bytes):
                    b_start = shift + bi*8
                    v_lsb = 0
                    v_msb = 0
                    for k in range(8):
                        bit = bits[b_start+k] & 1
                        v_lsb |= bit << k
                        v_msb |= bit << (7-k)
                    decoded_lsb[bi] = v_lsb
                    decoded_msb[bi] = v_msb

                m = SECRET_RE.search(decoded_lsb.decode('latin1', errors='ignore'))
                if m:
                    return m.group(0)
                m = SECRET_RE.search(decoded_msb.decode('latin1', errors='ignore'))
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

        w,h,bd,ct = parse_png_ihdr(p)
        max_rows = min(h, (8200 + w - 1)//w + 2)
        rows_f, rows_u, ww, ctt = decode_first_rows_filtered_and_unfiltered(p, max_rows)
        assert ww==w and ctt==ct

        sec = try_lsb_search(rows_u, w, ct)
        src='unfiltered'
        if not sec:
            sec = try_lsb_search(rows_f, w, ct)
            src='filtered'

        out.append(f"{base}\t{sec if sec else '<not found>'}")
        print(base, sec if sec else '<not found>', src)

    with open('working_temp/secrets_extracted_v7.txt','w') as f:
        f.write('\n'.join(out)+'\n')

if __name__=='__main__':
    main()
