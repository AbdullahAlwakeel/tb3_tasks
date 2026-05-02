import os, re, glob, struct, zlib, base64, subprocess, tempfile

SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")
PNG_SIG = b'\x89PNG\r\n\x1a\n'
HEX_SET = set(b'0123456789abcdefABCDEF')


def is_probably_base64_bytes(b: bytes) -> bool:
    if not b:
        return False
    # base64 is ASCII; ignore whitespace
    s = b.strip()
    if len(s) < 16 or len(s) % 4 != 0:
        return False
    for ch in s:
        if ch in b'\n\r\t ':
            continue
        if not (48 <= ch <= 57 or 65 <= ch <= 90 or 97 <= ch <= 122 or ch in (43, 47, 61)):
            return False
    return True


def decode_secret_from_payload(payload: bytes):
    # direct
    try:
        s = payload.decode('utf-8')
        if SECRET_RE.fullmatch(s):
            return s
    except Exception:
        pass

    # maybe base64
    if is_probably_base64_bytes(payload):
        try:
            raw = base64.b64decode(payload, validate=False)
            try:
                s2 = raw.decode('utf-8')
                if SECRET_RE.fullmatch(s2):
                    return s2
            except Exception:
                pass
        except Exception:
            pass

    # maybe plaintext appears inside
    m = SECRET_RE.search(payload.decode('latin1', errors='ignore'))
    if m:
        return m.group(0)
    return None


def parse_png_ihdr(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != PNG_SIG:
            raise ValueError('not png')
        hdr = f.read(8)
        while hdr:
            if len(hdr) < 8:
                break
            length, ctype = struct.unpack('>I4s', hdr)
            data = f.read(length)
            f.read(4)  # crc
            if ctype == b'IHDR':
                w, h, bit_depth, color_type, comp, filt, interlace = struct.unpack('>IIBBBBB', data)
                return w, h, bit_depth, color_type
            if ctype == b'IEND':
                break
            hdr = f.read(8)
    raise ValueError('IHDR not found')


def iter_png_idat_chunks(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != PNG_SIG:
            raise ValueError('not png')
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                return
            length, ctype = struct.unpack('>I4s', hdr)
            data = f.read(length)
            f.read(4)  # crc
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


def decode_png_first_n_rows(path, max_rows):
    w, h, bd, ct = parse_png_ihdr(path)
    channels = {2: 3, 6: 4}.get(ct)
    if channels is None:
        raise ValueError(f'unsupported color_type {ct}')
    bytes_per_row = w * channels

    decomp = zlib.decompressobj()
    produced = bytearray()
    bytes_needed_total = (bytes_per_row + 1) * max_rows

    for chunk in iter_png_idat_chunks(path):
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
        filter_type = produced[i]
        i += 1
        raw = produced[i:i+bytes_per_row]
        i += bytes_per_row

        out = bytearray(bytes_per_row)
        bpp = channels

        if filter_type == 0:
            out[:] = raw
        elif filter_type == 1:
            # Sub
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                out[x] = (raw[x] + left) & 0xFF
        elif filter_type == 2:
            # Up
            for x in range(bytes_per_row):
                up = prev[x] if prev is not None else 0
                out[x] = (raw[x] + up) & 0xFF
        elif filter_type == 3:
            # Average
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                up = prev[x] if prev is not None else 0
                out[x] = (raw[x] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            # Paeth
            for x in range(bytes_per_row):
                left = out[x - bpp] if x >= bpp else 0
                up = prev[x] if prev is not None else 0
                up_left = prev[x - bpp] if (prev is not None and x >= bpp) else 0
                out[x] = (raw[x] + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f'unsupported filter type {filter_type}')

        rows.append(out)
        prev = out

    return rows, w, h, ct


def lsb_extract_payload(path, max_payload_bytes_cap=2_000_000):
    # Extract framed bytes from LSBEmbed (RGB channels), return payload bytes.
    rows, w, h, ct = decode_png_first_n_rows(path, max_rows=8)
    channels = {2: 3, 6: 4}.get(ct)
    if channels is None:
        return None

    # First, extract 4 bytes (length prefix) from the first bits.
    raw_prefix_len = 4
    bits_needed = raw_prefix_len * 8
    pixel_needed = (bits_needed + 2) // 3  # each pixel contributes 3 bits (R,G,B)
    rows_needed = (pixel_needed + w - 1) // w + 2
    rows, w, h, ct = decode_png_first_n_rows(path, max_rows=rows_needed)

    # Stream bits -> raw_prefix
    raw_prefix = bytearray(raw_prefix_len)
    cur = 0
    bitpos = 0
    out_i = 0

    pixels_seen = 0
    for y in range(min(rows_needed, len(rows))):
        row = rows[y]
        for x in range(w):
            if pixels_seen >= pixel_needed:
                break
            base = x * channels
            for ch in (0, 1, 2):
                bit = row[base + ch] & 1
                cur = (cur << 1) | bit
                bitpos += 1
                if bitpos == 8:
                    raw_prefix[out_i] = cur
                    out_i += 1
                    cur = 0
                    bitpos = 0
                    if out_i == raw_prefix_len:
                        break
            if out_i == raw_prefix_len:
                break
            pixels_seen += 1
        if out_i == raw_prefix_len:
            break

    if out_i != raw_prefix_len:
        return None

    payload_len = struct.unpack('>I', bytes(raw_prefix))[0]
    if payload_len <= 0 or payload_len > max_payload_bytes_cap:
        return None

    raw_total_len = 4 + payload_len
    bits_needed_total = raw_total_len * 8
    pixel_needed_total = (bits_needed_total + 2) // 3
    rows_needed_total = (pixel_needed_total + w - 1) // w + 2

    rows, w, h, ct = decode_png_first_n_rows(path, max_rows=rows_needed_total)
    channels = {2: 3, 6: 4}.get(ct)

    # Stream bits -> raw_total
    raw_total = bytearray(raw_total_len)
    cur = 0
    bitpos = 0
    out_i = 0

    pixels_seen = 0
    for y in range(min(rows_needed_total, len(rows))):
        row = rows[y]
        for x in range(w):
            if pixels_seen >= pixel_needed_total:
                break
            base = x * channels
            for ch in (0, 1, 2):
                bit = row[base + ch] & 1
                cur = (cur << 1) | bit
                bitpos += 1
                if bitpos == 8:
                    raw_total[out_i] = cur
                    out_i += 1
                    cur = 0
                    bitpos = 0
                    if out_i == raw_total_len:
                        break
            if out_i == raw_total_len:
                break
            pixels_seen += 1
        if out_i == raw_total_len:
            break

    if out_i != raw_total_len:
        return None

    return bytes(raw_total[4:4+payload_len])


def alpha_extract_payload(path, max_payload_bytes_cap=2_000_000):
    rows, w, h, ct = decode_png_first_n_rows(path, max_rows=8)
    if ct != 6:
        return None
    channels = 4

    # length prefix = first 4 bytes, each output byte needs 8 bits, each bit from 1 alpha byte.
    raw_prefix_len = 4
    bits_needed = raw_prefix_len * 8
    pixel_needed = bits_needed  # 1 alpha bit per pixel
    rows_needed = (pixel_needed + w - 1) // w + 2

    rows, w, h, ct = decode_png_first_n_rows(path, max_rows=rows_needed)

    raw_prefix = bytearray(raw_prefix_len)
    cur = 0
    bitpos = 0
    out_i = 0

    pixels_seen = 0
    for y in range(min(rows_needed, len(rows))):
        row = rows[y]
        for x in range(w):
            if pixels_seen >= pixel_needed:
                break
            bit = row[x*channels + 3] & 1
            cur = (cur << 1) | bit
            bitpos += 1
            if bitpos == 8:
                raw_prefix[out_i] = cur
                out_i += 1
                cur = 0
                bitpos = 0
                if out_i == raw_prefix_len:
                    break
            pixels_seen += 1
        if out_i == raw_prefix_len:
            break

    if out_i != raw_prefix_len:
        return None

    payload_len = struct.unpack('>I', bytes(raw_prefix))[0]
    if payload_len <= 0 or payload_len > max_payload_bytes_cap:
        return None

    raw_total_len = 4 + payload_len
    bits_needed_total = raw_total_len * 8
    pixel_needed_total = bits_needed_total
    rows_needed_total = (pixel_needed_total + w - 1) // w + 2

    rows, w, h, ct = decode_png_first_n_rows(path, max_rows=rows_needed_total)

    raw_total = bytearray(raw_total_len)
    cur = 0
    bitpos = 0
    out_i = 0

    pixels_seen = 0
    for y in range(min(rows_needed_total, len(rows))):
        row = rows[y]
        for x in range(w):
            if pixels_seen >= pixel_needed_total:
                break
            bit = row[x*channels + 3] & 1
            cur = (cur << 1) | bit
            bitpos += 1
            if bitpos == 8:
                raw_total[out_i] = cur
                out_i += 1
                cur = 0
                bitpos = 0
                if out_i == raw_total_len:
                    break
            pixels_seen += 1
        if out_i == raw_total_len:
            break

    if out_i != raw_total_len:
        return None

    return bytes(raw_total[4:4+payload_len])


def metadata_extract_payload_exiftool(path):
    cmd = ['exiftool','-s','-s','-s','-ImageDescription','-b',path]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=20).strip()
    except Exception:
        return None
    if not out:
        return None

    if isinstance(out, str):
        out = out.encode('utf-8')

    # exiftool outputs the tag value as raw bytes (-b), which for this tag is base64 ASCII.
    try:
        raw_frame = base64.b64decode(out, validate=False)
    except Exception:
        return None

    if len(raw_frame) < 4:
        return None
    payload_len = struct.unpack('>I', raw_frame[:4])[0]
    if payload_len <= 0 or payload_len > 2_000_000:
        return None
    if len(raw_frame) < 4 + payload_len:
        return None
    return raw_frame[4:4+payload_len]


def append_extract_payload(path):
    with open(path,'rb') as f:
        data = f.read()
    if not data.startswith(PNG_SIG):
        return None
    pos = 8
    end_iend = None
    while pos + 12 <= len(data):
        length, ctype = struct.unpack('>I4s', data[pos:pos+8])
        pos += 8
        pos += length
        pos += 4
        if ctype == b'IEND':
            end_iend = pos
            break
    if end_iend is None:
        return None
    tail = data[end_iend:]
    if len(tail) < 4:
        return None
    length = struct.unpack('>I', tail[:4])[0]
    if length <= 0 or length > 2_000_000:
        return None
    if len(tail) < 4 + length:
        return None
    return tail[4:4+length]


def try_payload_to_secret_or_png(payload_bytes, depth, tmp_root):
    sec = decode_secret_from_payload(payload_bytes)
    if sec:
        return sec

    # base64-of-png case
    if is_probably_base64_bytes(payload_bytes):
        try:
            raw = base64.b64decode(payload_bytes, validate=False)
            if raw.startswith(PNG_SIG):
                inner_path = os.path.join(tmp_root, f'inner_d{depth}.png')
                with open(inner_path,'wb') as f:
                    f.write(raw)
                return solve_image(inner_path, depth+1, tmp_root)
        except Exception:
            pass

    if payload_bytes.startswith(PNG_SIG):
        inner_path = os.path.join(tmp_root, f'inner_d{depth}.png')
        with open(inner_path,'wb') as f:
            f.write(payload_bytes)
        return solve_image(inner_path, depth+1, tmp_root)

    return None


def ocr_lowcontrast_secret(path):
    # Simple OCR on whole image (may fail for very faint text), but we'll try.
    cmd = ['tesseract', path, 'stdout', '--psm', '6', '-l', 'eng',
           'tessedit_char_whitelist=secret0123456789abcdefABCDEF{}']
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=120).decode('utf-8','ignore')
    except Exception:
        return None
    m = SECRET_RE.search(out)
    return m.group(0) if m else None


def solve_image(path, depth, tmp_root):
    if depth > 3:
        return None

    # Try peeling via each reversible transform.
    # Order: append, metadata, alpha, lsb.
    for extract_fn in (
        ('append', lambda p: append_extract_payload(p)),
        ('metadata', lambda p: metadata_extract_payload_exiftool(p)),
        ('alpha', lambda p: alpha_extract_payload(p)),
        ('lsb', lambda p: lsb_extract_payload(p)),
    ):
        try:
            payload = extract_fn[1](path)
        except Exception:
            payload = None
        if not payload:
            continue

        # Secret or inner PNG?
        res = try_payload_to_secret_or_png(payload, depth, tmp_root)
        if res:
            return res

    # LowContrast fallback
    return ocr_lowcontrast_secret(path)


def main():
    os.makedirs('working_temp', exist_ok=True)
    out_lines=[]
    tmp_root = tempfile.mkdtemp(prefix='stego_solve_', dir='working_temp')

    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        print('solving', base)
        sec = solve_image(p, depth=0, tmp_root=tmp_root)
        out_lines.append(f"{base}\t{sec if sec else '<not found>'}")
        print(base, out_lines[-1].split('\t')[1])

    with open('working_temp/secrets_extracted_v19.txt','w') as f:
        f.write('\n'.join(out_lines)+'\n')

if __name__=='__main__':
    main()
