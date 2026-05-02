#!/usr/bin/env python3
import math
import os
import re
import struct
import zlib


SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def parse_png_bytes(p: str):
    data = open(p, "rb").read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a png")

    # Iterate chunks; gather IHDR and IDAT.
    pos = 8
    w = h = bit_depth = color_type = interlace = None
    idat = []
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos : pos + 4])[0]
        typ = data[pos + 4 : pos + 8]
        cdata = data[pos + 8 : pos + 8 + ln]
        pos = pos + 8 + ln
        # skip crc
        pos += 4
        if typ == b"IHDR":
            w, h, bit_depth, color_type, _cm, _fm, interlace = struct.unpack(
                ">IIBBBBB", cdata
            )
        elif typ == b"IDAT":
            idat.append(cdata)
        elif typ == b"IEND":
            break
    if w is None or h is None:
        raise ValueError("missing IHDR")
    if bit_depth != 8 or interlace != 0:
        raise ValueError(f"unsupported png bit_depth={bit_depth} interlace={interlace}")
    if color_type == 2:
        ch = 3
    elif color_type == 6:
        ch = 4
    else:
        raise ValueError(f"unsupported color_type={color_type}")
    return w, h, ch, zlib.decompress(b"".join(idat))


def recon_and_filtered(png_inflated: bytes, w: int, h: int, ch: int):
    """
    Returns (filtered, reconstructed), both as flat byte arrays of length w*h*ch.
    filtered: bytes from scanlines (after filter byte removed, but BEFORE reverse-filter)
    reconstructed: reverse-filtered bytes (actual pixel bytes).
    """
    stride = w * ch
    per_line = stride + 1
    if len(png_inflated) < h * per_line:
        raise ValueError("inflated shorter than expected")

    filtered = bytearray(w * h * ch)
    reconstructed = bytearray(w * h * ch)

    prev = bytearray(stride)
    off_out = 0
    off_in = 0
    for _y in range(h):
        ftype = png_inflated[off_in]
        off_in += 1
        scan = png_inflated[off_in : off_in + stride]
        off_in += stride

        filtered[off_out : off_out + stride] = scan
        recon = bytearray(stride)

        if ftype == 0:  # None
            recon[:] = scan
        elif ftype == 1:  # Sub
            for i in range(stride):
                left = recon[i - ch] if i >= ch else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = recon[i - ch] if i >= ch else 0
                up = prev[i]
                recon[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = recon[i - ch] if i >= ch else 0
                up = prev[i]
                up_left = prev[i - ch] if i >= ch else 0
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                pr = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                recon[i] = (scan[i] + pr) & 0xFF
        else:
            raise ValueError(f"bad filter type {ftype}")

        reconstructed[off_out : off_out + stride] = recon
        prev = recon
        off_out += stride

    return filtered, reconstructed


def bits_from_samples(samples, k_bits: int, take_end: str, within_order: str):
    """
    take_end:
      - 'lsb': take k_bits least significant bits from each sample byte
      - 'msb': take k_bits most significant bits from each sample byte
    within_order:
      - 'lsb_first': emit bits from low to high within the selected k_bits
      - 'msb_first': emit bits from high to low within the selected k_bits
    """
    mask = (1 << k_bits) - 1
    bits = []
    if take_end == "lsb":
        for b in samples:
            v = b & mask
            if within_order == "lsb_first":
                for i in range(k_bits):
                    bits.append((v >> i) & 1)
            else:
                for i in range(k_bits - 1, -1, -1):
                    bits.append((v >> i) & 1)
    else:
        shift = 8 - k_bits
        for b in samples:
            v = (b >> shift) & mask
            if within_order == "lsb_first":
                for i in range(k_bits):
                    bits.append((v >> i) & 1)
            else:
                for i in range(k_bits - 1, -1, -1):
                    bits.append((v >> i) & 1)
    return bits


def bits_to_bytes(bits, msb_first: bool):
    nbytes = len(bits) // 8
    out = bytearray(nbytes)
    for j in range(nbytes):
        chunk = bits[j * 8 : (j + 1) * 8]
        if msb_first:
            v = 0
            for bit in chunk:
                v = (v << 1) | bit
        else:
            v = 0
            for i, bit in enumerate(chunk):
                v |= (bit & 1) << i
        out[j] = v
    return bytes(out)


def try_decode_samples(samples, decode_bytes_max=1024):
    """
    Try common LSB/MSB bit-packing variants.
    Returns secret string if found else None.
    """
    # secret is 16 bytes; allow searching in first decode_bytes_max bytes.
    # Worst case: 1 bit per sample => need 8*decode_bytes_max bits.
    # If we don't have enough bits, skip.
    for k_bits in (1, 2, 3, 4):
        # Quick feasibility: max bits we can create from samples.
        max_bits_available = len(samples) * k_bits
        if max_bits_available < 8 * 16:
            continue

        for take_end in ("lsb", "msb"):
            for within_order in ("msb_first", "lsb_first"):
                bits = bits_from_samples(samples, k_bits=k_bits, take_end=take_end, within_order=within_order)
                # We'll try bit alignment offsets 0..7 (up to 1 byte) for grouping into bytes.
                for align in range(0, 8):
                    if len(bits) - align < 8 * 16:
                        continue
                    # Decode up to decode_bytes_max bytes.
                    slice_bits = bits[align:]
                    nbytes = min(decode_bytes_max, len(slice_bits) // 8)
                    if nbytes < 16:
                        continue
                    decoded = bits_to_bytes(slice_bits[: 8 * nbytes], msb_first=True)
                    m = SECRET_RE.search(decoded)
                    if m:
                        inner = m.group(0)[len(b"secret{") : -1]
                        return "secret{" + inner.decode("ascii").lower() + "}"
                    decoded = bits_to_bytes(slice_bits[: 8 * nbytes], msb_first=False)
                    m = SECRET_RE.search(decoded)
                    if m:
                        inner = m.group(0)[len(b"secret{") : -1]
                        return "secret{" + inner.decode("ascii").lower() + "}"
    return None


def channel_orders(ch: int):
    if ch == 3:
        return {
            "r": [0],
            "g": [1],
            "b": [2],
            "rgb": [0, 1, 2],
            "bgr": [2, 1, 0],
        }
    if ch == 4:
        return {
            "r": [0],
            "g": [1],
            "b": [2],
            "a": [3],
            "rgb": [0, 1, 2],
            "rgba": [0, 1, 2, 3],
        }
    raise ValueError("bad ch")


def sample_sequence(pix_bytes: bytes, w: int, h: int, ch: int, start_y: int, max_samples: int, order: str, ci_list):
    """
    pix_bytes: flat length w*h*ch, row-major.
    order:
      - 'row': y outer, x inner
      - 'col': x outer, y inner
    """
    stride = w * ch
    out = []
    if order == "row":
        for y in range(start_y, h):
            rowoff = y * stride
            for x in range(w):
                base = rowoff + x * ch
                for ci in ci_list:
                    out.append(pix_bytes[base + ci])
                    if len(out) >= max_samples:
                        return out
    elif order == "col":
        for x in range(w):
            for y in range(start_y, h):
                base = (y * w + x) * ch
                for ci in ci_list:
                    out.append(pix_bytes[base + ci])
                    if len(out) >= max_samples:
                        return out
    else:
        raise ValueError("bad order")
    return out


def find_secret_for_image(img_path: str):
    w, h, ch, inflated = parse_png_bytes(img_path)
    filtered, reconstructed = recon_and_filtered(inflated, w=w, h=h, ch=ch)

    # If the embedding is near the start of the scan order, a small window is enough.
    # We'll also try a few later start offsets in case the sequence begins later.
    start_y_candidates = [0, max(0, h // 8), max(0, h // 4), max(0, h // 2)]

    max_samples = 20000  # enough for k=1 to decode/search up to ~2k bytes.

    for mode in ("reconstructed", "filtered"):
        pix = reconstructed if mode == "reconstructed" else filtered
        for start_y in start_y_candidates:
            for order in ("row", "col"):
                for _name, ci_list in channel_orders(ch).items():
                    samples = sample_sequence(pix, w, h, ch, start_y=start_y, max_samples=max_samples, order=order, ci_list=ci_list)
                    if len(samples) < 256:
                        continue
                    sec = try_decode_samples(samples, decode_bytes_max=1024)
                    if sec:
                        return sec
    return None


def main():
    targets = ["puzzle_0009.png", "puzzle_0010.png", "puzzle_0011.png", "puzzle_0012.png"]
    for t in targets:
        p = os.path.join(".", t)
        sec = find_secret_for_image(p)
        print(t, sec or "<NOT_FOUND>")


if __name__ == "__main__":
    main()

