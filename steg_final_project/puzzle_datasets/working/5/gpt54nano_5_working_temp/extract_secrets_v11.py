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


def decode_first_rows(path, max_rows):
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


def channel_orders_for_ct(ct):
    if ct == 2:
        return [(0,), (1,), (2,), (0,1,2), (2,1,0)]
    return [(0,), (1,), (2,), (3,), (0,1,2), (2,1,0), (0,1,2,3), (3,2,1,0)]


def extract_bits(rows, w, ct, bitplane, ch_order, max_bits):
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


def decode_byte(bits, start, msb_first):
    # returns byte from 8 bits starting at bits[start]
    b = bits[start:start+8]
    v = 0
    if msb_first:
        # first bit is bit7
        for k in range(8):
            v |= (b[k] & 1) << (7-k)
    else:
        # first bit is bit0
        for k in range(8):
            v |= (b[k] & 1) << k
    return v


def decode_nibble(bits, start, msb_first):
    # 4 bits -> nibble
    b = bits[start:start+4]
    v = 0
    if msb_first:
        for k in range(4):
            v |= (b[k] & 1) << (3-k)
    else:
        for k in range(4):
            v |= (b[k] & 1) << k
    return v


def try_nibble_payload(bits, byte_msb_first, nibble_msb_first, start_byte_idx, shift):
    # Message format assumption in bitstream:
    # - 'secret{' as 7 ASCII bytes (56 bits)
    # - 8 hex digits as 8 nibbles (32 bits)
    # - '}' as final ASCII byte (8 bits)
    # Total = 96 bits.
    # start_byte_idx chooses starting byte within decoded-byte alignment.

    start_bit = shift + start_byte_idx*8
    needed = 96
    if start_bit + needed > len(bits):
        return None

    # Decode 'secret{'
    secret_prefix = b'secret{'
    out = bytearray()
    for i in range(len(secret_prefix)):
        out.append(decode_byte(bits, start_bit + i*8, byte_msb_first))
    if bytes(out) != secret_prefix:
        return None

    # Decode 8 hex nibbles
    hexchars = []
    base = start_bit + 7*8
    for j in range(8):
        n = decode_nibble(bits, base + j*4, nibble_msb_first)
        hexchars.append("0123456789abcdef"[n & 0xF])

    # Decode closing brace
    close = decode_byte(bits, base + 8*4, byte_msb_first)
    if close != ord('}'):
        return None

    token = ("secret{" + ''.join(hexchars) + "}")
    if SECRET_RE.fullmatch(token):
        return token
    return None


def extract_secret(bits, max_start_bytes=256):
    for shift in range(8):
        for byte_msb_first in (False, True):
            for nibble_msb_first in (False, True):
                for start_byte_idx in range(max_start_bytes):
                    tok = try_nibble_payload(bits, byte_msb_first, nibble_msb_first, start_byte_idx, shift)
                    if tok:
                        return tok
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
            print(base, known[base])
            out.append(f"{base}\t{known[base]}")
            continue

        w,h,bd,ct = parse_png_ihdr(p)
        # Need enough bits for up to max_start_bytes*8 + 96.
        # Worst-case extract 8192 bits as in prior scripts.
        pixel_budget = 8200
        max_rows = min(h, (pixel_budget + w - 1)//w + 2)
        rows, ww, ctt = decode_first_rows(p, max_rows)
        assert ww==w and ctt==ct

        channel_orders = channel_orders_for_ct(ct)
        found=None
        for bitplane in range(8):
            for ch_order in channel_orders:
                bits = extract_bits(rows, w, ct, bitplane, ch_order, max_bits=9000)
                if len(bits) < 96:
                    continue
                tok = extract_secret(bits, max_start_bytes=128)
                if tok:
                    found = tok
                    break
            if found:
                break

        print(base, found if found else '<not found>')
        out.append(f"{base}\t{found if found else '<not found>'}")

    with open('working_temp/secrets_extracted_v11.txt','w') as f:
        f.write('\n'.join(out)+'\n')

if __name__ == '__main__':
    main()
