import os, re, struct, zlib
from typing import List, Tuple, Optional

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def direct_bytes_search(path: str) -> Optional[str]:
    try:
        with open(path, 'rb') as f:
            b = f.read()
        m = SECRET_RE.search(b)
        return m.group(0).decode('ascii') if m else None
    except Exception:
        return None


def decode_png_prefix(path: str, pixels_limit: int = 2048) -> Tuple[int, int, int, bytes]:
    """Return (width, height, channels, raw_prefix_bytes) where raw_prefix_bytes is interleaved channel bytes for the first rows."""
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')

        width = height = bit_depth = color_type = None
        channels = None
        idat_chunks = []

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
                    raise ValueError(f'unsupported bit depth {bit_depth}')
                if color_type == 2:
                    channels = 3
                elif color_type == 6:
                    channels = 4
                else:
                    raise ValueError(f'unsupported color type {color_type}')
            elif ctype == b'IDAT':
                idat_chunks.append(data)
            elif ctype == b'IEND':
                break

        if width is None or channels is None:
            raise ValueError('missing IHDR')

        stride = width * channels
        rows_needed = (pixels_limit + width - 1) // width
        if rows_needed < 1:
            rows_needed = 1
        if rows_needed > height:
            rows_needed = height

        needed_raw_len = rows_needed * (1 + stride)

        # Streaming decompress until we have enough bytes.
        decomp = zlib.decompressobj()
        out = bytearray()
        for chunk in idat_chunks:
            if len(out) >= needed_raw_len:
                break
            out.extend(decomp.decompress(chunk))
        out = bytes(out[:needed_raw_len])

        # Reconstruct scanlines
        # We'll only materialize the interleaved channel bytes for the prefix.
        out_pixels = bytearray(rows_needed * stride)
        prev = bytearray(stride)
        in_off = 0
        out_off = 0
        bpp = channels

        for _row in range(rows_needed):
            ftype = out[in_off]
            in_off += 1
            scan = out[in_off:in_off + stride]
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
                    recon[i] = (scan[i] + prev[i]) & 0xFF
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

            out_pixels[out_off:out_off + stride] = recon
            out_off += stride
            prev = recon

        return width, rows_needed, channels, bytes(out_pixels)


def try_lsb_bytes(raw_prefix: bytes, channels: int, mask: Tuple[int, ...], bit_plane: int, byte_order: str, max_bytes: int = 256) -> Optional[str]:
    k = len(mask)
    pixel_cap = len(raw_prefix) // channels
    if pixel_cap <= 0:
        return None

    target_bits = max_bytes * 8
    max_bits_available = pixel_cap * k
    bits_to_take = min(target_bits, max_bits_available)
    if bits_to_take < 16 * 8:  # need at least 16 bytes of bits to ever fit the token
        # but keep going anyway if shorter; pattern might not need full
        pass

    bytes_to_take = bits_to_take // 8
    if bytes_to_take <= 0:
        return None

    # Precompute bit values for all channels and bit planes for the pixels we have.
    # Cache local to speed: bits[ch][b][i] -> 0/1
    # Since we call this for many attempts, caching would normally be better outside, but we keep it local here.

    # Build cache lazily: for this mask and bit_plane only.
    # We need bit_plane fixed, so just compute bits per channel for that plane.
    bits_by_ch = {}
    for ch in set(mask):
        arr = bytearray(pixel_cap)
        base = ch
        # raw_prefix is interleaved channels per pixel: pixel i => raw[i*channels + ch]
        for i in range(pixel_cap):
            arr[i] = (raw_prefix[i * channels + base] >> bit_plane) & 1
        bits_by_ch[ch] = arr

    out = bytearray(bytes_to_take)
    if byte_order == 'msb':
        for byte_i in range(bytes_to_take):
            b = 0
            t0 = byte_i * 8
            for j in range(8):
                t = t0 + j
                pixel_i = t // k
                mi = t % k
                ch = mask[mi]
                bit = bits_by_ch[ch][pixel_i]
                b = (b << 1) | bit
            out[byte_i] = b
    else:  # lsb
        for byte_i in range(bytes_to_take):
            b = 0
            t0 = byte_i * 8
            for j in range(8):
                t = t0 + j
                pixel_i = t // k
                mi = t % k
                ch = mask[mi]
                bit = bits_by_ch[ch][pixel_i]
                b |= (bit & 1) << j
            out[byte_i] = b

    m = SECRET_RE.search(bytes(out))
    return m.group(0).decode('ascii') if m else None


def lsb_extract_secret(path: str, pixels_limit: int = 2048, max_bytes: int = 256) -> Optional[str]:
    width, _rows, channels, raw_prefix = decode_png_prefix(path, pixels_limit=pixels_limit)

    all_ch = tuple(range(channels))

    # Candidate masks: singles, pairs, all.
    masks: List[Tuple[int, ...]] = []
    for ch in all_ch:
        masks.append((ch,))
    for i in range(len(all_ch)):
        for j in range(i + 1, len(all_ch)):
            masks.append((all_ch[i], all_ch[j]))
    masks.append(all_ch)

    for bit_plane in range(8):
        for mask in masks:
            for order in ('msb', 'lsb'):
                sec = try_lsb_bytes(raw_prefix, channels, mask, bit_plane, order, max_bytes=max_bytes)
                if sec:
                    return sec
    return None


def main():
    root = '.'
    files = sorted([f for f in os.listdir(root) if f.lower().endswith('.png') and f.startswith('puzzle_')])
    out_path = os.path.join('working_temp', 'fast_secrets.txt')
    os.makedirs('working_temp', exist_ok=True)

    # reset
    open(out_path, 'w').close()

    for fn in files:
        path = os.path.join(root, fn)
        sec = direct_bytes_search(path)
        method = 'direct_bytes'
        if not sec:
            sec = lsb_extract_secret(path)
            method = 'lsb'
        if sec:
            with open(out_path, 'a') as w:
                w.write(f"{fn}\t{sec}\t{method}\n")
            print(fn, sec, method)
        else:
            with open(out_path, 'a') as w:
                w.write(f"{fn}\tNO_SECRET\n")
            print(fn, 'NO_SECRET')


if __name__ == '__main__':
    main()
