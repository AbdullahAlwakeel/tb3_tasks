import os, re, struct, zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def decode_png_pixels(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')

        width = height = bit_depth = color_type = None
        channels = None
        idat_parts = []

        while True:
            len_bytes = f.read(4)
            if not len_bytes:
                break
            (length,) = struct.unpack('>I', len_bytes)
            ctype = f.read(4)
            data = f.read(length)
            _crc = f.read(4)

            if ctype == b'IHDR':
                width, height, bit_depth, color_type, _cm, _flt, _il = struct.unpack('>IIBBBBB', data)
                if bit_depth != 8:
                    raise ValueError(f'unsupported bit depth: {bit_depth}')
                if color_type == 2:
                    channels = 3
                elif color_type == 6:
                    channels = 4
                else:
                    raise ValueError(f'unsupported color type: {color_type}')
            elif ctype == b'IDAT':
                idat_parts.append(data)
            elif ctype == b'IEND':
                break

        if width is None or channels is None:
            raise ValueError('missing IHDR')

        raw = zlib.decompress(b''.join(idat_parts))
        bpp = channels  # bytes per pixel since bd=8
        bytes_per_scanline = width * channels
        stride = bytes_per_scanline
        expected = height * (1 + bytes_per_scanline)
        if len(raw) != expected:
            # Some encoders may include extra data; be permissive but ensure we can parse.
            if len(raw) < expected:
                raise ValueError(f'not enough decompressed data: {len(raw)} < {expected}')
            raw = raw[:expected]

        out = bytearray(height * stride)
        prev = bytearray(bytes_per_scanline)

        in_off = 0
        out_off = 0
        for _row in range(height):
            ftype = raw[in_off]
            in_off += 1
            scan = raw[in_off:in_off + stride]
            in_off += stride

            recon = bytearray(stride)

            if ftype == 0:
                recon[:] = scan
            elif ftype == 1:
                for i in range(stride):
                    left = recon[i - bpp] if i >= bpp else 0
                    recon[i] = (scan[i] + left) & 0xFF
            elif ftype == 2:
                for i in range(stride):
                    up = prev[i]
                    recon[i] = (scan[i] + up) & 0xFF
            elif ftype == 3:
                for i in range(stride):
                    left = recon[i - bpp] if i >= bpp else 0
                    up = prev[i]
                    recon[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
            elif ftype == 4:
                for i in range(stride):
                    left = recon[i - bpp] if i >= bpp else 0
                    up = prev[i]
                    up_left = prev[i - bpp] if i >= bpp else 0
                    p = left + up - up_left
                    pa = abs(p - left)
                    pb = abs(p - up)
                    pc = abs(p - up_left)
                    if pa <= pb and pa <= pc:
                        pr = left
                    elif pb <= pc:
                        pr = up
                    else:
                        pr = up_left
                    recon[i] = (scan[i] + pr) & 0xFF
            else:
                raise ValueError(f'unknown filter type {ftype}')

            out[out_off:out_off + stride] = recon
            out_off += stride
            prev = recon

        return width, height, channels, out


def try_extract(raw, channels, channel_mask, bit_plane, byte_order, max_bytes=128):
    # channel_mask is an iterable of channel indices
    mask = list(channel_mask)
    bits_per_pixel = len(mask)
    if bits_per_pixel <= 0:
        return None

    bytes_needed = max_bytes
    total_bits_needed = bytes_needed * 8
    pixels_needed = (total_bits_needed + bits_per_pixel - 1) // bits_per_pixel

    # Limit to available pixels; raw is interleaved
    max_pixels = len(raw) // channels
    pixels_needed = min(pixels_needed, max_pixels)

    bits = []
    # Preallocate list capacity to reduce reallocations
    bits = [0] * (pixels_needed * bits_per_pixel)
    bi = 0
    for p in range(pixels_needed):
        base = p * channels
        for ch in mask:
            bits[bi] = (raw[base + ch] >> bit_plane) & 1
            bi += 1

    bits = bits[: total_bits_needed]

    out = bytearray(bytes_needed)
    if byte_order == 'msb':
        for i in range(bytes_needed):
            b = 0
            for j in range(8):
                b = (b << 1) | bits[i * 8 + j]
            out[i] = b
    else:  # lsb
        for i in range(bytes_needed):
            b = 0
            for j in range(8):
                b |= (bits[i * 8 + j] & 1) << j
            out[i] = b

    m = SECRET_RE.search(bytes(out))
    return m.group(0).decode('ascii') if m else None


def lsb_search_for_file(path, debug_dir=None):
    width, height, channels, raw = decode_png_pixels(path)

    if channels == 3:
        all_ch = [0,1,2]
    else:
        all_ch = [0,1,2,3]

    # Candidate masks: single channels, all channels, and pairs.
    masks = []
    for ch in all_ch:
        masks.append((ch,))
    for i in range(len(all_ch)):
        for j in range(i+1, len(all_ch)):
            masks.append((all_ch[i], all_ch[j]))
    masks.append(tuple(all_ch))

    for bit_plane in range(8):
        for mask in masks:
            for order in ('msb','lsb'):
                sec = try_extract(raw, channels, mask, bit_plane, order)
                if sec:
                    return sec, {
                        'channels': channels,
                        'mask': mask,
                        'bit_plane': bit_plane,
                        'byte_order': order,
                        'method': 'lsb'
                    }
    return None, None


if __name__ == '__main__':
    root = '.'
    out_path = 'working_temp/lsb_results.txt'
    dbg_path = 'working_temp/lsb_debug.txt'

    open(out_path, 'w').close()
    open(dbg_path, 'w').close()

    for fn in sorted([f for f in os.listdir(root) if f.lower().endswith('.png') and f.startswith('puzzle_')]):
        path = os.path.join(root, fn)
        try:
            sec, info = lsb_search_for_file(path)
            if sec:
                with open(out_path, 'a') as w:
                    w.write(f"{fn}\t{sec}\t{info}\n")
                print(fn, sec)
            else:
                with open(dbg_path,'a') as w:
                    w.write(f"{fn}\tNO_SECRET\n")
                print(fn, 'NO')
        except Exception as e:
            with open(dbg_path,'a') as w:
                w.write(f"{fn}\tERROR\t{type(e).__name__}: {e}\n")
            print(fn, 'ERROR', type(e).__name__, e)
