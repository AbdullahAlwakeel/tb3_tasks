import os, re, glob, struct, zlib, base64, subprocess

SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")
HEX_SET = set('0123456789abcdefABCDEF')


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
                if bit_depth != 8 or comp != 0 or filt != 0 or interlace != 0:
                    # dataset uses these defaults
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

    # Decompress only enough to cover max_rows rows.
    decomp = zlib.decompressobj()
    produced = bytearray()
    bytes_needed_total = (bytes_per_row + 1) * max_rows
    for chunk in iter_idat_chunks(path):
        if len(produced) >= bytes_needed_total:
            break
        produced.extend(decomp.decompress(chunk))
        if len(produced) >= bytes_needed_total:
            break

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
                left = out[x-bpp] if x >= bpp else 0
                out[x] = (raw[x] + left) & 0xFF
        elif ftype == 2:
            for x in range(bytes_per_row):
                up = prev[x] if prev is not None else 0
                out[x] = (raw[x] + up) & 0xFF
        elif ftype == 3:
            for x in range(bytes_per_row):
                left = out[x-bpp] if x >= bpp else 0
                up = prev[x] if prev is not None else 0
                out[x] = (raw[x] + ((left + up) // 2)) & 0xFF
        elif ftype == 4:
            for x in range(bytes_per_row):
                left = out[x-bpp] if x >= bpp else 0
                up = prev[x] if prev is not None else 0
                up_left = prev[x-bpp] if (prev is not None and x >= bpp) else 0
                out[x] = (raw[x] + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"unsupported filter {ftype}")

        rows.append(out)
        prev = out

    return rows, w, ct


def unframe(raw: bytes):
    # _SENTINEL is empty for real encoding, so idx=0 always.
    if len(raw) < 4:
        raise ValueError('raw too short')
    length = struct.unpack('>I', raw[:4])[0]
    if length < 0:
        raise ValueError('bad length')
    if len(raw) < 4 + length:
        raise ValueError('truncated payload')
    return raw[4:4+length]


def decode_payload_to_secret(payload_bytes: bytes):
    # First try direct UTF-8
    try:
        s = payload_bytes.decode('utf-8')
        if SECRET_RE.fullmatch(s):
            return s
    except Exception:
        pass

    # Next, try base64 layer (when --use-base64 was used)
    try:
        decoded = base64.b64decode(payload_bytes, validate=False)
        s2 = decoded.decode('utf-8', errors='strict')
        if SECRET_RE.fullmatch(s2):
            return s2
    except Exception:
        pass

    return None


def lsb_embed_extract(path, max_payload_bytes=64):
    rows, w, ct = decode_first_rows(path, max_rows=20)
    channels = {2:3, 6:4}.get(ct)
    if channels is None:
        return None

    max_raw_bytes = 4 + max_payload_bytes
    needed_bits = max_raw_bytes * 8

    # Build flat channel bytes for RGB conversion.
    # For RGB: use all channels; for RGBA: drop alpha.
    flat = bytearray()
    pix_count = 0
    for row in rows:
        n_pix = w
        # ensure not exceed pixels needed; each pixel contributes 3 RGB bytes
        for x in range(n_pix):
            if pix_count*3 >= (needed_bits + 7)//8 * 3: # crude guard
                pass
            if (len(flat) >= needed_bits):
                break
            idx = x*channels
            flat.append(row[idx+0])
            flat.append(row[idx+1])
            flat.append(row[idx+2])
            pix_count += 1
        if len(flat) >= needed_bits:
            break

    if len(flat) < needed_bits:
        return None

    # Convert bits->bytes (big-endian within each byte)
    raw = bytearray(max_raw_bytes)
    for bi in range(max_raw_bytes):
        v = 0
        base = bi*8
        for k in range(8):
            bit = flat[base+k] & 1
            v = (v<<1) | bit
        raw[bi] = v

    try:
        payload = unframe(bytes(raw))
    except Exception:
        return None
    return decode_payload_to_secret(payload)


def alpha_embed_extract(path, max_payload_bytes=64):
    rows, w, ct = decode_first_rows(path, max_rows=50)
    if ct != 6:
        return None
    channels = 4

    max_raw_bytes = 4 + max_payload_bytes
    needed_bits = max_raw_bytes * 8

    flat_alpha = bytearray()
    for row in rows:
        # row is w*4
        for x in range(w):
            if len(flat_alpha) >= needed_bits:
                break
            flat_alpha.append(row[x*channels + 3])
        if len(flat_alpha) >= needed_bits:
            break
    if len(flat_alpha) < needed_bits:
        return None

    raw = bytearray(max_raw_bytes)
    for bi in range(max_raw_bytes):
        v = 0
        base = bi*8
        for k in range(8):
            bit = flat_alpha[base+k] & 1
            v = (v<<1) | bit
        raw[bi] = v

    try:
        payload = unframe(bytes(raw))
    except Exception:
        return None
    return decode_payload_to_secret(payload)


def append_embed_extract(path):
    # tail bytes after IEND
    with open(path,'rb') as f:
        data=f.read()

    # Parse chunks until end of IEND
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return None
    pos=8
    end_iend=None
    while pos+8<=len(data):
        length, ctype = struct.unpack('>I4s', data[pos:pos+8])
        pos += 8
        pos += length
        pos += 4  # CRC
        if ctype == b'IEND':
            end_iend = pos
            break
    if end_iend is None:
        return None
    tail = data[end_iend:]
    # tail might include whitespace/trailer; we need to locate framed payload.
    if len(tail) < 4:
        return None
    # framing for real encoding: _frame(payload)= len(payload)(4 bytes) + payload.
    # So try interpret first 4 bytes as length and check printable secret.
    try:
        length = struct.unpack('>I', tail[:4])[0]
        if length <= 0 or length > 2000:
            return None
        if len(tail) < 4 + length:
            return None
        payload = tail[4:4+length]
        return decode_payload_to_secret(payload)
    except Exception:
        return None


def metadata_embed_extract_exiftool(path):
    # Use exiftool to extract EXIF ImageDescription (base64 framed bytes)
    # If tag missing, it returns nothing.
    cmd = ['exiftool','-s','-s','-s','-ImageDescription','-b',path]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=20).strip()
    except Exception:
        return None
    if not out:
        return None
    # out is base64 string (ASCII)
    try:
        raw_frame = base64.b64decode(out, validate=False)
    except Exception:
        return None
    try:
        payload = unframe(raw_frame)
    except Exception:
        return None
    return decode_payload_to_secret(payload)


def try_ocr_lowcontrast(path):
    # Basic OCR fallback. LowContrastEmbed renders readable text after contrast changes,
    # but we don't have image-processing tooling here; try direct OCR.
    cmd = ['tesseract', path, 'stdout', '--psm', '6', '-l', 'eng',
           'tessedit_char_whitelist=secret0123456789abcdefABCDEF{}']
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=60).decode('utf-8','ignore')
    except Exception:
        return None
    m = SECRET_RE.search(out)
    if m:
        return m.group(0)
    return None


def main():
    results = {}
    for p in sorted(glob.glob('puzzle_*.png')):
        base = os.path.basename(p)
        print('processing', base)
        secret = None

        secret = lsb_embed_extract(p)
        if not secret:
            secret = alpha_embed_extract(p)
        if not secret:
            secret = metadata_embed_extract_exiftool(p)
        if not secret:
            secret = append_embed_extract(p)
        if not secret:
            secret = try_ocr_lowcontrast(p)

        results[base] = secret if secret else '<not found>'
        print(base, results[base])

    with open('working_temp/secrets_extracted_v18.txt','w') as f:
        for k in sorted(results.keys()):
            f.write(f"{k}\t{results[k]}\n")

if __name__=='__main__':
    main()
