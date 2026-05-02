#!/usr/bin/env python3
import math
import os
import re
import struct
import zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def parse_png(p: str):
    data = open(p, "rb").read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not png")
    pos = 8
    w = h = bit_depth = color_type = interlace = None
    idat = []
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos : pos + 4])[0]
        typ = data[pos + 4 : pos + 8]
        cdata = data[pos + 8 : pos + 8 + ln]
        # Skip: length(4) + type(4) + data(ln) + crc(4)
        pos += 12 + ln
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
    inflated = zlib.decompress(b"".join(idat))
    return w, h, ch, inflated


def filtered_flat_from_inflated(inflated: bytes, w: int, h: int, ch: int) -> bytes:
    # Inflated stream layout:
    #   for each y: 1 byte filter_type + (w*ch) filtered bytes
    stride = w * ch
    per_line = stride + 1
    if len(inflated) < h * per_line:
        raise ValueError("inflated shorter than expected")
    out = bytearray(w * h * ch)
    src_off = 0
    dst_off = 0
    for _y in range(h):
        _ftype = inflated[src_off]
        src_off += 1
        out[dst_off : dst_off + stride] = inflated[src_off : src_off + stride]
        src_off += stride
        dst_off += stride
    return bytes(out)


def bits_from_sample_values(samples, k_bits: int, take_end: str, within_order: str):
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


def bits_to_bytes(bits, msb_first: bool, nbytes: int):
    out = bytearray(nbytes)
    bit_count = 8 * nbytes
    bits = bits[:bit_count]
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


def try_decode(samples: list[int], decode_bytes_max: int) -> str | None:
    if len(samples) < 1:
        return None
    for k_bits in (1, 2, 3, 4):
        if len(samples) * k_bits < 8 * 16:
            continue
        nbytes = min(decode_bytes_max, (len(samples) * k_bits) // 8)
        if nbytes < 16:
            continue
        for take_end in ("lsb", "msb"):
            for within_order in ("msb_first", "lsb_first"):
                bits = bits_from_sample_values(samples, k_bits=k_bits, take_end=take_end, within_order=within_order)
                # Search using both byte packing endianness variants.
                for msb_first in (True, False):
                    for align in range(0, 8):
                        if len(bits) - align < 8 * 16:
                            continue
                        decoded = bits_to_bytes(bits[align:], msb_first=msb_first, nbytes=nbytes)
                        m = SECRET_RE.search(decoded)
                        if m:
                            inner = m.group(0)[len(b"secret{") : -1].decode("ascii").lower()
                            return f"secret{{{inner}}}"
    return None


def channel_sets(ch: int):
    if ch == 3:
        return [[0], [1], [2], [0, 1, 2]]
    if ch == 4:
        return [[0], [1], [2], [3], [0, 1, 2], [0, 1, 2, 3]]
    raise ValueError("bad ch")


def sample_sequence(filtered_flat: bytes, w: int, h: int, ch: int, start_y: int, order: str, ci_list: list[int], max_samples: int):
    stride = w * ch
    out = []
    if order == "row":
        for y in range(start_y, h):
            rowoff = y * stride
            for x in range(w):
                base = rowoff + x * ch
                for ci in ci_list:
                    out.append(filtered_flat[base + ci])
                    if len(out) >= max_samples:
                        return out
    elif order == "col":
        for x in range(w):
            for y in range(start_y, h):
                base = (y * w + x) * ch
                for ci in ci_list:
                    out.append(filtered_flat[base + ci])
                    if len(out) >= max_samples:
                        return out
    elif order == "row_rev":
        for y in range(start_y, h):
            rowoff = y * stride
            for x in range(w - 1, -1, -1):
                base = rowoff + x * ch
                for ci in ci_list:
                    out.append(filtered_flat[base + ci])
                    if len(out) >= max_samples:
                        return out
    elif order == "col_rev":
        for x in range(w - 1, -1, -1):
            for y in range(start_y, h):
                base = (y * w + x) * ch
                for ci in ci_list:
                    out.append(filtered_flat[base + ci])
                    if len(out) >= max_samples:
                        return out
    else:
        raise ValueError("bad order")
    return out


def find_secret(img_path: str) -> str | None:
    w, h, ch, inflated = parse_png(img_path)
    filtered_flat = filtered_flat_from_inflated(inflated, w=w, h=h, ch=ch)

    # For secrets likely embedded early in the chosen scan order, start positions in scanline space help.
    start_y_candidates = [0, max(0, h // 8), max(0, h // 4), max(0, h // 2)]
    orders = ("row", "col", "row_rev", "col_rev")
    # Max samples: enough to decode up to ~512 bytes when using 1 bit/sample mode.
    decode_bytes_max = 512

    # For k_bits=1, need ~8*decode_bytes_max bits => decode_bytes_max*8 samples.
    max_samples = decode_bytes_max * 8

    for start_y in start_y_candidates:
        for order in orders:
            for ci_list in channel_sets(ch):
                samples = sample_sequence(
                    filtered_flat, w=w, h=h, ch=ch,
                    start_y=start_y, order=order, ci_list=ci_list,
                    max_samples=max_samples,
                )
                sec = try_decode(samples, decode_bytes_max=decode_bytes_max)
                if sec:
                    return sec
    return None


def main():
    targets = ["puzzle_0009.png", "puzzle_0010.png", "puzzle_0011.png", "puzzle_0012.png"]
    for t in targets:
        sec = find_secret(t)
        print(t, sec or "<NOT_FOUND>")


if __name__ == "__main__":
    main()
